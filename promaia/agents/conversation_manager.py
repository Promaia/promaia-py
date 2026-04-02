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
    is_active: bool = True  # True = ongoing conversation, False = done
    summary: Optional[str] = None  # Agent-provided summary when conversation ends
    conversation_partner: Optional[str] = None  # Who this DM conversation is with

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
            new_cache = {(a.agent_id or a.name): a for a in agents}
            # Don't replace a working cache with an empty one (config file race condition)
            if new_cache or not self._agent_cache:
                self._agent_cache = new_cache
            else:
                logger.warning("⚠️ Config reload returned 0 agents, keeping previous cache")
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

            # Migration: Add is_active, summary, conversation_partner columns
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN is_active BOOLEAN DEFAULT 1")
                logger.info("Added is_active column to conversations table")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN conversation_partner TEXT")
            except sqlite3.OperationalError:
                pass

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
            response = f"I'm sorry, I encountered an error generating a response ({type(e).__name__}: {e}). Please try again."

        # Add response to conversation (skip if agentic turn already stored history_messages)
        if not getattr(state, '_skip_response_append', False):
            response_time = datetime.now(timezone.utc).isoformat()
            state.messages.append({
                'role': 'assistant',
                'content': response,
                'timestamp': response_time,
            })
        else:
            state._skip_response_append = False
        state.last_message_at = datetime.now(timezone.utc).isoformat()

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

            # Build messages from conversation history only.
            # Context is available via query tools (query_source, query_sql,
            # query_vector) — not pre-loaded.  The system prompt already
            # includes a lightweight database preview via generate_database_preview().
            context_block = ""
            messages = []

            # Add conversation history (last 20 messages, plain text only)
            # Strip tool_use/tool_result blocks to avoid mismatched pairs
            for msg in state.messages[-20:]:
                content = msg.get("content", "")
                # Skip messages with tool_use/tool_result content blocks
                if isinstance(content, list):
                    # Extract only text blocks from structured content
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts) if text_parts else ""
                if content and isinstance(content, str) and content.strip():
                    messages.append({
                        "role": msg["role"],
                        "content": content
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

            # Generate response using the shared agentic adapter
            # This gives Slack/Discord the same Think/Act mode, context sources,
            # memory, suite registry, and conversation_mode.md prompt as terminal chat.
            from promaia.chat.agentic_adapter import run_agentic_turn

            # Auto-add calendar to mcp_tools if agent has a dedicated calendar
            if getattr(agent, 'calendar_id', None):
                agent_mcp = getattr(agent, 'mcp_tools', None) or []
                if "calendar" not in agent_mcp:
                    agent.mcp_tools = list(agent_mcp) + ["calendar"]

            mcp_tools = getattr(agent, 'mcp_tools', []) or []
            databases = getattr(agent, 'databases', []) or []

            # Restore persisted notepad and source states from conversation context
            notepad_content = state.context.get('notepad_content')
            source_states = state.context.get('source_states')

            # Create a no-op print function for non-terminal contexts
            # (the on_tool_activity callback handles UX for Slack/Discord)
            def _noop_print(*args, **kwargs):
                pass

            result = await run_agentic_turn(
                system_prompt=system_prompt,
                messages=messages,
                workspace=agent.workspace,
                mcp_tools=mcp_tools,
                databases=databases,
                print_text_fn=_noop_print,
                notepad_content=notepad_content,
                source_states=source_states,
                on_tool_activity=on_tool_activity,
            )

            output = result.response_text

            # Log the turn for debugging (separate from terminal logs)
            try:
                from promaia.utils.env_writer import get_data_dir
                platform_name = state.platform or "messaging"
                log_dir = get_data_dir() / "context_logs" / f"{platform_name}_turn_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                agent_name = getattr(agent, 'name', state.agent_id)
                log_path = log_dir / f"{ts}_{agent_name}.md"
                log_content = (
                    f"# {platform_name} turn — {agent_name}\n\n"
                    f"## System Prompt\n\n{system_prompt[:2000]}...\n\n"
                    f"## Messages ({len(messages)})\n\n"
                )
                for msg in messages[-5:]:
                    content = msg.get('content', '')
                    if isinstance(content, str):
                        log_content += f"**{msg['role']}**: {content[:200]}\n\n"
                log_content += f"## Response\n\n{output[:1000]}\n"
                log_path.write_text(log_content)
            except Exception:
                pass

            # Persist notepad and source states for next turn
            if result.notepad_content is not None:
                state.context['notepad_content'] = result.notepad_content
            if result.source_states is not None:
                state.context['source_states'] = result.source_states

            # Store tool_use/tool_result blocks for conversation history
            if hasattr(result, 'history_messages') and result.history_messages:
                state.messages.extend(result.history_messages)
                state._skip_response_append = True
            if result.tool_calls_made:
                logger.info(
                    f"Agentic turn: {result.iterations_used} iterations, "
                    f"{len(result.tool_calls_made)} tool calls, "
                    f"{result.input_tokens}+{result.output_tokens} tokens"
                )

            if not output:
                logger.warning(f"Agent {state.agent_id} returned empty response")
                return "I'm sorry, I couldn't generate a response."

            # Handle end_conversation signal — mark DM conversations done with summary
            if result.signal and result.signal.get("type") == "end_conversation":
                summary = result.signal.get("summary")
                is_dm = state.context.get("is_dm", False)
                if is_dm and summary:
                    await self.mark_conversation_done(
                        state.conversation_id,
                        summary=summary,
                        reason=result.signal.get("reason", "agent_ended"),
                    )
                else:
                    await self.end_conversation(
                        state.conversation_id,
                        reason="agent_ended",
                    )

            logger.info(f"Agent {state.agent_id} generated response ({len(output)} chars)")
            return output

        except Exception as e:
            logger.error(f"Error generating response for {state.agent_id}: {e}", exc_info=True)
            return f"I'm sorry, I encountered an error ({type(e).__name__}: {e}). Please try again."

    def _build_conversation_system_prompt(self, agent, state: ConversationState) -> str:
        """Build the base system prompt for conversation mode.

        Uses the same create_system_prompt() as terminal chat for the base
        (prompt.md + database preview), then adds platform-specific context.
        The conversation_mode.md template is added later by run_agentic_turn()
        via build_agentic_system_prompt().
        """
        from promaia.ai.prompts import create_system_prompt

        # Build the same base prompt as terminal chat
        # (prompt.md + database preview — scoped to agent's accessible sources)
        queryable = agent.get_queryable_sources() if hasattr(agent, 'get_queryable_sources') else None
        base_prompt = create_system_prompt(
            multi_source_data={},  # No pre-loaded context — agent uses query tools
            mcp_tools_info=None,
            include_query_tools=False,  # agentic loop has its own tools
            workspace=agent.workspace,
            limit_to_databases=queryable,
        )

        # Add agent personality if it has one
        prompt_value = agent.prompt_file or ""
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

        if prompt_value:
            base_prompt = prompt_value + "\n\n" + base_prompt

        # Add platform-specific context
        ctx = state.context or {}
        platform = state.platform or "chat"

        base_prompt += f"\n\n## Conversation Location\n"
        if ctx.get("is_dm"):
            user_name = ctx.get("user_name", "the user")
            base_prompt += (
                f"You are running in {platform}. "
                f"This is a direct message with {user_name} — a private 1-on-1 conversation. "
                f"Respond to every message."
            )

            # Inject recent completed conversation summaries for DM context
            try:
                past_summaries = self._load_recent_dm_summaries(
                    conversation_partner=user_name,
                    platform=platform,
                    days=2,
                )
                if past_summaries:
                    base_prompt += f"\n\n## Recent Conversations with {user_name}\n"
                    for s in past_summaries:
                        date_str = (s.get('completed_at') or s.get('created_at') or '')[:10]
                        summary = s.get('summary', 'No summary')
                        base_prompt += f"- {date_str}: {summary}\n"
            except Exception as e:
                logger.debug(f"Could not load DM summaries: {e}")
        elif ctx.get("channel_name"):
            channel_name = ctx["channel_name"]
            base_prompt += (
                f"You are running in {platform}, in channel #{channel_name}. "
                f"You were @mentioned — respond helpfully and concisely."
            )
        else:
            base_prompt += f"You are running in {platform}."

        # Inject recent channel history if provided
        recent_messages = ctx.get("recent_messages")
        if recent_messages:
            base_prompt += (
                f"\n\n## Recent Channel History\n\n"
                f"Here's what was said recently in this channel. You are here 📍\n\n"
                f"{recent_messages}"
            )

        return base_prompt

    def _load_recent_dm_summaries(
        self,
        conversation_partner: str,
        platform: str = "slack",
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """Load recent completed DM conversation summaries with a specific user.

        Returns list of dicts with 'summary', 'completed_at', 'created_at' keys.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT summary, completed_at, created_at
                FROM conversations
                WHERE conversation_partner = ?
                AND platform = ?
                AND is_active = 0
                AND summary IS NOT NULL
                AND created_at > ?
                ORDER BY created_at DESC
                LIMIT 10
            """, (conversation_partner, platform, cutoff))
            return [dict(row) for row in cursor.fetchall()]

    async def _load_conversation_context(self, agent) -> str:
        """Deprecated: bulk context pre-loading removed.

        Agents now use query tools (query_source, query_sql, query_vector)
        to load relevant data on demand. The system prompt includes a
        lightweight database preview via generate_database_preview().
        """
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
            # Clear notepad and source states so they don't leak into future conversations
            state.context.pop('notepad_content', None)
            state.context.pop('source_states', None)
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
    
    async def mark_conversation_done(
        self,
        conversation_id: str,
        summary: str,
        reason: str = "agent_ended"
    ) -> None:
        """
        Mark a conversation as done (is_active=False) with a summary.

        Used for DM conversations where the agent signals completion.
        Saves the conversation to KB for future recall via query_source.

        Args:
            conversation_id: Conversation identifier
            summary: Agent-provided summary of the conversation
            reason: Reason for ending
        """
        state = await self._load_state(conversation_id)
        if not state:
            return

        state.is_active = False
        state.status = 'completed'
        state.completed_at = datetime.now(timezone.utc).isoformat()
        state.completion_reason = reason
        state.summary = summary
        # Clear notepad and source states — fresh start for next conversation
        state.context.pop('notepad_content', None)
        state.context.pop('source_states', None)
        await self._save_state(state)

        logger.info(
            f"Conversation done: {conversation_id[:30]}... | "
            f"summary={summary[:60]} | turns={state.turn_count}"
        )

        # Save to KB for future recall
        from promaia.messaging.slack_bot import _save_dm_to_history
        await _save_dm_to_history(self, state, summary=summary)

        # Fire completion callbacks
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

        # Seed thread context on first turn only — preserve existing messages
        # (which include tool calls, results, and structured content).
        # Previous approach (_sync_thread_context) replaced all messages with
        # flat text from the API, wiping tool call history.
        if thread_context and not state.messages:
            state.messages = [
                {'role': 'user', 'content': f"[Thread history]\n{thread_context}"},
                {'role': 'assistant', 'content': "Got it, I have the thread context."},
            ]

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
            response = f"I'm sorry, I encountered an error generating a response ({type(e).__name__}: {e}). Please try again."

        # Save response to conversation (skip if agentic turn already stored history_messages)
        if not getattr(state, '_skip_response_append', False):
            response_time = datetime.now(timezone.utc).isoformat()
            state.messages.append({
                'role': 'assistant',
                'content': response,
                'timestamp': response_time,
            })
        else:
            state._skip_response_append = False
        state.last_message_at = datetime.now(timezone.utc).isoformat()
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

            # First, try exact user match (is_active=1 means conversation is ongoing)
            cursor.execute("""
                SELECT * FROM conversations
                WHERE platform = ? AND channel_id = ? AND user_id = ? AND status = 'active'
                AND is_active = 1
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
                 completion_reason, orchestrator_task_id, cached_context, conversation_type,
                 is_active, summary, conversation_partner)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                1 if state.is_active else 0,
                state.summary,
                state.conversation_partner,
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
            is_active=bool(row.get('is_active', 1)),
            summary=row.get('summary'),
            conversation_partner=row.get('conversation_partner'),
        )
