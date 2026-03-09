"""
Task Queue for Agent Orchestrator.

Manages tasks with dependencies, status tracking, and persistence.
Tasks can be of various types: conversation, tool_call, synthesis, sub_agent.
"""

import sqlite3
import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Types of tasks that can be executed."""
    CONVERSATION = "conversation"  # Async, waits for human replies
    TOOL_CALL = "tool_call"        # Sync, runs MCP tool immediately
    SYNTHESIS = "synthesis"        # AI analyzes prior task results
    SUB_AGENT = "sub_agent"        # Delegates to another agent


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"  # Waiting for dependencies


@dataclass
class Task:
    """
    A single task in the orchestration queue.

    Attributes:
        id: Unique task identifier
        goal_id: ID of the parent goal this task belongs to
        type: Task type (conversation, tool_call, synthesis, sub_agent)
        description: Human-readable description of what this task does
        status: Current status (pending, running, completed, failed, blocked)
        depends_on: List of task IDs that must complete before this task can run
        config: Task-specific configuration (tool params, user ID, etc.)
        result: Result data after task completion
        error: Error message if task failed
        async_handle: Handle for async tasks (e.g., conversation_id)
        created_at: Timestamp when task was created
        started_at: Timestamp when task started running
        completed_at: Timestamp when task completed or failed
    """
    id: str
    goal_id: str
    type: TaskType
    description: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    async_handle: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['type'] = self.type.value if isinstance(self.type, TaskType) else self.type
        data['status'] = self.status.value if isinstance(self.status, TaskStatus) else self.status
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        """Create Task from dictionary."""
        # Convert string enums back to enum types
        if isinstance(data.get('type'), str):
            data['type'] = TaskType(data['type'])
        if isinstance(data.get('status'), str):
            data['status'] = TaskStatus(data['status'])
        return cls(**data)

    def is_blocked(self) -> bool:
        """Check if this task is blocked by incomplete dependencies."""
        return self.status == TaskStatus.BLOCKED

    def mark_running(self):
        """Mark task as running."""
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_completed(self, result: Any = None):
        """Mark task as completed with optional result."""
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.completed_at = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, error: str):
        """Mark task as failed with error message."""
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class Goal:
    """
    A goal represents the top-level objective being orchestrated.

    Attributes:
        id: Unique goal identifier
        agent_id: ID of the agent executing this goal
        description: The original goal description
        status: Overall goal status
        created_at: When goal was created
        completed_at: When goal completed
        metadata: Additional metadata (calendar event info, etc.)
    """
    id: str
    agent_id: str
    description: str
    status: str = "active"  # active, completed, failed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Goal':
        """Create Goal from dictionary."""
        return cls(**data)


class TaskQueue:
    """
    Task queue with dependency tracking and SQLite persistence.

    This is the central coordinator for task execution in the orchestrator.
    It handles:
    - Task creation and dependency management
    - Status tracking and updates
    - Persistence to SQLite for crash recovery
    - Query methods for the execution loop
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the task queue.

        Args:
            db_path: Path to SQLite database (default: data/hybrid_metadata.db)
        """
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = get_db_path()

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_database()

        logger.info(f"TaskQueue initialized (db: {self.db_path})")

    def _init_database(self):
        """Initialize SQLite database schema for tasks and goals."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Goals table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orchestrator_goals (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}'
                )
            """)

            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orchestrator_tasks (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    depends_on TEXT NOT NULL DEFAULT '[]',
                    config TEXT NOT NULL DEFAULT '{}',
                    result TEXT,
                    error TEXT,
                    async_handle TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY (goal_id) REFERENCES orchestrator_goals(id)
                )
            """)

            # Indexes for efficient queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_goal
                ON orchestrator_tasks(goal_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_status
                ON orchestrator_tasks(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_status
                ON orchestrator_goals(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_async_handle
                ON orchestrator_tasks(async_handle)
            """)

            conn.commit()
            logger.debug("Database schema initialized")

    # ==================== Goal Management ====================

    def create_goal(
        self,
        agent_id: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Goal:
        """
        Create a new goal.

        Args:
            agent_id: ID of the agent executing this goal
            description: The goal description
            metadata: Additional metadata

        Returns:
            Created Goal object
        """
        goal = Goal(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            description=description,
            metadata=metadata or {}
        )

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orchestrator_goals
                (id, agent_id, description, status, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                goal.id,
                goal.agent_id,
                goal.description,
                goal.status,
                goal.created_at,
                json.dumps(goal.metadata)
            ))
            conn.commit()

        logger.info(f"Created goal {goal.id[:8]}: {description[:50]}...")
        return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get a goal by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orchestrator_goals WHERE id = ?", (goal_id,))
            row = cursor.fetchone()

            if row:
                return Goal(
                    id=row['id'],
                    agent_id=row['agent_id'],
                    description=row['description'],
                    status=row['status'],
                    created_at=row['created_at'],
                    completed_at=row['completed_at'],
                    metadata=json.loads(row['metadata'])
                )
            return None

    def complete_goal(self, goal_id: str, status: str = "completed"):
        """Mark a goal as completed or failed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orchestrator_goals
                SET status = ?, completed_at = ?
                WHERE id = ?
            """, (status, datetime.now(timezone.utc).isoformat(), goal_id))
            conn.commit()

        logger.info(f"Goal {goal_id[:8]} marked as {status}")

    def cancel_goal(self, goal_id: str):
        """Cancel a goal and all its incomplete tasks."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Mark goal as superseded
            cursor.execute("""
                UPDATE orchestrator_goals
                SET status = 'superseded', completed_at = ?
                WHERE id = ?
            """, (now, goal_id))
            # Mark all pending/running tasks as failed so the old process exits
            cursor.execute("""
                UPDATE orchestrator_tasks
                SET status = 'failed', result = '"superseded by new run"'
                WHERE goal_id = ? AND status IN ('pending', 'running', 'blocked')
            """, (goal_id,))
            cancelled_tasks = cursor.rowcount
            conn.commit()

        logger.info(f"Goal {goal_id[:8]} cancelled ({cancelled_tasks} task(s) aborted)")

    def get_active_goals(self) -> List[Goal]:
        """Get all active goals."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM orchestrator_goals
                WHERE status = 'active'
                ORDER BY created_at DESC
            """)

            goals = []
            for row in cursor.fetchall():
                goals.append(Goal(
                    id=row['id'],
                    agent_id=row['agent_id'],
                    description=row['description'],
                    status=row['status'],
                    created_at=row['created_at'],
                    completed_at=row['completed_at'],
                    metadata=json.loads(row['metadata'])
                ))
            return goals

    # ==================== Task Management ====================

    def add_task(
        self,
        goal_id: str,
        task_type: TaskType,
        description: str,
        depends_on: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> Task:
        """
        Add a new task to the queue.

        Args:
            goal_id: ID of the parent goal
            task_type: Type of task
            description: What the task does
            depends_on: List of task IDs this task depends on
            config: Task-specific configuration

        Returns:
            Created Task object
        """
        task = Task(
            id=str(uuid.uuid4()),
            goal_id=goal_id,
            type=task_type,
            description=description,
            depends_on=depends_on or [],
            config=config or {}
        )

        # Check if task should start as blocked
        if task.depends_on:
            incomplete_deps = self._get_incomplete_dependencies(task.depends_on)
            if incomplete_deps:
                task.status = TaskStatus.BLOCKED

        self._save_task(task)
        logger.info(f"Added task {task.id[:8]}: {task.type.value} - {description[:50]}...")

        return task

    def add_tasks(self, tasks: List[Task]):
        """Add multiple tasks at once."""
        for task in tasks:
            self._save_task(task)
        logger.info(f"Added {len(tasks)} task(s)")

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orchestrator_tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_task(row)
            return None

    def get_task_by_async_handle(self, async_handle: str) -> Optional[Task]:
        """Get a task by its async handle (e.g., conversation_id)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orchestrator_tasks WHERE async_handle = ?",
                (async_handle,)
            )
            row = cursor.fetchone()

            if row:
                return self._row_to_task(row)
            return None

    def get_tasks_for_goal(self, goal_id: str) -> List[Task]:
        """Get all tasks for a goal."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orchestrator_tasks WHERE goal_id = ? ORDER BY created_at",
                (goal_id,)
            )
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def get_ready_tasks(self, goal_id: str) -> List[Task]:
        """
        Get tasks that are ready to run (unblocked and pending).

        A task is ready if:
        - Status is PENDING
        - All dependencies are COMPLETED

        Args:
            goal_id: ID of the goal to check tasks for

        Returns:
            List of tasks ready to execute
        """
        tasks = self.get_tasks_for_goal(goal_id)
        ready_tasks = []

        # Get completed task IDs for this goal
        completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}

        for task in tasks:
            if task.status != TaskStatus.PENDING and task.status != TaskStatus.BLOCKED:
                continue

            # Check if all dependencies are completed
            deps_satisfied = all(dep_id in completed_ids for dep_id in task.depends_on)

            if deps_satisfied:
                ready_tasks.append(task)

        return ready_tasks

    def get_running_tasks(self, goal_id: str) -> List[Task]:
        """Get all currently running tasks for a goal."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orchestrator_tasks WHERE goal_id = ? AND status = ?",
                (goal_id, TaskStatus.RUNNING.value)
            )
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def get_running_async_tasks(self, goal_id: str) -> List[Task]:
        """Get running async tasks (conversations) for a goal."""
        tasks = self.get_running_tasks(goal_id)
        return [t for t in tasks if t.type == TaskType.CONVERSATION and t.async_handle]

    def mark_running(self, task_id: str, async_handle: Optional[str] = None):
        """Mark a task as running."""
        task = self.get_task(task_id)
        if task:
            task.mark_running()
            task.async_handle = async_handle
            self._save_task(task)
            logger.info(f"Task {task_id[:8]} started running")

    def mark_completed(self, task_id: str, result: Any = None):
        """
        Mark a task as completed and unblock dependent tasks.

        Args:
            task_id: ID of completed task
            result: Result data from task execution
        """
        task = self.get_task(task_id)
        if task:
            task.mark_completed(result)
            self._save_task(task)

            # Unblock any tasks waiting on this one
            self._update_blocked_tasks(task.goal_id)

            logger.info(f"Task {task_id[:8]} completed")

    def mark_failed(self, task_id: str, error: str):
        """Mark a task as failed."""
        task = self.get_task(task_id)
        if task:
            task.mark_failed(error)
            self._save_task(task)
            logger.error(f"Task {task_id[:8]} failed: {error}")

    def all_complete(self, goal_id: str) -> bool:
        """Check if all tasks for a goal are complete."""
        tasks = self.get_tasks_for_goal(goal_id)
        if not tasks:
            return True

        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in tasks
        )

    def has_running_async(self, goal_id: str) -> bool:
        """Check if there are any running async tasks."""
        return len(self.get_running_async_tasks(goal_id)) > 0

    def get_failures(self, goal_id: str) -> List[Task]:
        """Get all failed tasks for a goal."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM orchestrator_tasks WHERE goal_id = ? AND status = ?",
                (goal_id, TaskStatus.FAILED.value)
            )
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def get_completed_results(self, goal_id: str) -> Dict[str, Any]:
        """Get results from all completed tasks for a goal."""
        tasks = self.get_tasks_for_goal(goal_id)
        return {
            t.id: t.result
            for t in tasks
            if t.status == TaskStatus.COMPLETED and t.result is not None
        }

    # ==================== Internal Methods ====================

    def _save_task(self, task: Task):
        """Save a task to the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orchestrator_tasks
                (id, goal_id, type, description, status, depends_on, config,
                 result, error, async_handle, created_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.id,
                task.goal_id,
                task.type.value if isinstance(task.type, TaskType) else task.type,
                task.description,
                task.status.value if isinstance(task.status, TaskStatus) else task.status,
                json.dumps(task.depends_on),
                json.dumps(task.config),
                json.dumps(task.result) if task.result is not None else None,
                task.error,
                task.async_handle,
                task.created_at,
                task.started_at,
                task.completed_at
            ))
            conn.commit()

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert database row to Task object."""
        return Task(
            id=row['id'],
            goal_id=row['goal_id'],
            type=TaskType(row['type']),
            description=row['description'],
            status=TaskStatus(row['status']),
            depends_on=json.loads(row['depends_on']),
            config=json.loads(row['config']),
            result=json.loads(row['result']) if row['result'] else None,
            error=row['error'],
            async_handle=row['async_handle'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            completed_at=row['completed_at']
        )

    def _get_incomplete_dependencies(self, dep_ids: List[str]) -> List[str]:
        """Get IDs of dependencies that aren't completed."""
        if not dep_ids:
            return []

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(dep_ids))
            cursor.execute(f"""
                SELECT id FROM orchestrator_tasks
                WHERE id IN ({placeholders}) AND status != ?
            """, (*dep_ids, TaskStatus.COMPLETED.value))

            return [row[0] for row in cursor.fetchall()]

    def _update_blocked_tasks(self, goal_id: str):
        """Update status of blocked tasks when a dependency completes."""
        tasks = self.get_tasks_for_goal(goal_id)
        completed_ids = {t.id for t in tasks if t.status == TaskStatus.COMPLETED}

        for task in tasks:
            if task.status == TaskStatus.BLOCKED:
                # Check if all dependencies are now satisfied
                if all(dep_id in completed_ids for dep_id in task.depends_on):
                    task.status = TaskStatus.PENDING
                    self._save_task(task)
                    logger.info(f"Task {task.id[:8]} unblocked")
