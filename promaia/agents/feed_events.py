"""Event data models for the unified agent activity feed."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class EventType(Enum):
    """Types of events in the agent activity feed."""
    AGENT_START = "agent_start"
    AGENT_COMPLETE = "agent_complete"
    TOOL_CALL = "tool_call"
    QUERY_EXECUTE = "query_execute"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    CONVERSATION_START = "conversation_start"
    MESSAGE_SENT = "message_sent"
    MESSAGE_RECEIVED = "message_received"
    CONVERSATION_END = "conversation_end"
    CALENDAR_TRIGGER = "calendar_trigger"
    SYNC_OPERATION = "sync_operation"
    LOG_MESSAGE = "log_message"


@dataclass
class FeedEvent:
    """A single event in the agent activity feed."""
    timestamp: datetime
    source: str  # 'daemon', 'orchestrator', 'executor', 'conversation', 'slack'
    event_type: EventType
    level: str  # 'DEBUG', 'INFO', 'WARNING', 'ERROR'
    message: str
    agent_name: Optional[str] = None
    goal_id: Optional[str] = None
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other):
        """Allow sorting by timestamp."""
        return self.timestamp < other.timestamp
