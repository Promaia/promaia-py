"""
AI-powered Planner for goal decomposition.

Takes a high-level goal and decomposes it into executable tasks with dependencies.
Uses Claude to analyze the goal and create an appropriate task plan.
"""

import json
import logging
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from promaia.agents.task_queue import Task, TaskType, TaskStatus, TaskQueue
from promaia.agents.agent_config import AgentConfig
from promaia.config.team import get_team_manager, TeamMember

logger = logging.getLogger(__name__)


# Common goal patterns for fast decomposition without AI
# NOTE: Patterns are checked in order, first match wins
GOAL_PATTERNS = {
    # Pattern: "check in with X" (always adds journal task)
    r'check.?in.+(?:with|on)\s+(\w+)': [
        {
            'type': TaskType.CONVERSATION,
            'description_template': 'Have check-in conversation with {user}',
            'config_keys': ['user', 'channel']
        },
        {
            'type': TaskType.SYNTHESIS,
            'description_template': 'Write conversation summary and takeaways to journal',
            'depends_on': [0],
            'config_keys': ['database']
        }
    ],

    # Pattern: "have/start conversation with X" (always adds journal task)
    r'(?:have|start).+conversation.+(?:with|about)\s+(\w+)': [
        {
            'type': TaskType.CONVERSATION,
            'description_template': 'Have conversation with {user}',
            'config_keys': ['topic', 'user', 'channel']
        },
        {
            'type': TaskType.SYNTHESIS,
            'description_template': 'Write conversation summary and takeaways to journal',
            'depends_on': [0],
            'config_keys': ['database']
        }
    ],

    # Pattern: "review X then write/summarize"
    r'review\s+(.+?)(?:then|and)\s+(?:write|summarize|create)': [
        {
            'type': TaskType.TOOL_CALL,
            'description_template': 'Review {subject}',
            'config_keys': ['subject']
        },
        {
            'type': TaskType.SYNTHESIS,
            'description_template': 'Create summary of review',
            'depends_on': [0]
        }
    ],

    # Pattern: "gather X from multiple sources and synthesize"
    r'gather.+from.+(?:sources?|people|team).+(?:synthesize|combine|summarize)': [
        {
            'type': TaskType.CONVERSATION,
            'description_template': 'Gather information via conversation',
            'config_keys': ['user', 'channel']
        },
        {
            'type': TaskType.SYNTHESIS,
            'description_template': 'Synthesize gathered information',
            'depends_on': [0]
        }
    ],

    # Pattern: "talk/chat/message X" (simple conversation + journal)
    r'(?:talk|chat|speak|message).+(?:to|with)\s+(\w+)': [
        {
            'type': TaskType.CONVERSATION,
            'description_template': 'Have conversation with {user}',
            'config_keys': ['user', 'channel']
        },
        {
            'type': TaskType.SYNTHESIS,
            'description_template': 'Write conversation summary to journal',
            'depends_on': [0]
        }
    ],

    # Simple journal/note
    r'(?:write|record|note|journal).+(?:about|regarding)\s+(.+)': [
        {
            'type': TaskType.TOOL_CALL,
            'description_template': 'Write note about {topic}',
            'config_keys': ['topic', 'database']
        }
    ],
}


@dataclass
class PlannerConfig:
    """Configuration for the planner."""
    use_ai: bool = True  # Whether to use AI for complex goals
    ai_model: str = "claude-sonnet-4-6"
    max_tasks: int = 10  # Maximum tasks to create
    default_conversation_timeout: int = 15  # minutes
    default_conversation_max_turns: int = 20


class Planner:
    """
    AI-powered planner for goal decomposition.

    Takes high-level goals like "Check in with Alice and summarize results"
    and decomposes them into executable tasks with proper dependencies.
    """

    def __init__(
        self,
        config: Optional[PlannerConfig] = None,
        agent_config: Optional[AgentConfig] = None
    ):
        """
        Initialize the planner.

        Args:
            config: Planner configuration
            agent_config: Agent configuration (for context about available tools)
        """
        self.config = config or PlannerConfig()
        self.agent_config = agent_config

    async def decompose(
        self,
        goal: str,
        task_queue: TaskQueue,
        goal_id: str,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Task]:
        """
        Decompose a goal into executable tasks.

        Args:
            goal: The goal description to decompose
            task_queue: TaskQueue to add tasks to
            goal_id: ID of the parent goal
            context: Additional context (e.g., calendar event info)

        Returns:
            List of created Task objects
        """
        logger.info(f"[goal:{goal_id[:8]}] 🧠 Planning: {goal[:100]}...")

        # Try pattern matching first (fast path)
        tasks = self._decompose_with_patterns(goal, task_queue, goal_id, context)
        if tasks:
            task_summary = ", ".join(t.description[:60] for t in tasks)
            logger.info(f"[goal:{goal_id[:8]}] 📋 Planned {len(tasks)} task(s) via pattern match: {task_summary}")
            return tasks

        # Fall back to AI decomposition
        if self.config.use_ai:
            logger.info(f"[goal:{goal_id[:8]}] 🧠 No pattern match — using AI to decompose goal...")
            tasks = await self._decompose_with_ai(goal, task_queue, goal_id, context)
            if tasks:
                task_summary = ", ".join(t.description[:60] for t in tasks)
                logger.info(f"[goal:{goal_id[:8]}] 📋 Planned {len(tasks)} task(s) via AI: {task_summary}")
                return tasks

        # Default: single synthesis task
        logger.warning(f"[goal:{goal_id[:8]}] Could not decompose goal, creating single task")
        task = task_queue.add_task(
            goal_id=goal_id,
            task_type=TaskType.SYNTHESIS,
            description=f"Execute goal: {goal}",
            config={'goal': goal, 'context': context}
        )
        return [task]

    def _decompose_with_patterns(
        self,
        goal: str,
        task_queue: TaskQueue,
        goal_id: str,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Task]:
        """
        Try to decompose using predefined patterns.

        This is faster and more predictable than AI for common goals.

        Returns:
            List of tasks if pattern matched, empty list otherwise
        """
        goal_lower = goal.lower()
        context = context or {}

        for pattern, task_templates in GOAL_PATTERNS.items():
            match = re.search(pattern, goal_lower)
            if match:
                # Extract matched groups
                groups = match.groups()

                # If we extracted a user name, try to resolve it to platform IDs
                resolved_user = None
                if groups:
                    user_name = groups[0]
                    resolved_user = self._resolve_user(user_name)
                    if resolved_user:
                        logger.info(f"Resolved user '{user_name}' to team member: {resolved_user.name}")
                    else:
                        logger.warning(f"Could not resolve user '{user_name}' - not found in team list")

                # Create tasks from templates
                tasks = []
                task_id_map = {}  # Map template index to actual task ID

                for i, template in enumerate(task_templates):
                    # Build description
                    description = template['description_template']
                    # Use resolved name if available, otherwise original
                    display_name = resolved_user.name if resolved_user else (groups[0] if groups else 'unknown')
                    if '{user}' in description:
                        description = description.replace('{user}', display_name)
                    if groups and '{topic}' in description:
                        description = description.replace('{topic}', groups[0] if len(groups) > 0 else goal)
                    if groups and '{subject}' in description:
                        description = description.replace('{subject}', groups[0] if len(groups) > 0 else goal)

                    # Build config
                    task_config = dict(context)
                    if template.get('config_keys'):
                        for key in template['config_keys']:
                            if key == 'user' and groups:
                                task_config['user'] = display_name
                            elif key == 'topic' and groups:
                                task_config['topic'] = groups[0]
                            elif key == 'database':
                                # Auto-populate journal database for synthesis tasks
                                if self.agent_config:
                                    journal_db = self._find_journal_database()
                                    if journal_db:
                                        task_config['database'] = journal_db
                                        logger.info(f"Synthesis task will write to journal: {journal_db}")

                    # For conversation tasks, add user platform IDs and default config
                    if template['type'] == TaskType.CONVERSATION:
                        task_config.setdefault('timeout_minutes', self.config.default_conversation_timeout)
                        task_config.setdefault('max_turns', self.config.default_conversation_max_turns)
                        # Pass the full goal as the conversation topic so the
                        # opener generator knows what to talk about (the task
                        # description is kept short for display purposes).
                        task_config.setdefault('topic', goal)

                        # Add resolved platform IDs
                        if resolved_user:
                            # Determine platform from available user IDs (prefer Slack)
                            if resolved_user.slack_id:
                                task_config['platform'] = 'slack'
                                task_config['slack_user_id'] = resolved_user.slack_id
                                task_config['slack_username'] = resolved_user.slack_username
                                logger.info(f"Conversation task will use Slack DM with {resolved_user.name}")
                            elif resolved_user.discord_id:
                                task_config['platform'] = 'discord'
                                task_config['discord_user_id'] = resolved_user.discord_id
                                task_config['discord_username'] = resolved_user.discord_username
                                logger.info(f"Conversation task will use Discord DM with {resolved_user.name}")
                            else:
                                logger.warning(f"User {resolved_user.name} has no Slack or Discord ID - conversation will fail")

                            if resolved_user.timezone:
                                task_config['user_timezone'] = resolved_user.timezone

                    # Resolve dependencies (template indices to actual task IDs)
                    depends_on = []
                    if template.get('depends_on'):
                        for dep_idx in template['depends_on']:
                            if dep_idx in task_id_map:
                                depends_on.append(task_id_map[dep_idx])

                    # Create task
                    task = task_queue.add_task(
                        goal_id=goal_id,
                        task_type=template['type'],
                        description=description,
                        depends_on=depends_on,
                        config=task_config
                    )

                    tasks.append(task)
                    task_id_map[i] = task.id

                return tasks

        return []

    def _resolve_user(self, user_name: str) -> Optional[TeamMember]:
        """
        Resolve a user name to a TeamMember from the team list.

        Args:
            user_name: Name, alias, or username to search for

        Returns:
            TeamMember if found, None otherwise
        """
        try:
            team_manager = get_team_manager()
            return team_manager.find_member(user_name)
        except Exception as e:
            logger.warning(f"Error resolving user '{user_name}': {e}")
            return None

    def _find_journal_database(self) -> Optional[str]:
        """
        Find the agent's journal database ID from its configuration.

        Returns:
            Journal database ID if found, None otherwise
        """
        if not self.agent_config:
            return None

        # Check if agent has a dedicated journal database ID (from Notion agent setup)
        if hasattr(self.agent_config, 'journal_db_id') and self.agent_config.journal_db_id:
            logger.debug(f"Found journal database ID: {self.agent_config.journal_db_id}")
            return self.agent_config.journal_db_id

        # Fallback: look for a database named "journal" in the agent's databases list
        for db in self.agent_config.databases:
            db_name = db.split(':')[0]  # Get base name without qualifier
            if 'journal' in db_name.lower():
                logger.debug(f"Found journal database in databases list: {db}")
                return db

        logger.warning("No journal database found in agent config")
        return None

    def _get_team_context(self) -> str:
        """Get team roster summary for AI prompts."""
        try:
            team_manager = get_team_manager()
            platform = "slack"  # default
            if self.agent_config and hasattr(self.agent_config, 'messaging_platform'):
                platform = self.agent_config.messaging_platform or "slack"
            summary = team_manager.get_roster_summary(platform)
            return summary
        except Exception as e:
            logger.warning(f"Error getting team context: {e}")
            return ""

    def _resolve_conversation_config(self, config: Dict[str, Any]) -> None:
        """
        Resolve user/channel names in a conversation task config.

        Resolves user names to platform IDs and channel names to channel IDs,
        so the orchestrator can open DMs or post to channels.
        """
        # Set conversation defaults
        config.setdefault('timeout_minutes', self.config.default_conversation_timeout)
        config.setdefault('max_turns', self.config.default_conversation_max_turns)

        # Resolve user name → platform IDs
        user_name = config.get('user')
        if user_name:
            resolved = self._resolve_user(user_name)
            if resolved:
                logger.info(f"AI task: resolved user '{user_name}' to {resolved.name}")
                config['user'] = resolved.name
                if resolved.slack_id:
                    config['platform'] = 'slack'
                    config['slack_user_id'] = resolved.slack_id
                    config['slack_username'] = resolved.slack_username
                elif resolved.discord_id:
                    config['platform'] = 'discord'
                    config['discord_user_id'] = resolved.discord_id
                    config['discord_username'] = resolved.discord_username
                if resolved.timezone:
                    config['user_timezone'] = resolved.timezone
            else:
                logger.warning(f"AI task: could not resolve user '{user_name}' — not found in team list")

        # Resolve channel name → channel ID
        channel = config.get('channel')
        if channel and channel.startswith('#'):
            try:
                team_manager = get_team_manager()
                found = team_manager.find_channel(channel)
                if found:
                    config['channel'] = found.id
                    config.setdefault('platform', 'slack')
                    logger.info(f"AI task: resolved channel '{channel}' to {found.id}")
                else:
                    logger.warning(f"AI task: channel '{channel}' not found in team data")
            except Exception as e:
                logger.warning(f"Error resolving channel '{channel}': {e}")

    async def _decompose_with_ai(
        self,
        goal: str,
        task_queue: TaskQueue,
        goal_id: str,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Task]:
        """
        Use AI to decompose complex goals.

        This handles goals that don't match predefined patterns.
        """
        try:
            from promaia.nlq.nl_orchestrator import PromaiLLMAdapter

            model = PromaiLLMAdapter(client_type="auto")

            # Build prompt for decomposition
            prompt = self._build_decomposition_prompt(goal, context)

            # Call AI
            response = model.invoke([{'role': 'user', 'content': prompt}])

            # Parse response
            if hasattr(response, 'content'):
                response_text = response.content
            else:
                response_text = str(response)

            # Extract JSON from response
            tasks_data = self._parse_ai_response(response_text)

            if not tasks_data:
                return []

            # Create tasks from AI response
            tasks = []
            task_id_map = {}

            for i, task_data in enumerate(tasks_data[:self.config.max_tasks]):
                # Parse task type
                task_type_str = task_data.get('type', 'synthesis')
                try:
                    task_type = TaskType(task_type_str)
                except ValueError:
                    task_type = TaskType.SYNTHESIS

                # Resolve dependencies
                depends_on = []
                for dep_idx in task_data.get('depends_on', []):
                    if dep_idx in task_id_map:
                        depends_on.append(task_id_map[dep_idx])

                # Create task
                task_config = task_data.get('config', {})
                if context:
                    task_config.update(context)

                # Resolve user/channel names BEFORE creating task (so DB gets full config)
                if task_type == TaskType.CONVERSATION:
                    self._resolve_conversation_config(task_config)

                task = task_queue.add_task(
                    goal_id=goal_id,
                    task_type=task_type,
                    description=task_data.get('description', f'Task {i+1}'),
                    depends_on=depends_on,
                    config=task_config
                )

                tasks.append(task)
                task_id_map[i] = task.id

            return tasks

        except Exception as e:
            logger.error(f"AI decomposition failed: {e}", exc_info=True)
            return []

    def _build_decomposition_prompt(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Build the prompt for AI decomposition."""
        context_str = ""
        if context:
            context_str = f"\nContext: {json.dumps(context, indent=2)}"

        agent_info = ""
        if self.agent_config:
            agent_info = f"""
Agent Name: {self.agent_config.name}
Workspace: {self.agent_config.workspace}
Available Databases: {', '.join(self.agent_config.databases)}
MCP Tools: {', '.join(self.agent_config.mcp_tools) if self.agent_config.mcp_tools else 'None'}
"""

        team_context = self._get_team_context()
        team_block = ""
        if team_context:
            team_block = f"""
Reachable people and channels:
{team_context}
"""

        return f"""You are a task planner. Decompose the following goal into executable tasks.

Goal: {goal}
{context_str}
{agent_info}
{team_block}
Available task types:
- conversation: Start an async conversation with a human (waits for replies)
- tool_call: Execute an MCP tool directly (no dependency on prior tasks)
- synthesis: AI analyzes prior task results and creates output (journal entries, summaries, reports). The conversation transcript is automatically provided.
- sub_agent: Delegate to another agent

Rules:
- Tasks that write about, summarize, or log takeaways from a conversation MUST be type "synthesis" (not "tool_call"). Synthesis tasks receive the conversation transcript automatically.
- Each task description should ONLY describe that task's job — do NOT include instructions for other tasks.
- For conversation tasks, set "user" in config to the person's name from the team list. Set "channel" to "#channel-name" for channel posts instead of DMs.
- For conversation tasks, put the detailed topic/instructions in config.topic. The "description" field is shown in the UI and should be SHORT and action-focused (e.g. "Have conversation with Alice"), NOT a restatement of the goal.
- The goal text is already displayed separately in the UI — task descriptions should NOT repeat it.

Return a JSON array of tasks. Each task should have:
- "type": one of conversation, tool_call, synthesis, sub_agent
- "description": SHORT action label (shown in UI alongside the goal — don't repeat the goal)
- "depends_on": array of task indices (0-indexed) this task depends on
- "config": configuration object (use "topic" for detailed conversation instructions)

Example response:
```json
[
  {{"type": "conversation", "description": "Have conversation with Alice", "config": {{"user": "Alice", "topic": "check in and mention the project status"}}}},
  {{"type": "synthesis", "description": "Log takeaways to journal", "depends_on": [0]}}
]
```

IMPORTANT: Return ONLY the JSON array, no other text. Keep tasks focused and minimal (2-4 tasks typical).
"""

    def _parse_ai_response(self, response: str) -> List[Dict[str, Any]]:
        """Parse AI response to extract task definitions."""
        # Try to extract JSON from response
        json_match = re.search(r'\[[\s\S]*\]', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Try to parse entire response as JSON
        try:
            data = json.loads(response)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

        logger.warning("Could not parse AI decomposition response")
        return []

    async def replan(
        self,
        goal: str,
        task_queue: TaskQueue,
        goal_id: str,
        failures: List[Task],
        context: Optional[Dict[str, Any]] = None
    ) -> List[Task]:
        """
        Create new tasks after failures.

        This is called when some tasks fail and we need to adapt the plan.

        Args:
            goal: Original goal description
            task_queue: TaskQueue to add tasks to
            goal_id: ID of the parent goal
            failures: List of failed tasks
            context: Additional context

        Returns:
            List of newly created tasks
        """
        logger.info(f"Replanning after {len(failures)} failure(s)")

        # Build failure summary
        failure_summary = []
        for task in failures:
            failure_summary.append({
                'type': task.type.value,
                'description': task.description,
                'error': task.error
            })

        # For now, just log and return empty (don't auto-retry)
        # In the future, could use AI to suggest recovery tasks
        logger.warning(f"Failed tasks: {failure_summary}")

        return []
