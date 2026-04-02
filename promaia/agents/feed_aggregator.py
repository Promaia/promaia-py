"""Core feed aggregator that combines multiple log sources."""

import asyncio
import hashlib
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.rule import Rule
from rich.text import Text

from promaia.agents.feed_events import FeedEvent, EventType
from promaia.agents.feed_formatters import (
    classify_event,
    format_event,
    format_goal_banner,
    format_goal_complete_banner,
    format_task_checklist,
    format_task_header,
    format_spinner_text,
    format_idle_spinner,
    is_spinner_event,
    is_spinner_completion,
    Significance,
)
from promaia.agents.feed_watchers import LogFileWatcher, DatabaseWatcher


class FeedAggregator:
    """Aggregates events from multiple sources and displays them in real-time."""

    def __init__(self, verbose: bool = False, show_timestamps: bool = False,
                 stop_on_complete: bool = False):
        self.event_queue = asyncio.Queue()
        self.watchers = []
        self.active = True
        self.console = Console()
        self.verbose = verbose
        self.show_timestamps = show_timestamps
        self.stop_on_complete = stop_on_complete

        # Dedup cache: fingerprint -> timestamp
        self._seen: Dict[str, float] = {}
        self._DEDUP_WINDOW = 2.0   # seconds — group events in this window
        self._DEDUP_TTL = 10.0     # seconds — prune entries older than this

        # Phase tracking state
        self._current_goal_id: Optional[str] = None
        self._current_agent: Optional[str] = None
        self._goal_start_time: Optional[float] = None
        self._goal_description: Optional[str] = None
        self._task_count: int = 0
        self._tasks_completed: int = 0
        self._current_task_index: int = 0

        # Agent name resolution: goal_id -> agent_name
        self._goal_agent_map: Dict[str, str] = {}
        # Pending agent name from "Orchestrator initialized" (before goal_id is known)
        self._pending_agent_name: Optional[str] = None
        # Task list collected from "Added task" log lines (before banner prints)
        self._pending_tasks: list[str] = []
        # Whether we're waiting for "Created N task(s)" to print the deferred banner
        self._banner_deferred: bool = False
        self._deferred_banner_event: Optional[FeedEvent] = None
        # Track task_ids we've already printed headers for (dedup same task from multiple log lines)
        self._seen_task_ids: set[str] = set()
        # Track whether goal completion banner has been printed (suppress duplicates)
        self._goal_completed: bool = False
        # Track conversation_ids that have been ended (suppress duplicate CONVERSATION_END)
        self._ended_conversations: set[str] = set()

        # Live task checklist state (rendered in-place at bottom of terminal)
        self._live_tasks: list[dict] = []  # {"description": str, "completed": bool}
        self._show_checklist: bool = False
        self._spinner_text: Optional[str] = None  # current spinner message, or None for idle
        self._in_conversation: bool = False  # True between CONVERSATION_START and CONVERSATION_END
        self._awaiting_response: bool = False  # True after agent sends a message, until user replies
        self._seen_conv_msgs: set[str] = set()  # content keys for dedup of awaiting-state changes

    async def start_feed(self, filters: Dict[str, Any] = None):
        """Start the feed aggregator and display loop."""
        filters = filters or {}

        # Spawn all watchers
        self.watchers = [
            asyncio.create_task(self._watch_log_file()),
            asyncio.create_task(self._watch_database()),
        ]

        try:
            await self._display_loop(filters)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.active = False
            for watcher in self.watchers:
                watcher.cancel()
            await asyncio.gather(*self.watchers, return_exceptions=True)

    async def _watch_log_file(self):
        """Start the log file watcher."""
        watcher = LogFileWatcher(self.event_queue)
        watcher.active = self.active
        try:
            await watcher.watch()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Log file watcher error: {e}")

    async def _watch_database(self):
        """Start the database watcher."""
        watcher = DatabaseWatcher(self.event_queue)
        watcher.active = self.active
        try:
            await watcher.watch()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Database watcher error: {e}")

    # --- Live renderable ---

    def _build_live_renderable(self):
        """Build the composite Live renderable: rule + task checklist + spinner."""
        if self._spinner_text:
            spinner = format_spinner_text(self._spinner_text)
        elif self._awaiting_response:
            spinner = format_spinner_text("💬 Awaiting response...")
        elif self._current_goal_id:
            spinner = format_spinner_text("Working...")
        else:
            # Nothing active — hide the live renderable entirely
            return Text("")

        parts = [Rule(style="dim")]
        if self._show_checklist and self._live_tasks:
            parts.append(format_task_checklist(self._live_tasks))
        parts.append(spinner)
        return Group(*parts)

    # --- Bootstrap ---

    async def _bootstrap_active_goal(self, live, filters: Dict[str, Any]):
        """Load active goal/task state from the TaskQueue database.

        When the feed starts after the orchestrator has already created
        tasks (e.g. first run after Docker restart), the initial log
        lines are in the past.  This reads the current state from
        SQLite so the goal banner and task checklist are shown.
        """
        try:
            from promaia.utils.env_writer import get_db_path

            db_path = get_db_path()
            if not db_path.exists():
                return

            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Find the most recent active goal (filtered by agent if set)
                if filters.get('agent'):
                    cursor.execute(
                        "SELECT * FROM orchestrator_goals "
                        "WHERE status = 'active' AND agent_id = ? "
                        "ORDER BY created_at DESC LIMIT 1",
                        (filters['agent'],)
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM orchestrator_goals "
                        "WHERE status = 'active' "
                        "ORDER BY created_at DESC LIMIT 1"
                    )

                goal_row = cursor.fetchone()
                if not goal_row:
                    return

                # Skip stale goals (older than 10 minutes)
                try:
                    created_dt = datetime.fromisoformat(goal_row['created_at'])
                    age = (datetime.now(timezone.utc) - created_dt).total_seconds()
                    if age > 600:
                        return
                except (ValueError, TypeError):
                    pass

                goal_id = goal_row['id']
                agent_id = goal_row['agent_id']
                description = goal_row['description']

                # Get tasks for this goal
                cursor.execute(
                    "SELECT * FROM orchestrator_tasks "
                    "WHERE goal_id = ? ORDER BY created_at",
                    (goal_id,)
                )
                task_rows = cursor.fetchall()

                if not task_rows:
                    return

                # Populate feed state
                self._current_goal_id = goal_id
                self._current_agent = agent_id
                self._goal_agent_map[goal_id] = agent_id
                self._goal_start_time = time.monotonic()
                self._goal_description = description
                self._task_count = len(task_rows)
                self._tasks_completed = sum(
                    1 for t in task_rows if t['status'] == 'completed'
                )
                self._goal_completed = False

                # Print goal banner
                banner = format_goal_banner(agent_id, description, [])
                live.console.print(banner)

                # Populate live task checklist
                self._live_tasks = [
                    {
                        "description": t['description'],
                        "completed": t['status'] == 'completed',
                    }
                    for t in task_rows
                ]
                self._show_checklist = True

                # Check if there's an active conversation
                for t in task_rows:
                    if t['type'] == 'conversation' and t['status'] == 'running':
                        self._in_conversation = True
                        self._awaiting_response = True
                        break

                live.update(self._build_live_renderable())
                logging.debug(
                    f"Bootstrapped feed: goal={goal_id[:8]}, "
                    f"{len(task_rows)} tasks, {self._tasks_completed} completed"
                )
        except Exception as e:
            logging.debug(f"Could not bootstrap goal state: {e}")

    # --- Display loop ---

    async def _display_loop(self, filters: Dict[str, Any]):
        """Two-layer display: permanent prints + live renderable at bottom."""
        with Live(self._build_live_renderable(), console=self.console, refresh_per_second=4) as live:
            # Bootstrap: load active goal/task state from the TaskQueue DB
            # so the banner and checklist are shown even when the feed starts
            # after the orchestrator has already written its initial log lines.
            await self._bootstrap_active_goal(live, filters)

            while self.active:
                try:
                    event = await asyncio.wait_for(
                        self.event_queue.get(),
                        timeout=0.5,
                    )
                except asyncio.TimeoutError:
                    continue
                except (asyncio.CancelledError, KeyboardInterrupt):
                    return

                # --- Agent name resolution ---
                self._resolve_agent_name(event)

                # --- Collect "Added task" lines for deferred banner ---
                if self._handle_deferred_banner(event, live):
                    continue  # event was consumed by banner logic

                # --- Track conversation lifecycle (before filtering so state
                #     is always updated even if the event is deduped/filtered) ---
                if event.event_type == EventType.CONVERSATION_START:
                    self._in_conversation = True
                elif event.event_type == EventType.CONVERSATION_END:
                    self._in_conversation = False
                    self._awaiting_response = False

                # --- Track awaiting-response state ---
                # Don't gate on _in_conversation: user-initiated conversations
                # (via @mention) bypass CONVERSATION_START and create state
                # directly.  MESSAGE_SENT/RECEIVED from the DB watcher are
                # only emitted for real conversation messages, so it's safe
                # to enable tracking unconditionally.
                if event.event_type in (
                    EventType.MESSAGE_SENT, EventType.MESSAGE_RECEIVED
                ):
                    self._in_conversation = True
                    # Content-based dedup: skip duplicate deliveries of the same
                    # message from different watchers so they don't toggle state twice.
                    msg_key = f"{event.event_type.value}|{self._normalize_message(event.message)[:100]}"
                    if msg_key not in self._seen_conv_msgs:
                        self._seen_conv_msgs.add(msg_key)
                        if event.event_type == EventType.MESSAGE_SENT:
                            self._awaiting_response = True
                            self._spinner_text = "💬 Awaiting response..."
                        else:
                            self._awaiting_response = False
                            self._spinner_text = "💭 Agent is thinking..."
                        live.update(self._build_live_renderable())

                # Apply filters
                if not self._matches_filters(event, filters):
                    continue

                # Dedup — skip if we've seen this fingerprint recently
                if self._is_duplicate(event):
                    continue

                # Significance filter — hide DETAIL unless verbose
                # Always show errors/warnings and spinner completions
                sig = classify_event(event)
                if sig == Significance.DETAIL and not self.verbose and event.level != 'ERROR':
                    if not is_spinner_completion(event):
                        continue

                # --- Phase tracking: emit banners for goal/task transitions ---
                # Returns True if the event was consumed (banner/header printed)
                if self._handle_phase_transitions(event, live):
                    continue

                # --- Render the event ---
                if is_spinner_event(event) and not is_spinner_completion(event):
                    # When awaiting a human reply, preserve that state —
                    # don't let orchestrator polling messages override it.
                    if self._awaiting_response:
                        self._spinner_text = "💬 Awaiting response..."
                        live.update(self._build_live_renderable())
                    else:
                        # Simplify known spinner messages
                        spinner_msg = event.message
                        if '🧠 Planning:' in spinner_msg:
                            spinner_msg = "🧠 Planning..."
                        elif 'Waiting for async tasks' in spinner_msg:
                            spinner_msg = "💬 Conversation in progress..."
                        elif 'Loading context from' in spinner_msg:
                            spinner_msg = "📚 Loading context..."
                        elif 'Starting Claude SDK' in spinner_msg:
                            spinner_msg = "🤖 Starting agent..."
                        self._spinner_text = spinner_msg
                        live.update(self._build_live_renderable())
                else:
                    if is_spinner_completion(event):
                        # Promote: print the completed step permanently, reset spinner
                        formatted = format_event(event, show_timestamps=self.show_timestamps)
                        live.console.print(formatted)
                        self._spinner_text = None
                        live.update(self._build_live_renderable())
                    else:
                        # Normal permanent print above the spinner
                        formatted = format_event(event, show_timestamps=self.show_timestamps)
                        live.console.print(formatted)

    # --- Phase tracking ---

    def _resolve_agent_name(self, event: FeedEvent):
        """Resolve agent name from various sources.

        Maintains a goal_id → agent_name map and a pending agent name
        for the "Orchestrator initialized" → "Starting goal" sequence.
        """
        # "Orchestrator initialized for agent: Chief of Staff" — no goal_id yet
        m = re.search(r'Orchestrator initialized for agent:\s*(.+)', event.message)
        if m:
            self._pending_agent_name = m.group(1).strip()

        # If event already has an agent_name, record it
        if event.agent_name and event.goal_id:
            self._goal_agent_map[event.goal_id] = event.agent_name

        # If event has goal_id but no agent_name, try to fill it in
        if event.goal_id and not event.agent_name:
            if event.goal_id in self._goal_agent_map:
                event.agent_name = self._goal_agent_map[event.goal_id]
            elif self._pending_agent_name:
                event.agent_name = self._pending_agent_name
                self._goal_agent_map[event.goal_id] = self._pending_agent_name

        # Final fallback: use the current agent name if still unresolved
        if not event.agent_name and self._current_agent:
            event.agent_name = self._current_agent

    def _handle_deferred_banner(self, event: FeedEvent, live: Live) -> bool:
        """Handle deferred goal banner and "Added task" collection.

        Returns True if the event was consumed (should not be processed further).
        """
        # Collect "Added task xxx: type - description" lines
        task_match = re.search(r'Added task \w+: \w+ - (.+)', event.message)
        if task_match:
            self._pending_tasks.append(task_match.group(1).strip())
            return True  # consume — don't print as a regular event

        # Consume "📋 Planned N task(s)" when banner is deferred or already bootstrapped
        if (self._banner_deferred or self._show_checklist) and re.search(r'📋 Planned \d+ task', event.message):
            return True  # consume — the banner/checklist already shows the plan

        # Consume "Created N task(s)" when goal is already bootstrapped from DB
        if self._show_checklist and not self._banner_deferred and re.search(r'Created \d+ task', event.message):
            return True  # consume — checklist already populated

        # AGENT_START — defer the banner, wait for task list
        if event.event_type == EventType.AGENT_START:
            # If this goal was already bootstrapped from the DB, skip the
            # banner setup to avoid duplicates from tail -n replay.
            if event.goal_id and event.goal_id == self._current_goal_id and self._show_checklist:
                return True  # consume — already displaying this goal

            agent = event.agent_name or self._pending_agent_name or "agent"
            goal_desc = self._extract_goal_description(event.message)

            self._current_goal_id = event.goal_id
            self._current_agent = agent
            self._goal_start_time = time.monotonic()
            self._goal_description = goal_desc
            self._tasks_completed = 0
            self._current_task_index = 0
            self._pending_tasks = []
            self._banner_deferred = True
            self._deferred_banner_event = event
            self._goal_completed = False
            self._in_conversation = False
            self._awaiting_response = False
            self._seen_conv_msgs.clear()
            self._ended_conversations.clear()
            return True  # consume — banner printed when tasks arrive

        # "Created N task(s)" — print the deferred banner now (without task list)
        if self._banner_deferred and re.search(r'Created \d+ task', event.message):
            self._flush_deferred_banner(live)
            return True  # consume the "Created N task(s)" line

        # Flush deferred banner on any tool call or plan event (agentic_turn
        # path doesn't emit "Created N task(s)", so we flush on first activity)
        if self._banner_deferred and event.event_type in (
            EventType.TOOL_CALL, EventType.QUERY_EXECUTE,
        ):
            self._flush_deferred_banner(live)
            return False  # don't consume — let the tool call render

        # Also flush on plan events from the agentic_turn feed logger
        if self._banner_deferred and re.search(r'^\[\S+\] Plan:', event.message):
            self._flush_deferred_banner(live)
            return True  # consume the plan line (already shown in banner)

        return False

    def _flush_deferred_banner(self, live: Live):
        """Print the deferred goal banner and populate the task checklist."""
        agent = self._current_agent or "agent"
        tasks = self._pending_tasks[:]
        self._task_count = len(tasks) if tasks else 0

        # Print banner without tasks — the checklist goes in the live renderable
        banner = format_goal_banner(agent, self._goal_description or "", [])
        live.console.print(banner)

        # Populate live checklist
        self._live_tasks = [
            {"description": t, "completed": False} for t in tasks
        ]
        self._show_checklist = bool(tasks)
        self._spinner_text = None
        live.update(self._build_live_renderable())

        self._banner_deferred = False
        self._deferred_banner_event = None
        self._pending_agent_name = None

    def _handle_phase_transitions(self, event: FeedEvent, live: Live) -> bool:
        """Detect goal/task lifecycle events and print banners.

        Returns True if the event was consumed (should not be rendered again).
        """
        # Goal start — already handled by _handle_deferred_banner (AGENT_START is
        # consumed there).  But if no "Created N task(s)" ever fires (no planner),
        # the banner would be stuck.  So if we somehow reach here with AGENT_START,
        # print immediately.
        if event.event_type == EventType.AGENT_START:
            # Skip if already displaying this goal (bootstrapped from DB)
            if event.goal_id and event.goal_id == self._current_goal_id and self._show_checklist:
                return True  # consume — already displaying this goal

            agent = event.agent_name or self._current_agent or "agent"
            goal_desc = self._extract_goal_description(event.message)

            self._current_goal_id = event.goal_id
            self._current_agent = agent
            self._goal_start_time = time.monotonic()
            self._goal_description = goal_desc
            self._task_count = 0
            self._tasks_completed = 0
            self._current_task_index = 0
            self._goal_completed = False
            self._ended_conversations.clear()

            banner = format_goal_banner(agent, goal_desc, [])
            live.console.print(banner)
            return True  # consumed — don't also render "🤖 Starting agent"

        # Task start — print task header (dedup by task_id)
        if event.event_type == EventType.TASK_START:
            # Skip if we already printed a header for this task_id
            if event.task_id and event.task_id in self._seen_task_ids:
                return True  # consume duplicate
            if event.task_id:
                self._seen_task_ids.add(event.task_id)

            self._current_task_index += 1
            self._awaiting_response = False  # new task — no longer waiting
            task_desc = self._extract_task_description(event.message)
            total = self._task_count if self._task_count > 0 else "?"
            header = format_task_header(self._current_task_index, total, task_desc)
            live.console.print(header)
            return True  # consumed — don't also render raw task line

        # Task complete — track count + update live checklist
        if event.event_type == EventType.TASK_COMPLETE:
            self._awaiting_response = False  # task done — reset waiting state
            if self._show_checklist and self._tasks_completed < len(self._live_tasks):
                self._live_tasks[self._tasks_completed]["completed"] = True
            self._tasks_completed += 1
            live.update(self._build_live_renderable())
            return True  # consumed — checklist tracks this

        # Conversation end — show once per conversation, suppress duplicates
        if event.event_type == EventType.CONVERSATION_END:
            conv_key = event.conversation_id or event.task_id or "default"
            if conv_key in self._ended_conversations:
                return True  # suppress duplicate
            self._ended_conversations.add(conv_key)
            return False  # let it render normally (just once)

        # Goal complete — print completion banner (suppress duplicates)
        if event.event_type == EventType.AGENT_COMPLETE:
            if self._goal_completed:
                return True  # already printed — suppress duplicate

            # Flush deferred banner if it never got a "Created N task(s)"
            if self._banner_deferred:
                agent = self._current_agent or "agent"
                banner = format_goal_banner(agent, self._goal_description or "", self._pending_tasks)
                live.console.print(banner)
                self._task_count = len(self._pending_tasks)
                self._banner_deferred = False

            agent = event.agent_name or self._current_agent or "agent"
            duration = ""
            if self._goal_start_time:
                elapsed = time.monotonic() - self._goal_start_time
                duration = self._format_duration(elapsed)

            task_count = self._tasks_completed or self._task_count
            summary = self._extract_summary(event.message)
            banner = format_goal_complete_banner(agent, task_count, duration, summary)
            live.console.print(banner)

            # Clear all state before updating renderable
            self._show_checklist = False
            self._live_tasks = []
            self._spinner_text = None
            self._in_conversation = False
            self._awaiting_response = False
            self._seen_conv_msgs.clear()
            self._current_goal_id = None
            self._goal_start_time = None
            self._pending_agent_name = None
            self._seen_task_ids.clear()
            self._goal_completed = True
            live.update(self._build_live_renderable())

            # In foreground mode, stop the feed after goal completion
            if self.stop_on_complete:
                self.active = False

            return True  # consumed — don't also render "✅ agent complete"

        # Suppress late agent messages after goal completion (DB watcher
        # delivering the final reply after the completion banner).  But when
        # a NEW user message arrives, a new conversation has started — stop
        # suppressing so user-initiated conversations are visible.
        if self._goal_completed and event.event_type == EventType.MESSAGE_SENT:
            return True  # suppress late agent message
        if self._goal_completed and event.event_type == EventType.MESSAGE_RECEIVED:
            self._goal_completed = False  # new user message → new conversation

        return False

    # --- Dedup ---

    def _is_duplicate(self, event: FeedEvent) -> bool:
        """Check if we've seen a similar event recently (cross-watcher dedup)."""
        now = time.monotonic()

        # Prune old entries
        cutoff = now - self._DEDUP_TTL
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

        # Fingerprint on event_type + content only (no time bucket).
        # Truncate so watchers with different message lengths produce the same hash.
        raw = f"{event.event_type.value}|{self._normalize_message(event.message)[:150]}"
        fingerprint = hashlib.md5(raw.encode()).hexdigest()

        # Duplicate if same fingerprint was seen within the dedup window.
        # This avoids bucket-boundary misses while still allowing genuinely
        # repeated messages (e.g. user sends "ok" twice) after the window.
        if fingerprint in self._seen and (now - self._seen[fingerprint]) < self._DEDUP_WINDOW:
            return True

        self._seen[fingerprint] = now
        return False

    @staticmethod
    def _normalize_message(message: str) -> str:
        """Normalize a message for dedup comparison.

        Strips correlation ID tags, exec tags, and message prefixes so the
        same logical event from different watchers produces the same fingerprint.
        """
        msg = re.sub(r'\[(?:goal|task|conv|exec):[^\]]+\]', '', message)
        # Strip message prefixes that differ between log and DB watchers
        msg = re.sub(r'^💭 Agent:\s*', '', msg)
        msg = re.sub(r'^📩 Message from [^:]+:\s*', '', msg)
        return msg.strip().lower()

    # --- Helpers ---

    @staticmethod
    def _extract_goal_description(message: str) -> str:
        """Extract goal description from an agent start message."""
        # Strip correlation tags first
        clean = re.sub(r'\[(?:goal|task|conv|exec):[^\]]+\]', '', message).strip()
        # Try patterns like: Starting goal: "Touch base..." or Starting goal: Touch base...
        match = re.search(r'Starting goal:\s*"?([^"]+)"?', clean)
        if match:
            return match.group(1).strip()
        # Generic: Goal: "description"
        match = re.search(r'[Gg]oal:\s*"?([^"]+)"?', clean)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_task_list(message: str) -> list[str]:
        """Extract task descriptions from a message listing tasks."""
        # Look for numbered tasks: 1. Chat with user  2. Write journal
        tasks = re.findall(r'\d+\.\s*([^0-9]+?)(?=\d+\.|$)', message)
        return [t.strip().rstrip(',').strip() for t in tasks if t.strip()]

    @staticmethod
    def _extract_task_description(message: str) -> str:
        """Extract task description from a task start message."""
        # Strip ALL correlation tags first so they don't pollute the match
        clean = re.sub(r'\[(?:goal|task|conv|exec):[^\]]+\]', '', message).strip()
        # Pattern: "Executing: Have conversation..." or "Executing conversation: ..."
        match = re.search(r'Executing(?:\s+\w+)?:\s*(.+)', clean)
        if match:
            return match.group(1).strip()
        # Fallback: "Task: Chat with user"
        match = re.search(r'[Tt]ask:\s*(.+)', clean)
        if match:
            return match.group(1).strip()
        return clean or message

    @staticmethod
    def _extract_summary(message: str) -> str:
        """Extract summary from an agent completion message."""
        match = re.search(r'[Ss]ummary:\s*(.+)', message)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds into a human-readable duration string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs:02d}s"

    def _matches_filters(self, event: FeedEvent, filters: Dict[str, Any]) -> bool:
        """Check if an event matches the specified filters."""
        if not filters:
            return True

        if filters.get('agent'):
            if not event.agent_name or event.agent_name != filters['agent']:
                return False

        if filters.get('goal_id'):
            if not event.goal_id or not event.goal_id.startswith(filters['goal_id']):
                return False

        if filters.get('task_id'):
            if not event.task_id or not event.task_id.startswith(filters['task_id']):
                return False

        if filters.get('level'):
            levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR']
            min_level = filters['level'].upper()
            try:
                min_level_idx = levels.index(min_level)
                event_level_idx = levels.index(event.level)
                if event_level_idx < min_level_idx:
                    return False
            except ValueError:
                pass

        return True
