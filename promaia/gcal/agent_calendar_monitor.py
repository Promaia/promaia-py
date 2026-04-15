"""
Foreground calendar monitor that triggers agents from calendar events.

Designed for running in a terminal so you can watch logs live.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from promaia.gcal import get_calendar_manager, google_account_for_workspace
from promaia.agents import load_agents
from promaia.agents.executor import AgentExecutor

logger = logging.getLogger(__name__)


class AgentCalendarMonitor:
    """Monitor agent calendars and trigger runs when events are near start time."""

    def __init__(self, check_interval_minutes: int = 1, trigger_window_minutes: int = 5):
        self.check_interval_seconds = max(1, check_interval_minutes) * 60
        self.trigger_window_seconds = max(1, trigger_window_minutes) * 60
        self.triggered_events: set[str] = set()
        self.running = False

    async def start(self):
        self.running = True
        sync_counter = 0  # Track cycles for 5-minute database sync

        logger.info("Calendar monitor started (reloads agent configs each cycle).")
        logger.info("Tip: if you edit an agent (databases/MCP tools), you don't need to restart this monitor.")
        logger.info("Database sync: Every 5 minutes (5 check cycles)")

        while self.running:
            agents = load_agents()
            agents_with_calendars = [a for a in agents if a.calendar_id and a.enabled]

            if not agents_with_calendars:
                logger.debug("No enabled agents with calendar integration (calendar_id missing).")
            else:
                for agent in agents_with_calendars:
                    google_account = google_account_for_workspace(getattr(agent, "workspace", None))
                    calendar_mgr = get_calendar_manager(account=google_account)
                    upcoming = calendar_mgr.get_upcoming_agent_runs(
                        hours_ahead=3,
                        calendar_id=agent.calendar_id,
                    )

                    for event in upcoming:
                        event_id = event.get("event_id")
                        if not event_id or event_id in self.triggered_events:
                            continue

                        start_raw = event.get("start")
                        if not start_raw:
                            continue

                        start_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                        now = datetime.now(start_time.tzinfo)
                        time_until = (start_time - now).total_seconds()

                        # Trigger only when event has started or is starting in next minute
                        # (negative time_until means event already started)
                        if -self.trigger_window_seconds <= time_until <= 60:
                            self.triggered_events.add(event_id)

                            summary = event.get("summary") or ""
                            description = (event.get("description") or "").strip()
                            link = event.get("html_link") or ""

                            logger.info("Calendar event detected → triggering agent")
                            logger.info(f"  Agent: {agent.name}")
                            logger.info(f"  Event: {summary}")
                            logger.info(f"  Start: {start_time.isoformat()}")
                            if link:
                                logger.info(f"  Link: {link}")

                            run_request = description or summary
                            if not run_request:
                                run_request = "Run based on your system instructions."

                            logger.info("  Running agentic turn with calendar event as goal.")

                            from promaia.agents.run_goal import _run_agentic

                            result = await _run_agentic(
                                agent_config=agent,
                                goal=run_request,
                                metadata={
                                    "calendar_event_id": event_id,
                                    "calendar_event_start": start_raw,
                                    "calendar_event_summary": summary,
                                    "calendar_event_link": link,
                                },
                            )

                            if result.get("success"):
                                logger.info("Agentic turn completed successfully.")
                            else:
                                logger.error(f"Agentic turn failed: {result.get('error')}")

            # Trigger database sync every 5 cycles (5 minutes with 1-minute interval)
            sync_counter += 1
            if sync_counter >= 5:
                logger.info("⏰ Triggering 5-minute bidirectional sync...")
                try:
                    # Pull from Notion (existing)
                    await self._sync_all_databases()

                    # Push to Notion (only agent journal databases)
                    await self._push_agent_databases()

                    logger.info("✅ Bidirectional sync completed")
                except Exception as e:
                    logger.error(f"❌ Bidirectional sync failed: {e}")
                finally:
                    sync_counter = 0

            await asyncio.sleep(self.check_interval_seconds)

    async def _sync_all_databases(self):
        """Sync all enabled databases."""
        from promaia.cli.database_commands import handle_database_sync
        from argparse import Namespace

        # Create minimal args for sync (sync all enabled databases)
        args = Namespace(
            sources=None,  # None = sync all enabled databases
            workspace=None,
            browse=None
        )

        await handle_database_sync(args)

    async def _push_agent_databases(self):
        """Push agent journal changes back to Notion (only agent journal databases)."""
        from promaia.storage.notion_push import push_database_changes
        from promaia.config.databases import get_database_manager

        db_manager = get_database_manager()
        agents = load_agents()

        pushed_count = 0
        for agent in agents:
            if not agent.enabled or not agent.journal_db_id:
                continue

            # Find DB config by journal_db_id
            for db_name in db_manager.list_databases(workspace=agent.workspace):
                db_config = db_manager.get_database(db_name)
                if db_config and db_config.database_id == agent.journal_db_id:
                    try:
                        result = await push_database_changes(
                            database_name=db_config.nickname,
                            workspace=db_config.workspace,
                            force=False
                        )

                        if result['success']:
                            total_changes = result['created'] + result['updated']
                            if total_changes > 0:
                                logger.info(
                                    f"📤 Pushed {agent.name} journal ({db_config.nickname}): "
                                    f"{result['created']} created, {result['updated']} updated"
                                )
                                pushed_count += 1

                            conflicts = sum(1 for r in result.get('results', []) if r.get('status') == 'conflict')
                            if conflicts > 0:
                                logger.warning(f"⚠️  {conflicts} conflict(s) in {agent.name} journal")
                        else:
                            logger.warning(f"⚠️  Push failed for {agent.name} journal: {result.get('error')}")

                    except Exception as e:
                        logger.error(f"Failed to push {agent.name} journal: {e}")
                    break

        if pushed_count > 0:
            logger.info(f"✅ Push completed: {pushed_count} agent journal(s) with changes")

    def stop(self):
        self.running = False


async def run_foreground(check_interval_minutes: int = 1, trigger_window_minutes: int = 5):
    monitor = AgentCalendarMonitor(
        check_interval_minutes=check_interval_minutes,
        trigger_window_minutes=trigger_window_minutes,
    )
    await monitor.start()

