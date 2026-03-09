"""Log source watchers for the unified agent activity feed."""

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from promaia.agents.feed_events import FeedEvent, EventType
from promaia.agents.feed_formatters import extract_correlation_ids
from promaia.utils.env_writer import get_logs_dir

# ---------------------------------------------------------------------------
# Log paths — all processes write to the central log so ``maia feed`` can
# aggregate everything.  Each daemon / background process also gets its own
# log for easy debugging.
# ---------------------------------------------------------------------------
LOGS_DIR = get_logs_dir()
CENTRAL_LOG_PATH = LOGS_DIR / "promaia.log"

# Keep old name around so imports that referenced it still work.
SHARED_LOG_PATH = CENTRAL_LOG_PATH

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _make_file_handler(path: Path) -> logging.FileHandler:
    """Create a standard file handler."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    return handler


def setup_agent_file_logging(process_log_name: Optional[str] = None):
    """Configure logging for a background process.

    Every background process gets:
    1. A handler that writes to the **central** ``<data_dir>/logs/promaia.log``
       (this is what ``maia feed`` tails).
    2. Optionally, a **per-process** log file under ``<data_dir>/logs/``
       named ``<process_log_name>.log`` for easier debugging.

    Args:
        process_log_name: Optional name for a per-process log file
            (e.g. ``"daemon"`` → ``<data_dir>/logs/daemon.log``).
    """
    root = logging.getLogger()

    # Central log — everything goes here
    root.addHandler(_make_file_handler(CENTRAL_LOG_PATH))

    # Per-process log (if requested)
    if process_log_name:
        process_log_path = LOGS_DIR / f"{process_log_name}.log"
        root.addHandler(_make_file_handler(process_log_path))

    # Ensure the root logger level is low enough to pass INFO to the handlers
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)

    # Suppress noisy loggers that clutter the feed
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("notion_client").setLevel(logging.WARNING)


class LogFileWatcher:
    """Watches a log file and emits feed events."""

    def __init__(self, event_queue: asyncio.Queue, log_path: Optional[Path] = None):
        self.queue = event_queue
        self.log_path = log_path or CENTRAL_LOG_PATH
        self.active = True
        self.last_message = None  # Track last message to skip duplicates
        # Map goal_id -> agent_name so later events inherit the name
        self._goal_agent_map: dict[str, str] = {}

    async def watch(self):
        """Tail the log file and emit events."""
        # Wait for the log file to appear.  After a Docker restart the
        # file won't exist until the first background process writes to it.
        while self.active and not self.log_path.exists():
            await asyncio.sleep(1)

        if not self.active:
            return

        # Use tail -f to follow the log file.
        # -n 50: replay recent lines so the feed picks up initial
        # orchestrator events (goal creation, task planning) that may
        # have been written before the feed started tailing.
        process = await asyncio.create_subprocess_exec(
            'tail', '-f', '-n', '50', str(self.log_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            while self.active and process.returncode is None:
                line = await process.stdout.readline()
                if not line:
                    break

                line_str = line.decode('utf-8').strip()
                if line_str:
                    event = self._parse_log_line(line_str)
                    if event:
                        # Skip duplicates (same message appearing consecutively)
                        if event.message != self.last_message:
                            await self.queue.put(event)
                            self.last_message = event.message
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()

    def _parse_log_line(self, line: str) -> Optional[FeedEvent]:
        """Parse a log line into a FeedEvent.

        Expected format: YYYY-MM-DD HH:MM:SS - [logger.name -] LEVEL - message
        """
        # Regex pattern for standard Python logging format (with or without logger name)
        # Format 1: YYYY-MM-DD HH:MM:SS - logger.name - LEVEL - message
        pattern1 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?) - ([\w.]+) - (\w+) - (.+)'
        match = re.match(pattern1, line)

        if not match:
            # Format 2: YYYY-MM-DD HH:MM:SS - LEVEL - message (no logger name)
            pattern2 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?) - (\w+) - (.+)'
            match2 = re.match(pattern2, line)
            if match2:
                timestamp_str, level, message = match2.groups()
                logger_name = 'unknown'
            else:
                return None
        else:
            timestamp_str, logger_name, level, message = match.groups()


        # Parse timestamp
        try:
            # Handle both formats: with and without milliseconds
            if ',' in timestamp_str:
                timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
            else:
                timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            # Make timezone aware
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        except ValueError:
            timestamp = datetime.now(timezone.utc)

        # Extract correlation IDs from message
        goal_id, task_id = extract_correlation_ids(message)

        # Determine source from logger name
        source = self._determine_source(logger_name)

        # Determine event type
        event_type = self._determine_event_type(message, source)

        # Extract agent name from message content
        agent_name = self._extract_agent_name(message)

        # If we have a goal_id and agent_name, record the mapping
        if goal_id and agent_name:
            self._goal_agent_map[goal_id] = agent_name
        # If we have a goal_id but no agent_name, try to look it up
        elif goal_id and not agent_name:
            agent_name = self._goal_agent_map.get(goal_id)

        return FeedEvent(
            timestamp=timestamp,
            source=source,
            event_type=event_type,
            level=level,
            message=message,
            agent_name=agent_name,
            goal_id=goal_id,
            task_id=task_id,
            metadata={'logger': logger_name, 'raw_log': line}
        )

    def _determine_source(self, logger_name: str) -> str:
        """Determine the source component from logger name."""
        if 'daemon' in logger_name or 'calendar_monitor' in logger_name:
            return 'daemon'
        elif 'orchestrator' in logger_name:
            return 'orchestrator'
        elif 'executor' in logger_name:
            return 'executor'
        elif 'conversation' in logger_name:
            return 'conversation'
        elif 'sync' in logger_name:
            return 'sync'
        else:
            return logger_name.split('.')[-1] if '.' in logger_name else logger_name

    @staticmethod
    def _extract_agent_name(message: str) -> Optional[str]:
        """Extract agent name from log message content."""
        # Bracketed agent name: [quasar-3779] ... (agentic_turn feed logger)
        m = re.match(r'\[([^\]]+)\]\s', message)
        if m:
            return m.group(1).strip()
        # Orchestrator initialized for agent: Chief of Staff
        m = re.search(r'Orchestrator initialized for agent:\s*(.+)', message)
        if m:
            return m.group(1).strip()
        # Starting agent 'Chief of Staff' or Agent 'Chief of Staff'
        m = re.search(r"(?:Starting agent|Agent)\s+'([^']+)'", message)
        if m:
            return m.group(1).strip()
        # Using agent: Chief of Staff (optional parenthetical)
        m = re.search(r'Using agent:\s*([^(]+)', message)
        if m:
            return m.group(1).strip()
        # for agent chief-of-staff (conversation_manager pattern)
        m = re.search(r'for agent\s+(\S+)', message)
        if m:
            return m.group(1).strip()
        return None

    def _determine_event_type(self, message: str, source: str) -> EventType:
        """Determine event type from message content.

        Matches real promaia.log patterns — goal lifecycle comes from the
        orchestrator and agentic_turn feed logger.
        """
        # Goal lifecycle (orchestrator + agentic_turn)
        if re.search(r'Starting goal:', message):
            return EventType.AGENT_START
        if re.search(r'Goal \w+ marked as completed', message) or re.search(r'Goal completed successfully', message):
            return EventType.AGENT_COMPLETE
        # Agentic turn completion
        if re.search(r'Agentic turn complete:', message):
            return EventType.AGENT_COMPLETE

        # Task lifecycle
        if re.search(r'\[task:\w+\] Executing', message):
            return EventType.TASK_START
        if re.search(r'Task \w+ completed', message):
            return EventType.TASK_COMPLETE

        # Conversation
        if re.search(r'Conversation started:', message):
            return EventType.CONVERSATION_START
        if re.search(r'Conversation ended:|🏁 Ending conversation', message):
            return EventType.CONVERSATION_END

        # Messages (from log lines)
        # NOTE: Neither 💭 Agent: nor 📩 Message from lines are classified as
        # MESSAGE_SENT / MESSAGE_RECEIVED — the DB watcher provides the
        # canonical messages with full content.  The log watcher's versions
        # are truncated duplicates that cause noise (and show raw Slack IDs
        # instead of friendly names).

        # Calendar
        if 'triggered goal' in message.lower() or 'calendar event' in message.lower():
            return EventType.CALENDAR_TRIGGER

        # Tool calls (orchestrator format + agentic_turn feed logger format)
        if re.search(r'^🔧 Calling tool:', message):
            return EventType.TOOL_CALL
        # [agent-name] tool_name (params) — tool start from feed logger
        if re.search(r'^\[\S+\] [a-z][a-z_]+ \(', message):
            return EventType.TOOL_CALL
        # [agent-name] ✓ tool_name: summary — tool complete from feed logger
        if re.search(r'^\[\S+\] ✓ ', message):
            return EventType.TOOL_CALL

        return EventType.LOG_MESSAGE


class DatabaseWatcher:
    """Watches the conversations database for new messages."""

    def __init__(self, event_queue: asyncio.Queue):
        self.queue = event_queue
        from promaia.utils.env_writer import get_conversations_db_path
        self.db_path = get_conversations_db_path()
        self.last_check = datetime.now(timezone.utc)
        self.active = True
        # Track how many messages we've already seen per conversation so we
        # emit events for ALL new messages, not just messages[-1].
        self._seen_msg_counts: dict[str, int] = {}

    async def watch(self):
        """Poll the database for new conversation messages."""
        if not self.db_path.exists():
            # Database doesn't exist yet
            await asyncio.sleep(1)
            return

        while self.active:
            try:
                await self._check_for_updates()
            except Exception as e:
                logging.error(f"Error watching database: {e}")

            await asyncio.sleep(1)  # Poll every second

    async def _check_for_updates(self):
        """Check database for new messages since last check."""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        try:
            # Query for conversations updated since last check
            cursor.execute("""
                SELECT id, agent_id, messages, last_message_at
                FROM conversations
                WHERE last_message_at > ?
                ORDER BY last_message_at ASC
            """, (self.last_check.isoformat(),))

            for row in cursor.fetchall():
                conv_id, agent_id, messages_json, timestamp_str = row

                try:
                    messages = json.loads(messages_json)

                    # Emit events for ALL new messages (not just the latest).
                    # This handles the case where user msg + agent reply are
                    # written in a single save.
                    prev_count = self._seen_msg_counts.get(conv_id, 0)
                    new_messages = messages[prev_count:]
                    self._seen_msg_counts[conv_id] = len(messages)

                    for msg in new_messages:
                        event = self._create_message_event(
                            conv_id, agent_id, msg, timestamp_str
                        )
                        if event:
                            await self.queue.put(event)

                    # Update last check time
                    msg_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    if msg_time > self.last_check:
                        self.last_check = msg_time

                except (json.JSONDecodeError, KeyError) as e:
                    logging.debug(f"Error parsing conversation state: {e}")

        finally:
            conn.close()

    def _create_message_event(
        self,
        conv_id: str,
        agent_id: str,
        message: dict,
        timestamp_str: str
    ) -> Optional[FeedEvent]:
        """Create a FeedEvent from a conversation message."""
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except ValueError:
            timestamp = datetime.now(timezone.utc)

        role = message.get('role', 'user')
        content = message.get('content', '')

        # Extract text content from content blocks if present
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text_parts.append(block.get('text', ''))
            content = ' '.join(text_parts)

        # Skip trivially short or separator-only messages
        if not content or len(content.strip()) < 4 or content.strip().replace('-', '') == '':
            return None

        event_type = EventType.MESSAGE_SENT if role == 'assistant' else EventType.MESSAGE_RECEIVED

        return FeedEvent(
            timestamp=timestamp,
            source='conversation',
            event_type=event_type,
            level='INFO',
            message=content[:200] + '...' if len(content) > 200 else content,
            agent_name=agent_id,
            conversation_id=conv_id,
            metadata={'message': message, 'role': role}
        )


class LoggerCapture:
    """Captures logs from Python loggers and emits feed events."""

    def __init__(self, event_queue: asyncio.Queue):
        self.queue = event_queue
        self.active = True

    async def watch(self):
        """Install custom logging handler that captures logs."""
        handler = QueueHandler(self.queue)
        handler.setLevel(logging.INFO)

        # Attach to relevant loggers
        loggers = [
            'promaia.agents.orchestrator',
            'promaia.agents.executor',
            'promaia.agents.conversation_manager',
            'promaia.agents.daemon',
        ]

        for logger_name in loggers:
            logger = logging.getLogger(logger_name)
            logger.addHandler(handler)

        # Keep watcher alive
        while self.active:
            await asyncio.sleep(1)


class QueueHandler(logging.Handler):
    """Logging handler that emits events to an asyncio queue."""

    def __init__(self, event_queue: asyncio.Queue):
        super().__init__()
        self.queue = event_queue

    def emit(self, record):
        """Handle a log record by converting it to a FeedEvent."""
        try:
            event = self._parse_log_record(record)
            if event:
                # Create a task to put the event in the queue
                asyncio.create_task(self.queue.put(event))
        except Exception:
            self.handleError(record)

    def _parse_log_record(self, record: logging.LogRecord) -> Optional[FeedEvent]:
        """Parse a log record into a FeedEvent."""
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
        message = record.getMessage()

        # Extract correlation IDs
        goal_id, task_id = extract_correlation_ids(message)

        # Determine source
        source = self._determine_source(record.name)

        # Determine event type
        event_type = self._determine_event_type(message, source)

        return FeedEvent(
            timestamp=timestamp,
            source=source,
            event_type=event_type,
            level=record.levelname,
            message=message,
            goal_id=goal_id,
            task_id=task_id,
            metadata={'logger': record.name, 'pathname': record.pathname, 'lineno': record.lineno}
        )

    def _determine_source(self, logger_name: str) -> str:
        """Determine the source component from logger name."""
        if 'daemon' in logger_name or 'calendar_monitor' in logger_name:
            return 'daemon'
        elif 'orchestrator' in logger_name:
            return 'orchestrator'
        elif 'executor' in logger_name:
            return 'executor'
        elif 'conversation' in logger_name:
            return 'conversation'
        elif 'sync' in logger_name:
            return 'sync'
        else:
            return logger_name.split('.')[-1] if '.' in logger_name else logger_name

    def _determine_event_type(self, message: str, source: str) -> EventType:
        """Determine event type from message content."""
        message_lower = message.lower()

        if 'triggered goal' in message_lower or 'calendar event' in message_lower:
            return EventType.CALENDAR_TRIGGER
        elif 'starting agent' in message_lower or 'agent start' in message_lower:
            return EventType.AGENT_START
        elif 'completed successfully' in message_lower or 'agent complete' in message_lower:
            return EventType.AGENT_COMPLETE
        elif 'tool call' in message_lower:
            return EventType.TOOL_CALL
        elif 'query' in message_lower and source == 'executor':
            return EventType.QUERY_EXECUTE
        elif 'task:' in message and ('executing' in message_lower or 'starting' in message_lower):
            return EventType.TASK_START
        elif 'task:' in message and 'completed' in message_lower:
            return EventType.TASK_COMPLETE
        elif 'conversation' in message_lower and 'started' in message_lower:
            return EventType.CONVERSATION_START
        elif 'conversation' in message_lower and ('ended' in message_lower or 'completed' in message_lower):
            return EventType.CONVERSATION_END
        elif 'sync' in message_lower:
            return EventType.SYNC_OPERATION
        else:
            return EventType.LOG_MESSAGE
