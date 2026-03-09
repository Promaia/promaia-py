"""
Daemon wrapper for running the calendar monitor as a background service.

Provides process management, signal handling, and logging infrastructure
for 24/7 operation via launchd (macOS) or systemd (Linux).
"""

import os
import sys
import signal
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CalendarMonitorDaemon:
    """Wrapper for running calendar monitor as a daemon process."""

    def __init__(
        self,
        pid_file: Optional[Path] = None,
        log_file: Optional[Path] = None,
        check_interval_minutes: int = 1,
        trigger_window_minutes: int = 5,
    ):
        """
        Initialize daemon.

        Args:
            pid_file: Path to PID file for process tracking
            log_file: Path to log file for daemon output
            check_interval_minutes: Calendar check interval
            trigger_window_minutes: Event trigger window (minutes after start time to allow late triggers)
        """
        # Default paths
        from promaia.utils.env_writer import get_data_dir, get_logs_dir
        data_dir = get_data_dir()
        data_dir.mkdir(exist_ok=True)

        self.pid_file = pid_file or (data_dir / "calendar_monitor.pid")
        self.log_file = log_file or (get_logs_dir() / "daemon.log")
        self.check_interval_minutes = check_interval_minutes
        self.trigger_window_minutes = trigger_window_minutes
        self.monitor = None
        self._shutdown = False

    def _write_pid(self):
        """Write current process ID to PID file."""
        with open(self.pid_file, 'w') as f:
            f.write(str(os.getpid()))
        logger.info(f"PID {os.getpid()} written to {self.pid_file}")

    def _remove_pid(self):
        """Remove PID file."""
        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
                logger.info(f"Removed PID file {self.pid_file}")
        except Exception as e:
            logger.warning(f"Failed to remove PID file: {e}")

    def _setup_logging(self):
        """Configure logging to central + daemon-specific log files."""
        from promaia.agents.feed_watchers import setup_agent_file_logging

        # Clear existing handlers to avoid duplicates on SIGHUP reload
        logging.getLogger().handlers.clear()

        # Central log (promaia.log) + daemon-specific log (daemon.log)
        setup_agent_file_logging(process_log_name="daemon")

        # Console handler for systemd/launchd
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        logging.getLogger().addHandler(console_handler)

        from promaia.utils.env_writer import get_logs_dir
        logs = get_logs_dir()
        logger.info("Logging configured")
        logger.info(f"Central log: {logs / 'promaia.log'}")
        logger.info(f"Daemon log:  {self.log_file}")

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            signame = signal.Signals(signum).name
            logger.info(f"Received signal {signame} ({signum})")
            self._shutdown = True
            if self.monitor:
                logger.info("Stopping calendar monitor...")
                self.monitor.stop()

        # Handle termination signals
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # Handle SIGHUP for log rotation
        def sighup_handler(signum, frame):
            logger.info("Received SIGHUP - reopening log file")
            self._setup_logging()

        signal.signal(signal.SIGHUP, sighup_handler)

        logger.info("Signal handlers configured")

    async def run(self):
        """Run the calendar monitor daemon."""
        try:
            # Setup infrastructure
            self._write_pid()
            self._setup_logging()
            self._setup_signal_handlers()

            logger.info("=" * 70)
            logger.info("Calendar Monitor Daemon Starting")
            logger.info("=" * 70)
            logger.info(f"PID: {os.getpid()}")
            logger.info(f"Check interval: {self.check_interval_minutes} minutes")
            logger.info(f"Trigger window: {self.trigger_window_minutes} minutes")
            logger.info(f"Started at: {datetime.now().isoformat()}")
            logger.info("=" * 70)

            # Import and start monitor
            from promaia.gcal.agent_calendar_monitor import AgentCalendarMonitor

            self.monitor = AgentCalendarMonitor(
                check_interval_minutes=self.check_interval_minutes,
                trigger_window_minutes=self.trigger_window_minutes,
            )

            # Run monitor (blocks until stopped)
            await self.monitor.start()

        except Exception as e:
            logger.error(f"Fatal error in daemon: {e}", exc_info=True)
            raise
        finally:
            logger.info("Calendar monitor daemon shutting down")
            self._remove_pid()
            logger.info("Shutdown complete")

    def start(self):
        """Start the daemon (main entry point)."""
        try:
            # Check for existing process
            if self.pid_file.exists():
                with open(self.pid_file) as f:
                    old_pid = int(f.read().strip())

                # Check if process is still running
                try:
                    os.kill(old_pid, 0)  # Signal 0 checks if process exists
                    logger.error(f"Daemon already running with PID {old_pid}")
                    logger.error(f"PID file: {self.pid_file}")
                    logger.error("Stop the existing daemon first: maia daemon stop")
                    sys.exit(1)
                except ProcessLookupError:
                    # Stale PID file
                    logger.warning(f"Removing stale PID file (PID {old_pid} not running)")
                    self._remove_pid()

            # Run the async daemon
            asyncio.run(self.run())

        except KeyboardInterrupt:
            logger.info("Daemon interrupted by user")
        except Exception as e:
            logger.error(f"Daemon failed: {e}", exc_info=True)
            sys.exit(1)


def get_daemon_status() -> dict:
    """
    Check daemon status.

    Returns:
        Dictionary with status information:
        - running (bool): Whether daemon is running
        - pid (int): Process ID if running
        - pid_file (Path): Path to PID file
        - log_file (Path): Path to log file
    """
    from promaia.utils.env_writer import get_data_dir, get_logs_dir
    pid_file = get_data_dir() / "calendar_monitor.pid"
    log_file = get_logs_dir() / "daemon.log"

    status = {
        "running": False,
        "pid": None,
        "pid_file": pid_file,
        "log_file": log_file,
    }

    if pid_file.exists():
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())

            # Check if process is running
            try:
                os.kill(pid, 0)
                status["running"] = True
                status["pid"] = pid
            except ProcessLookupError:
                # Stale PID file
                status["running"] = False
                status["stale_pid"] = pid
        except (ValueError, FileNotFoundError):
            pass

    return status


def stop_daemon() -> bool:
    """
    Stop the running daemon.

    Returns:
        True if daemon was stopped, False if not running
    """
    status = get_daemon_status()

    if not status["running"]:
        return False

    pid = status["pid"]
    logger.info(f"Stopping daemon (PID {pid})...")

    try:
        # Send SIGTERM for graceful shutdown
        os.kill(pid, signal.SIGTERM)

        # Wait for process to exit (up to 10 seconds)
        import time
        for i in range(20):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                logger.info(f"Daemon stopped successfully")
                return True

        # Force kill if still running
        logger.warning("Daemon did not stop gracefully, forcing...")
        os.kill(pid, signal.SIGKILL)
        return True

    except ProcessLookupError:
        # Already stopped
        return True
    except PermissionError:
        logger.error(f"Permission denied when stopping PID {pid}")
        return False


def main():
    """Entry point for direct daemon execution."""
    import argparse

    parser = argparse.ArgumentParser(description="Calendar Monitor Daemon")
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=1,
        help='Check interval in minutes (default: 1)'
    )
    parser.add_argument(
        '--window', '-w',
        type=int,
        default=5,
        help='Trigger window in minutes after start time (default: 5)'
    )

    args = parser.parse_args()

    daemon = CalendarMonitorDaemon(
        check_interval_minutes=args.interval,
        trigger_window_minutes=args.window,
    )
    daemon.start()


if __name__ == "__main__":
    main()
