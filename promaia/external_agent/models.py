"""
Data models for external agent tasks and results.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from enum import Enum
import uuid
import json


class TaskStatus(str, Enum):
    """Status of an agent task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TaskType(str, Enum):
    """Types of tasks that can be delegated to external agents."""
    EMAIL_DRAFT = "email_draft"              # Review/edit email draft
    CODE_CHANGE = "code_change"              # Make code modifications
    QUERY_RESULTS = "query_results"          # Process query results
    CONTENT_WRITE = "content_write"          # Write/edit content
    SYNC_OPERATION = "sync_operation"        # Handle sync tasks
    GENERAL = "general"                      # General purpose task


@dataclass
class AgentTask:
    """
    A task to be executed by an external agent (e.g., Claude Code).

    This represents work that promaia wants to delegate to an external
    agent, along with all the context needed to complete it.
    """
    task_id: str
    task_type: TaskType
    workspace: str
    instructions: str                         # Natural language instructions
    context: Dict[str, Any]                   # Full context data
    metadata: Dict[str, Any] = field(default_factory=dict)

    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Related entities
    related_draft_id: Optional[str] = None    # If task relates to an email draft
    related_thread_id: Optional[str] = None   # If task relates to a thread

    # Progress tracking
    progress_notes: List[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        task_type: TaskType,
        workspace: str,
        instructions: str,
        context: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        expires_in_hours: Optional[int] = 24,
        related_draft_id: Optional[str] = None,
        related_thread_id: Optional[str] = None
    ) -> 'AgentTask':
        """
        Create a new agent task.

        Args:
            task_type: Type of task
            workspace: Workspace context
            instructions: Natural language instructions for the agent
            context: Full context data (databases, query results, etc.)
            metadata: Additional metadata
            expires_in_hours: Task expiration time (default 24 hours)
            related_draft_id: Optional related draft ID
            related_thread_id: Optional related thread ID

        Returns:
            New AgentTask instance
        """
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = None

        if expires_in_hours:
            expires_at = now + timedelta(hours=expires_in_hours)

        return cls(
            task_id=task_id,
            task_type=task_type,
            workspace=workspace,
            instructions=instructions,
            context=context,
            metadata=metadata or {},
            expires_at=expires_at,
            related_draft_id=related_draft_id,
            related_thread_id=related_thread_id
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary for storage/transmission."""
        data = asdict(self)

        # Convert enums to strings
        data['task_type'] = self.task_type.value
        data['status'] = self.status.value

        # Convert datetimes to ISO format
        data['created_at'] = self.created_at.isoformat()
        if self.expires_at:
            data['expires_at'] = self.expires_at.isoformat()
        if self.started_at:
            data['started_at'] = self.started_at.isoformat()
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentTask':
        """Create task from dictionary."""
        # Convert string enums back to enum types
        data['task_type'] = TaskType(data['task_type'])
        data['status'] = TaskStatus(data['status'])

        # Convert ISO strings back to datetime objects
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        if data.get('expires_at'):
            data['expires_at'] = datetime.fromisoformat(data['expires_at'])
        if data.get('started_at'):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if data.get('completed_at'):
            data['completed_at'] = datetime.fromisoformat(data['completed_at'])

        return cls(**data)

    def is_expired(self) -> bool:
        """Check if task has expired."""
        if not self.expires_at:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def mark_started(self):
        """Mark task as started."""
        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.now(timezone.utc)

    def mark_completed(self):
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self):
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)

    def add_progress_note(self, note: str):
        """Add a progress note."""
        self.progress_notes.append(f"{datetime.now(timezone.utc).isoformat()}: {note}")


@dataclass
class TaskResult:
    """
    Result from an external agent's task execution.

    This is what comes back from the agent after it completes (or fails) a task.
    """
    task_id: str
    status: TaskStatus
    result_data: Dict[str, Any]               # Actual results/outputs
    metadata: Dict[str, Any] = field(default_factory=dict)

    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Agent information
    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    execution_time_seconds: Optional[float] = None

    @classmethod
    def create_success(
        cls,
        task_id: str,
        result_data: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None,
        execution_time: Optional[float] = None
    ) -> 'TaskResult':
        """Create a successful task result."""
        return cls(
            task_id=task_id,
            status=TaskStatus.COMPLETED,
            result_data=result_data,
            metadata=metadata or {},
            agent_name=agent_name,
            execution_time_seconds=execution_time
        )

    @classmethod
    def create_failure(
        cls,
        task_id: str,
        error_message: str,
        partial_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None
    ) -> 'TaskResult':
        """Create a failed task result."""
        return cls(
            task_id=task_id,
            status=TaskStatus.FAILED,
            result_data=partial_data or {},
            metadata=metadata or {},
            error_message=error_message,
            agent_name=agent_name
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for storage/transmission."""
        data = asdict(self)
        data['status'] = self.status.value
        data['created_at'] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TaskResult':
        """Create result from dictionary."""
        data['status'] = TaskStatus(data['status'])
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        return cls(**data)

    def is_success(self) -> bool:
        """Check if result represents success."""
        return self.status == TaskStatus.COMPLETED
