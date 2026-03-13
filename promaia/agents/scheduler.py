"""
Agent Scheduler - Runs scheduled agents at specified intervals using asyncio.
"""
import asyncio
import logging
import signal
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set
from pathlib import Path

from promaia.agents.agent_config import load_agents, AgentConfig
from promaia.agents.executor import AgentExecutor

logger = logging.getLogger(__name__)


class AgentScheduler:
    """
    Schedules and runs agents at their configured intervals.

    Uses asyncio for lightweight concurrent execution without additional dependencies.
    """

    def __init__(self):
        """Initialize the scheduler."""
        self.running = False
        self.tasks: Dict[str, asyncio.Task] = {}
        self.shutdown_event = asyncio.Event()

    async def start(self):
        """
        Start the scheduler and run all enabled agents.

        This is the main entry point that:
        1. Loads all agent configurations
        2. Creates interval tasks for each enabled agent
        3. Runs until shutdown is requested
        """
        logger.info("🚀 Starting Agent Scheduler...")

        self.running = True

        # Load agents
        agents = load_agents()
        enabled_agents = [a for a in agents if a.enabled]

        if not enabled_agents:
            logger.info("No scheduled agents found")
        else:
            logger.info(f"📋 Found {len(enabled_agents)} enabled agents")

            # Create tasks for each agent
            for agent in enabled_agents:
                if not agent.interval_minutes:
                    logger.warning(f"   ⚠ Skipping '{agent.name}': no interval_minutes set (schedule-based execution not yet supported)")
                    continue
                task = asyncio.create_task(self._run_agent_loop(agent))
                self.tasks[agent.name] = task
                logger.info(f"   ✓ Scheduled '{agent.name}' (every {agent.interval_minutes} min)")

        # Start Shopify sync loop if any Shopify databases are configured
        shopify_task = asyncio.create_task(self._shopify_sync_loop())
        self.tasks['_shopify_sync'] = shopify_task

        logger.info("✅ Scheduler started. Press Ctrl+C to stop.\n")

        # Wait for shutdown signal
        await self.shutdown_event.wait()

        # Cleanup
        await self._shutdown()

    async def _run_agent_loop(self, agent: AgentConfig):
        """
        Run an agent in a loop at its configured interval.

        Args:
            agent: The agent configuration
        """
        interval_seconds = agent.interval_minutes * 60

        logger.info(f"🔄 Starting loop for '{agent.name}'")

        while self.running:
            try:
                # Run the agent
                logger.info(f"\n⏰ Triggering '{agent.name}' (interval: {agent.interval_minutes}m)")

                executor = AgentExecutor(agent)
                result = await executor.execute()

                if result['success']:
                    logger.info(f"✅ '{agent.name}' completed successfully")

                    if result.get('metrics'):
                        metrics = result['metrics']
                        logger.info(
                            f"   Metrics: {metrics.get('iterations_used', 0)} iterations, "
                            f"{metrics.get('tokens_used', 0):,} tokens, "
                            f"${metrics.get('cost_estimate', 0):.4f}"
                        )
                else:
                    logger.error(f"❌ '{agent.name}' failed: {result.get('error')}")

            except Exception as e:
                logger.error(f"❌ Error running '{agent.name}': {e}")

            # Wait for next interval
            if self.running:
                logger.info(f"⏳ '{agent.name}' sleeping for {agent.interval_minutes} minutes...")
                try:
                    await asyncio.sleep(interval_seconds)
                except asyncio.CancelledError:
                    logger.info(f"🛑 '{agent.name}' loop cancelled")
                    break

    async def _shopify_sync_loop(self):
        """Sync all Shopify databases every 60 seconds."""
        from promaia.connectors import ConnectorRegistry

        logger.info("🛍️  Shopify sync loop started (every 60s)")

        while self.running:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            shopify_dbs = [
                db for db in (db_manager.get_database(name) for name in db_manager.list_databases())
                if db and db.source_type == 'shopify' and db.sync_enabled
            ]

            for db_config in shopify_dbs:
                try:
                    connector = ConnectorRegistry.get_connector(
                        'shopify', db_config.to_dict()
                    )
                    if not connector:
                        continue

                    from promaia.connectors.base import DateRangeFilter
                    date_filter = DateRangeFilter(
                        property_name='updated_at',
                        days_back=db_config.default_days or 30,
                    )

                    result = await connector.sync_to_local_unified(
                        storage=None,
                        db_config=db_config,
                        date_filter=date_filter,
                    )

                    if result.pages_saved > 0:
                        logger.info(
                            f"🛍️  {db_config.get_qualified_name()}: "
                            f"{result.pages_saved} saved ({result.duration_seconds:.1f}s)"
                        )
                except Exception as e:
                    logger.error(f"🛍️  Shopify sync error ({db_config.get_qualified_name()}): {e}")

            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

    async def _shutdown(self):
        """Gracefully shutdown all running tasks."""
        logger.info("\n🛑 Shutting down scheduler...")

        self.running = False

        # Cancel all tasks
        for name, task in self.tasks.items():
            logger.info(f"   Cancelling '{name}'...")
            task.cancel()

        # Wait for all tasks to complete
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

        logger.info("✅ Scheduler stopped")

    def stop(self):
        """Signal the scheduler to stop."""
        logger.info("📢 Stop signal received")
        self.shutdown_event.set()


# Global scheduler instance for signal handling
_scheduler: Optional[AgentScheduler] = None


def _handle_shutdown_signal(signum, frame):
    """Handle shutdown signals (SIGINT, SIGTERM)."""
    if _scheduler:
        _scheduler.stop()


async def run_scheduler():
    """
    Run the agent scheduler.

    This is the main entry point for the scheduler daemon.
    """
    global _scheduler

    # Setup signal handlers
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    _scheduler = AgentScheduler()

    try:
        await _scheduler.start()
    except KeyboardInterrupt:
        logger.info("\n⚠️  Keyboard interrupt received")
        _scheduler.stop()
        await _scheduler._shutdown()


def run_scheduler_sync():
    """
    Synchronous entry point for the scheduler.

    Use this from CLI commands.
    """
    asyncio.run(run_scheduler())


# PID file management for daemon control
from promaia.utils.env_writer import get_data_dir as _get_data_dir
PID_FILE = _get_data_dir() / "scheduler.pid"


def write_pid_file():
    """Write the current process ID to the PID file."""
    import os

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    logger.info(f"📝 PID file written: {PID_FILE}")


def read_pid_file() -> Optional[int]:
    """Read the process ID from the PID file."""
    if not PID_FILE.exists():
        return None

    try:
        with open(PID_FILE, 'r') as f:
            return int(f.read().strip())
    except Exception as e:
        logger.error(f"Error reading PID file: {e}")
        return None


def remove_pid_file():
    """Remove the PID file."""
    if PID_FILE.exists():
        PID_FILE.unlink()
        logger.info(f"🗑️  PID file removed: {PID_FILE}")


def is_scheduler_running() -> bool:
    """Check if the scheduler is currently running."""
    import os

    pid = read_pid_file()

    if not pid:
        return False

    # Check if process exists
    try:
        os.kill(pid, 0)  # Doesn't actually kill, just checks if process exists
        return True
    except OSError:
        # Process doesn't exist, clean up stale PID file
        remove_pid_file()
        return False


def stop_scheduler():
    """Stop the running scheduler daemon."""
    import os

    pid = read_pid_file()

    if not pid:
        logger.info("No scheduler is running")
        return False

    try:
        # Send SIGTERM to gracefully stop
        os.kill(pid, signal.SIGTERM)
        logger.info(f"✅ Sent stop signal to scheduler (PID: {pid})")

        # Wait a bit for graceful shutdown
        import time
        time.sleep(2)

        # Check if still running
        try:
            os.kill(pid, 0)
            logger.warning(f"⚠️  Scheduler still running, sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
        except OSError:
            # Process stopped
            pass

        remove_pid_file()
        return True

    except ProcessLookupError:
        logger.info("Scheduler process not found (already stopped)")
        remove_pid_file()
        return False
    except Exception as e:
        logger.error(f"Error stopping scheduler: {e}")
        return False


async def run_scheduler_daemon():
    """
    Run the scheduler as a background daemon.

    This handles PID file management and proper cleanup.
    """
    # Check if already running
    if is_scheduler_running():
        logger.error("❌ Scheduler is already running")
        return

    # Write PID file
    write_pid_file()

    try:
        await run_scheduler()
    finally:
        # Cleanup PID file on exit
        remove_pid_file()


def run_scheduler_daemon_sync():
    """
    Synchronous entry point for the scheduler daemon.

    Use this from CLI commands.
    """
    asyncio.run(run_scheduler_daemon())
