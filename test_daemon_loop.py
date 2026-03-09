#!/usr/bin/env python3
"""
Debug script to test the daemon monitoring loop with detailed logging.
"""
import asyncio
import logging
import sys
from datetime import datetime

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

async def test_monitoring_loop():
    """Test the monitoring loop with detailed logging at each step."""
    logger.info("=" * 70)
    logger.info("Starting daemon monitoring loop test")
    logger.info("=" * 70)

    # Step 1: Import modules
    try:
        logger.info("Step 1: Importing modules...")
        from promaia.gcal import get_calendar_manager
        from promaia.agents import load_agents
        from promaia.agents.executor import AgentExecutor
        logger.info("✓ Modules imported successfully")
    except Exception as e:
        logger.error(f"✗ Failed to import modules: {e}", exc_info=True)
        return

    # Step 2: Get calendar manager
    try:
        logger.info("Step 2: Getting calendar manager...")
        calendar_mgr = get_calendar_manager()
        logger.info("✓ Calendar manager obtained")
    except Exception as e:
        logger.error(f"✗ Failed to get calendar manager: {e}", exc_info=True)
        return

    # Step 3: Load agents
    try:
        logger.info("Step 3: Loading agents...")
        agents = load_agents()
        logger.info(f"✓ Loaded {len(agents)} agents")
        for agent in agents:
            logger.info(f"  - {agent.name}: enabled={agent.enabled}, has_calendar={bool(agent.calendar_id)}")
    except Exception as e:
        logger.error(f"✗ Failed to load agents: {e}", exc_info=True)
        return

    # Step 4: Filter agents with calendars
    try:
        logger.info("Step 4: Filtering agents with calendar integration...")
        agents_with_calendars = [a for a in agents if a.calendar_id and a.enabled]
        logger.info(f"✓ Found {len(agents_with_calendars)} agents with calendars")
    except Exception as e:
        logger.error(f"✗ Failed to filter agents: {e}", exc_info=True)
        return

    if not agents_with_calendars:
        logger.warning("No agents with calendar integration found!")
        return

    # Step 5: Run monitoring loop (3 iterations for testing)
    logger.info("Step 5: Starting monitoring loop (3 iterations)...")
    check_interval_seconds = 10  # 10 seconds for testing

    for iteration in range(3):
        logger.info(f"\n{'=' * 70}")
        logger.info(f"Iteration {iteration + 1}/3 at {datetime.now().strftime('%H:%M:%S')}")
        logger.info(f"{'=' * 70}")

        for agent in agents_with_calendars:
            try:
                logger.info(f"Checking calendar for agent: {agent.name}")
                logger.info(f"  Calendar ID: {agent.calendar_id[:40]}...")

                # Get upcoming events
                logger.debug("Calling get_upcoming_agent_runs...")
                upcoming = calendar_mgr.get_upcoming_agent_runs(
                    hours_ahead=3,
                    calendar_id=agent.calendar_id,
                )
                logger.info(f"  Found {len(upcoming)} upcoming events")

                for event in upcoming:
                    event_id = event.get("event_id")
                    summary = event.get("summary", "No title")
                    start = event.get("start", "No time")
                    logger.info(f"    - {summary} ({start})")
                    logger.info(f"      Event ID: {event_id[:30]}...")

            except Exception as e:
                logger.error(f"✗ Error checking calendar for {agent.name}: {e}", exc_info=True)

        if iteration < 2:  # Don't sleep after last iteration
            logger.info(f"\nSleeping for {check_interval_seconds} seconds...")
            await asyncio.sleep(check_interval_seconds)

    logger.info("\n" + "=" * 70)
    logger.info("Monitoring loop test completed successfully!")
    logger.info("=" * 70)

if __name__ == "__main__":
    try:
        asyncio.run(test_monitoring_loop())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        sys.exit(1)
