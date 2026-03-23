"""
Agent Executor - Runs scheduled agents with multi-step query capability.

Now supports both legacy custom iteration loop and new Claude Agent SDK integration.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path

from promaia.agents.agent_config import AgentConfig, update_agent_last_run
from promaia.agents.execution_tracker import ExecutionTracker
from promaia.agents.notion_writer import NotionOutputWriter
from promaia.storage.files import load_database_pages_with_filters
from promaia.chat.query_tools import QueryToolExecutor
from promaia.ai.prompts import format_context_data
from promaia.ai.nl_orchestrator import PromaiLLMAdapter
from promaia.config.databases import get_database_config

logger = logging.getLogger(__name__)

# Hard cap on number of pages per source to prevent extremely large context.
DEFAULT_MAX_PAGES_PER_SOURCE = 50

# Try to import Claude Agent SDK (optional, falls back to legacy mode)
SDK_AVAILABLE = False
_SDK_IMPORT_DEBUG = []
try:
    _SDK_IMPORT_DEBUG.append("Attempting SDK import...")
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, AssistantMessage, ResultMessage
    SDK_AVAILABLE = True
    _SDK_IMPORT_DEBUG.append("✓ SDK import successful")
    logger.info("Claude Agent SDK imported successfully")
    # Write to debug file
    import os
    with open(f"/tmp/promaia_sdk_debug_{os.getpid()}.txt", "w") as f:
        f.write(f"SDK_AVAILABLE = True\n")
        f.write(f"PID: {os.getpid()}\n")
except ImportError as e:
    SDK_AVAILABLE = False
    _SDK_IMPORT_ERROR = str(e)
    _SDK_IMPORT_DEBUG.append(f"✗ ImportError: {e}")
    logger.info(f"Claude Agent SDK not available, will use legacy execution mode: {e}")
    # Write to debug file
    import os
    with open(f"/tmp/promaia_sdk_debug_{os.getpid()}.txt", "w") as f:
        f.write(f"SDK_AVAILABLE = False (ImportError)\n")
        f.write(f"Error: {e}\n")
        f.write(f"PID: {os.getpid()}\n")
except Exception as e:
    SDK_AVAILABLE = False
    _SDK_IMPORT_ERROR = f"Unexpected error importing claude_agent_sdk: {e}"
    _SDK_IMPORT_DEBUG.append(f"✗ Exception: {e}")
    logger.error(f"Claude Agent SDK import failed (Exception): {e}")
    # Write to debug file
    import os
    with open(f"/tmp/promaia_sdk_debug_{os.getpid()}.txt", "w") as f:
        f.write(f"SDK_AVAILABLE = False (Exception)\n")
        f.write(f"Error: {e}\n")
        f.write(f"PID: {os.getpid()}\n")


def _save_context_log(
    agent_name: str,
    execution_id,
    system_prompt: str,
    formatted_context: str,
    run_request: str | None,
    databases: list[str],
) -> Path:
    """Save full assembled prompt to context_logs/agent_context_logs/<agent>/."""
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
    exec_str = str(execution_id) if execution_id else "unknown"
    short_id = exec_str[:8]

    from promaia.utils.env_writer import get_data_dir
    data_root = get_data_dir()
    log_dir = data_root / "context_logs" / "agent_context_logs" / agent_name.replace(" ", "_").lower()
    log_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{timestamp_str}_exec-{short_id}.md"
    log_path = log_dir / filename

    db_summary = ", ".join(databases) if databases else "none"

    content = f"""# Context Log: {agent_name}
**Execution**: exec-{exec_str}
**Timestamp**: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}
**Databases**: {db_summary}

---

## System Prompt

{system_prompt}

---

## Loaded Context

{formatted_context}

---

## Run Request

{run_request or '(none)'}
"""
    log_path.write_text(content, encoding="utf-8")
    return log_path


class AgentExecutor:
    """
    Executes scheduled agents with multi-step query capability.

    This executor:
    1. Loads context from specified databases
    2. Creates a prompt with custom agent instructions
    3. Allows AI to make multiple query tool calls iteratively
    4. Writes results to Notion
    5. Tracks execution metrics
    """

    def __init__(self, agent_config: AgentConfig):
        """
        Initialize the executor.

        Args:
            agent_config: The agent configuration
        """
        self.config = agent_config
        self.tracker = ExecutionTracker()
        self.notion_writer = NotionOutputWriter(workspace=agent_config.workspace)

    async def execute(
        self,
        run_request: Optional[str] = None,
        run_metadata: Optional[Dict[str, Any]] = None,
        cached_context: Optional[Dict[str, List[Dict[str, Any]]]] = None
    ) -> Dict[str, Any]:
        """
        Execute the agent.

        Args:
            run_request: Optional run-specific instruction (e.g., calendar event description)
            run_metadata: Optional metadata (e.g., calendar event id, start time)
            cached_context: Optional pre-loaded context to reuse (performance optimization)

        Returns:
            Execution result with status, metrics, output, and context for caching
        """
        execution_id = None
        start_time = datetime.now(timezone.utc)

        try:
            # Load fresh config from Notion if available
            if self.config.notion_page_id and self.config.agent_id:
                from promaia.agents.notion_config import load_agent_by_id
                notion_agent = await load_agent_by_id(self.config.agent_id, self.config.workspace)
                if notion_agent:
                    self.config = notion_agent
                    logger.info(f"Loaded System Prompt from Notion for '{self.config.name}'")

            # Start tracking execution
            execution_id = self.tracker.start_execution(self.config.name)
            logger.info(f"🤖 [exec:{execution_id}] Starting agent '{self.config.name}'")

            # Step 1: Load initial context (or use cached)
            if cached_context:
                logger.info(f"📚 [exec:{execution_id}] Using cached context (performance optimization)")
                initial_context = cached_context
            else:
                logger.info(f"📚 [exec:{execution_id}] Loading context from {len(self.config.databases)} source(s)...")
                initial_context = await self._load_initial_context()

            if not initial_context:
                logger.warning("No context data loaded")

            # Step 2: Execute agent (SDK or legacy mode)
            logger.debug(f"sdk_enabled={self.config.sdk_enabled}, SDK_AVAILABLE={SDK_AVAILABLE}")
            
            if self.config.sdk_enabled and SDK_AVAILABLE:
                logger.info("🚀 Using Claude Agent SDK for execution")
                result = await self._execute_with_sdk(initial_context, run_request=run_request, run_metadata=run_metadata, execution_id=execution_id)
            else:
                if self.config.sdk_enabled and not SDK_AVAILABLE:
                    import sys
                    details = ""
                    try:
                        details = f" (python={sys.executable}"
                        if '_SDK_IMPORT_ERROR' in globals():
                            details += f", import_error={globals().get('_SDK_IMPORT_ERROR')}"
                        details += ")"
                    except Exception:
                        details = ""
                    logger.warning(f"⚠️ SDK enabled but not available, falling back to legacy mode{details}")
                else:
                    logger.info(f"ℹ️ SDK disabled in config (sdk_enabled={self.config.sdk_enabled})")
                logger.info("🔄 Using legacy iteration loop")

                # Legacy mode: Load custom prompt and create full prompt with context
                custom_prompt = self._load_custom_prompt()
                full_prompt = self._create_agent_prompt(custom_prompt, initial_context)
                if run_request:
                    full_prompt += f"\n\n# Run Request\n\n{run_request}\n"

                # Save full context log for debugging/review
                try:
                    context_log_path = _save_context_log(
                        agent_name=self.config.name,
                        execution_id=execution_id or "unknown",
                        system_prompt=custom_prompt,
                        formatted_context=full_prompt,
                        run_request=run_request,
                        databases=self.config.databases,
                    )
                    logger.info(f"📋 Context log: {context_log_path}")
                except Exception as e:
                    logger.warning(f"Failed to save context log: {e}")

                result = await self._execute_with_iterations(full_prompt, initial_context)

            # Step 5: Write results to Notion (if output page configured)
            if result.get('output') and self.config.output_notion_page_id:
                success = await self._write_to_notion(result['output'])
                result['notion_written'] = success
            else:
                result['notion_written'] = False
            
            # Step 5.5: Send to messaging platform (if configured)
            # Skip if: already responding within an active conversation, OR
            # this is a synthesis task (journal writing) — output is the journal, not a message.
            in_conversation = run_metadata and run_metadata.get('conversation_id')
            is_synthesis = run_metadata and run_metadata.get('write_to_journal')

            if result.get('output') and self.config.messaging_enabled and not in_conversation and not is_synthesis:
                try:
                    messaging_success = await self._send_to_messaging_platform(result['output'])
                    result['messaging_sent'] = messaging_success
                except Exception as e:
                    logger.error(f"Error sending to messaging platform: {e}", exc_info=True)
                    result['messaging_sent'] = False
            else:
                result['messaging_sent'] = False

            # Step 6: Calculate metrics
            end_time = datetime.now(timezone.utc)
            duration = (end_time - start_time).total_seconds()

            metrics = {
                'iterations_used': result.get('iterations_used', 0),
                'tokens_used': result.get('tokens_used', 0),
                'cost_estimate': result.get('cost_estimate', 0.0),
                'duration_seconds': duration
            }

            # Complete execution tracking
            self.tracker.complete_execution(
                execution_id=execution_id,
                status='completed',
                iterations_used=metrics['iterations_used'],
                tokens_used=metrics['tokens_used'],
                cost_estimate=metrics['cost_estimate'],
                output_notion_page_id=self.config.output_notion_page_id,
                context_summary=f"Processed {len(initial_context)} context items"
            )

            # Update agent's last run time
            timestamp = datetime.now(timezone.utc).isoformat()
            update_agent_last_run(self.config.name, timestamp)

            # Update Last Run in Notion (but NOT journal - journal is for agent notes only)
            if self.config.notion_page_id and self.config.agent_id:
                try:
                    from promaia.agents.notion_config import update_last_run

                    await update_last_run(
                        agent_id=self.config.agent_id,
                        workspace=self.config.workspace,
                        timestamp=timestamp
                    )

                except Exception as e:
                    logger.warning(f"Could not update Last Run in Notion: {e}")
            
            # Note: Execution logs are stored in ExecutionTracker (SQLite: data/hybrid_metadata.db)
            # The Notion journal is ONLY for agent-initiated notes via write_journal tool

            logger.info(f"✅ [exec:{execution_id}] Agent '{self.config.name}' completed successfully")
            return {
                'success': True,
                'execution_id': execution_id,
                'metrics': metrics,
                'output': result.get('output'),
                'cached_context': initial_context  # Return for conversation caching
            }

        except Exception as e:
            logger.error(f"❌ [exec:{execution_id}] Agent '{self.config.name}' failed: {e}")

            if execution_id:
                self.tracker.complete_execution(
                    execution_id=execution_id,
                    status='failed',
                    error_message=str(e)
                )
            
            # Note: Execution errors are logged in ExecutionTracker (SQLite)
            # The Notion journal is ONLY for agent-initiated notes

            return {
                'success': False,
                'execution_id': execution_id,
                'error': str(e)
            }

    async def _load_initial_context(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Load initial context from configured databases.

        Returns:
            Dictionary mapping database names to lists of pages
        """
        context = {}

        for source_spec in self.config.databases:
            try:
                # Parse source spec (e.g., "journal:7", "stories:all", "ds#channelId:7")
                if ':' in source_spec:
                    database_name, days_str = source_spec.split(':', 1)
                    days = None if days_str == 'all' else int(days_str)
                else:
                    database_name = source_spec
                    days = None

                # Parse optional #channelId from database name
                # Format: "ds#1291943271509135415" -> db_name="ds", channel_id="1291943271509135415"
                channel_id = None
                if '#' in database_name:
                    database_name, channel_id = database_name.split('#', 1)

                # Get database config
                database_config = get_database_config(database_name, self.config.workspace)
                if not database_config:
                    logger.warning(f"  ⚠ Database '{database_name}' not found in workspace '{self.config.workspace}'")
                    continue

                # Build channel filter if a channel was specified
                property_filters = {}
                if channel_id:
                    if database_config.source_type == 'slack':
                        property_filters['slack_channel_id'] = channel_id
                    else:
                        property_filters['discord_channel_id'] = channel_id

                # Load pages for this database
                pages = load_database_pages_with_filters(
                    database_config=database_config,
                    days=days,
                    property_filters=property_filters if property_filters else None,
                )

                if pages:
                    # Use display name that includes channel for context key
                    display_name = f"{database_name}#{channel_id}" if channel_id else database_name
                    context[display_name] = self._shrink_pages_for_prompt(
                        database_name=display_name,
                        pages=pages,
                        max_pages=DEFAULT_MAX_PAGES_PER_SOURCE,
                    )
                    logger.info(f"  ✓ Loaded {len(pages)} pages from '{display_name}'")
                else:
                    logger.info(f"  ⚠ No pages found in '{database_name}'")

            except Exception as e:
                logger.error(f"  ✗ Failed to load '{source_spec}': {e}")

        # Load agent's own journal (fast local-file path via journal_db_id)
        if self.config.journal_db_id:
            try:
                from promaia.config.databases import get_database_manager
                db_manager = get_database_manager()
                journal_config = None
                for _key, db_cfg in db_manager.databases.items():
                    if db_cfg.database_id == self.config.journal_db_id:
                        journal_config = db_cfg
                        break

                if journal_config:
                    days = self.config.journal_memory_days
                    pages = load_database_pages_with_filters(
                        database_config=journal_config,
                        days=days
                    )
                    if pages:
                        context["agent_journal"] = self._shrink_pages_for_prompt(
                            database_name="agent_journal",
                            pages=pages,
                            max_pages=DEFAULT_MAX_PAGES_PER_SOURCE,
                        )
                        logger.info(f"  ✓ Loaded {len(pages)} journal entries ({days}d)")
                    else:
                        logger.info(f"  ⚠ No journal entries found")
                else:
                    logger.debug(f"  Journal DB not in database manager, skipping")
            except Exception as e:
                logger.warning(f"  ⚠ Could not load agent journal: {e}")

        return context

    def _shrink_pages_for_prompt(
        self,
        database_name: str,
        pages: List[Dict[str, Any]],
        max_pages: int,
    ) -> List[Dict[str, Any]]:
        """
        Cap the number of entries per source before stuffing pages into a model prompt.

        Individual entry content is NOT truncated — the overall context budget
        is managed at the conversation manager level against the model's token limit.
        """
        if not pages:
            return pages

        total = len(pages)
        trimmed = pages[:max_pages] if total > max_pages else list(pages)

        if total > max_pages:
            trimmed.insert(
                0,
                {
                    "filename": f"{database_name} (truncated)",
                    "content": (
                        f"NOTE: Initial context was truncated for safety. "
                        f"Showing first {max_pages} of {total} entries from '{database_name}'. "
                        f"Use query tools to retrieve more if needed."
                    ),
                },
            )

        return trimmed

    def _load_custom_prompt(self) -> str:
        """
        Load the custom prompt for the agent.

        Returns:
            Prompt text
        """
        prompt_value = self.config.prompt_file or ""

        # Heuristic: only treat as a path if it looks like one.
        # Many agents store the full prompt inline (multi-line markdown), and
        # blindly calling Path(...).exists() on that can raise "File name too long".
        looks_like_path = (
            isinstance(prompt_value, str)
            and "\n" not in prompt_value
            and len(prompt_value) <= 240
            and (
                prompt_value.startswith(("/", "./", "../", "~"))
                or prompt_value.endswith((".md", ".txt"))
                or ("/" in prompt_value)
            )
        )

        if looks_like_path:
            try:
                prompt_path = Path(prompt_value).expanduser()
                if prompt_path.is_file():
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        return f.read()
            except OSError:
                # Treat as inline if the "path" is invalid for the OS
                pass

        # Otherwise, treat it as inline content
        return prompt_value

    def _create_agent_prompt(
        self,
        custom_prompt: str,
        context_data: Dict[str, List[Dict[str, Any]]]
    ) -> str:
        """
        Create the full agent prompt with context and tools.

        Args:
            custom_prompt: The agent's custom instructions
            context_data: Loaded context from databases

        Returns:
            Full prompt string
        """
        # Start with custom instructions
        prompt = f"""# Agent: {self.config.name}

{custom_prompt}

# Available Tools

You have access to the following query tools to gather additional information:

1. **query_sql**: Execute natural language queries against structured data
   - Example: <tool_call><tool_name>query_sql</tool_name><parameters><query>find emails from our manufacturer</query></parameters></tool_call>

2. **query_vector**: Semantic search across all content
   - Example: <tool_call><tool_name>query_vector</tool_name><parameters><query>discussions about inventory</query></parameters></tool_call>

3. **query_source**: Load additional source data
   - Example: <tool_call><tool_name>query_source</tool_name><parameters><source>gmail:7</source></parameters></tool_call>

You can make multiple tool calls to gather all needed information. After gathering sufficient data, provide your final analysis and recommendations.

# Current Context

"""

        # Add formatted context
        if context_data:
            prompt += format_context_data(context_data)
        else:
            prompt += "No initial context loaded.\n"

        prompt += f"""

# Instructions

1. Analyze the context provided above
2. Use query tools if you need additional information
3. Identify any issues, patterns, or insights
4. Provide clear, actionable recommendations
5. Format your final response as a structured report

Current time: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
"""

        return prompt

    async def _execute_with_iterations(
        self,
        initial_prompt: str,
        context_data: Dict[str, List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """
        Execute agent with iteration loop for multi-step queries.

        Args:
            initial_prompt: The initial prompt with instructions
            context_data: Initial context data

        Returns:
            Result dictionary with output and metrics
        """
        # Initialize context state for query tools
        context_state = {
            'ai_queries': [],
            'context_data': context_data
        }

        query_executor = QueryToolExecutor(context_state)

        # Get AI model
        model = PromaiLLMAdapter(client_type="auto")

        # Conversation history
        messages = []
        total_tokens = 0
        total_cost = 0.0

        # Initial message
        messages.append({
            'role': 'user',
            'content': initial_prompt
        })

        # Iteration loop
        for iteration in range(self.config.max_iterations):
            logger.info(f"🔄 Iteration {iteration + 1}/{self.config.max_iterations}")

            try:
                # Call AI
                response = await self._call_ai(model, messages)
                response_text = response.get('content', '')

                # Track tokens/cost (if available)
                if response.get('usage'):
                    total_tokens += response['usage'].get('total_tokens', 0)
                    # Rough cost estimate (adjust based on model pricing)
                    total_cost += self._estimate_cost(response['usage'])

                # Check for query tool calls
                if query_executor.has_query_tool_calls(response_text):
                    logger.info("🔍 Found query tool calls, executing...")

                    # Parse tool calls
                    tool_calls = query_executor.parse_query_tool_calls(response_text)

                    # Execute queries (without permission callbacks - agents run autonomously)
                    query_results = []
                    for tool_call in tool_calls:
                        result = await query_executor._execute_query_only(tool_call)
                        query_results.append(result)

                    # Add results to conversation
                    results_text = "\n\n".join([
                        f"Query Result:\n{r.get('content', r.get('error', 'No result'))}"
                        for r in query_results
                    ])

                    messages.append({'role': 'assistant', 'content': response_text})
                    messages.append({'role': 'user', 'content': f"Tool Results:\n{results_text}\n\nPlease continue your analysis."})

                else:
                    # No more tool calls, this is the final response
                    logger.info("✅ Agent produced final output")
                    return {
                        'output': response_text,
                        'iterations_used': iteration + 1,
                        'tokens_used': total_tokens,
                        'cost_estimate': total_cost
                    }

            except Exception as e:
                logger.error(f"Error in iteration {iteration + 1}: {e}")
                break

        # Max iterations reached
        logger.warning(f"⚠️ Max iterations ({self.config.max_iterations}) reached")

        # Return last response if available
        if messages:
            last_message = messages[-1]
            if last_message.get('role') == 'assistant':
                return {
                    'output': last_message.get('content', 'No output generated'),
                    'iterations_used': self.config.max_iterations,
                    'tokens_used': total_tokens,
                    'cost_estimate': total_cost
                }

        return {
            'output': 'No output generated (max iterations reached)',
            'iterations_used': self.config.max_iterations,
            'tokens_used': total_tokens,
            'cost_estimate': total_cost
        }

    async def _call_ai(self, model, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Call the AI model.

        Args:
            model: The AI model instance (PromaiLLMAdapter)
            messages: Conversation messages

        Returns:
            Response dictionary with content and usage
        """
        # Use PromaiLLMAdapter's invoke method
        response = model.invoke(messages)

        # PromaiLLMAdapter returns a MockResponse with .content attribute
        if hasattr(response, 'content'):
            return {'content': response.content}
        elif isinstance(response, str):
            return {'content': response}
        else:
            return {'content': str(response)}

    def _estimate_cost(self, usage: Dict[str, int]) -> float:
        """
        Estimate cost based on token usage.

        Args:
            usage: Usage dict with token counts

        Returns:
            Estimated cost in USD
        """
        # Rough estimates (adjust based on actual model pricing)
        # Claude Sonnet: ~$3/$15 per 1M tokens (input/output)
        # GPT-4: ~$10/$30 per 1M tokens
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)

        # Conservative estimate
        cost = (input_tokens * 3 / 1_000_000) + (output_tokens * 15 / 1_000_000)
        return cost

    async def _write_to_notion(self, output: str) -> bool:
        """
        Write agent output to Notion page.

        Args:
            output: The agent's output text

        Returns:
            True if successful
        """
        try:
            success = await self.notion_writer.append_to_page(
                page_id=self.config.output_notion_page_id,
                content=output,
                agent_name=self.config.name,
                add_timestamp=True,
                add_divider=True
            )

            if success:
                logger.info(f"📝 Wrote output to Notion page {self.config.output_notion_page_id}")
            else:
                logger.error("Failed to write to Notion")

            return success

        except Exception as e:
            logger.error(f"Error writing to Notion: {e}")
            return False
    
    async def _send_to_messaging_platform(self, output: str) -> bool:
        """
        Send agent output to configured messaging platform.
        
        Supports both one-way posts and conversation initiation.
        Platform-agnostic: works with Slack, Discord, or any registered platform.
        
        Args:
            output: The agent's output text
        
        Returns:
            True if successful
        """
        try:
            if not self.config.messaging_platform or not self.config.messaging_channel_id:
                logger.warning("Messaging enabled but platform or channel not configured")
                return False
            
            # Import conversation manager
            from promaia.agents.conversation_manager import ConversationManager
            
            conv_manager = ConversationManager()
            
            # Register appropriate platform
            if self.config.messaging_platform == 'slack':
                from promaia.agents.messaging.slack_platform import SlackPlatform
                
                bot_token = os.environ.get('SLACK_BOT_TOKEN')
                if not bot_token:
                    logger.error("SLACK_BOT_TOKEN not found in environment")
                    return False
                
                platform = SlackPlatform(bot_token=bot_token)
                conv_manager.register_platform('slack', platform)
            
            elif self.config.messaging_platform == 'discord':
                from promaia.agents.messaging.discord_platform import DiscordPlatform
                
                bot_token = os.environ.get('DISCORD_BOT_TOKEN')
                if not bot_token:
                    logger.error("DISCORD_BOT_TOKEN not found in environment")
                    return False
                
                platform = DiscordPlatform(bot_token=bot_token)
                conv_manager.register_platform('discord', platform)
            
            else:
                logger.error(f"Unknown messaging platform: {self.config.messaging_platform}")
                return False
            
            # Either start conversation or post one-way message
            if self.config.initiate_conversation:
                # Start interactive conversation
                conversation = await conv_manager.start_conversation(
                    agent_id=self.config.agent_id or self.config.name,
                    platform=self.config.messaging_platform,
                    channel_id=self.config.messaging_channel_id,
                    initial_message=output,
                    timeout_minutes=self.config.conversation_timeout_minutes,
                    max_turns=self.config.conversation_max_turns
                )
                logger.info(f"💬 Started conversation on {self.config.messaging_platform}: {conversation.conversation_id}")
            else:
                # Just post output (one-way)
                platform_impl = conv_manager.platforms[self.config.messaging_platform]
                await platform_impl.send_message(
                    channel_id=self.config.messaging_channel_id,
                    content=platform_impl.format_message(output, self.config.name)
                )
                logger.info(f"📤 Posted to {self.config.messaging_platform} channel {self.config.messaging_channel_id}")
            
            return True
        
        except Exception as e:
            logger.error(f"Error sending to messaging platform: {e}", exc_info=True)
            return False

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
        """Return a concise, human-readable summary of a tool call's input."""
        if not tool_input:
            return "(no input)"
        try:
            # File write tools — show path + size
            if tool_name in ("Write", "write", "create_file"):
                path = tool_input.get("file_path") or tool_input.get("path", "?")
                content = tool_input.get("content", "")
                return f"{path} ({len(content)} chars)"

            # Read / Glob / Grep
            if tool_name in ("Read", "read"):
                return tool_input.get("file_path") or tool_input.get("path", str(tool_input)[:200])
            if tool_name in ("Glob", "glob"):
                return tool_input.get("pattern", str(tool_input)[:200])
            if tool_name in ("Grep", "grep"):
                return f"/{tool_input.get('pattern', '?')}/ in {tool_input.get('path', '.')}"

            # Edit tool
            if tool_name in ("Edit", "edit"):
                path = tool_input.get("file_path", "?")
                old = tool_input.get("old_string", "")[:60]
                return f"{path} (replacing '{old}...')"

            # Bash
            if tool_name in ("Bash", "bash"):
                return tool_input.get("command", str(tool_input)[:200])

            # MCP tools — show all keys briefly
            parts = []
            for k, v in tool_input.items():
                v_str = str(v)
                if len(v_str) > 80:
                    v_str = v_str[:77] + "..."
                parts.append(f"{k}={v_str}")
            return ", ".join(parts)[:300]
        except Exception:
            return str(tool_input)[:200]

    # ==================== SDK INTEGRATION METHODS ====================

    async def _execute_with_sdk(
        self,
        initial_context: Dict[str, List[Dict[str, Any]]],
        run_request: Optional[str] = None,
        run_metadata: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute agent using Claude Agent SDK with multi-message context approach.

        Uses message history to split large context across multiple messages,
        bypassing the 40KB per-message subprocess buffer limit.

        Args:
            initial_context: Preloaded context from databases
            run_request: Optional run-specific instruction
            run_metadata: Optional metadata
            execution_id: Current execution ID for tracking

        Returns:
            Result dictionary with output and metrics
        """
        if not SDK_AVAILABLE:
            raise RuntimeError("SDK execution requested but SDK not available")

        try:
            # Build initial message history with preloaded context (may be multi-message)
            initial_messages = self._build_initial_message_history(
                initial_context,
                run_request=run_request,
                run_metadata=run_metadata
            )

            # Build system prompt once, reuse in SDK options and context log
            system_prompt = self._build_system_prompt(run_metadata=run_metadata)

            # Build SDK options with tools and MCP servers
            sdk_options = self._build_sdk_options(run_metadata=run_metadata, system_prompt=system_prompt)

            # Execute with SDK
            messages = []
            final_result = None
            last_assistant_text = None
            total_iterations = 0

            logger.info("🤖 Starting SDK agent loop...")
            logger.info(f"📨 Initial context: {len(initial_messages)} message(s)")

            # For now, concatenate all messages into single prompt
            # SDK multi-message support seems to have issues, so we'll use single large prompt
            full_prompt_parts = []
            for i, msg in enumerate(initial_messages):
                role = msg['role']
                content = msg['content']

                # Skip context-chunking acknowledgments (short assistant messages)
                if role == 'assistant' and content == "Context received. Ready for more.":
                    continue

                # All messages should be 'user' role at this point (context + conversation history + instructions)
                if role == 'user':
                    if i == 0:
                        full_prompt_parts.append(content)
                    else:
                        full_prompt_parts.append(f"\n---\n\n{content}")

            full_prompt = "\n".join(full_prompt_parts)
            logger.info(f"📨 Combined prompt size: {len(full_prompt):,} chars (~{len(full_prompt)/1024:.1f} KB)")

            # Save full context log for debugging/review
            try:
                context_log_path = _save_context_log(
                    agent_name=self.config.name,
                    execution_id=execution_id or "unknown",
                    system_prompt=system_prompt,
                    formatted_context=full_prompt,
                    run_request=run_request,
                    databases=self.config.databases,
                )
                logger.info(f"📋 Context log: {context_log_path}")
            except Exception as e:
                logger.warning(f"Failed to save context log: {e}")

            # Log first 1000 chars of prompt for debugging conversation context
            logger.info(f"📝 Prompt preview (first 1000 chars):\n{full_prompt[:1000]}...")
            # Check if conversation context is included
            if "# Previous Conversation Context" in full_prompt:
                logger.info(f"✅ Prompt includes previous conversation context")

            # Use ClaudeSDKClient for better MCP support
            logger.info("⏳ Starting Claude SDK subprocess...")
            async with ClaudeSDKClient(options=sdk_options) as client:
                logger.info("✅ SDK subprocess ready — sending prompt")
                await client.query(full_prompt)
                logger.info("💭 Agent is thinking...")

                async for message in client.receive_response():
                    messages.append(message)
                    message_type = type(message).__name__

                    # ----- ResultMessage (final output) -----
                    if message_type == "ResultMessage":
                        # ResultMessage has .result (str), not .content
                        result_text = getattr(message, 'result', None)
                        if result_text:
                            final_result = result_text

                    # ----- AssistantMessage (agent speaks / calls tools) -----
                    elif message_type == "AssistantMessage":
                        if hasattr(message, 'content'):
                            content_blocks = []
                            tool_calls = []
                            for block in message.content:
                                # Skip thinking blocks (have .thinking, not .text)
                                if hasattr(block, 'thinking'):
                                    continue
                                if hasattr(block, 'text'):
                                    content_blocks.append(block.text)
                                elif hasattr(block, 'type') and block.type == 'tool_use':
                                    tool_name = getattr(block, 'name', 'unknown')
                                    tool_id = getattr(block, 'id', '?')
                                    tool_input = getattr(block, 'input', {})
                                    tool_calls.append((tool_name, tool_id, tool_input))

                            assistant_content = "\n".join(content_blocks)

                            # Log what the agent said (full text, not truncated)
                            if assistant_content:
                                logger.info(f"💭 Agent: {assistant_content[:500]}")

                                # Skip SDK/API error messages from user-visible output
                                if assistant_content.startswith("API Error:"):
                                    logger.warning(f"⚠️ Stripping API error from output: {assistant_content[:200]}")
                                    continue

                                # Only keep the LAST assistant text as output.
                                # Intermediate messages are narration between tool calls
                                # and should not be sent to the user.
                                last_assistant_text = assistant_content

                            # Log each tool call with its input
                            for tool_name, tool_id, tool_input in tool_calls:
                                # Show useful input details depending on the tool
                                input_summary = self._summarize_tool_input(tool_name, tool_input)
                                logger.info(f"🔧 Calling tool: {tool_name} — {input_summary}")

                    # ----- UserMessage / ToolResultMessage (tool results) -----
                    elif message_type in ("ToolResultMessage", "UserMessage"):
                        content = getattr(message, 'content', None)
                        if content:
                            if isinstance(content, str):
                                logger.info(f"📥 Result: {content[:300]}")
                            elif isinstance(content, list):
                                for block in content:
                                    is_error = getattr(block, 'is_error', False)
                                    result_content = getattr(block, 'content', getattr(block, 'text', getattr(block, 'result', '')))
                                    status = "❌" if is_error else "✅"
                                    logger.info(f"📥 {status} {str(result_content)[:300]}")
                                logger.info("💭 Agent is thinking...")

                    # ----- SystemMessage -----
                    elif message_type == "SystemMessage":
                        logger.info(f"⚙️ System message received")

                    # ----- Unknown -----
                    else:
                        logger.info(f"📨 {message_type}")

                    total_iterations += 1

            logger.info(f"✅ SDK execution completed ({total_iterations} turns)")

            # Extract metrics from messages
            total_tokens = 0
            total_cost = 0.0
            input_tokens_total = 0
            output_tokens_total = 0

            for msg in messages:
                if hasattr(msg, 'usage'):
                    usage = msg.usage
                    # Handle both dict and object formats
                    if isinstance(usage, dict):
                        input_tok = usage.get('input_tokens', 0)
                        output_tok = usage.get('output_tokens', 0)
                    else:
                        input_tok = getattr(usage, 'input_tokens', 0)
                        output_tok = getattr(usage, 'output_tokens', 0)
                    
                    input_tokens_total += input_tok
                    output_tokens_total += output_tok
                    logger.debug(f"📊 Usage from {type(msg).__name__}: input={input_tok}, output={output_tok}")
            
            # Calculate total tokens
            total_tokens = input_tokens_total + output_tokens_total

            # Cost estimation for Sonnet 4.5 1M context
            # Pricing: $3 per 1M input tokens, $15 per 1M output tokens
            total_cost = (input_tokens_total * 3 / 1_000_000) + (output_tokens_total * 15 / 1_000_000)

            logger.info(f"📊 Total tokens: {total_tokens} (input: {input_tokens_total}, output: {output_tokens_total})")
            logger.info(f"💰 Estimated cost: ${total_cost:.4f}")

            # Prefer ResultMessage.result; fall back to last assistant text
            if not final_result and last_assistant_text:
                final_result = last_assistant_text

            return {
                'output': final_result or "No output generated",
                'iterations_used': total_iterations,
                'tokens_used': total_tokens,
                'cost_estimate': total_cost,
                'messages': messages
            }

        except Exception as e:
            logger.error(f"Error in SDK execution: {e}")
            # Log full traceback for debugging
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    def _build_initial_message_history(
        self,
        initial_context: Dict[str, List[Dict[str, Any]]],
        run_request: Optional[str] = None,
        run_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, str]]:
        """
        Build initial message history with FULL preloaded context.

        Splits large context across multiple messages to bypass 40KB per-message
        subprocess buffer limit. No truncation - all context is preserved.

        Args:
            initial_context: Dict mapping database names to lists of pages

        Returns:
            List of message dicts for conversation history
        """
        messages = []

        # Format FULL context data
        if initial_context:
            formatted_context = format_context_data(initial_context)
            context_size = len(formatted_context)
            logger.info(f"Total context size: {context_size:,} chars (~{context_size/1024:.1f} KB)")

            # Split context into chunks if needed (35KB per chunk for safety)
            chunk_size = 35000
            context_chunks = []

            if context_size <= chunk_size:
                # Fits in one message
                context_chunks = [formatted_context]
            else:
                # Split into chunks
                logger.info(f"Splitting context into chunks (max {chunk_size/1024:.0f}KB each)...")

                # Try to split at natural boundaries (database sections)
                current_chunk = ""
                for line in formatted_context.split('\n'):
                    if len(current_chunk) + len(line) + 1 > chunk_size and current_chunk:
                        # Current chunk is full, start new one
                        context_chunks.append(current_chunk)
                        current_chunk = line + '\n'
                    else:
                        current_chunk += line + '\n'

                # Add final chunk
                if current_chunk:
                    context_chunks.append(current_chunk)

            logger.info(f"Context split into {len(context_chunks)} chunk(s)")

            # Build message history with context chunks
            for i, chunk in enumerate(context_chunks):
                if i == 0:
                    # First message includes header
                    msg = f"""# Preloaded Context (Part {i+1}/{len(context_chunks)})

Here is your initial working context loaded from configured sources:

{chunk}"""
                else:
                    # Subsequent messages are continuations
                    msg = f"""# Preloaded Context (Part {i+1}/{len(context_chunks)} - continued)

{chunk}"""

                messages.append({"role": "user", "content": msg})

                # Add acknowledgment from assistant (except for last chunk)
                if i < len(context_chunks) - 1:
                    messages.append({"role": "assistant", "content": "Context received. Ready for more."})

                logger.info(f"  Chunk {i+1}: {len(msg):,} chars (~{len(msg)/1024:.1f} KB)")

        else:
            # No context loaded
            messages.append({
                "role": "user",
                "content": "# Preloaded Context\n\nNo initial context data loaded."
            })

        # Add conversation history if present (for conversational agents)
        run_metadata = run_metadata or {}
        conversation_history = run_metadata.get('conversation_history', [])

        if conversation_history and len(conversation_history) > 1:
            # Format conversation history as a single context block
            # Exclude the last message (current user message) from history
            history_messages = conversation_history[:-1]
            logger.info(f"Adding {len(history_messages)} previous conversation messages to context")

            conv_parts = ["""# Conversation Mode

You are in an ONGOING CONVERSATION with the user. This is NOT a new interaction.

## Conversation History

Here is everything that's been said so far:
"""]
            for i, msg in enumerate(history_messages):
                role_label = "User" if msg['role'] == 'user' else "You (your previous response)"
                conv_parts.append(f"\n{role_label}: {msg['content']}")

            conv_parts.append("""

---

## Instructions for Continuing the Conversation

1. **Remember everything** from the conversation above
2. **Build on previous topics** - don't start over or ask questions already answered
3. **Reference what was said** - show you remember the context
4. **Be natural** - this is a flowing conversation, not isolated Q&A
5. If the user says something brief or vague, interpret it in the context of what you've been discussing

IMPORTANT: This is turn #{} of an ongoing conversation. Act like you remember everything that's been said.""".format(len(history_messages) // 2 + 1))

            # Add as a single context message
            messages.append({
                "role": "user",
                "content": "\n".join(conv_parts)
            })

        # Add final instructions message
        run_context_block = ""
        if run_request:
            meta_lines = []
            for k in ["calendar_event_id", "calendar_event_start", "calendar_event_summary", "calendar_event_link"]:
                if run_metadata.get(k):
                    meta_lines.append(f"- {k}: {run_metadata.get(k)}")
            meta_text = "\n".join(meta_lines)
            if meta_text:
                meta_text = "\n\nMetadata:\n" + meta_text

            # If this is a conversation, label it clearly as the user's new message
            in_conversation = conversation_history and len(conversation_history) > 1
            if in_conversation:
                run_context_block = f"""
---

# User's Current Message

User: {run_request}

Respond naturally to continue the conversation.{meta_text}
"""
            else:
                run_context_block = f"""
---

# Run Request

{run_request}{meta_text}
"""

        from promaia.utils.env_writer import get_data_subdir
        data_location = get_data_subdir() / "md" / "notion" / self.config.workspace

        final_instructions = f"""
---

# How to Expand Context

If you need more data beyond what's preloaded above, use these query tools:

1. **query_source(database, days)**: Load different time ranges
   - Example: `query_source(database='journal', days=30)` for more history

2. **query_sql(query, reasoning)**: Search for specific keywords
   - Example: `query_sql(query='emails about budget', reasoning='need financial context')`

3. **query_vector(query, reasoning)**: Semantic/conceptual search
   - Example: `query_vector(query='team morale discussions', reasoning='understanding dynamics')`

## Additional Tools Available:

- File operations: Read, Write, Edit, Bash (data at `{data_location}/`)
- Search: Grep, Glob
- Web: WebSearch, WebFetch

---

# Your Task

Review the preloaded context above and take appropriate actions based on your system instructions.
You can query for additional context if needed, but start by analyzing what's already provided.
{run_context_block}

Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
"""
        messages.append({"role": "user", "content": final_instructions})

        return messages

    def _build_system_prompt(self, run_metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Build system prompt with instructions and schema preview (NO context data).

        Context data goes in the initial user message, not here.
        This allows the system prompt to stay lean and the agent to expand context dynamically.

        Args:
            run_metadata: Optional metadata for context-aware prompts (e.g., conversation mode)

        Returns:
            System prompt string
        """
        # Load custom prompt from file or Notion
        custom_instructions = self._load_custom_prompt()
        run_metadata = run_metadata or {}

        # Resolve data paths for prompt text
        from promaia.utils.env_writer import get_data_subdir
        data_location = get_data_subdir() / "md" / "notion" / self.config.workspace
        journal_location = data_location / f"{self.config.agent_id.replace('-', '_')}_journal"

        parts = [
            f"You are {self.config.name}.",
            "",
            "# System Instructions",
            custom_instructions,
            "",
            "# Available Data Sources & Permissions",
            self._format_source_permissions(),
            "",
            "# Data Source Schema Preview",
            self._build_schema_preview(),
            "",
            "# Query Tools",
            "",
            "Use these to access your data:",
            "",
            "1. **query_sql(query, reasoning)**: Search for EXACT TEXT/KEYWORDS",
            "   - Example: `query_sql(query='tasks in current sprint', reasoning='need sprint status')`",
            "",
            "2. **query_vector(query, reasoning, top_k, min_similarity)**: Semantic/conceptual search",
            "   - Example: `query_vector(query='project delays', reasoning='understanding blockers')`",
            "",
            "3. **query_source(database, days)**: Load from specific source",
            "   - Example: `query_source(database='journal', days=30)`",
            "",
            "## Query Tool Usage Guidelines",
            "",
            "**CRITICAL**: Only use query tools when you genuinely CANNOT answer the user's question with the current context.",
            "",
            "Before using a query tool, ask yourself:",
            "1. Can I provide a reasonable answer with the current context?",
            "2. Is the user explicitly asking for information from other sources?",
            "3. Would my answer be incomplete or incorrect without additional data?",
            "",
            "If you can answer with current context (even partially), DO NOT use query tools.",
            "",
            "If you must expand context:",
            "1. Explain WHY you need to search elsewhere in your reasoning",
            "2. The system will ask the user for approval BEFORE executing",
            "3. User may decline - be prepared to answer with available data",
            "",
            "# Additional Tools",
            "",
            "File operations: Read, Write, Edit, Bash",
            "Search: Glob, Grep",
            "Web: WebSearch, WebFetch",
            "",
            f"File data location: `{data_location}/`",
            "",
            "## ✅ Writing Journal Entries (LOCAL-FIRST)",
            "",
            "**IMPORTANT**: To create journal entries or notes:",
            "",
            "1. **Write markdown files directly** to the local filesystem",
            "2. **Use the Write tool** - DO NOT use Notion MCP tools",
            "3. **Location**: Write to YOUR agent's journal directory",
            "",
            f"**YOUR JOURNAL DIRECTORY**: `{journal_location}/`",
            "",
            "**Filename format**: `YYYY-MM-DD Title.md`",
            "Example: `2026-02-07 Technical Cofounder Breakthrough.md`",
            "",
            "**Why local-first?**",
            "- ⚡ Instant writes (no API latency)",
            "- 💰 Zero token cost (no MCP in prompts)",
            "- 🔄 Automatic sync daemon pushes to Notion every 5 minutes",
            "",
            "**Template**:",
            "```markdown",
            "# [Title]",
            "",
            "[Your content here]",
            "```",
            "",
            "**Example**:",
            "```python",
            f"Write(file_path=\"{journal_location}/2026-02-07 Meeting Notes.md\",",
            "      content=\"# Meeting Notes\\n\\nKey takeaways...\")",
            "```",
        ]

        # Add MCP tool documentation if any are configured
        if self.config.mcp_tools:
            parts.append("")
            parts.append("# External Integrations (MCP Tools)")
            parts.append("")
            parts.append("You have access to external services via MCP tools.")
            parts.append("⚠️ MCP tools are DEFERRED - use ToolSearch to load them first!")
            parts.append("")

            for tool_name in self.config.mcp_tools:
                if tool_name == "notion-helper":
                    parts.append("## Notion Helper (Simple MCP Tools)")
                    parts.append("")
                    parts.append("✅ USE THESE! They work reliably without parameter issues.")
                    parts.append("")
                    parts.append("**Available Tools:**")
                    parts.append("- `mcp__notion-helper__search_databases`: Find database by name")
                    parts.append("  - Params: `query` (string)")
                    parts.append("- `mcp__notion-helper__create_page_in_database`: Create page")
                    parts.append("  - Params: `database_id`, `title`, `content` (markdown)")
                    parts.append("")
                    parts.append("**Workflow:**")
                    parts.append("```python")
                    parts.append("# 1. Search for database")
                    parts.append("ToolSearch(query=\"notion helper search\")")
                    parts.append("mcp__notion-helper__search_databases(query=\"Stories\")")
                    parts.append("")
                    parts.append("# 2. Create page with extracted ID")
                    parts.append("ToolSearch(query=\"notion helper create page\")")
                    parts.append("mcp__notion-helper__create_page_in_database(")
                    parts.append("  database_id=\"1d1d1339-6967-803f-a4d0-ec557db459f8\",")
                    parts.append("  title=\"My Page Title\",")
                    parts.append("  content=\"Optional markdown content\"")
                    parts.append(")")
                    parts.append("```")
                    parts.append("")
                elif tool_name == "notion":
                    parts.append("## Notion API (MCP Tools)")
                    parts.append("")
                    parts.append("You have FULL read/write access to Notion via MCP tools.")
                    parts.append("All tools are prefixed with `mcp__notion__`")
                    parts.append("")
                    parts.append("### 🔍 Step 1: Load Tools with ToolSearch")
                    parts.append("")
                    parts.append("Before using Notion tools, search for them:")
                    parts.append("```")
                    parts.append("ToolSearch(query=\"notion create page\")")
                    parts.append("ToolSearch(query=\"notion search database\")")
                    parts.append("```")
                    parts.append("")
                    parts.append("### 📝 Step 2: Use Loaded Tools")
                    parts.append("")
                    parts.append("Available Notion tools (after ToolSearch):")
                    parts.append("- `mcp__notion__API-post-search`: Search Notion by title")
                    parts.append("- `mcp__notion__API-query-data-source`: Query a database")
                    parts.append("- `mcp__notion__API-retrieve-a-data-source`: Get database schema")
                    parts.append("- `mcp__notion__API-post-page`: Create new page in database")
                    parts.append("- `mcp__notion__API-patch-page`: Update page")
                    parts.append("- `mcp__notion__API-retrieve-a-page`: Get page content")
                    parts.append("")
                    parts.append("### ✅ CORRECT Way to Create a Page")
                    parts.append("")
                    parts.append("**Example: Create page in Stories database**")
                    parts.append("```python")
                    parts.append("# Step 1: Find the database ID")
                    parts.append("ToolSearch(query=\"notion search\")")
                    parts.append("mcp__notion__API-post-search(")
                    parts.append("  query=\"Stories\",")
                    parts.append("  filter={\"value\": \"database\", \"property\": \"object\"}")
                    parts.append(")")
                    parts.append("# Extract database_id from the results")
                    parts.append("")
                    parts.append("# Step 2: Create page with OBJECT parameters")
                    parts.append("ToolSearch(query=\"notion create page\")")
                    parts.append("mcp__notion__API-post-page(")
                    parts.append("  parent={\"database_id\": \"<extracted-id>\"},")
                    parts.append("  properties={")
                    parts.append("    \"title\": {")
                    parts.append("      \"title\": [{\"text\": {\"content\": \"My Page Title\"}}]")
                    parts.append("    }")
                    parts.append("  }")
                    parts.append(")")
                    parts.append("```")
                    parts.append("")
                    parts.append("### ❌ WRONG - These cause 400 errors:")
                    parts.append("```python")
                    parts.append("# DON'T stringify parent or properties!")
                    parts.append("parent='{\"database_id\": \"...\"}' ← WRONG - this is a STRING not an OBJECT")
                    parts.append("properties='{\"title\": [...]}' ← WRONG - this is a STRING not an OBJECT")
                    parts.append("```")
                    parts.append("")
                    parts.append("### 🎯 Correct Workflow for Creating Pages")
                    parts.append("")
                    parts.append("**Step-by-step:**")
                    parts.append("1. Search for the database by name to get its ID:")
                    parts.append("   ```")
                    parts.append("   ToolSearch(query=\"notion search\")")
                    parts.append("   mcp__notion__API-post-search(")
                    parts.append("     query=\"Stories\",")
                    parts.append("     filter={\"value\": \"database\", \"property\": \"object\"}")
                    parts.append("   )")
                    parts.append("   ```")
                    parts.append("")
                    parts.append("2. Extract the database ID from results (look for `id` field)")
                    parts.append("")
                    parts.append("3. Create the page with OBJECT parameters:")
                    parts.append("   ```")
                    parts.append("   ToolSearch(query=\"notion create page\")")
                    parts.append("   mcp__notion__API-post-page(")
                    parts.append("     parent={\"database_id\": \"<id-from-search>\"},")
                    parts.append("     properties={")
                    parts.append("       \"title\": {")
                    parts.append("         \"title\": [{\"text\": {\"content\": \"Page Title\"}}]")
                    parts.append("       }")
                    parts.append("     }")
                    parts.append("   )")
                    parts.append("   ```")
                    parts.append("")
                    parts.append("⚠️ CRITICAL: Database IDs change! Always search first, never hardcode IDs!")
                    parts.append("")
                elif tool_name == "gmail":
                    parts.append("## Gmail API (MCP Tools)")
                    parts.append("")
                    parts.append("You can read and send emails. Key tools:")
                    parts.append("- `mcp__gmail__list_messages`: Search/list emails")
                    parts.append("- `mcp__gmail__get_message`: Read specific email")
                    parts.append("- `mcp__gmail__send_message`: Send new email")
                    parts.append("- `mcp__gmail__create_draft`: Create email draft")
                    parts.append("")

        # TODO: Add conversation-specific guidance once end_conversation tool is working
        # Disabled for now because SDK tool registration needs to be fixed
        # if run_metadata.get('conversation_id'):
        #     parts.append("")
        #     parts.append("# Ending Conversations")
        #     parts.append("")
        #     parts.append("You have access to an `end_conversation` tool. Call this when:")
        #     parts.append("- User says goodbye (bye, see you, talk later, etc.)")
        #     parts.append("- User indicates they need to leave (gotta go, have to run, etc.)")
        #     parts.append("- User thanks you and indicates completion (thanks, that's all, we're done, etc.)")
        #     parts.append("- Natural end of conversation reached")
        #     parts.append("")
        #     parts.append("**Important**: After responding with your farewell message, call the `end_conversation` tool")
        #     parts.append("to formally end the conversation. This lets the system know the conversation is complete.")
        #     parts.append("")
        #     parts.append("Example:")
        #     parts.append("```")
        #     parts.append("User: 'Thanks! Gotta run now, bye!'")
        #     parts.append("You: 'Great chatting with you! Have a wonderful day!'")
        #     parts.append("[Then call: end_conversation(reason='user said goodbye')]")
        #     parts.append("```")
        #     parts.append("")

        system_prompt = "\n".join(parts)

        # Monitor size
        logger.info(f"System prompt size: {len(system_prompt):,} chars (~{len(system_prompt)/1024:.1f} KB)")

        return system_prompt

    def _format_source_permissions(self) -> str:
        """
        Format available sources and agent permissions for system prompt.

        Returns:
            Formatted permissions string
        """
        sources_info = []

        # Get queryable sources
        queryable = self.config.get_queryable_sources()
        writable = self.config.get_writable_sources()

        # Format source info
        for source in queryable:
            permissions = ["queryable"]
            if source in writable:
                permissions.append("writable")

            sources_info.append(f"- **{source}**: {', '.join(permissions)}")

        if sources_info:
            return "You have access to the following data sources:\n" + "\n".join(sources_info)
        else:
            return "No queryable sources configured."

    def _build_schema_preview(self) -> str:
        """
        Build schema preview showing available sources with sample data.
        Filtered by agent's queryable permissions.

        This allows agents to "see over the fence" and know what data sources
        exist beyond their initial context.

        Returns:
            Formatted schema preview string
        """
        from promaia.ai.prompts import generate_database_preview

        # Get sources agent can query
        queryable_sources = self.config.get_queryable_sources()

        if not queryable_sources:
            return "No queryable data sources configured."

        # Generate preview for queryable sources only
        preview = generate_database_preview(
            workspace=self.config.workspace,
            exclude_databases=[],  # Show all queryable sources
            limit_to_databases=queryable_sources,  # Filter to permissions
            max_examples=3
        )

        if preview:
            # Monitor schema preview size
            logger.debug(f"Schema preview size: {len(preview):,} chars (~{len(preview)/1024:.1f} KB)")
            return preview
        else:
            return "No data sources available for preview."

    def _build_sdk_options(
        self,
        run_metadata: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
    ):
        """
        Build Claude Agent SDK options with tools and MCP servers.

        Uses external stdio MCP server for Promaia query tools to bypass SDK bug.

        Args:
            run_metadata: Optional metadata containing conversation context
            system_prompt: Optional pre-built system prompt. Built automatically if not provided.

        Returns:
            ClaudeAgentOptions configured for this agent
        """
        if not SDK_AVAILABLE:
            raise RuntimeError("SDK not available")

        import sys
        from pathlib import Path

        # Path to our external MCP server
        mcp_server_path = Path(__file__).parent.parent / "mcp" / "query_tools_server.py"

        # MCP servers - load from config
        import os
        # Ensure .env is loaded
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        # Build MCP servers configuration based on agent's mcp_tools permissions
        # Each agent gets its own MCP server subprocess for proper isolation and permission enforcement
        mcp_servers = {}

        # Launch Promaia MCP server if agent has permission
        if self.config.mcp_tools and "promaia" in self.config.mcp_tools:
            mcp_servers["promaia"] = {
                "command": sys.executable,
                "args": [
                    "-m", "promaia.mcp.query_tools_server",
                    "--workspace", self.config.workspace,
                    "--agent-id", self.config.agent_id or self.config.name
                ],
                "env": {}
            }
            logger.info(f"✓ Configured Promaia MCP server (query tools)")

        # Launch Gmail MCP server if agent has permission (write-only: send, draft, reply)
        if self.config.mcp_tools and "gmail" in self.config.mcp_tools:
            mcp_servers["gmail"] = {
                "command": sys.executable,
                "args": [
                    "-m", "promaia.mcp.gmail_tools_server",
                    "--workspace", self.config.workspace,
                    "--agent-id", self.config.agent_id or self.config.name
                ],
                "env": {}
            }
            logger.info(f"✓ Configured Gmail MCP server (write-only: send/draft/reply)")

        # Launch Calendar MCP server if agent has permission (write-only: create, update, delete)
        if self.config.mcp_tools and "calendar" in self.config.mcp_tools:
            mcp_servers["calendar"] = {
                "command": sys.executable,
                "args": [
                    "-m", "promaia.mcp.calendar_tools_server",
                    "--workspace", self.config.workspace,
                    "--agent-id", self.config.agent_id or self.config.name
                ],
                "env": {}
            }
            logger.info(f"✓ Configured Calendar MCP server (write-only: create/update/delete)")

        # Log configured MCP tools
        if self.config.mcp_tools:
            configured = [tool for tool in self.config.mcp_tools if tool in mcp_servers]
            pending = [tool for tool in self.config.mcp_tools if tool not in mcp_servers]
            if configured:
                logger.info(f"✓ Active MCP tools: {configured}")
            if pending:
                logger.info(f"⚠️ Pending MCP tools (not yet implemented): {pending}")

        # Custom tools list (for SDK-native tools, not MCP)
        custom_tools = []

        # TODO: Re-enable end_conversation tool once SDK tool registration is fixed
        # The tool needs to be registered via McpSdkServerConfig, not passed directly
        # For now, rely on regex-based goodbye detection in conversation_manager.py

        # Add end_conversation tool if we're in conversation mode
        # run_metadata = run_metadata or {}
        # if run_metadata.get('conversation_id') and run_metadata.get('conversation_manager'):
        #     from promaia.agents.custom_tools import create_conversation_end_tool
        #
        #     conversation_id = run_metadata['conversation_id']
        #     conversation_manager = run_metadata['conversation_manager']
        #
        #     end_tool = create_conversation_end_tool(conversation_manager, conversation_id)
        #     custom_tools.append(end_tool)
        #
        #     logger.info(f"✅ Added end_conversation tool for conversation {conversation_id[:20]}...")

        # Allow all tools - no restriction list
        allowed_tools_list = None

        # Only use explicit list if user configured one
        if self.config.sdk_allowed_tools:
            allowed_tools_list = self.config.sdk_allowed_tools

        # Map permission mode
        permission_mode = self.config.sdk_permission_mode
        if permission_mode == "acceptAll":
            permission_mode = "bypassPermissions"

        logger.info(f"Configured {len(mcp_servers)} MCP server(s): {list(mcp_servers.keys())}")
        if allowed_tools_list:
            logger.info(f"SDK tools restricted to: {', '.join(allowed_tools_list)}")
        else:
            logger.info("SDK tools: ALL (no restrictions)")

        # Log MCP server configurations
        for server_name, server_config in mcp_servers.items():
            logger.debug(f"MCP Server '{server_name}': command={server_config.get('command')}, args={server_config.get('args')}")

        return ClaudeAgentOptions(
            system_prompt=system_prompt or self._build_system_prompt(run_metadata=run_metadata),
            allowed_tools=allowed_tools_list,
            # tools parameter removed - was causing SDK to fail
            # TODO: Register custom tools via McpSdkServerConfig instead
            mcp_servers=mcp_servers,
            setting_sources=['local', 'project'],  # Load local & project MCP config
            permission_mode=permission_mode,
            max_turns=self.config.max_iterations,
            model="claude-sonnet-4-6",  # Sonnet 4.6
        )


# Synchronous wrapper
def execute_agent_sync(agent_config: AgentConfig) -> Dict[str, Any]:
    """
    Synchronous wrapper for agent execution.

    Args:
        agent_config: The agent configuration

    Returns:
        Execution result
    """
    executor = AgentExecutor(agent_config)
    return asyncio.run(executor.execute())
