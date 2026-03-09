"""
Agent Orchestrator - Planning Layer for Multi-Step Tasks.

Orchestrates complex goals that involve multiple tasks, async conversations,
and sequential/parallel execution patterns.

Example goals:
- "Check in with team and summarize results at end of day"
- "Review PRs, then write weekly summary"
- "Have goal-setting conversation with Alice and record takeaways"
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Callable, Awaitable
from pathlib import Path

from promaia.agents.task_queue import (
    TaskQueue, Task, TaskType, TaskStatus, Goal
)
from promaia.agents.planner import Planner, PlannerConfig
from promaia.agents.agent_config import AgentConfig
from promaia.agents.conversation_manager import ConversationManager

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main orchestrator for multi-step goal execution.

    The orchestrator:
    1. Receives a goal from calendar events or user requests
    2. Calls the Planner to decompose into tasks
    3. Monitors the task queue
    4. Handles async completions (conversations)
    5. Evaluates goal completion, triggers re-planning if needed

    Usage:
        orchestrator = Orchestrator(agent_config)
        result = await orchestrator.run_goal("Check in with Alice about goals")
    """

    def __init__(
        self,
        agent_config: AgentConfig,
        task_queue: Optional[TaskQueue] = None,
        conversation_manager: Optional[ConversationManager] = None,
        planner: Optional[Planner] = None
    ):
        """
        Initialize the orchestrator.

        Args:
            agent_config: Configuration for the agent executing goals
            task_queue: TaskQueue instance (creates default if None)
            conversation_manager: ConversationManager instance (creates default if None)
            planner: Planner instance (creates default if None)
        """
        self.agent_config = agent_config
        self.task_queue = task_queue or TaskQueue()
        self.conversation_manager = conversation_manager or ConversationManager()
        self.planner = planner or Planner(agent_config=agent_config)

        # Register callback for conversation completions
        self.conversation_manager.register_end_callback(self._on_conversation_end)

        # Track active goal
        self._active_goal_id: Optional[str] = None
        self._conversation_completion_event = asyncio.Event()

        logger.info(f"Orchestrator initialized for agent: {agent_config.name}")

    def _print_status(self, message: str):
        """Log status message."""
        logger.info(message)

    def _print_task_list(self, tasks: List[Task]):
        """Log the task list."""
        lines = ["\n📋 TASK LIST:", "-" * 50]
        for i, task in enumerate(tasks, 1):
            status_icon = {
                TaskStatus.PENDING: "⏳",
                TaskStatus.BLOCKED: "🔒",
                TaskStatus.RUNNING: "🔄",
                TaskStatus.COMPLETED: "✅",
                TaskStatus.FAILED: "❌"
            }.get(task.status, "❓")

            type_label = {
                TaskType.CONVERSATION: "💬 conversation",
                TaskType.TOOL_CALL: "🔧 tool_call",
                TaskType.SYNTHESIS: "📝 synthesis",
                TaskType.SUB_AGENT: "🤖 sub_agent"
            }.get(task.type, task.type.value)

            deps = ""
            if task.depends_on:
                deps = f" (blocked by task {', '.join(str(tasks.index(self.task_queue.get_task(d))+1) if self.task_queue.get_task(d) in tasks else d[:8] for d in task.depends_on)})"

            lines.append(f"  {i}. {status_icon} [{type_label}] {task.description}{deps}")
        lines.append("-" * 50)
        logger.info("\n".join(lines))

    def _update_task_display(self, task: Task, status: str):
        """Log task status update."""
        status_icon = {
            "starting": "🔄",
            "completed": "✅",
            "failed": "❌",
            "waiting": "⏳"
        }.get(status, "•")
        msg = f"{status_icon} Task: {task.description}"
        if status == "starting":
            msg += f" | Type: {task.type.value}"
        elif status == "completed" and task.result:
            if isinstance(task.result, dict) and task.result.get('message_count'):
                msg += f" | Conversation ended with {task.result['message_count']} messages"
            else:
                preview = str(task.result)[:100]
                msg += f" | Result: {preview}..."
        logger.info(msg)

    async def run_goal(
        self,
        goal: str,
        metadata: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 3600  # 1 hour default
    ) -> Dict[str, Any]:
        """
        Execute a goal through decomposition and task execution.

        This is the main entry point for the orchestrator.

        Args:
            goal: The goal description (e.g., "Check in with team and summarize")
            metadata: Additional metadata (e.g., calendar event info)
            timeout_seconds: Maximum time to wait for goal completion

        Returns:
            Dict with:
            - success: bool
            - goal_id: str
            - tasks_completed: int
            - tasks_failed: int
            - results: Dict mapping task IDs to results
            - error: Optional error message
        """
        start_time = datetime.now(timezone.utc)
        metadata = metadata or {}

        # Cancel stale goals and conversations from previous runs of this agent
        await self._cancel_stale_runs()

        # Create the goal first to get the ID
        goal_obj = self.task_queue.create_goal(
            agent_id=self.agent_config.agent_id or self.agent_config.name,
            description=goal,
            metadata=metadata
        )
        goal_id = goal_obj.id

        logger.info(f"[goal:{goal_id[:8]}] Starting goal: {goal[:100]}...")
        self._print_status(f"\n{'='*60}")
        self._print_status(f"GOAL: {goal}")
        self._print_status(f"{'='*60}\n")

        try:
            # Goal already created above to get the ID
            self._active_goal_id = goal_id

            # Ensure team data is fresh before planning
            await self._ensure_team_synced()

            # Decompose into tasks
            self._print_status("Decomposing goal into tasks...")
            tasks = await self.planner.decompose(
                goal=goal,
                task_queue=self.task_queue,
                goal_id=goal_obj.id,
                context=metadata
            )

            if not tasks:
                logger.warning(f"[goal:{goal_id[:8]}] No tasks created for goal")
                self._print_status("ERROR: Failed to decompose goal into tasks")
                return {
                    'success': False,
                    'goal_id': goal_id,
                    'tasks_completed': 0,
                    'tasks_failed': 0,
                    'results': {},
                    'error': 'Failed to decompose goal into tasks'
                }

            logger.info(f"[goal:{goal_id[:8]}] Created {len(tasks)} task(s)")
            self._print_task_list(tasks)

            # Execute the main loop
            result = await self._execute_loop(
                goal_id=goal_obj.id,
                timeout_seconds=timeout_seconds
            )

            # Mark goal complete
            if result['success']:
                self.task_queue.complete_goal(goal_obj.id, "completed")
            else:
                self.task_queue.complete_goal(goal_obj.id, "failed")

            return result

        except Exception as e:
            logger.error(f"Goal execution failed: {e}", exc_info=True)
            return {
                'success': False,
                'goal_id': self._active_goal_id,
                'tasks_completed': 0,
                'tasks_failed': 0,
                'results': {},
                'error': str(e)
            }
        finally:
            self._active_goal_id = None

    async def _execute_loop(
        self,
        goal_id: str,
        timeout_seconds: int
    ) -> Dict[str, Any]:
        """
        Main execution loop for processing tasks.

        Handles:
        - Running ready tasks
        - Waiting for async tasks (conversations)
        - Re-planning on failures
        """
        start_time = datetime.now(timezone.utc)
        tasks_completed = 0
        tasks_failed = 0

        while not self.task_queue.all_complete(goal_id):
            # Check timeout
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            if elapsed > timeout_seconds:
                logger.warning(f"[goal:{goal_id[:8]}] Goal timed out after {elapsed:.0f}s")
                break

            # Get tasks ready to run
            ready_tasks = self.task_queue.get_ready_tasks(goal_id)

            if ready_tasks:
                # Execute ready tasks (could be parallel in future)
                for task in ready_tasks:
                    logger.info(f"[goal:{goal_id[:8]}] [task:{task.id[:8]}] Executing: {task.description}")
                    self._update_task_display(task, "starting")
                    try:
                        await self._execute_task(task)
                    except Exception as e:
                        logger.error(f"[goal:{goal_id[:8]}] [task:{task.id[:8]}] Task failed: {e}", exc_info=True)
                        self.task_queue.mark_failed(task.id, str(e))
                        self._update_task_display(task, "failed")
                        logger.error(f"   Error: {e}")
                        tasks_failed += 1

            # If conversations are running, wait for completion events
            if self.task_queue.has_running_async(goal_id):
                logger.info(f"[goal:{goal_id[:8]}] 💬 Conversation in progress...")
                try:
                    # Wait for completion - polls database every 5 seconds
                    await asyncio.wait_for(
                        self._wait_for_async_completion(goal_id),
                        timeout=timeout_seconds - elapsed
                    )
                except asyncio.TimeoutError:
                    logger.warning("⚠️  Goal timed out while waiting for conversation")
                    break
            elif not ready_tasks:
                # No ready tasks and no async tasks running
                # Either we're blocked or something is wrong
                running = self.task_queue.get_running_tasks(goal_id)
                if not running:
                    # All tasks are either completed, failed, or blocked
                    break
                # Otherwise, some tasks are still running (sync)
                await asyncio.sleep(0.5)

        # Calculate final stats
        all_tasks = self.task_queue.get_tasks_for_goal(goal_id)
        tasks_completed = sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED)
        tasks_failed = sum(1 for t in all_tasks if t.status == TaskStatus.FAILED)

        # Get results from completed tasks
        results = self.task_queue.get_completed_results(goal_id)

        # Check if goal achieved
        success = tasks_failed == 0 and tasks_completed == len(all_tasks)

        # Handle failures (could trigger replanning)
        if tasks_failed > 0:
            failures = self.task_queue.get_failures(goal_id)
            logger.warning(f"{tasks_failed} task(s) failed")

            # Optionally replan
            # new_tasks = await self.planner.replan(...)

        return {
            'success': success,
            'goal_id': goal_id,
            'tasks_completed': tasks_completed,
            'tasks_failed': tasks_failed,
            'results': results,
            'error': None if success else f'{tasks_failed} task(s) failed'
        }

    async def _execute_task(self, task: Task):
        """
        Execute a single task based on its type.

        Args:
            task: The task to execute
        """
        goal_id = task.goal_id
        logger.info(f"[goal:{goal_id[:8]}] [task:{task.id[:8]}] Executing {task.type.value}: {task.description}")

        self.task_queue.mark_running(task.id)

        if task.type == TaskType.CONVERSATION:
            await self._execute_conversation_task(task)

        elif task.type == TaskType.TOOL_CALL:
            await self._execute_tool_call_task(task)

        elif task.type == TaskType.SYNTHESIS:
            await self._execute_synthesis_task(task)

        elif task.type == TaskType.SUB_AGENT:
            await self._execute_sub_agent_task(task)

        else:
            raise ValueError(f"Unknown task type: {task.type}")

    async def _execute_conversation_task(self, task: Task):
        """
        Execute a conversation task (async, waits for human).

        The task completes when the conversation ends (callback fires).
        """
        config = task.config

        # Get platform from config or agent config
        platform = config.get('platform', self.agent_config.messaging_platform)

        # Determine channel: either DM with user or use configured channel
        channel_id = config.get('channel', self.agent_config.messaging_channel_id)
        target_user_id = None
        target_user_name = config.get('user', 'there')

        # If we have the user's platform ID, we can DM them directly
        if platform == 'slack' and config.get('slack_user_id'):
            target_user_id = config.get('slack_user_id')
            # Open a DM channel with the user
            dm_channel = await self._open_slack_dm(target_user_id)
            if dm_channel:
                channel_id = dm_channel
                logger.info(f"Using DM channel {channel_id} for user {target_user_name} ({target_user_id})")
            else:
                logger.error(f"Could not open DM with {target_user_name}")
                raise ValueError(f"Failed to open Slack DM with user {target_user_name}")

        elif platform == 'discord' and config.get('discord_user_id'):
            target_user_id = config.get('discord_user_id')
            # Open a DM channel with the user
            dm_channel = await self._open_discord_dm(target_user_id)
            if dm_channel:
                channel_id = dm_channel
                logger.info(f"Using DM channel {channel_id} for user {target_user_name} ({target_user_id})")
            else:
                logger.error(f"Could not open DM with {target_user_name}")
                raise ValueError(f"Failed to open Discord DM with user {target_user_name}")

        # Resolve channel name (e.g. "#engineering") → channel ID
        if channel_id and channel_id.startswith('#'):
            from promaia.config.team import get_team_manager
            team = get_team_manager()
            found = team.find_channel(channel_id)
            if found:
                channel_id = found.id
                platform = platform or 'slack'
                logger.info(f"Resolved channel '{config.get('channel')}' to {channel_id}")
            else:
                raise ValueError(
                    f"Channel '{channel_id}' not found in team data. "
                    "Run `maia team sync` to update channel list."
                )

        if not platform:
            raise ValueError("Conversation task requires platform (set messaging_platform on agent or specify user with platform ID)")

        if not channel_id:
            raise ValueError(f"Conversation task requires channel (no channel configured and no user ID to DM)")

        # Register platform if needed
        await self._ensure_platform_registered(platform)

        # Generate a proper conversational opener using the agent
        initial_message = await self._generate_conversation_opener(task)

        # Start conversation
        conversation = await self.conversation_manager.start_conversation(
            agent_id=self.agent_config.agent_id or self.agent_config.name,
            platform=platform,
            channel_id=channel_id,
            initial_message=initial_message,
            user_id=target_user_id,  # Track who we're talking to
            timeout_minutes=config.get('timeout_minutes', 15),
            max_turns=config.get('max_turns', 20),
            orchestrator_task_id=task.id
        )

        # Update task with async handle
        self.task_queue.mark_running(task.id, async_handle=conversation.conversation_id)

        logger.info(f"Started conversation {conversation.conversation_id} for task {task.id[:8]}")

    async def _generate_conversation_opener(self, task: Task) -> str:
        """
        Generate a natural conversational opening message.

        Uses the agent to craft a friendly, contextual opener based on:
        - The conversation goal/purpose
        - The agent's personality
        - Any context about the person we're talking to

        Args:
            task: The conversation task with description and config

        Returns:
            A natural opening message
        """
        from promaia.nlq.nl_orchestrator import PromaiLLMAdapter

        config = task.config
        user_name = config.get('user', 'there')
        # Prefer config.topic (detailed instructions) over the short task
        # description, which is kept brief for feed display purposes.
        conversation_topic = config.get('topic') or task.description

        # Build prompt for generating opener.
        # NOTE: Use the *topic*, NOT the full goal.  The goal may contain
        # instructions for other tasks (e.g. "log to journal") which the
        # model would incorrectly include in the message.
        prompt = f"""You are {self.agent_config.name}, starting a conversation.

Your task: {conversation_topic}
Person you're talking to: {user_name}

Write a brief, friendly, natural opening message to start this conversation.
- Be warm and personable, like messaging a colleague
- Reference the purpose naturally (don't be robotic)
- Keep it to 1-3 sentences
- Don't include greetings like "Hey [name]!" if you don't know their name for sure
- End with an open question to invite response

Write ONLY the message text, nothing else. Do not include journal entries, summaries, or meta-commentary."""

        try:
            model = PromaiLLMAdapter(client_type="auto")
            response = model.invoke([{'role': 'user', 'content': prompt}])

            if hasattr(response, 'content'):
                opener = response.content.strip()
            else:
                opener = str(response).strip()

            # Clean up any quotes the model might have added
            if opener.startswith('"') and opener.endswith('"'):
                opener = opener[1:-1]

            logger.info(f"Generated conversation opener: {opener[:100]}...")
            return opener

        except Exception as e:
            logger.warning(f"Failed to generate opener, using fallback: {e}")
            # Fallback to a simple but friendly message
            return f"Hi! I wanted to chat with you about {conversation_topic.lower()}. Do you have a few minutes?"

    async def _execute_tool_call_task(self, task: Task):
        """
        Execute a tool call task (sync).

        Uses the agent's executor to run MCP tools or other operations.
        """
        from promaia.agents.executor import AgentExecutor

        config = task.config

        # Create executor for tool calls
        executor = AgentExecutor(self.agent_config)

        # Build a request that describes what to do
        tool_request = config.get('tool_request', task.description)

        # Get context from dependent tasks — if any dependency was a
        # conversation, include its transcript directly in the request
        # so the executor can see it (metadata alone isn't surfaced).
        context_data = {}
        for dep_id in task.depends_on:
            dep_task = self.task_queue.get_task(dep_id)
            if dep_task and dep_task.result:
                context_data[f'task_{dep_id[:8]}_result'] = dep_task.result

                # Append conversation transcript to request
                if dep_task.type == TaskType.CONVERSATION and isinstance(dep_task.result, dict):
                    transcript = dep_task.result.get('transcript', [])
                    if transcript:
                        tool_request += "\n\nCONVERSATION TRANSCRIPT:\n"
                        for msg in transcript:
                            role = msg.get('role', 'unknown')
                            content = msg.get('content', '')
                            tool_request += f"[{role}]: {content}\n"

        # Execute
        result = await executor.execute(
            run_request=tool_request,
            run_metadata={'context': context_data, 'task_id': task.id}
        )

        if result.get('success'):
            self.task_queue.mark_completed(task.id, result.get('output'))
        else:
            self.task_queue.mark_failed(task.id, result.get('error', 'Unknown error'))

    async def _execute_synthesis_task(self, task: Task):
        """
        Execute a synthesis task.

        Uses the agent to analyze conversation results and write to Notion journal.
        """
        from promaia.agents.executor import AgentExecutor

        config = task.config

        # Gather results from dependencies
        dependency_results = []
        conversation_transcript = None

        for dep_id in task.depends_on:
            dep_task = self.task_queue.get_task(dep_id)
            if dep_task:
                dependency_results.append({
                    'task_id': dep_id,
                    'type': dep_task.type.value,
                    'description': dep_task.description,
                    'result': dep_task.result,
                    'status': dep_task.status.value
                })
                # Extract conversation transcript if available
                if dep_task.type == TaskType.CONVERSATION and dep_task.result:
                    conversation_transcript = dep_task.result.get('transcript', [])

        # Format transcript for the prompt
        transcript_text = ""
        if conversation_transcript:
            transcript_text = "\n\nCONVERSATION TRANSCRIPT:\n"
            for msg in conversation_transcript:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                transcript_text += f"[{role}]: {content}\n"

        # Get the target journal database ID from task config
        journal_db_id = config.get('database')
        if not journal_db_id:
            # Fallback to agent's journal database
            journal_db_id = getattr(self.agent_config, 'journal_db_id', None)

        database_instruction = ""
        if journal_db_id:
            database_instruction = f"\nTarget database ID: {journal_db_id}"
        else:
            logger.warning("No journal database specified for synthesis task")

        # Build synthesis request that instructs the agent to write to journal
        synthesis_request = f"""
You just completed a conversation. Now you need to write a journal entry summarizing it.

TASK: {task.description}
{transcript_text}

INSTRUCTIONS:
1. Analyze the conversation above
2. Extract key takeaways, action items, and important points
3. Write a journal entry as a markdown file to your local journal directory
4. The journal entry should include:
   - A clear title summarizing the conversation topic
   - Key discussion points
   - Any action items or follow-ups
   - Notable insights or decisions made

Use the Write tool to create the journal entry locally (NOT Notion MCP tools). Follow the LOCAL-FIRST instructions in your system prompt for the correct directory path and filename format.
"""

        logger.info("Writing to journal...")

        # Execute synthesis with agent - make sure MCP tools are enabled
        executor = AgentExecutor(self.agent_config)

        result = await executor.execute(
            run_request=synthesis_request,
            run_metadata={
                'task_id': task.id,
                'dependencies': task.depends_on,
                'write_to_journal': True
            }
        )

        if result.get('success'):
            self.task_queue.mark_completed(task.id, result.get('output'))
            self._update_task_display(task, "completed")

            # Trigger Notion sync for the journal database
            await self._push_journal_to_notion()
        else:
            self.task_queue.mark_failed(task.id, result.get('error', 'Synthesis failed'))
            self._update_task_display(task, "failed")

    async def _push_journal_to_notion(self):
        """Push the most recent local journal entry to Notion.

        Flow: local file (already written) → register in DB → push to Notion.
        """
        from promaia.agents.notion_journal import write_journal_entry
        from promaia.storage.hybrid_storage import get_hybrid_registry

        agent_id = getattr(self.agent_config, 'agent_id', '')
        workspace = getattr(self.agent_config, 'workspace', '')
        journal_db_id = getattr(self.agent_config, 'journal_db_id', None)
        db_nickname = f"{agent_id.replace('-', '_')}_journal"

        if not agent_id or not workspace or not journal_db_id:
            logger.debug("Skipping journal push: missing agent_id, workspace, or journal_db_id")
            return

        # 1. Find the most recently written local journal file
        from promaia.utils.env_writer import get_data_subdir
        journal_dir = get_data_subdir() / "md" / "notion" / workspace / db_nickname
        if not journal_dir.exists():
            logger.debug(f"Journal directory does not exist: {journal_dir}")
            return

        md_files = sorted(journal_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not md_files:
            logger.debug("No journal files found to push")
            return

        latest_file = md_files[0]
        content = latest_file.read_text(encoding='utf-8')
        if not content.strip():
            logger.debug(f"Latest journal file is empty: {latest_file.name}")
            return

        title = latest_file.stem  # filename without .md

        # 2. Push to Notion
        logger.info(f"Pushing journal entry to Notion: {latest_file.name}")
        try:
            page_id = await write_journal_entry(
                agent_id=agent_id,
                workspace=workspace,
                entry_type="Note",
                content=content,
            )
        except Exception as e:
            logger.error(f"Failed to push journal to Notion: {e}")
            return

        if not page_id:
            logger.warning("Push succeeded but no page_id returned, skipping registration")
            return

        # 3. Register in hybrid registry so sync knows about this file
        try:
            registry = get_hybrid_registry()
            now = datetime.now(timezone.utc).isoformat()
            registry.add_content({
                'page_id': page_id,
                'workspace': workspace,
                'database_id': journal_db_id,
                'database_name': db_nickname,
                'file_path': str(latest_file),
                'title': title,
                'created_time': now,
                'last_edited_time': now,
                'synced_time': now,
                'file_size': latest_file.stat().st_size,
            })
            logger.info(f"Registered journal entry in local DB: {title}")
        except Exception as e:
            logger.error(f"Failed to register journal entry in DB: {e}")

    async def _execute_sub_agent_task(self, task: Task):
        """
        Execute a sub-agent task (delegates to another agent).
        """
        from promaia.agents.agent_config import get_agent
        from promaia.agents.executor import AgentExecutor

        config = task.config
        sub_agent_name = config.get('agent_name')

        if not sub_agent_name:
            raise ValueError("Sub-agent task requires agent_name in config")

        # Load sub-agent config
        sub_agent = get_agent(sub_agent_name)
        if not sub_agent:
            raise ValueError(f"Sub-agent '{sub_agent_name}' not found")

        # Execute sub-agent
        executor = AgentExecutor(sub_agent)

        result = await executor.execute(
            run_request=config.get('request', task.description),
            run_metadata={'parent_task_id': task.id}
        )

        if result.get('success'):
            self.task_queue.mark_completed(task.id, result.get('output'))
        else:
            self.task_queue.mark_failed(task.id, result.get('error', 'Sub-agent failed'))

    async def _on_conversation_end(
        self,
        conversation_id: str,
        transcript: List[Dict[str, Any]],
        reason: str
    ):
        """
        Callback when a conversation ends.

        This is called by ConversationManager when:
        - User says goodbye/thanks
        - Conversation times out
        - Max turns reached
        - User types /done

        Args:
            conversation_id: ID of the completed conversation
            transcript: Full conversation transcript
            reason: Why conversation ended
        """
        logger.info(f"Conversation {conversation_id} ended: {reason}")

        # Find the task associated with this conversation
        task = self.task_queue.get_task_by_async_handle(conversation_id)

        if task:
            # Mark task completed with transcript as result
            result = {
                'conversation_id': conversation_id,
                'transcript': transcript,
                'completion_reason': reason,
                'message_count': len(transcript)
            }

            self.task_queue.mark_completed(task.id, result)
            logger.info(f"[goal:{task.goal_id[:8]}] [task:{task.id[:8]}] ✅ Completed with conversation transcript")

            # Signal that an async task completed
            self._conversation_completion_event.set()
        else:
            logger.warning(f"No task found for conversation {conversation_id}")

    async def _wait_for_async_completion(self, goal_id: str):
        """
        Wait for async tasks (conversations) to complete.

        Since conversations are handled by a separate Slack bot process,
        we poll the database for completion status rather than relying
        on in-memory callbacks.
        """
        poll_interval = 5  # seconds
        polls = 0

        while True:
            # Check all running async tasks
            async_tasks = self.task_queue.get_running_async_tasks(goal_id)

            if not async_tasks:
                # No more async tasks running
                return

            for task in async_tasks:
                # Check conversation status in database
                completed = await self._check_conversation_completed(task.async_handle)
                if completed:
                    # Get the transcript and mark task complete
                    transcript, reason = await self._get_conversation_result(task.async_handle)
                    result = {
                        'conversation_id': task.async_handle,
                        'transcript': transcript,
                        'completion_reason': reason,
                        'message_count': len(transcript)
                    }
                    self.task_queue.mark_completed(task.id, result)
                    self._update_task_display(task, "completed")
                    logger.info(f"Conversation task {task.id[:8]} completed via polling ({reason})")
                    return  # Exit to let main loop handle next tasks

            # Show periodic status
            polls += 1
            if polls % 6 == 0:  # Every 30 seconds
                logger.info("Still waiting... (conversation in progress)")

            # Wait before polling again
            await asyncio.sleep(poll_interval)

    async def _check_conversation_completed(self, conversation_id: str) -> bool:
        """Check if a conversation has completed by querying the database."""
        import sqlite3
        from promaia.utils.env_writer import get_conversations_db_path

        db_path = get_conversations_db_path()

        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT status FROM conversations WHERE id = ?",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                if row:
                    status = row[0]
                    return status in ('completed', 'timeout', 'ended_by_user')
                return False
        except Exception as e:
            logger.error(f"Error checking conversation status: {e}")
            return False

    async def _get_conversation_result(self, conversation_id: str) -> tuple:
        """Get conversation transcript and completion reason from database."""
        import sqlite3
        import json
        from promaia.utils.env_writer import get_conversations_db_path

        db_path = get_conversations_db_path()

        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT messages, completion_reason FROM conversations WHERE id = ?",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                if row:
                    messages = json.loads(row['messages']) if row['messages'] else []
                    reason = row['completion_reason'] or 'unknown'
                    return messages, reason
                return [], 'not_found'
        except Exception as e:
            logger.error(f"Error getting conversation result: {e}")
            return [], 'error'

    async def _cancel_stale_runs(self):
        """Cancel active goals and conversations from previous runs of this agent.

        When `maia agent run` is invoked multiple times, each run spawns a
        new process with its own Orchestrator.  The earlier runs' goals and
        conversations will never complete properly, so we cancel them to
        prevent zombie processes from lingering for an hour.
        """
        import sqlite3

        agent_id = self.agent_config.agent_id or self.agent_config.name
        now = datetime.now(timezone.utc).isoformat()

        # Cancel stale goals and their tasks in the task queue
        try:
            active_goals = self.task_queue.get_active_goals()
            stale_goals = [g for g in active_goals if g.agent_id == agent_id]
            for goal in stale_goals:
                self.task_queue.cancel_goal(goal.id)
            if stale_goals:
                logger.info(f"Cancelled {len(stale_goals)} stale goal(s) from previous runs")
        except Exception as e:
            logger.warning(f"Could not cancel stale goals: {e}")

        # Cancel stale conversations
        try:
            db_path = self.conversation_manager.db_path
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE conversations SET status = 'completed', "
                    "completion_reason = 'superseded', "
                    "completed_at = ? "
                    "WHERE agent_id = ? AND status = 'active'",
                    (now, agent_id)
                )
                cancelled = cursor.rowcount
                conn.commit()

                if cancelled > 0:
                    logger.info(f"Cancelled {cancelled} stale conversation(s) from previous runs")
        except Exception as e:
            logger.warning(f"Could not cancel stale conversations: {e}")

    async def _ensure_team_synced(self):
        """Auto-sync team data from Slack if stale or empty. Best-effort."""
        import os
        try:
            from promaia.config.team import get_team_manager

            team = get_team_manager()
            if not team.is_stale():
                return

            slack_token = os.environ.get('SLACK_BOT_TOKEN')
            if not slack_token:
                logger.debug("Skipping auto-sync: SLACK_BOT_TOKEN not set")
                return

            logger.info("Team data is stale — auto-syncing from Slack...")
            await team.sync_from_slack(slack_token)
            await team.sync_channels_from_slack(slack_token)
            logger.info(f"Auto-sync complete: {len(team.members)} members, {len(team.channels)} channels")
        except Exception as e:
            logger.warning(f"Auto-sync failed (non-blocking): {e}")

    async def _ensure_platform_registered(self, platform: str):
        """Ensure the messaging platform is registered."""
        import os

        if platform in self.conversation_manager.platforms:
            return

        if platform == 'slack':
            from promaia.agents.messaging.slack_platform import SlackPlatform

            bot_token = os.environ.get('SLACK_BOT_TOKEN')
            if not bot_token:
                raise ValueError("SLACK_BOT_TOKEN not found in environment")

            platform_impl = SlackPlatform(bot_token=bot_token)
            self.conversation_manager.register_platform('slack', platform_impl)

        elif platform == 'discord':
            from promaia.agents.messaging.discord_platform import DiscordPlatform

            bot_token = os.environ.get('DISCORD_BOT_TOKEN')
            if not bot_token:
                raise ValueError("DISCORD_BOT_TOKEN not found in environment")

            platform_impl = DiscordPlatform(bot_token=bot_token)
            self.conversation_manager.register_platform('discord', platform_impl)

        else:
            raise ValueError(f"Unknown platform: {platform}")

    async def _open_slack_dm(self, user_id: str) -> Optional[str]:
        """
        Open a DM channel with a Slack user.

        Args:
            user_id: Slack user ID (e.g., "U01234567")

        Returns:
            Channel ID for the DM, or None if failed
        """
        import os

        try:
            from slack_sdk.web.async_client import AsyncWebClient

            bot_token = os.environ.get('SLACK_BOT_TOKEN')
            if not bot_token:
                logger.warning("SLACK_BOT_TOKEN not set")
                return None

            client = AsyncWebClient(token=bot_token)

            # Open a DM channel with the user
            result = await client.conversations_open(users=[user_id])

            if result['ok']:
                channel_id = result['channel']['id']
                logger.info(f"Opened DM channel {channel_id} with user {user_id}")
                return channel_id
            else:
                logger.error(f"Failed to open DM: {result.get('error')}")
                return None

        except ImportError:
            logger.warning("slack_sdk not installed, cannot open DM")
            return None
        except Exception as e:
            logger.error(f"Error opening Slack DM: {e}")
            return None

    async def _open_discord_dm(self, user_id: str) -> Optional[str]:
        """
        Open a DM channel with a Discord user.

        Args:
            user_id: Discord user ID

        Returns:
            Channel ID for the DM, or None if failed
        """
        import os

        try:
            import aiohttp

            bot_token = os.environ.get('DISCORD_BOT_TOKEN')
            if not bot_token:
                logger.warning("DISCORD_BOT_TOKEN not set")
                return None

            headers = {
                'Authorization': f'Bot {bot_token}',
                'Content-Type': 'application/json'
            }

            async with aiohttp.ClientSession() as session:
                # Create DM channel
                url = 'https://discord.com/api/v10/users/@me/channels'
                payload = {'recipient_id': user_id}

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        channel_id = data['id']
                        logger.info(f"Opened DM channel {channel_id} with user {user_id}")
                        return channel_id
                    else:
                        error = await resp.text()
                        logger.error(f"Failed to open Discord DM: {resp.status} - {error}")
                        return None

        except ImportError:
            logger.warning("aiohttp not installed, cannot open DM")
            return None
        except Exception as e:
            logger.error(f"Error opening Discord DM: {e}")
            return None

    def _format_dependency_results(
        self,
        results: List[Dict[str, Any]]
    ) -> str:
        """Format dependency results for synthesis prompt."""
        if not results:
            return "No previous results."

        parts = []
        for r in results:
            parts.append(f"""
--- Task: {r['description']} ---
Type: {r['type']}
Status: {r['status']}
Result:
{r.get('result', 'No result')}
""")

        return "\n".join(parts)


# Convenience function for simple usage
async def run_goal(
    goal: str,
    agent_config: AgentConfig,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convenience function to run a goal with default orchestrator.

    Args:
        goal: Goal description
        agent_config: Agent configuration
        metadata: Optional metadata

    Returns:
        Execution result dictionary
    """
    orchestrator = Orchestrator(agent_config)
    return await orchestrator.run_goal(goal, metadata)
