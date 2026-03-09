"""Formatters for converting feed events into Claude Code-style display."""

import re
from enum import Enum
from typing import Optional
from rich.text import Text

from promaia.agents.feed_events import FeedEvent, EventType


class Significance(Enum):
    """How significant an event is for display purposes."""
    MILESTONE = "milestone"  # Always shown (agent start/end, task start/end)
    ACTIVITY = "activity"    # Shown by default (messages, tool calls, status updates)
    DETAIL = "detail"        # Hidden unless --verbose (SDK noise, config, token counts)


# Patterns that mark a LOG_MESSAGE as DETAIL (implementation noise)
_DETAIL_PATTERNS = [
    r"Configured \d+ MCP server",
    r"SDK tools:",
    r"System prompt size:",
    r"✓ Configured",
    r"✓ Active MCP tools",
    r"✓ Loaded \d+ pages",
    r"✓ Loaded \d+ entries",
    r"Active MCP tools",
    r"token[s ]",
    r"cost estimate",
    r"Cost:",
    r"Tokens:",
    r"Input tokens:",
    r"Output tokens:",
    r"Cache .* tokens:",
    r"Total context size:",
    r"Context split into",
    r"Chunk \d+:",
    r"Combined prompt size:",
    r"Prompt preview",
    r"Initial context:",
    r"📊 Total tokens:",
    r"💰 Estimated cost:",
    r"Starting SDK agent loop",
    r"SDK subprocess",
    r"Using Claude Agent SDK",
    r"Registered messaging platform",
    r"Conversation manager initialized",
    r"execution_tracker",
    r"🚀 Using Claude",
    r"📨 Combined prompt",
    r"📨 Initial context",
    r"📝 Prompt preview",
    r"⚙️ System message",
    r"🤖 \[exec:",
    r"✅ \[exec:",
    r"Started execution \d+",
    r"Completed execution \d+",
    r"Unclosed client session",
    r"Using bundled Claude",
    r"🔄 Task:.*\| Type:",
    r"📋 🔄 Task:",
    r"✅ Task:.*\| Type:",
    r"Orchestrator initialized",
    r"Created goal \w+:",
    r"Decomposing goal into tasks",
    r"GOAL:",
    r"^={10,}",
    r"📋 TASK LIST:",
    r"^-{10,}",
    r"^\s+\d+\.\s+[⏳🔒🔄✅❌❓]",
    r"Generated conversation opener:",
    r"Resolved user '",
    r"Could not resolve user '",
    r"Conversation task will use",
    r"Synthesis task will write to journal",
    r"Using DM channel",
    r"Opened DM channel",
    r"Started conversation \w+ for task",
]

# Patterns that mark a LOG_MESSAGE as ACTIVITY (meaningful status updates)
_ACTIVITY_PATTERNS = [
    r"Loading context",
    r"Writing .*journal",
    r"Writing to journal",
    r"Journal entry created",
    r"📓 Writing to Notion journal",
    r"Waiting for.*conversation",
    r"Waiting for async tasks",
    r"Thinking",
    r"Agent finished",
    r"Pushing.*to Notion",
    r"Pushed to Notion",
    r"Journal saved",
    r"🏁 Ending conversation",
    r"Created \d+ task",
    r"Added task \w+:",
    r"File created successfully",
    r"🧠 Planning:",
    r"📋 Planned \d+ task",
    r"🧠 No pattern match",
    r"📤 Pushing journal to Notion",
    r"📤 Notion sync complete",
    r"📋 Context log:",
    # Synthesis / journal task progress
    r"SDK execution completed",
    r"Agent '.*' completed successfully",
    r"Registered journal entry",
    r"Messaging enabled but",
]

# Patterns that indicate a spinner event (in-progress, not yet completed)
_SPINNER_PATTERNS = [
    r"Loading context from",
    r"💭 Agent is thinking",
    r"⏳ Waiting for conversation",
    r"Waiting for async tasks",
    r"⏳ Starting Claude SDK",
    r"Pushing to Notion",
    r"Writing .*journal",
    r"Working",
    r"🧠 Planning:",
]

# Patterns that indicate a spinner event has completed
_SPINNER_COMPLETION_PATTERNS = [
    r"SDK execution completed",
    r"Conversation ended:",
    r"Journal entry created",
    r"File created successfully",
    r"Goal \w+ marked as completed",
    r"Pushed to Notion",
    r"Journal saved",
    r"Journal (?:entry )?written",
    r"📋 Planned \d+ task",
]


def classify_event(event: FeedEvent) -> Significance:
    """Classify an event's significance for display filtering."""
    # Milestones — always shown
    if event.event_type in (
        EventType.AGENT_START,
        EventType.AGENT_COMPLETE,
        EventType.CALENDAR_TRIGGER,
        EventType.TASK_START,
        EventType.TASK_COMPLETE,
        EventType.CONVERSATION_START,
        EventType.CONVERSATION_END,
    ):
        return Significance.MILESTONE

    # Activity — shown by default
    if event.event_type in (
        EventType.MESSAGE_SENT,
        EventType.MESSAGE_RECEIVED,
        EventType.TOOL_CALL,
    ):
        return Significance.ACTIVITY

    # For LOG_MESSAGE and SYNC_OPERATION, check patterns
    msg = event.message

    # Check detail patterns first (these are noise)
    for pattern in _DETAIL_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return Significance.DETAIL

    # Check activity patterns (meaningful updates)
    for pattern in _ACTIVITY_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return Significance.ACTIVITY

    # SYNC_OPERATION defaults to detail
    if event.event_type == EventType.SYNC_OPERATION:
        return Significance.DETAIL

    # Unclassified LOG_MESSAGE defaults to detail
    if event.event_type == EventType.LOG_MESSAGE:
        return Significance.DETAIL

    # Everything else is activity
    return Significance.ACTIVITY


def is_spinner_event(event: FeedEvent) -> bool:
    """Check if this event should be shown as a live spinner (in-progress work)."""
    for pattern in _SPINNER_PATTERNS:
        if re.search(pattern, event.message, re.IGNORECASE):
            return True
    return False


def is_spinner_completion(event: FeedEvent) -> bool:
    """Check if this event completes a spinner (promotes it to permanent line)."""
    # Task/conversation completions always resolve spinners
    if event.event_type in (
        EventType.TASK_COMPLETE,
        EventType.CONVERSATION_END,
        EventType.AGENT_COMPLETE,
    ):
        return True

    for pattern in _SPINNER_COMPLETION_PATTERNS:
        if re.search(pattern, event.message, re.IGNORECASE):
            return True
    return False


def format_event(event: FeedEvent, show_timestamps: bool = False) -> Text:
    """Format a feed event in Claude Code style.

    Returns a Rich Text object for permanent printing above the spinner.
    """
    text = Text()

    if show_timestamps:
        timestamp = event.timestamp.strftime("%H:%M:%S")
        text.append(f"[{timestamp}] ", style="dim")

    # Route to the appropriate formatter
    if event.event_type in (EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED):
        text.append(_format_conversation_message(event))
    elif event.event_type == EventType.TOOL_CALL:
        text.append(_format_tool_call(event))
    elif event.event_type == EventType.CALENDAR_TRIGGER:
        text.append(_format_calendar_trigger(event))
    elif event.event_type in (EventType.TASK_START, EventType.TASK_COMPLETE):
        text.append(_format_task_event(event))
    elif event.event_type in (EventType.CONVERSATION_START, EventType.CONVERSATION_END):
        text.append(_format_conversation_lifecycle(event))
    elif event.event_type in (EventType.AGENT_START, EventType.AGENT_COMPLETE):
        text.append(_format_agent_lifecycle(event))
    else:
        text.append(_format_status_line(event))

    return text


def format_goal_banner(agent_name: str, goal_description: str, tasks: list[str] = None) -> Text:
    """Format a goal start banner.

    Example:
        ━━━ 🎯 Chief of Staff ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        Goal: Touch base about goals for the upcoming period
          Plan:
            ☐ Chat with user
            ☐ Write journal summary
    """
    text = Text()
    # Banner line
    header = f" {agent_name} "
    padding = max(0, 60 - len(header) - 6)
    text.append("\n")
    text.append("━━━ 🎯", style="bold yellow")
    text.append(header, style="bold cyan")
    text.append("━" * padding, style="bold yellow")
    text.append("\n")

    if goal_description:
        text.append("Goal: ", style="bold")
        text.append(goal_description)
        text.append("\n")

    if tasks:
        text.append("  Plan:\n", style="bold")
        for t in tasks:
            text.append("    ☐ ", style="dim")
            text.append(t)
            text.append("\n")

    return text


def format_goal_complete_banner(
    agent_name: str,
    task_count: int,
    duration_str: str,
    summary: str = None,
) -> Text:
    """Format a goal completion banner.

    Example:
        ━━━ Done chief-of-staff done (2 tasks, 2m 14s) ━━━━━━━━━━━━━━━━━━━━━
          Summary: Discussed goal-setting with user
    """
    text = Text()
    stats = f"{task_count} task{'s' if task_count != 1 else ''}"
    if duration_str:
        stats += f", {duration_str}"
    header = f" {agent_name} done ({stats}) "
    padding = max(0, 60 - len(header) - 6)
    text.append("\n")
    text.append("━━━ ✅", style="bold green")
    text.append(header, style="bold cyan")
    text.append("━" * padding, style="bold green")
    text.append("\n")

    if summary:
        text.append("  Summary: ", style="dim bold")
        text.append(summary, style="dim")
        text.append("\n")

    return text


def format_task_header(task_index: int, task_total: int, task_description: str) -> Text:
    """Format a task progress header.

    Example:
        Task 1/2: Chat with user
    """
    text = Text()
    text.append(f"\nTask {task_index}/{task_total}: ", style="bold")
    text.append(task_description)
    return text


def format_spinner_text(message: str) -> Text:
    """Format spinner text for the live display line."""
    text = Text()
    # Use a simple dot spinner character
    text.append("  ✱ ", style="bold yellow")
    text.append(_strip_tags(message), style="yellow")
    return text


def format_idle_spinner() -> Text:
    """Format the idle waiting spinner."""
    text = Text()
    text.append("✱ ", style="bold yellow")
    text.append("Waiting for next trigger...", style="yellow")
    return text


def format_task_checklist(tasks: list[dict]) -> Text:
    """Format a live task checklist for the Live renderable.

    Each task: {"description": str, "completed": bool}
    Renders completed tasks as dim green ☑, pending as normal ☐.
    """
    text = Text()
    for task in tasks:
        if task["completed"]:
            text.append("    ☑ ", style="dim green")
            text.append(task["description"], style="dim")
        else:
            text.append("    ☐ ", style="dim")
            text.append(task["description"])
        text.append("\n")
    return text


# --- Helpers ---


def _strip_tags(message: str) -> str:
    """Strip [goal:xxx], [task:xxx], [conv:xxx], [exec:xxx] correlation tags."""
    return re.sub(r'\[(?:goal|task|conv|exec):[^\]]+\]\s*', '', message).strip()


# --- Internal formatters ---


def _format_conversation_message(event: FeedEvent) -> Text:
    """Format: 💬 Agent Name: "message..." or 💬 User: "message..." """
    text = Text()
    msg_data = event.metadata.get('message', {})
    text_content = msg_data.get('text', event.message) if isinstance(msg_data, dict) else event.message

    # Strip log-line prefixes that the watcher preserved (our formatter adds its own)
    text_content = re.sub(r'^💭 Agent:\s*', '', text_content)
    # Extract user name from "📩 Message from {name} in {channel}: {text}" before stripping
    user_name_match = re.match(r'^📩 Message from (\S+)', text_content)
    text_content = re.sub(r'^📩 Message from [^:]+:\s*', '', text_content)

    # Truncate long messages
    if len(text_content) > 200:
        text_content = text_content[:197] + "..."

    if event.event_type == EventType.MESSAGE_SENT:
        agent = event.agent_name or "Agent"
        emoji = "💭" if event.source != "conversation" else "💬"
        text.append(f"  {emoji} {agent}: ", style="bold cyan")
        text.append(f'"{text_content}"', style="italic")
    else:
        user = user_name_match.group(1) if user_name_match else "User"
        text.append(f"  💬 {user}: ", style="bold yellow")
        text.append(f'"{text_content}"', style="italic")

    return text


def _format_tool_call(event: FeedEvent) -> Text:
    """Format:   🔧 tool_name: summary"""
    text = Text()
    text.append(f"  🔧 ", style="dim")
    # Strip [agent-name] prefix from agentic_turn feed logger messages
    msg = re.sub(r'^\[\S+\]\s+', '', event.message)
    # Strip "✓ " prefix from tool completion messages
    msg = re.sub(r'^✓\s+', '', msg)
    text.append(msg)
    return text


def _format_calendar_trigger(event: FeedEvent) -> Text:
    """Format: 🗓️  Calendar triggered agent-name"""
    text = Text()
    agent = event.agent_name or _extract_agent_from_trigger(event.message) or "agent"
    text.append("🗓️  Calendar triggered ", style="bold")
    text.append(agent, style="bold cyan")
    return text


def _extract_agent_from_trigger(message: str) -> Optional[str]:
    """Try to extract agent name from a calendar trigger message."""
    m = re.search(r'triggered goal.*for\s+(.+)', message)
    return m.group(1).strip() if m else None


def _format_task_event(event: FeedEvent) -> Text:
    """Format task start/complete events."""
    text = Text()
    msg = _strip_tags(event.message)
    if event.event_type == EventType.TASK_START:
        text.append("  🔄 ", style="")
        text.append(msg)
    else:
        text.append("  ✅ ", style="green")
        text.append(msg)
    return text


def _format_conversation_lifecycle(event: FeedEvent) -> Text:
    """Format conversation start/end."""
    text = Text()
    if event.event_type == EventType.CONVERSATION_START:
        text.append("  💬 ", style="")
        text.append("Conversation started", style="dim")
    else:
        text.append("  ✅ ", style="green")
        # Try to extract turn count from message
        turns_match = re.search(r'(\d+)\s*turns?', event.message)
        if turns_match:
            text.append(f"Conversation complete ({turns_match.group(1)} turns)")
        else:
            text.append("Conversation complete")
    return text


def _format_agent_lifecycle(event: FeedEvent) -> Text:
    """Format agent start/complete (used for non-banner contexts)."""
    text = Text()
    if event.event_type == EventType.AGENT_START:
        agent = event.agent_name or "agent"
        text.append("🤖 ", style="")
        text.append(f"Starting {agent}", style="bold")
    else:
        agent = event.agent_name or "agent"
        text.append("✅ ", style="green")
        text.append(f"{agent} complete", style="bold")
    return text


def _format_status_line(event: FeedEvent) -> Text:
    """Format a generic status/activity line."""
    text = Text()

    # Pick an emoji based on message content — strip correlation tags for display
    msg = _strip_tags(event.message)

    # If message already starts with an emoji, just indent — don't stack emojis
    if msg and ord(msg[0]) > 0x2000:
        text.append("  ", style="")
    elif re.search(r"loading|context", msg, re.IGNORECASE):
        text.append("  📚 ", style="")
    elif re.search(r"writing|journal", msg, re.IGNORECASE):
        text.append("  📝 ", style="")
    elif re.search(r"push|notion", msg, re.IGNORECASE):
        text.append("  📤 ", style="")
    elif re.search(r"complete|done|finish|saved", msg, re.IGNORECASE):
        text.append("  ✅ ", style="green")
    else:
        text.append("  📋 ", style="dim")

    text.append(msg)
    return text


# --- Legacy support ---


def format_as_group_chat(event: FeedEvent) -> Text:
    """Legacy formatter — delegates to format_event for backwards compat."""
    return format_event(event, show_timestamps=True)


def get_emoji_for_event(event: FeedEvent) -> str:
    """Get appropriate emoji for an event based on source and type."""
    if event.event_type == EventType.CALENDAR_TRIGGER:
        return "🗓️"
    elif event.event_type in (EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED,
                              EventType.CONVERSATION_START, EventType.CONVERSATION_END):
        return "💬"
    elif event.event_type == EventType.TOOL_CALL:
        return "🔧"
    elif event.event_type == EventType.AGENT_COMPLETE:
        return "✅"
    elif event.event_type in (EventType.TASK_START, EventType.AGENT_START):
        return "🔄"
    elif event.event_type == EventType.QUERY_EXECUTE:
        return "🔍"

    if event.level == "ERROR":
        return "❌"
    elif event.level == "WARNING":
        return "⚠️"

    source_mapping = {
        'daemon': '🗓️',
        'orchestrator': '🎯',
        'executor': '🤖',
        'conversation': '💬',
        'slack': '💬',
        'tool': '🔧',
        'sync': '🔄',
    }
    return source_mapping.get(event.source, '📋')


def extract_correlation_ids(message: str) -> tuple[Optional[str], Optional[str]]:
    """Extract [goal:abc123] and [task:xyz789] tags from message."""
    goal_match = re.search(r'\[goal:([a-f0-9]+)\]', message)
    task_match = re.search(r'\[task:([a-f0-9]+)\]', message)

    goal_id = goal_match.group(1) if goal_match else None
    task_id = task_match.group(1) if task_match else None

    return goal_id, task_id
