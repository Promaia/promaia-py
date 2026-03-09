"""
External Agent Integration - Bidirectional bridge with Claude Code.

This module provides the infrastructure for promaia to communicate with
external AI agents like Claude Code, enabling task delegation and result handling.
"""

from .models import AgentTask, TaskResult, TaskStatus, TaskType
from .task_manager import TaskManager

__all__ = [
    'AgentTask',
    'TaskResult',
    'TaskStatus',
    'TaskType',
    'TaskManager'
]
