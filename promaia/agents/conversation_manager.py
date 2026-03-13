"""
Platform-agnostic conversation manager.

Manages multi-turn conversations across any messaging platform (Slack, Discord, etc.).
Handles conversation state, timeouts, security validation, and AI responses.

Supports orchestrator integration via completion callbacks that fire when
conversations end (timeout, user signal, max turns, or explicit /done command).
"""

import sqlite3
import json
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable
from dataclasses import dataclass, asdict
import logging

from promaia.agents.messaging.base import BaseMessagingPlatform

logger = logging.getLogger(__name__)

# Callback type for conversation end events
# Takes (conversation_id: str, transcript: List[Dict], reason: str) -> None
ConversationEndCallback = Callable[[str, List[Dict[str, Any]], str], Awaitable[None]]

# Patterns that indicate user wants to end conversation
END_CONVERSATION_PATTERNS = [
    # Explicit goodbyes
    r'\b(bye|goodbye|bye-bye|byebye)\b',
    r'\b(see you|see ya|catch you later|talk (to you )?later)\b',
    r'\b(cya|peace out|ttyl)\b',  # Removed standalone "later" - too ambiguous
    r'^\s*(later|k bye|ok bye)\s*[!.]*\s*$',  # "later" only when it's the entire message

    # Need to leave (must be at end of sentence or followed by time indicators)
    r'\b(gotta (go|run)|gtg)\b\s*[!.]*\s*$',  # "gotta go" / "gtg" at end of message
    r'\b(have to|need to) (go|run|leave)\s*(now|soon|right now)?\s*[!.]*\s*$',  # "need to go" at end of message

    # Completion/ending phrases
    r'\b(that\'?s (all|it)|all done|done for now|we\'?re done|i\'?m done)\b',

    # More flexible "end conversation" patterns (without requiring "this/the")
    r'\bend\s+(the\s+)?(conversation|chat|convo)(?:\s+please)?\b',  # "end conversation", "end the conversation", "end conversation please"
    r'\b(wrap(ping)? up|finish(ing)? up)\b',  # "wrap up", "wrapping up", "finish up", "finishing up"
    r'\b(call it|that (will )?do it)\b',

    # Thanks + ending (at end of message)
    r'\b(thanks|thank you|thx|ty|appreciate it)\b.*\b(bye|end|done|enough)\b',
    r'\b(thanks|thank you|thx|ty)\s*[!.]*\s*$',  # Thanks at very end

    # Standalone end/done/quit words (must be alone or at end)
    r'^\s*(end|done|quit|stop)\s*[!.]*\s*$',  # Just "end", "done", "quit", or "stop"

    # Slash commands
    r'^/(done|end|exit|quit)\s*$',
]


@dataclass
class ConversationState:
    """
    Platform-agnostic conversation state.

    Tracks everything needed to maintain a conversation regardless of platform.
    """
    conversation_id: str
    agent_id: str
    platform: str  # "slack" or "discord"
    channel_id: str
    user_id: str
    thread_id: Optional[str]
    status: str  # active, waiting, timeout, completed, ended_by_user, dormant, paused, stopped
    last_message_at: str  # ISO format datetime
    messages: List[Dict[str, Any]]  # Full conversation history
    context: Dict[str, Any]  # Agent-specific context
    timeout_seconds: int = 900  # 15 minutes default
    max_turns: Optional[int] = None  # Unlimited if None
    turn_count: int = 0
    malicious_attempt_count: int = 0
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    completion_reason: Optional[str] = None  # Why conversation ended
    orchestrator_task_id: Optional[str] = None  # Link to orchestrator task
    cached_context: Optional[str] = None  # Cached preloaded context (markdown format)
    conversation_type: str = "direct"  # "direct" (1:1) or "tag_to_chat" (thread-based, multi-user)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationState':
        """Create from dictionary."""
        return cls(**data)

    def get_transcript(self) -> List[Dict[str, Any]]:
        """Get the conversation transcript (messages only)."""
        return self.messages.copy()


class ConversationManager:
    """
    Platform-agnostic conversation manager.

    This is the core of the conversational AI system. It:
    - Tracks conversation state across all platforms
    - Routes messages to the appropriate platform
    - Handles timeouts and conversation lifecycle
    - Validates security
    - Generates AI responses
    - Fires completion callbacks for orchestrator integration
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize conversation manager.

        Args:
            db_path: Path to SQLite database (default: maia-data/data/conversations.db)
        """
        self.platforms: Dict[str, BaseMessagingPlatform] = {}
        self.security = None  # Lazy import to avoid circular dependency

        # Completion callbacks for orchestrator integration
        self._end_callbacks: List[ConversationEndCallback] = []

        # Agent cache for performance (avoid reloading config on every message)
        self._agent_cache: Dict[str, Any] = {}  # agent_id -> AgentConfig
        self._agent_cache_mtime: Optional[float] = None  # config file modification time

        # SDK client pool for reuse across turns (significant performance boost)
        self._sdk_client_pool: Dict[str, Any] = {}  # conversation_id -> SDK client

        # Database setup
        if db_path is None:
            from promaia.utils.env_writer import get_conversations_db_path
            db_path = get_conversations_db_path()

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_database()

        logger.info(f"Conversation manager initialized (db: {self.db_path})")

    def _reload_agent_cache_if_needed(self):
        """Reload agent cache if config file has changed."""
        from promaia.agents.agent_config import get_config_file_path, load_agents
        from pathlib import Path

        # Check both config files for changes
        config_path = get_config_file_path()
        from promaia.agents.agent_config import _get_agents_file
        agents_path = _get_agents_file()

        # Get most recent modification time from both files
        current_mtime = 0
        if config_path.exists():
            current_mtime = max(current_mtime, config_path.stat().st_mtime)
        if agents_path.exists():
            current_mtime = max(current_mtime, agents_path.stat().st_mtime)

        if current_mtime == 0:
            logger.debug("No config files exist, skipping cache reload")
            return

        # Reload if first time or either file has changed
        if self._agent_cache_mtime is None or current_mtime > self._agent_cache_mtime:
            logger.info("🔄 Reloading agent cache (config changed)")
            agents = load_agents()
            self._agent_cache = {a.agent_id: a for a in agents}
            self._agent_cache_mtime = current_mtime
            logger.info(f"✅ Cached {len(self._agent_cache)} agents")

    def _get_cached_agent(self, agent_id: str) -> Optional[Any]:
        """
        Get agent from cache, reloading if config has changed.

        Args:
            agent_id: Agent ID or name to look up

        Returns:
            AgentConfig if found, None otherwise
        """
        self._reload_agent_cache_if_needed()

        # Try by agent_id first
        if agent_id in self._agent_cache:
            return self._agent_cache[agent_id]

        # Try by name as fallback
        for agent in self._agent_cache.values():
            if agent.name == agent_id:
                return agent

        return None

    def register_end_callback(self, callback: ConversationEndCallback):
        """
        Register a callback to be called when conversations end.

        This is used by the orchestrator to track conversation task completion.

        Args:
            callback: Async function taking (conversation_id, transcript, reason)
        """
        self._end_callbacks.append(callback)
        logger.debug(f"Registered conversation end callback ({len(self._end_callbacks)} total)")

    def unregister_end_callback(self, callback: ConversationEndCallback):
        """Remove a previously registered callback."""
        if callback in self._end_callbacks:
            self._end_callbacks.remove(callback)

    async def _fire_end_callbacks(
        self,
        conversation_id: str,
        transcript: List[Dict[str, Any]],
        reason: str
    ):
        """Fire all registered end callbacks."""
        for callback in self._end_callbacks:
            try:
                await callback(conversation_id, transcript, reason)
            except Exception as e:
                logger.error(f"Error in conversation end callback: {e}", exc_info=True)
    
    def _init_database(self):
        """Initialize SQLite database schema."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT,
                    status TEXT NOT NULL,
                    last_message_at TEXT NOT NULL,
                    turn_count INTEGER DEFAULT 0,
                    max_turns INTEGER,
                    messages TEXT NOT NULL,
                    context TEXT NOT NULL,
                    timeout_seconds INTEGER DEFAULT 900,
                    malicious_attempt_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    completion_reason TEXT,
                    orchestrator_task_id TEXT,
                    cached_context TEXT
                )
            """)

            # Migration: Add cached_context column if it doesn't exist
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN cached_context TEXT")
                logger.info("✅ Added cached_context column to conversations table")
            except Exception:
                # Column already exists
                pass

            # Create index for fast lookup of active conversations
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_active_conversations
                ON conversations(platform, channel_id, user_id, status)
            """)

            # Add new columns if they don't exist (for existing databases)
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN completion_reason TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN orchestrator_task_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Migration: Add conversation_type column for tag-to-chat
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN conversation_type TEXT DEFAULT 'direct'")
                logger.info("Added conversation_type column to conversations table")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Index for thread-based lookups (tag-to-chat uses thread_id matching)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_thread_conversations
                ON conversations(platform, thread_id, status)
            """)

            conn.commit()
            logger.debug("Database schema initialized")
    
    def register_platform(self, name: str, platform: BaseMessagingPlatform):
        """
        Register a messaging platform.
        
        Args:
            name: Platform name ("slack", "discord", etc.)
            platform: Platform implementation
        """
        self.platforms[name] = platform
        logger.info(f"Registered messaging platform: {name}")
    
    async def start_conversation(
        self,
        agent_id: str,
        platform: str,
        channel_id: str,
        initial_message: str,
        user_id: Optional[str] = None,
        timeout_minutes: int = 15,
        max_turns: Optional[int] = None,
        orchestrator_task_id: Optional[str] = None
    ) -> ConversationState:
        """
        Start a new conversation on any platform.
        
        Args:
            agent_id: ID of the agent initiating conversation
            platform: Platform name ("slack" or "discord")
            channel_id: Platform-specific channel ID
            initial_message: First message to send
            user_id: Optional user ID (will be set from message metadata if None)
            timeout_minutes: Minutes before timeout (default 15)
            max_turns: Maximum conversation turns (None = unlimited)
        
        Returns:
            ConversationState object
        
        Raises:
            ValueError: If platform not registered
        """
        if platform not in self.platforms:
            raise ValueError(f"Platform '{platform}' not registered. Available: {list(self.platforms.keys())}")
        
        platform_impl = self.platforms[platform]
        
        # Send initial message
        logger.info(f"Starting conversation for agent {agent_id} on {platform} in channel {channel_id}")
        
        msg_meta = await platform_impl.send_message(
            channel_id=channel_id,
            content=platform_impl.format_message(initial_message, agent_id)
        )
        
        # Create conversation state
        now = datetime.now(timezone.utc).isoformat()
        conversation_id = f"{platform}_{channel_id}_{msg_meta.timestamp}"

        # Don't use threads in DMs (channel IDs starting with 'D')
        is_dm = channel_id.startswith('D')

        state = ConversationState(
            conversation_id=conversation_id,
            agent_id=agent_id,
            platform=platform,
            channel_id=channel_id,
            user_id=user_id or msg_meta.user_id,
            thread_id=None if is_dm else msg_meta.thread_id,
            status='active',
            last_message_at=now,
            messages=[{
                'role': 'assistant',
                'content': initial_message,
                'timestamp': now
            }],
            context={},
            timeout_seconds=timeout_minutes * 60,
            max_turns=max_turns,
            turn_count=0,
            created_at=now,
            orchestrator_task_id=orchestrator_task_id
        )
        
        await self._save_state(state)
        
        logger.info(f"Conversation started: {conversation_id}")
        return state
    
    async def handle_user_message(
        self,
        conversation_id: str,
        user_message: str,
        user_id: str
    ) -> str:
        """
        Handle incoming user message and generate AI response.
        
        This is platform-agnostic - works for any platform.
        
        Args:
            conversation_id: Conversation identifier
            user_message: User's message text
            user_id: User identifier
        
        Returns:
            AI-generated response text
        """
        # Load conversation state
        state = await self._load_state(conversation_id)
        
        if not state:
            logger.warning(f"Conversation {conversation_id} not found")
            return "Sorry, I couldn't find that conversation."
        
        # Security checks
        from promaia.agents.conversation_security import ConversationSecurity
        if self.security is None:
            self.security = ConversationSecurity()
        
        if not await self.security.validate_user(user_id, state):
            logger.warning(f"Unauthorized user {user_id} in conversation {conversation_id}")
            return "Sorry, you're not authorized for this conversation."

        # Check for malicious input FIRST (before rate limiting)
        # This ensures spam messages don't count toward rate limit
        is_malicious, reason = await self.security.detect_malicious_input(user_message)
        if is_malicious:
            state.malicious_attempt_count += 1
            await self._save_state(state)
            logger.warning(f"Malicious input detected in {conversation_id}: {reason}")
            # Silently drop malicious messages to avoid spam
            return None

        # THEN check rate limit for legitimate messages only
        if not await self.security.check_rate_limit(user_id):
            logger.warning(f"Rate limit exceeded for user {user_id}")
            # Silently drop rate-limited messages to avoid spam
            return None
        
        # Update state
        now = datetime.now(timezone.utc).isoformat()
        state.last_message_at = now
        state.turn_count += 1
        state.messages.append({
            'role': 'user',
            'content': user_message,
            'timestamp': now
        })

        # Agentic conversations: just store the message, no AI response.
        # The agentic_turn loop polls for this message and handles the reply.
        if getattr(state, 'conversation_type', None) == 'agentic':
            await self._save_state(state)
            logger.info(f"Stored agentic message in {conversation_id} (no auto-response)")
            return None

        # Check turn limit
        if state.max_turns and state.turn_count >= state.max_turns:
            await self.end_conversation(conversation_id, "max_turns_reached")
            return "We've reached the maximum number of turns for this conversation. Feel free to start a new one!"

        # Check for end-of-conversation signals from user
        end_reason = self._detect_end_signal(user_message)
        if end_reason:
            await self.end_conversation(conversation_id, end_reason)
            return "Thanks for the conversation! Feel free to reach out anytime."
        
        # Get AI response
        try:
            response = await self._get_ai_response(state, user_message)
        except Exception as e:
            logger.error(f"Error generating AI response: {e}", exc_info=True)
            response = "I'm sorry, I encountered an error generating a response. Please try again."
        
        # Add response to conversation
        response_time = datetime.now(timezone.utc).isoformat()
        state.messages.append({
            'role': 'assistant',
            'content': response,
            'timestamp': response_time,
        })
        state.last_message_at = response_time

        await self._save_state(state)
        
        logger.debug(f"Handled message in {conversation_id}, turn {state.turn_count}")
        return response
    
    async def _get_ai_response(
        self,
        state: ConversationState,
        user_message: str,
        on_tool_activity=None,
        platform=None,
        channel_context=None,
    ) -> str:
        """
        Generate AI response using direct Anthropic API call.

        Uses the agent's personality and preloaded context with conversation
        history to generate a natural reply — no SDK subprocess needed.

        Args:
            state: Conversation state
            user_message: User's message

        Returns:
            AI-generated response
        """
        import os
        from anthropic import Anthropic

        # Get agent from cache (reloads config if it changed)
        agent = self._get_cached_agent(state.agent_id)

        if not agent:
            logger.error(f"Agent {state.agent_id} not found in cache")
            return f"Sorry, I couldn't find the '{state.agent_id}' agent configuration."

        logger.info(f"Using agent: {agent.name} (id: {agent.agent_id})")

        try:
            # Build system prompt from agent personality
            system_prompt = self._build_conversation_system_prompt(agent, state)

            # Load context on first turn, cache for subsequent turns.
            # Invalidate cache daily so calendar/date-sensitive data stays fresh.
            context_block = ""
            cache_stale = False
            if state.cached_context:
                try:
                    # Check if cache was from a different day
                    cached_date = state.context.get('_cached_context_date')
                    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    if cached_date and cached_date != today_str:
                        cache_stale = True
                        logger.info("📅 Cached context is from a different day, reloading")
                    else:
                        context_block = state.cached_context
                        logger.info("♻️ Reusing cached context")
                except Exception:
                    pass

            if not context_block:
                context_block = await self._load_conversation_context(agent)
                if context_block:
                    state.cached_context = context_block
                    state.context['_cached_context_date'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                    await self._save_state(state)
                    logger.info("💾 Cached context for future turns")

            # Build messages: context + conversation history
            messages = []

            if context_block:
                messages.append({
                    "role": "user",
                    "content": f"# Background Context\n\nHere is relevant context about the people and projects you work with:\n\n{context_block}"
                })
                messages.append({
                    "role": "assistant",
                    "content": "Got it, I have the background context. I'm ready to continue our conversation."
                })

            # Add conversation history (last 20 messages)
            for msg in state.messages[-20:]:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

            # Save context log for debugging
            try:
                self._save_conversation_context_log(
                    agent_name=state.agent_id,
                    conversation_id=state.conversation_id,
                    system_prompt=system_prompt,
                    context_block=context_block,
                    messages=messages,
                )
            except Exception as e:
                logger.debug(f"Failed to save context log: {e}")

            # Generate response: agentic loop (with tools) or single LLM call
            agentic_enabled = getattr(agent, 'agentic_loop_enabled', True)

            if agentic_enabled:
                from promaia.agents.agentic_turn import (
                    agentic_turn, ToolExecutor, build_tool_definitions,
                    _generate_plan,
                )
                # Auto-add calendar to mcp_tools if agent has a dedicated calendar
                if getattr(agent, 'calendar_id', None):
                    agent_mcp = getattr(agent, 'mcp_tools', None) or []
                    if "calendar" not in agent_mcp:
                        agent.mcp_tools = list(agent_mcp) + ["calendar"]

                has_platform = platform is not None
                tools = build_tool_definitions(agent, has_platform=has_platform)
                executor = ToolExecutor(
                    agent=agent,
                    workspace=agent.workspace,
                    platform=platform,
                    channel_context=channel_context,
                )

                # Planning: decompose complex requests before the agentic loop
                tool_names = [t["name"] for t in tools]
                plan = await _generate_plan(user_message, agent, tool_names)

                # Emit plan to UX callback so tag_to_chat can display it
                if plan and on_tool_activity:
                    try:
                        await on_tool_activity(
                            tool_name="__plan__",
                            tool_input={"steps": plan},
                            completed=True,
                            summary=None,
                        )
                    except Exception:
                        pass

                # Proactive context trimming before agentic turn
                from promaia.agents.context_trimmer import trim_context_to_fit
                system_prompt, messages = await trim_context_to_fit(
                    system_prompt, messages, tools=tools
                )

                result = await agentic_turn(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    tool_executor=executor,
                    max_iterations=agent.max_iterations or 40,
                    on_tool_activity=on_tool_activity,
                    plan=plan,
                )
                output = result.response_text
                if result.tool_calls_made:
                    logger.info(
                        f"Agentic turn: {result.iterations_used} iterations, "
                        f"{len(result.tool_calls_made)} tool calls, "
                        f"{result.input_tokens}+{result.output_tokens} tokens"
                    )
            else:
                # Fallback: single LLM call (no tool use)
                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if not api_key:
                    logger.error("ANTHROPIC_API_KEY not set")
                    return "I'm sorry, I couldn't generate a response (missing API key)."

                client = Anthropic(api_key=api_key, max_retries=5)
                response = await asyncio.to_thread(
                    client.messages.create,
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=system_prompt,
                    messages=messages,
                )
                output = response.content[0].text if response.content else ""

            if not output:
                logger.warning(f"Agent {state.agent_id} returned empty response")
                return "I'm sorry, I couldn't generate a response."

            logger.info(f"Agent {state.agent_id} generated response ({len(output)} chars)")
            return output

        except Exception as e:
            logger.error(f"Error generating response for {state.agent_id}: {e}", exc_info=True)
            return "I'm sorry, I encountered an error. Please try again."

    def _build_conversation_system_prompt(self, agent, state: ConversationState) -> str:
        """Build the system prompt for conversation mode.

        Loads the agent's personality prompt and the conversation_mode.md
        template from the prompts directory, filling in dynamic variables.
        """
        from promaia.utils.env_writer import get_prompts_dir

        # ── Agent personality ──────────────────────────────────────────
        prompt_value = agent.prompt_file or ""

        # Check if it's a file path
        looks_like_path = (
            isinstance(prompt_value, str)
            and "\n" not in prompt_value
            and len(prompt_value) <= 240
            and (prompt_value.startswith(("/", "./", "../", "~"))
                 or prompt_value.endswith((".md", ".txt"))
                 or "/" in prompt_value)
        )
        if looks_like_path:
            try:
                p = Path(prompt_value).expanduser()
                if p.is_file():
                    prompt_value = p.read_text(encoding="utf-8")
            except OSError:
                pass

        # ── Date/time ──────────────────────────────────────────────────
        try:
            import zoneinfo
            local_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        except Exception:
            local_tz = timezone.utc
        now = datetime.now(local_tz)

        # ── Header ─────────────────────────────────────────────────────
        parts = [
            prompt_value,
            "",
            f"Current date: {now.strftime('%A, %B %d, %Y %I:%M %p %Z')}",
            "",
        ]

        # ── Conversation mode template ─────────────────────────────────
        agentic_enabled = getattr(agent, 'agentic_loop_enabled', True)

        if agentic_enabled:
            conv_prompt_path = get_prompts_dir() / "conversation_mode.md"
            if conv_prompt_path.is_file():
                template = conv_prompt_path.read_text(encoding="utf-8")

                # Fill in template variables
                queryable = agent.get_queryable_sources()
                sources_list = ", ".join(queryable) if queryable else "(none configured)"

                # Build conditional tool sections
                mcp_tools = getattr(agent, 'mcp_tools', []) or []
                tool_sections_parts = []

                if "gmail" in mcp_tools:
                    tool_sections_parts.append(
                        "## Gmail Tools (Write)\n\n"
                        "- **send_email**: Send email (to, subject, body)\n"
                        "- **create_email_draft**: Create draft (not sent)\n"
                        "- **reply_to_email**: Reply to a thread (thread_id, message_id, body)\n"
                        "  Always search for the thread first to get the thread_id and message_id."
                    )
                if "calendar" in mcp_tools:
                    cal_section = (
                        "## Calendar Tools (Write)\n\n"
                        "- **create_calendar_event**: Create event on the **user's** calendar (summary, start_time, end_time)\n"
                        "- **update_calendar_event**: Update event (event_id + fields to change)\n"
                        "- **delete_calendar_event**: Delete event (event_id)\n"
                        "  Always check for conflicts with query_sql before creating events."
                    )
                    if getattr(agent, 'calendar_id', None):
                        cal_section += (
                            "\n\n## Self-Scheduling\n\n"
                            "- **schedule_self**: Schedule a future task for **yourself**. Creates an event "
                            "on your own dedicated calendar that will trigger you to run at the specified time.\n"
                            "  - Use for: reminders, follow-ups, multi-step workflows spanning hours/days\n"
                            "  - Params: summary (required), start_time (required), end_time (optional), "
                            "description (optional — include context for your future self)\n\n"
                            "### Which calendar tool to use\n\n"
                            "- User says \"put X on my calendar\" / \"schedule a meeting\" → **create_calendar_event** (user's calendar)\n"
                            "- You need to follow up later / check on something tomorrow / continue a workflow → **schedule_self** (your calendar)"
                        )
                    tool_sections_parts.append(cal_section)
                if "notion" in mcp_tools:
                    tool_sections_parts.append(
                        "## Notion Tools (Read & Write)\n\n"
                        "- **notion_search**: Search Notion for pages/databases by title\n"
                        "- **notion_create_page**: Create a new page in a Notion database\n"
                        "- **notion_update_page**: Update an existing page's properties or content\n"
                        "- **notion_query_database**: Query a Notion database with filters"
                    )

                tool_sections = "\n\n".join(tool_sections_parts)

                # Notion-specific guidance
                notion_guidance = ""
                if "notion" in mcp_tools:
                    notion_guidance = (
                        "## Built-in query tools vs Notion tools\n\n"
                        "- **Prefer built-in query tools** (query_sql, query_vector, query_source) "
                        "for loading large chunks of context from synced data. They are cheaper, "
                        "faster, and more effective for anything that has had time to be synced.\n"
                        "- **Use Notion tools** for transient, specific pages — especially ones "
                        "you are actively creating or editing in this session, or anything that "
                        "may have been updated very recently and not yet synced.\n"
                        "- After creating a Notion page, use the URL from the create response "
                        "to share links — do not construct Notion URLs manually."
                    )

                # Apply template substitutions
                agent_name = getattr(agent, 'name', None) or getattr(agent, 'agent_id', 'Agent')
                filled = template.replace("{agent_name}", agent_name)
                filled = filled.replace("{platform}", state.platform or "chat")
                filled = filled.replace("{sources}", sources_list)
                filled = filled.replace("{tool_sections}", tool_sections)
                filled = filled.replace("{notion_guidance}", notion_guidance)

                parts.append(filled)
            else:
                # Fallback: minimal instructions if template file is missing
                logger.warning(f"conversation_mode.md not found at {conv_prompt_path}")
                parts.extend([
                    "# Conversation Mode",
                    "",
                    f"You are in a {state.platform} conversation.",
                    "Keep responses concise, warm, and natural.",
                ])
        else:
            parts.extend([
                "# Conversation Mode",
                "",
                f"You are in a {state.platform} conversation.",
                "Keep responses concise, warm, and natural — like messaging a colleague.",
                "Don't repeat information already covered. Build on what's been said.",
            ])

        return "\n".join(parts)

    async def _load_conversation_context(self, agent) -> str:
        """Load preloaded context for conversation (lighter than full executor)."""
        try:
            from promaia.agents.executor import AgentExecutor
            executor = AgentExecutor(agent)
            initial_context = await executor._load_initial_context()
            if initial_context:
                from promaia.nlq.prompts import format_context_data
                formatted = format_context_data(initial_context)
                # Trim only if approaching model context limit
                # Claude Sonnet 4.6 has 200K token context (~700K chars)
                # Reserve ~50K tokens for system prompt, conversation history, and response
                max_context_chars = 525_000  # ~150K tokens worth
                if len(formatted) > max_context_chars:
                    formatted = formatted[:max_context_chars] + "\n\n[... context trimmed to fit model limit ...]"
                return formatted
        except Exception as e:
            logger.warning(f"Failed to load conversation context: {e}")
        return ""
    
    def _save_conversation_context_log(
        self,
        agent_name: str,
        conversation_id: str,
        system_prompt: str,
        context_block: str,
        messages: list,
    ):
        """Save conversation context log for debugging."""
        from promaia.utils.env_writer import get_data_dir
        now = datetime.now(timezone.utc)
        data_root = get_data_dir()
        log_dir = data_root / "context_logs" / "agent_context_logs" / agent_name.replace(" ", "_").lower()
        log_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{now.strftime('%Y-%m-%dT%H-%M-%S')}_conv-{conversation_id[:20]}.md"
        log_path = log_dir / filename

        # Format messages for log (skip context injection messages)
        msg_log = []
        for m in messages:
            role = m.get('role', '?')
            content = m.get('content', '')[:500]
            msg_log.append(f"**{role}**: {content}")

        content = f"""# Conversation Context Log: {agent_name}
**Conversation**: {conversation_id}
**Timestamp**: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}

---

## System Prompt

{system_prompt}

---

## Loaded Context ({len(context_block)} chars)

{context_block}

---

## Messages ({len(messages)} total)

{chr(10).join(msg_log)}
"""
        log_path.write_text(content, encoding="utf-8")
        logger.info(f"📝 Conversation context log saved: {log_path.name}")

    async def check_timeout(self, conversation_id: str) -> bool:
        """
        Check if conversation has timed out.

        Args:
            conversation_id: Conversation identifier

        Returns:
            True if timed out, False otherwise
        """
        state = await self._load_state(conversation_id)

        if not state or state.status != 'active':
            return False

        last_message = datetime.fromisoformat(state.last_message_at)
        elapsed = (datetime.now(timezone.utc) - last_message).total_seconds()

        if elapsed > state.timeout_seconds:
            # Send timeout message via platform
            platform = self.platforms.get(state.platform)
            if platform:
                try:
                    await platform.send_message(
                        channel_id=state.channel_id,
                        content="I haven't heard from you in a while. Feel free to continue our conversation anytime!",
                        thread_id=state.thread_id
                    )
                except Exception as e:
                    logger.error(f"Error sending timeout message: {e}")

            state.status = 'timeout'
            state.completed_at = datetime.now(timezone.utc).isoformat()
            state.completion_reason = "timeout"
            await self._save_state(state)

            logger.info(f"Conversation {conversation_id} timed out")

            # Fire completion callbacks (for orchestrator integration)
            await self._fire_end_callbacks(
                conversation_id=conversation_id,
                transcript=state.get_transcript(),
                reason="timeout"
            )

            return True

        return False
    
    def _detect_end_signal(self, message: str) -> Optional[str]:
        """
        Detect if the user's message signals end of conversation.

        Args:
            message: User's message text

        Returns:
            Reason string if end signal detected, None otherwise
        """
        message_lower = message.strip().lower()

        logger.debug(f"Checking end signal for message: '{message[:50]}...'")

        for i, pattern in enumerate(END_CONVERSATION_PATTERNS):
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                matched_text = match.group(0)
                logger.info(f"✅ End signal detected! Pattern #{i} matched: '{matched_text}'")

                # Determine specific reason
                if '/done' in message_lower or '/end' in message_lower or '/exit' in message_lower or '/quit' in message_lower:
                    reason = "user_command"
                elif any(word in message_lower for word in ['bye', 'goodbye']):
                    reason = "user_goodbye"
                elif any(word in message_lower for word in ['thanks', 'thank', 'thx', 'ty']):
                    reason = "user_thanks"
                elif any(word in message_lower for word in ['wrap', 'finish', 'done', 'end']):
                    reason = "user_ended"
                else:
                    reason = "user_ended"

                logger.info(f"🏁 Ending conversation with reason: {reason}")
                return reason

        logger.debug("❌ No end signal detected")
        return None

    async def end_conversation(
        self,
        conversation_id: str,
        reason: str = "user_ended"
    ) -> None:
        """
        End conversation gracefully and fire completion callbacks.

        Args:
            conversation_id: Conversation identifier
            reason: Reason for ending (for logging and callbacks)
        """
        state = await self._load_state(conversation_id)

        if state:
            state.status = 'completed'
            state.completed_at = datetime.now(timezone.utc).isoformat()
            state.completion_reason = reason
            await self._save_state(state)

            logger.info(
                f"Conversation ended: {conversation_id[:30]}... | "
                f"reason={reason} | turns={state.turn_count} | messages={len(state.messages)}"
            )

            # Fire completion callbacks (for orchestrator integration)
            await self._fire_end_callbacks(
                conversation_id=conversation_id,
                transcript=state.get_transcript(),
                reason=reason
            )
    
    async def handle_batched_messages(
        self,
        conversation_id: str,
        messages: List[Dict[str, Any]],
        thread_context: Optional[str] = None,
        on_tool_activity=None,
        platform=None,
        channel_context=None,
    ) -> str:
        """
        Handle multiple messages as a single conversational turn.

        Used by tag-to-chat to batch rapid-fire messages into one response.

        Args:
            conversation_id: Conversation identifier
            messages: List of dicts with 'user_id', 'username', 'text', 'timestamp'
            thread_context: Full thread history for context (fetched from platform)

        Returns:
            AI-generated response text
        """
        # Combine messages into a single user turn with attribution
        combined = "\n".join(f"{m['username']}: {m['text']}" for m in messages)

        # Load conversation state
        state = await self._load_state(conversation_id)
        if not state:
            logger.warning(f"Conversation {conversation_id} not found for batched messages")
            return "Sorry, I couldn't find that conversation."

        # If thread context provided, sync it into conversation state so
        # the AI sees the full thread history (not just what's in the DB).
        if thread_context:
            state.messages = self._sync_thread_context(state.messages, thread_context)

        # Update state
        now = datetime.now(timezone.utc).isoformat()
        state.last_message_at = now
        state.turn_count += 1
        state.messages.append({
            'role': 'user',
            'content': combined,
            'timestamp': now,
        })

        # Generate AI response
        try:
            response = await self._get_ai_response(
                state, combined,
                on_tool_activity=on_tool_activity,
                platform=platform,
                channel_context=channel_context,
            )
        except Exception as e:
            logger.error(f"Error generating batched response: {e}", exc_info=True)
            response = "I'm sorry, I encountered an error generating a response. Please try again."

        # Save response to conversation
        response_time = datetime.now(timezone.utc).isoformat()
        state.messages.append({
            'role': 'assistant',
            'content': response,
            'timestamp': response_time,
        })
        state.last_message_at = response_time
        await self._save_state(state)

        logger.debug(f"Handled batched messages in {conversation_id}, turn {state.turn_count}")
        return response

    def _sync_thread_context(
        self,
        existing_messages: List[Dict[str, Any]],
        thread_context: str,
    ) -> List[Dict[str, Any]]:
        """
        Replace conversation messages with the full thread history.

        The thread_context is the authoritative source of what was said in the
        thread (fetched from the platform API). We inject it as a single user
        message so the AI sees everything, then keep any prior assistant
        messages from the DB so the AI knows what it already said.
        """
        # Keep only assistant messages from the existing conversation
        # (so the AI knows its own prior responses)
        assistant_msgs = [m for m in existing_messages if m.get('role') == 'assistant']

        # Build fresh message list: thread history as context, then prior turns
        context_msg = {
            'role': 'user',
            'content': f"[Thread history so far]\n{thread_context}",
        }

        # If there are prior assistant messages, interleave them for valid
        # message structure (user/assistant alternation)
        if assistant_msgs:
            # Start with thread context, then replay assistant responses
            result = [context_msg]
            for a_msg in assistant_msgs:
                result.append(a_msg)
                # Add a placeholder user turn between assistant messages
                # so the API sees valid alternation
                result.append({'role': 'user', 'content': '(continued conversation)'})
            # Remove trailing placeholder — the real user message gets appended next
            if result[-1].get('content') == '(continued conversation)':
                result.pop()
            return result
        else:
            return [context_msg]

    async def get_tag_to_chat_conversation(
        self,
        platform: str,
        thread_id: str,
    ) -> Optional[ConversationState]:
        """
        Look up a tag-to-chat conversation by thread_id.

        Used by event handlers to check if a thread belongs to a known
        tag-to-chat conversation (dormant, active, or paused).

        Args:
            platform: Platform name
            thread_id: Thread identifier

        Returns:
            ConversationState if found and not stopped, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM conversations
                WHERE platform = ? AND thread_id = ?
                AND conversation_type = 'tag_to_chat'
                AND status IN ('dormant', 'active', 'paused')
                ORDER BY created_at DESC
                LIMIT 1
            """, (platform, thread_id))

            row = cursor.fetchone()
            if row:
                return self._row_to_state(dict(row))
            return None

    async def get_active_conversation(
        self,
        platform: str,
        channel_id: str,
        user_id: str
    ) -> Optional[ConversationState]:
        """
        Get active conversation for user in channel.

        Also finds agent-initiated conversations (from orchestrator) that are
        waiting for any user to respond in the channel.

        Args:
            platform: Platform name
            channel_id: Channel ID
            user_id: User ID

        Returns:
            ConversationState if found, None otherwise
        """
        logger.debug(f"Looking for conversation: platform={platform}, channel={channel_id}, user={user_id}")

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # First, try exact user match
            cursor.execute("""
                SELECT * FROM conversations
                WHERE platform = ? AND channel_id = ? AND user_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
            """, (platform, channel_id, user_id))

            row = cursor.fetchone()

            if row:
                logger.debug(f"Found conversation by exact user match: {row['id']}")
                return self._row_to_state(dict(row))

            # Next, look for agent-initiated conversations waiting for any user
            # (orchestrator_task_id is set, indicating it came from orchestrator)
            cursor.execute("""
                SELECT * FROM conversations
                WHERE platform = ? AND channel_id = ? AND status = 'active'
                AND orchestrator_task_id IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 1
            """, (platform, channel_id))

            row = cursor.fetchone()

            if row:
                # "Adopt" this user into the conversation
                logger.info(f"Found orchestrator conversation {row['id'][:20]}..., adopting user {user_id}")
                state = self._row_to_state(dict(row))
                state.user_id = user_id
                await self._save_state(state)
                logger.info(f"Adopted user {user_id} into orchestrator conversation {state.conversation_id}")
                return state

            # Debug: check what conversations exist
            cursor.execute("""
                SELECT id, channel_id, user_id, status, orchestrator_task_id
                FROM conversations
                WHERE platform = ? AND status = 'active'
                LIMIT 5
            """, (platform,))
            active = cursor.fetchall()
            if active:
                logger.info(f"Active conversations in DB: {[(r['id'][:12], r['channel_id'], r['user_id'], r['orchestrator_task_id']) for r in active]}")
            else:
                logger.info("No active conversations found in DB")

            return None
    
    async def _save_state(self, state: ConversationState):
        """Save conversation state to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO conversations
                (id, agent_id, platform, channel_id, user_id, thread_id, status,
                 last_message_at, turn_count, max_turns, messages, context,
                 timeout_seconds, malicious_attempt_count, created_at, completed_at,
                 completion_reason, orchestrator_task_id, cached_context, conversation_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.conversation_id,
                state.agent_id,
                state.platform,
                state.channel_id,
                state.user_id,
                state.thread_id,
                state.status,
                state.last_message_at,
                state.turn_count,
                state.max_turns,
                json.dumps(state.messages),
                json.dumps(state.context),
                state.timeout_seconds,
                state.malicious_attempt_count,
                state.created_at,
                state.completed_at,
                state.completion_reason,
                state.orchestrator_task_id,
                state.cached_context,
                state.conversation_type,
            ))

            conn.commit()
    
    async def _load_state(self, conversation_id: str) -> Optional[ConversationState]:
        """Load conversation state from database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM conversations WHERE id = ?
            """, (conversation_id,))
            
            row = cursor.fetchone()
            
            if row:
                return self._row_to_state(dict(row))
            
            return None
    
    def _row_to_state(self, row: Dict[str, Any]) -> ConversationState:
        """Convert database row to ConversationState."""
        return ConversationState(
            conversation_id=row['id'],
            agent_id=row['agent_id'],
            platform=row['platform'],
            channel_id=row['channel_id'],
            user_id=row['user_id'],
            thread_id=row['thread_id'],
            status=row['status'],
            last_message_at=row['last_message_at'],
            messages=json.loads(row['messages']),
            context=json.loads(row['context']),
            timeout_seconds=row['timeout_seconds'],
            max_turns=row['max_turns'],
            turn_count=row['turn_count'],
            malicious_attempt_count=row['malicious_attempt_count'],
            created_at=row['created_at'],
            completed_at=row['completed_at'],
            completion_reason=row.get('completion_reason'),
            orchestrator_task_id=row.get('orchestrator_task_id'),
            cached_context=row.get('cached_context'),
            conversation_type=row.get('conversation_type', 'direct'),
        )
