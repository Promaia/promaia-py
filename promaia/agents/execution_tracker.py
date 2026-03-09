"""
Execution Tracker - Storage and monitoring for agent executions.
"""
import sqlite3
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AgentExecution:
    """Record of a single agent execution."""
    id: Optional[int] = None
    agent_name: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "pending"  # pending, running, completed, failed
    iterations_used: int = 0
    tokens_used: int = 0
    cost_estimate: float = 0.0
    output_notion_page_id: Optional[str] = None
    error_message: Optional[str] = None
    context_summary: Optional[str] = None  # Brief summary of what was processed


class ExecutionTracker:
    """
    Tracks agent executions in SQLite database.

    Handles execution logging, status tracking, and monitoring
    for scheduled agents.
    """

    def __init__(self, db_path: str = None, timeout: float = 30.0):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self.timeout = timeout
        self._ensure_tables()

    def _get_connection(self):
        """Get SQLite connection with proper timeout settings."""
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for better concurrency
        return conn

    def _ensure_tables(self):
        """Create agent_executions table if it doesn't exist."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Agent executions table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS agent_executions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_name TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        status TEXT NOT NULL,

                        iterations_used INTEGER DEFAULT 0,
                        tokens_used INTEGER DEFAULT 0,
                        cost_estimate REAL DEFAULT 0.0,

                        output_notion_page_id TEXT,
                        error_message TEXT,
                        context_summary TEXT,

                        created_at TEXT NOT NULL
                    )
                """)

                # Create indexes for common queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_executions_agent
                    ON agent_executions(agent_name, started_at DESC)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_executions_status
                    ON agent_executions(status)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_executions_date
                    ON agent_executions(started_at DESC)
                """)

                conn.commit()
                logger.info("✅ Agent executions table initialized")

        except Exception as e:
            logger.error(f"❌ Failed to initialize agent_executions table: {e}")
            raise

    def start_execution(self, agent_name: str) -> int:
        """
        Record the start of an agent execution.

        Args:
            agent_name: Name of the agent

        Returns:
            execution_id
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                now = datetime.now(timezone.utc).isoformat()

                cursor.execute("""
                    INSERT INTO agent_executions (
                        agent_name, started_at, status, created_at
                    ) VALUES (?, ?, ?, ?)
                """, (
                    agent_name,
                    now,
                    "running",
                    now
                ))

                execution_id = cursor.lastrowid
                conn.commit()
                logger.info(f"Started execution {execution_id} for agent '{agent_name}'")
                return execution_id

        except Exception as e:
            logger.error(f"Failed to record execution start: {e}")
            raise

    def complete_execution(
        self,
        execution_id: int,
        status: str = "completed",
        iterations_used: int = 0,
        tokens_used: int = 0,
        cost_estimate: float = 0.0,
        output_notion_page_id: Optional[str] = None,
        error_message: Optional[str] = None,
        context_summary: Optional[str] = None
    ):
        """
        Mark an execution as completed.

        Args:
            execution_id: ID of the execution
            status: Final status (completed, failed)
            iterations_used: Number of iterations performed
            tokens_used: Estimated tokens consumed
            cost_estimate: Estimated cost in USD
            output_notion_page_id: Page where results were written
            error_message: Error message if failed
            context_summary: Brief summary of what was processed
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                now = datetime.now(timezone.utc).isoformat()

                cursor.execute("""
                    UPDATE agent_executions
                    SET completed_at = ?,
                        status = ?,
                        iterations_used = ?,
                        tokens_used = ?,
                        cost_estimate = ?,
                        output_notion_page_id = ?,
                        error_message = ?,
                        context_summary = ?
                    WHERE id = ?
                """, (
                    now,
                    status,
                    iterations_used,
                    tokens_used,
                    cost_estimate,
                    output_notion_page_id,
                    error_message,
                    context_summary,
                    execution_id
                ))

                conn.commit()
                logger.info(f"Completed execution {execution_id} with status '{status}'")

        except Exception as e:
            logger.error(f"Failed to complete execution {execution_id}: {e}")
            raise

    def get_execution(self, execution_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific execution by ID."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM agent_executions WHERE id = ?
                """, (execution_id,))

                row = cursor.fetchone()
                if not row:
                    return None

                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))

        except Exception as e:
            logger.error(f"Failed to get execution {execution_id}: {e}")
            return None

    def list_executions(
        self,
        agent_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List agent executions with optional filtering.

        Args:
            agent_name: Filter by agent name
            status: Filter by status
            limit: Maximum number of results

        Returns:
            List of execution records
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                query = "SELECT * FROM agent_executions WHERE 1=1"
                params = []

                if agent_name:
                    query += " AND agent_name = ?"
                    params.append(agent_name)

                if status:
                    query += " AND status = ?"
                    params.append(status)

                query += " ORDER BY started_at DESC LIMIT ?"
                params.append(limit)

                cursor.execute(query, params)
                rows = cursor.fetchall()

                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

        except Exception as e:
            logger.error(f"Failed to list executions: {e}")
            return []

    def get_agent_stats(self, agent_name: str) -> Dict[str, Any]:
        """
        Get statistics for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            Dictionary with stats (total runs, success rate, avg cost, etc.)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Total executions
                cursor.execute("""
                    SELECT COUNT(*) FROM agent_executions WHERE agent_name = ?
                """, (agent_name,))
                total_runs = cursor.fetchone()[0]

                # Successful executions
                cursor.execute("""
                    SELECT COUNT(*) FROM agent_executions
                    WHERE agent_name = ? AND status = 'completed'
                """, (agent_name,))
                successful_runs = cursor.fetchone()[0]

                # Failed executions
                cursor.execute("""
                    SELECT COUNT(*) FROM agent_executions
                    WHERE agent_name = ? AND status = 'failed'
                """, (agent_name,))
                failed_runs = cursor.fetchone()[0]

                # Average cost
                cursor.execute("""
                    SELECT AVG(cost_estimate) FROM agent_executions
                    WHERE agent_name = ? AND cost_estimate > 0
                """, (agent_name,))
                avg_cost = cursor.fetchone()[0] or 0.0

                # Total cost
                cursor.execute("""
                    SELECT SUM(cost_estimate) FROM agent_executions
                    WHERE agent_name = ?
                """, (agent_name,))
                total_cost = cursor.fetchone()[0] or 0.0

                # Last run
                cursor.execute("""
                    SELECT started_at, status FROM agent_executions
                    WHERE agent_name = ?
                    ORDER BY started_at DESC LIMIT 1
                """, (agent_name,))
                last_run = cursor.fetchone()

                return {
                    "total_runs": total_runs,
                    "successful_runs": successful_runs,
                    "failed_runs": failed_runs,
                    "success_rate": (successful_runs / total_runs * 100) if total_runs > 0 else 0,
                    "avg_cost": round(avg_cost, 4),
                    "total_cost": round(total_cost, 4),
                    "last_run_at": last_run[0] if last_run else None,
                    "last_run_status": last_run[1] if last_run else None
                }

        except Exception as e:
            logger.error(f"Failed to get agent stats: {e}")
            return {}
