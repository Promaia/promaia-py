"""
External Agent API - Endpoints for bidirectional communication with external agents.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from datetime import datetime

from promaia.external_agent import TaskManager, AgentTask, TaskResult, TaskType, TaskStatus

router = APIRouter()
task_manager = TaskManager()


# Request/Response Models
class TaskSubmitRequest(BaseModel):
    """Request to submit a new task."""
    task_type: str
    workspace: str
    instructions: str
    context: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
    expires_in_hours: Optional[int] = 24
    related_draft_id: Optional[str] = None
    related_thread_id: Optional[str] = None


class TaskSubmitResponse(BaseModel):
    """Response from task submission."""
    task_id: str
    status: str
    created_at: str
    expires_at: Optional[str] = None
    message: str


class TaskStatusResponse(BaseModel):
    """Response for task status query."""
    task_id: str
    task_type: str
    workspace: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    expires_at: Optional[str] = None
    related_draft_id: Optional[str] = None
    related_thread_id: Optional[str] = None
    progress_notes: List[str]


class TaskDetailResponse(TaskStatusResponse):
    """Full task details including context."""
    instructions: str
    context: Dict[str, Any]
    metadata: Dict[str, Any]


class ResultSubmitRequest(BaseModel):
    """Request to submit a task result."""
    task_id: str
    status: str  # "completed" or "failed"
    result_data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    agent_name: Optional[str] = None
    agent_version: Optional[str] = None
    execution_time_seconds: Optional[float] = None


class ResultSubmitResponse(BaseModel):
    """Response from result submission."""
    task_id: str
    message: str


class TaskListResponse(BaseModel):
    """Response for task list query."""
    tasks: List[TaskStatusResponse]
    total: int


# Endpoints

@router.post("/tasks/submit", response_model=TaskSubmitResponse)
async def submit_task(request: TaskSubmitRequest):
    """
    Submit a new task for an external agent to execute.

    This is how promaia tells an external agent (like Claude Code)
    that there's work to be done.
    """
    try:
        # Validate task type
        try:
            task_type = TaskType(request.task_type)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid task_type: {request.task_type}. Valid types: {[t.value for t in TaskType]}"
            )

        # Create task
        task = AgentTask.create(
            task_type=task_type,
            workspace=request.workspace,
            instructions=request.instructions,
            context=request.context,
            metadata=request.metadata,
            expires_in_hours=request.expires_in_hours,
            related_draft_id=request.related_draft_id,
            related_thread_id=request.related_thread_id
        )

        # Submit to task manager
        task_id = task_manager.submit_task(task)

        return TaskSubmitResponse(
            task_id=task_id,
            status=task.status.value,
            created_at=task.created_at.isoformat(),
            expires_at=task.expires_at.isoformat() if task.expires_at else None,
            message=f"Task {task_id} submitted successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit task: {str(e)}")


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(task_id: str):
    """
    Get full details of a specific task.

    This is how an external agent retrieves a task with all its context.
    """
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return TaskDetailResponse(
        task_id=task.task_id,
        task_type=task.task_type.value,
        workspace=task.workspace,
        status=task.status.value,
        instructions=task.instructions,
        context=task.context,
        metadata=task.metadata,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        expires_at=task.expires_at.isoformat() if task.expires_at else None,
        related_draft_id=task.related_draft_id,
        related_thread_id=task.related_thread_id,
        progress_notes=task.progress_notes
    )


@router.get("/tasks/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    Get the status of a task (without full context).

    Lightweight endpoint for checking task progress.
    """
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return TaskStatusResponse(
        task_id=task.task_id,
        task_type=task.task_type.value,
        workspace=task.workspace,
        status=task.status.value,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        expires_at=task.expires_at.isoformat() if task.expires_at else None,
        related_draft_id=task.related_draft_id,
        related_thread_id=task.related_thread_id,
        progress_notes=task.progress_notes
    )


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    workspace: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: Optional[int] = 50
):
    """
    List tasks with optional filtering.

    Use this to discover pending tasks or check task history.
    """
    try:
        # Validate filters
        status_filter = TaskStatus(status) if status else None
        type_filter = TaskType(task_type) if task_type else None

        tasks = task_manager.list_tasks(
            workspace=workspace,
            status=status_filter,
            task_type=type_filter,
            limit=limit
        )

        task_responses = [
            TaskStatusResponse(
                task_id=task.task_id,
                task_type=task.task_type.value,
                workspace=task.workspace,
                status=task.status.value,
                created_at=task.created_at.isoformat(),
                started_at=task.started_at.isoformat() if task.started_at else None,
                completed_at=task.completed_at.isoformat() if task.completed_at else None,
                expires_at=task.expires_at.isoformat() if task.expires_at else None,
                related_draft_id=task.related_draft_id,
                related_thread_id=task.related_thread_id,
                progress_notes=task.progress_notes
            )
            for task in tasks
        ]

        return TaskListResponse(
            tasks=task_responses,
            total=len(task_responses)
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid filter value: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list tasks: {str(e)}")


@router.get("/tasks/pending", response_model=TaskListResponse)
async def get_pending_tasks(workspace: Optional[str] = None):
    """
    Get all pending tasks (convenience endpoint).

    This is what an external agent polls to find new work.
    """
    tasks = task_manager.get_pending_tasks(workspace=workspace)

    task_responses = [
        TaskStatusResponse(
            task_id=task.task_id,
            task_type=task.task_type.value,
            workspace=task.workspace,
            status=task.status.value,
            created_at=task.created_at.isoformat(),
            started_at=task.started_at.isoformat() if task.started_at else None,
            completed_at=task.completed_at.isoformat() if task.completed_at else None,
            expires_at=task.expires_at.isoformat() if task.expires_at else None,
            related_draft_id=task.related_draft_id,
            related_thread_id=task.related_thread_id,
            progress_notes=task.progress_notes
        )
        for task in tasks
    ]

    return TaskListResponse(
        tasks=task_responses,
        total=len(task_responses)
    )


@router.post("/tasks/{task_id}/start")
async def start_task(task_id: str):
    """
    Mark a task as started.

    An external agent calls this when it begins working on a task.
    """
    task = task_manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.status != TaskStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Task {task_id} cannot be started (current status: {task.status.value})"
        )

    task_manager.update_task_status(task_id, TaskStatus.IN_PROGRESS, "Task started by external agent")

    return {"task_id": task_id, "status": "in_progress", "message": "Task started"}


@router.post("/tasks/{task_id}/result", response_model=ResultSubmitResponse)
async def submit_result(task_id: str, request: ResultSubmitRequest):
    """
    Submit a result for a completed (or failed) task.

    This is how an external agent returns results back to promaia.
    """
    try:
        # Verify task exists
        task = task_manager.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Verify task IDs match
        if request.task_id != task_id:
            raise HTTPException(
                status_code=400,
                detail="Task ID in request body does not match URL parameter"
            )

        # Parse status
        try:
            status = TaskStatus(request.status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {request.status}"
            )

        # Create result
        if status == TaskStatus.COMPLETED:
            result = TaskResult.create_success(
                task_id=task_id,
                result_data=request.result_data,
                metadata=request.metadata,
                agent_name=request.agent_name,
                execution_time=request.execution_time_seconds
            )
        else:
            result = TaskResult.create_failure(
                task_id=task_id,
                error_message=request.error_message or "Task failed",
                partial_data=request.result_data,
                metadata=request.metadata,
                agent_name=request.agent_name
            )

        # Save result
        task_manager.save_result(result)

        return ResultSubmitResponse(
            task_id=task_id,
            message=f"Result submitted successfully for task {task_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit result: {str(e)}")


@router.get("/tasks/{task_id}/result")
async def get_result(task_id: str):
    """
    Get the result of a completed task.

    This is how promaia retrieves results from an external agent.
    """
    result = task_manager.get_result(task_id)

    if not result:
        raise HTTPException(status_code=404, detail=f"No result found for task {task_id}")

    return {
        "task_id": result.task_id,
        "status": result.status.value,
        "result_data": result.result_data,
        "metadata": result.metadata,
        "error_message": result.error_message,
        "created_at": result.created_at.isoformat(),
        "agent_name": result.agent_name,
        "agent_version": result.agent_version,
        "execution_time_seconds": result.execution_time_seconds
    }


@router.post("/tasks/cleanup-expired")
async def cleanup_expired_tasks():
    """
    Cleanup expired tasks.

    Marks tasks past their expiration time as EXPIRED.
    """
    count = task_manager.cleanup_expired_tasks()
    return {"message": f"Marked {count} tasks as expired"}
