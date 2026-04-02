# Agent Bridge Quickstart

This guide shows how promaia can submit tasks to external agents (like Claude Code) using a local SQLite database.

## Architecture

```
┌──────────────┐                           ┌──────────────┐
│   promaia    │                           │ Claude Code  │
│              │                           │              │
│ 1. Detects   │──┐                    ┌──│ 3. Polls DB  │
│    signal    │  │                    │  │    for tasks │
│              │  │   SQLite Database  │  │              │
│ 2. Submits   │  │  (data/hybrid_    │  │ 4. Executes  │
│    task      │──┼──>metadata.db)───┼─>│    work      │
│              │  │                    │  │              │
│ 6. Retrieves │<─┘                    └──│ 5. Submits   │
│    result    │                           │    result    │
└──────────────┘                           └──────────────┘
```

## Quick Start

### 1. Submit a Task from promaia

```bash
# Simple task
python -m promaia agent submit \
  --type "general" \
  --workspace "koii" \
  --instructions "Review and edit this draft email"

# Task with context
python -m promaia agent submit \
  --type "email_draft" \
  --workspace "koii" \
  --instructions "Review this email draft and improve the tone" \
  --context '{"draft_id": "abc123", "thread_id": "xyz789"}' \
  --draft-id "abc123"
```

### 2. List Pending Tasks

```bash
# See all pending tasks
python -m promaia agent pending

# Filter by workspace
python -m promaia agent pending --workspace koii

# Full list with filters
python -m promaia agent list --status pending --type email_draft
```

### 3. Get Task Details

```bash
# Show full task details including context
python -m promaia agent show <task-id>
```

### 4. Mark Task as Started

```bash
# External agent marks task as in-progress
python -m promaia agent start <task-id>
```

### 5. Submit Results

```bash
# Success result
python -m promaia agent complete <task-id> \
  --result '{"changes": "Improved email tone", "draft_updated": true}' \
  --agent-name "Claude Code"

# Failed result
python -m promaia agent complete <task-id> \
  --result '{"error_details": "Missing context"}' \
  --failed \
  --error "Failed to complete task" \
  --agent-name "Claude Code"
```

## Task Types

The system supports these task types:

- `email_draft` - Review/edit email drafts
- `code_change` - Make code modifications
- `query_results` - Process query results
- `content_write` - Write/edit content
- `sync_operation` - Handle sync tasks
- `general` - General purpose tasks

## Task Context

Tasks include a `context` field (JSON) that can contain:

```json
{
  "draft_id": "uuid",           // Related email draft
  "thread_id": "gmail-thread",  // Gmail thread ID
  "query_results": [...],       // Database query results
  "workspace_data": {...},      // Workspace-specific data
  "files": ["path1", "path2"],  // Related files
  "any_custom_data": "..."      // Any other context needed
}
```

## Integration Example

### promaia Side (Task Submission)

Here's how you'd integrate task submission into promaia's email processor:

```python
from promaia.external_agent import TaskManager, TaskType

# In email processor
task_manager = TaskManager()

task = AgentTask.create(
    task_type=TaskType.EMAIL_DRAFT,
    workspace=workspace,
    instructions=f"Review and improve this email draft reply to {sender}",
    context={
        "draft_id": draft_id,
        "thread_id": thread_id,
        "inbound_email": {
            "subject": email.subject,
            "from": email.sender,
            "body": email.body
        },
        "draft_body": generated_draft
    },
    related_draft_id=draft_id,
    related_thread_id=thread_id
)

task_id = task_manager.submit_task(task)
print(f"✅ Task submitted for external review: {task_id}")
```

### Claude Code Side (Task Polling)

Claude Code (or any external agent) can poll for tasks:

```python
from promaia.external_agent import TaskManager, TaskStatus

task_manager = TaskManager()

# Get pending tasks
tasks = task_manager.get_pending_tasks(workspace="koii")

for task in tasks:
    print(f"Found task: {task.task_id}")
    print(f"Instructions: {task.instructions}")
    print(f"Context: {task.context}")

    # Mark as started
    task_manager.update_task_status(task.task_id, TaskStatus.IN_PROGRESS)

    # Do the work...
    result_data = {"changes": "Made improvements"}

    # Submit result
    result = TaskResult.create_success(
        task_id=task.task_id,
        result_data=result_data,
        agent_name="Claude Code"
    )
    task_manager.save_result(result)
```

## Database Location

Tasks are stored in: `data/hybrid_metadata.db`

Tables:
- `agent_tasks` - Task definitions and status
- `agent_results` - Task results from external agents

## CLI Commands Reference

```bash
# Submit task
maia agent submit --type TYPE --workspace WS --instructions "..." [--context JSON]

# List tasks
maia agent list [--workspace WS] [--status STATUS] [--type TYPE]

# Show task
maia agent show TASK_ID

# Pending tasks
maia agent pending [--workspace WS]

# Start task
maia agent start TASK_ID

# Complete task
maia agent complete TASK_ID --result JSON [--failed] [--error MSG]

# Cleanup expired
maia agent cleanup
```

## Next Steps

1. **promaia Integration**: Add task submission to email processor, Discord handler, etc.
2. **Claude Code Integration**: Build polling mechanism in Claude Code
3. **Result Processing**: Handle results in promaia (update drafts, save files, etc.)
4. **Web UI**: Optional web interface for task monitoring

## Benefits

- **Asynchronous**: promaia doesn't block waiting for external agents
- **Simple**: Just SQLite, no web server required
- **Flexible**: Any system can read/write tasks
- **Persistent**: Tasks survive restarts
- **Trackable**: Full history of tasks and results
