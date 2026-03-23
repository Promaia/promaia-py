"""
CLI commands for external agent task management.

These commands allow promaia to submit tasks locally via SQLite,
and for external agents to poll and complete tasks.
"""
import asyncio
import json
import logging
from typing import Optional

from promaia.external_agent import TaskManager, AgentTask, TaskResult, TaskType, TaskStatus

logger = logging.getLogger(__name__)


async def handle_agent_submit(args):
    """
    Submit a task for an external agent.

    Usage:
        maia agent submit --type email_draft --workspace koii --instructions "Review this draft" --context '{"draft_id": "123"}'
    """
    task_manager = TaskManager()

    # Parse context JSON
    try:
        context = json.loads(args.context) if args.context else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON in --context")
        return

    # Parse metadata JSON
    try:
        metadata = json.loads(args.metadata) if args.metadata else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON in --metadata")
        return

    # Validate task type
    try:
        task_type = TaskType(args.type)
    except ValueError:
        print(f"❌ Invalid task type: {args.type}")
        print(f"Valid types: {[t.value for t in TaskType]}")
        return

    # Create task
    task = AgentTask.create(
        task_type=task_type,
        workspace=args.workspace,
        instructions=args.instructions,
        context=context,
        metadata=metadata,
        expires_in_hours=args.expires_in_hours,
        related_draft_id=args.draft_id,
        related_thread_id=args.thread_id
    )

    # Submit task
    task_id = task_manager.submit_task(task)

    print(f"✅ Task submitted: {task_id}")
    print(f"   Type: {task.task_type.value}")
    print(f"   Workspace: {task.workspace}")
    print(f"   Status: {task.status.value}")
    if task.expires_at:
        print(f"   Expires: {task.expires_at.isoformat()}")


async def handle_agent_list(args):
    """
    List tasks with optional filtering.

    Usage:
        maia agent list
        maia agent list --workspace koii
        maia agent list --status pending
        maia agent list --type email_draft
    """
    task_manager = TaskManager()

    # Parse filters
    status_filter = TaskStatus(args.status) if args.status else None
    type_filter = TaskType(args.type) if args.type else None

    tasks = task_manager.list_tasks(
        workspace=args.workspace,
        status=status_filter,
        task_type=type_filter,
        limit=args.limit
    )

    if not tasks:
        print("No tasks found")
        return

    print(f"\n📋 Found {len(tasks)} tasks:\n")

    for task in tasks:
        print(f"Task: {task.task_id}")
        print(f"  Type: {task.task_type.value}")
        print(f"  Workspace: {task.workspace}")
        print(f"  Status: {task.status.value}")
        print(f"  Created: {task.created_at.isoformat()}")
        print(f"  Instructions: {task.instructions[:100]}...")

        if task.related_draft_id:
            print(f"  Draft ID: {task.related_draft_id}")

        if task.progress_notes:
            print(f"  Progress: {len(task.progress_notes)} notes")

        print()


async def handle_agent_show(args):
    """
    Show full details of a specific task.

    Usage:
        maia agent show <task_id>
    """
    task_manager = TaskManager()

    task = task_manager.get_task(args.task_id)

    if not task:
        print(f"❌ Task not found: {args.task_id}")
        return

    print(f"\n📄 Task Details:\n")
    print(f"Task ID: {task.task_id}")
    print(f"Type: {task.task_type.value}")
    print(f"Workspace: {task.workspace}")
    print(f"Status: {task.status.value}")
    print(f"Created: {task.created_at.isoformat()}")

    if task.started_at:
        print(f"Started: {task.started_at.isoformat()}")
    if task.completed_at:
        print(f"Completed: {task.completed_at.isoformat()}")
    if task.expires_at:
        print(f"Expires: {task.expires_at.isoformat()}")

    print(f"\nInstructions:\n{task.instructions}")

    print(f"\nContext:")
    print(json.dumps(task.context, indent=2))

    if task.metadata:
        print(f"\nMetadata:")
        print(json.dumps(task.metadata, indent=2))

    if task.related_draft_id:
        print(f"\nRelated Draft: {task.related_draft_id}")

    if task.related_thread_id:
        print(f"Related Thread: {task.related_thread_id}")

    if task.progress_notes:
        print(f"\nProgress Notes:")
        for note in task.progress_notes:
            print(f"  - {note}")

    # Check for result
    result = task_manager.get_result(task.task_id)
    if result:
        print(f"\n✅ Result:")
        print(f"Status: {result.status.value}")
        if result.agent_name:
            print(f"Agent: {result.agent_name}")
        if result.execution_time_seconds:
            print(f"Execution Time: {result.execution_time_seconds:.2f}s")
        if result.error_message:
            print(f"Error: {result.error_message}")
        print(f"\nResult Data:")
        print(json.dumps(result.result_data, indent=2))


async def handle_agent_pending(args):
    """
    Show pending tasks (convenience command).

    Usage:
        maia agent pending
        maia agent pending --workspace koii
    """
    task_manager = TaskManager()

    tasks = task_manager.get_pending_tasks(workspace=args.workspace)

    if not tasks:
        print("✅ No pending tasks")
        return

    print(f"\n⏳ {len(tasks)} pending tasks:\n")

    for task in tasks:
        print(f"[{task.task_id[:8]}...] {task.task_type.value}")
        print(f"  Workspace: {task.workspace}")
        print(f"  Instructions: {task.instructions[:80]}...")
        if task.related_draft_id:
            print(f"  Draft: {task.related_draft_id[:8]}...")
        print()


async def handle_agent_start(args):
    """
    Mark a task as started.

    Usage:
        maia agent start <task_id>
    """
    task_manager = TaskManager()

    task = task_manager.get_task(args.task_id)
    if not task:
        print(f"❌ Task not found: {args.task_id}")
        return

    if task.status != TaskStatus.PENDING:
        print(f"❌ Task cannot be started (current status: {task.status.value})")
        return

    task_manager.update_task_status(args.task_id, TaskStatus.IN_PROGRESS, "Task started")
    print(f"✅ Task {args.task_id} marked as started")


async def handle_agent_complete(args):
    """
    Mark a task as completed with result data.

    Usage:
        maia agent complete <task_id> --result '{"changes": "Made edits to draft"}'
        maia agent complete <task_id> --result '{"error": "Failed"}' --failed
    """
    task_manager = TaskManager()

    task = task_manager.get_task(args.task_id)
    if not task:
        print(f"❌ Task not found: {args.task_id}")
        return

    # Parse result JSON
    try:
        result_data = json.loads(args.result) if args.result else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON in --result")
        return

    # Parse metadata JSON
    try:
        metadata = json.loads(args.metadata) if args.metadata else {}
    except json.JSONDecodeError:
        print("❌ Invalid JSON in --metadata")
        return

    # Create result
    if args.failed:
        result = TaskResult.create_failure(
            task_id=args.task_id,
            error_message=args.error or "Task failed",
            partial_data=result_data,
            metadata=metadata,
            agent_name=args.agent_name
        )
    else:
        result = TaskResult.create_success(
            task_id=args.task_id,
            result_data=result_data,
            metadata=metadata,
            agent_name=args.agent_name,
            execution_time=args.execution_time
        )

    # Save result
    task_manager.save_result(result)

    status_icon = "✅" if not args.failed else "❌"
    print(f"{status_icon} Task {args.task_id} completed")
    print(f"   Status: {result.status.value}")


async def handle_agent_cleanup(args):
    """
    Cleanup expired tasks.

    Usage:
        maia agent cleanup
    """
    task_manager = TaskManager()
    count = task_manager.cleanup_expired_tasks()

    if count > 0:
        print(f"🧹 Marked {count} tasks as expired")
    else:
        print("✅ No expired tasks to cleanup")


def add_agent_commands(subparsers, include_scheduled=True):
    """Add agent commands to the main parser.

    Args:
        subparsers: The main argument parser's subparsers
        include_scheduled: Whether to include scheduled agent commands (default: True)
    """
    agent_parser = subparsers.add_parser('agent', help='Manage agents and scheduled tasks')
    agent_subparsers = agent_parser.add_subparsers(dest='agent_command', help='Agent commands')

    # Submit command
    submit_parser = agent_subparsers.add_parser('submit', help='Submit a new task for an external agent')
    submit_parser.add_argument('--type', '-t', required=True, help='Task type (e.g., email_draft, code_change)')
    submit_parser.add_argument('--workspace', '-w', required=True, help='Workspace context')
    submit_parser.add_argument('--instructions', '-i', required=True, help='Natural language instructions')
    submit_parser.add_argument('--context', '-c', help='Context data as JSON string')
    submit_parser.add_argument('--metadata', '-m', help='Metadata as JSON string')
    submit_parser.add_argument('--expires-in-hours', type=int, default=24, help='Task expiration time in hours')
    submit_parser.add_argument('--draft-id', help='Related draft ID')
    submit_parser.add_argument('--thread-id', help='Related thread ID')
    submit_parser.set_defaults(func=handle_agent_submit)

    # List command
    list_parser = agent_subparsers.add_parser('list', help='List tasks with optional filtering')
    list_parser.add_argument('--workspace', '-w', help='Filter by workspace')
    list_parser.add_argument('--status', '-s', help='Filter by status')
    list_parser.add_argument('--type', '-t', help='Filter by task type')
    list_parser.add_argument('--limit', '-l', type=int, default=50, help='Maximum number of tasks to show')
    list_parser.set_defaults(func=handle_agent_list)

    # Show command
    show_parser = agent_subparsers.add_parser('show', help='Show full details of a specific task')
    show_parser.add_argument('task_id', help='Task ID to show')
    show_parser.set_defaults(func=handle_agent_show)

    # Pending command
    pending_parser = agent_subparsers.add_parser('pending', help='Show pending tasks')
    pending_parser.add_argument('--workspace', '-w', help='Filter by workspace')
    pending_parser.set_defaults(func=handle_agent_pending)

    # Start command
    start_parser = agent_subparsers.add_parser('start', help='Mark a task as started')
    start_parser.add_argument('task_id', help='Task ID to start')
    start_parser.set_defaults(func=handle_agent_start)

    # Complete command
    complete_parser = agent_subparsers.add_parser('complete', help='Mark a task as completed')
    complete_parser.add_argument('task_id', help='Task ID to complete')
    complete_parser.add_argument('--result', '-r', help='Result data as JSON string')
    complete_parser.add_argument('--metadata', '-m', help='Metadata as JSON string')
    complete_parser.add_argument('--failed', action='store_true', help='Mark as failed')
    complete_parser.add_argument('--error', help='Error message (for failed tasks)')
    complete_parser.add_argument('--agent-name', help='Name of agent completing the task')
    complete_parser.add_argument('--execution-time', type=float, help='Execution time in seconds')
    complete_parser.set_defaults(func=handle_agent_complete)

    # Cleanup command
    cleanup_parser = agent_subparsers.add_parser('cleanup', help='Cleanup expired tasks')
    cleanup_parser.set_defaults(func=handle_agent_cleanup)

    # Return the subparsers so scheduled commands can be added
    return agent_subparsers if include_scheduled else None
