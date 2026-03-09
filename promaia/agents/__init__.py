"""
Promaia Agent System

Interval-based agents that monitor multiple sources, perform multi-step analysis,
and output results to Notion pages.
"""

from .agent_config import AgentConfig, load_agents, save_agent, delete_agent, get_agent
from .execution_tracker import ExecutionTracker, AgentExecution

# Optional imports:
# Some environments (minimal schedulers, test harnesses) may not have all
# heavyweight dependencies installed (dotenv/notion-client/etc.). Avoid making
# *import promaia.agents* crash just because optional integrations aren't present.
AgentExecutor = None  # type: ignore
execute_agent_sync = None  # type: ignore
NotionOutputWriter = None  # type: ignore
SyncNotionOutputWriter = None  # type: ignore
AgentScheduler = None  # type: ignore
run_scheduler_daemon_sync = None  # type: ignore
run_scheduler_daemon = None  # type: ignore
is_scheduler_running = None  # type: ignore
stop_scheduler = None  # type: ignore

try:  # pragma: no cover
    from .executor import AgentExecutor, execute_agent_sync  # type: ignore
except Exception:
    pass

try:  # pragma: no cover
    from .notion_writer import NotionOutputWriter, SyncNotionOutputWriter  # type: ignore
except Exception:
    pass

try:  # pragma: no cover
    from .scheduler import (  # type: ignore
        AgentScheduler,
        run_scheduler_daemon_sync,
        run_scheduler_daemon,
        is_scheduler_running,
        stop_scheduler,
    )
except Exception:
    pass

# Orchestrator imports (optional)
TaskQueue = None  # type: ignore
Task = None  # type: ignore
TaskType = None  # type: ignore
TaskStatus = None  # type: ignore
Goal = None  # type: ignore
Orchestrator = None  # type: ignore
Planner = None  # type: ignore

try:  # pragma: no cover
    from .task_queue import TaskQueue, Task, TaskType, TaskStatus, Goal  # type: ignore
except Exception:
    pass

try:  # pragma: no cover
    from .orchestrator import Orchestrator  # type: ignore
except Exception:
    pass

try:  # pragma: no cover
    from .planner import Planner  # type: ignore
except Exception:
    pass

__all__ = [
    'AgentConfig',
    'load_agents',
    'save_agent',
    'delete_agent',
    'get_agent',
    'ExecutionTracker',
    'AgentExecution',
]

# Expose optional names if available
if AgentExecutor is not None:
    __all__.append('AgentExecutor')
if execute_agent_sync is not None:
    __all__.append('execute_agent_sync')
if NotionOutputWriter is not None:
    __all__.append('NotionOutputWriter')
if SyncNotionOutputWriter is not None:
    __all__.append('SyncNotionOutputWriter')
if AgentScheduler is not None:
    __all__.append('AgentScheduler')
if run_scheduler_daemon_sync is not None:
    __all__.append('run_scheduler_daemon_sync')
if is_scheduler_running is not None:
    __all__.append('is_scheduler_running')
if stop_scheduler is not None:
    __all__.append('stop_scheduler')

# Orchestrator exports
if TaskQueue is not None:
    __all__.extend(['TaskQueue', 'Task', 'TaskType', 'TaskStatus', 'Goal'])
if Orchestrator is not None:
    __all__.append('Orchestrator')
if Planner is not None:
    __all__.append('Planner')
