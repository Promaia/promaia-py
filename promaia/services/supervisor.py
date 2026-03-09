"""
Service supervisor — wraps a child process and watches services.json
for enable/disable and restart changes.

Usage:
    python -m promaia.services.supervisor <service-name> -- <command...>

The supervisor:
  1. Reads maia-data/services.json to check if the service is enabled
  2. If enabled, spawns the child command as a subprocess
  3. Polls services.json every 5 seconds for mtime changes
  4. Starts/stops the child when the enabled flag changes
  5. Restarts the child when restart_requested timestamp changes
  6. Restarts the child if it crashes while enabled (with backoff)
  7. Forwards SIGTERM/SIGINT to the child for graceful shutdown
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _log(service_name: str, msg: str) -> None:
    """Log a message with supervisor prefix to stderr."""
    print(f"[supervisor:{service_name}] {msg}", file=sys.stderr, flush=True)


def _get_config_path() -> Path:
    """Resolve the path to services.json."""
    from promaia.utils.env_writer import get_data_dir
    return get_data_dir() / "services.json"


def _read_service_config(config_path: Path, service_name: str) -> dict:
    """Read the config block for a service from services.json.

    Returns a dict with at least {"enabled": True/False}.
    Defaults to enabled if the file is missing or unreadable.
    """
    if not config_path.exists():
        return {"enabled": True}
    try:
        cfg = json.loads(config_path.read_text())
        return cfg.get(service_name, {"enabled": True})
    except (json.JSONDecodeError, OSError):
        return {"enabled": True}


def _is_enabled(config_path: Path, service_name: str) -> bool:
    """Check if a service is enabled in the config file."""
    return _read_service_config(config_path, service_name).get("enabled", True)


def _get_mtime(config_path: Path) -> float:
    """Get the modification time of the config file, or 0 if missing."""
    try:
        return config_path.stat().st_mtime
    except OSError:
        return 0.0


def main() -> None:
    # Parse: supervisor <service-name> -- <command...>
    args = sys.argv[1:]
    if len(args) < 3 or "--" not in args:
        print(
            "Usage: python -m promaia.services.supervisor <service-name> -- <command...>",
            file=sys.stderr,
        )
        sys.exit(1)

    sep_idx = args.index("--")
    service_name = args[0]
    child_cmd = args[sep_idx + 1 :]

    if not child_cmd:
        print("No command specified after '--'", file=sys.stderr)
        sys.exit(1)

    config_path = _get_config_path()
    child: subprocess.Popen | None = None
    shutting_down = False

    def start_child() -> subprocess.Popen | None:
        nonlocal child
        if child is not None:
            return child
        _log(service_name, f"starting: {' '.join(child_cmd)}")
        child = subprocess.Popen(child_cmd)
        return child

    def stop_child() -> None:
        nonlocal child
        if child is None:
            return
        _log(service_name, "stopping child")
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _log(service_name, "child did not exit in 30s, killing")
            child.kill()
            child.wait()
        child = None

    def on_signal(signum: int, frame) -> None:
        nonlocal shutting_down
        shutting_down = True
        sig_name = signal.Signals(signum).name
        _log(service_name, f"received {sig_name}, shutting down")
        stop_child()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    # Initial state
    enabled = _is_enabled(config_path, service_name)
    last_mtime = _get_mtime(config_path)
    svc_cfg = _read_service_config(config_path, service_name)
    last_restart_requested = svc_cfg.get("restart_requested", 0)

    if enabled:
        start_child()
    else:
        _log(service_name, "disabled in services.json, idling")

    # Poll loop
    restart_backoff = 0
    while not shutting_down:
        time.sleep(5)

        # Check for config changes
        mtime = _get_mtime(config_path)
        if mtime != last_mtime:
            last_mtime = mtime
            svc_cfg = _read_service_config(config_path, service_name)

            # Check enable/disable changes
            new_enabled = svc_cfg.get("enabled", True)
            if new_enabled != enabled:
                enabled = new_enabled
                if enabled:
                    _log(service_name, "enabled in services.json")
                    start_child()
                    restart_backoff = 0
                else:
                    _log(service_name, "disabled in services.json")
                    stop_child()

            # Check for restart request
            restart_requested = svc_cfg.get("restart_requested", 0)
            if restart_requested != last_restart_requested:
                last_restart_requested = restart_requested
                if child is not None and enabled:
                    _log(service_name, "restart requested via services.json")
                    stop_child()
                    start_child()
                    restart_backoff = 0

        # Restart crashed child if still enabled
        if child is not None and child.poll() is not None:
            exit_code = child.returncode
            child = None
            if enabled and not shutting_down:
                restart_backoff = min(restart_backoff + 5, 60)
                _log(
                    service_name,
                    f"child exited with code {exit_code}, "
                    f"restarting in {restart_backoff}s",
                )
                time.sleep(restart_backoff)
                if enabled and not shutting_down:
                    start_child()


if __name__ == "__main__":
    main()
