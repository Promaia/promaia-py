"""Standalone entry point for running agent goals in a background process.

Usage:
    python -m promaia.agents.run_goal --agent <name> --goal <goal> [--metadata-json '{}'] [--orchestrate]

This module is spawned as a detached subprocess by ``maia agent run``
so that the CLI can exit immediately while the agent runs to completion.
All output is routed to the shared feed log file via ``setup_agent_file_logging``.

By default, goals run through ``agentic_turn`` — the same tool-use loop that
powers ``maia chat``.  Pass ``--orchestrate`` to use the multi-task Orchestrator
for long-horizon goals with async Slack/Discord conversations.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────

def _load_agent_prompt(agent_config) -> str:
    """Load the agent's system prompt (inline or file path).

    Mirrors the logic in ``executor.py:_load_custom_prompt`` — supports
    both inline markdown and file path references.
    """
    prompt_value = agent_config.prompt_file or ""

    looks_like_path = (
        isinstance(prompt_value, str)
        and "\n" not in prompt_value
        and len(prompt_value) <= 240
        and (
            prompt_value.startswith(("/", "./", "../", "~"))
            or prompt_value.endswith((".md", ".txt"))
            or ("/" in prompt_value)
        )
    )

    if looks_like_path:
        try:
            prompt_path = Path(prompt_value).expanduser()
            if prompt_path.is_file():
                return prompt_path.read_text(encoding="utf-8")
        except OSError:
            pass

    return prompt_value


def _init_messaging_platform(agent_config):
    """Create a messaging platform from environment bot tokens.

    Returns the first available platform (Slack preferred) if the agent
    has messaging_enabled=True. Returns None if the agent doesn't have
    messaging permission or no bot tokens are in the environment.
    """
    import os

    if not getattr(agent_config, "messaging_enabled", False):
        return None

    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    if bot_token:
        try:
            from promaia.agents.messaging.slack_platform import SlackPlatform
            return SlackPlatform(bot_token=bot_token)
        except ImportError:
            logger.warning("slack-sdk not installed, skipping Slack platform")

    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    if bot_token:
        try:
            from promaia.agents.messaging.discord_platform import DiscordPlatform
            return DiscordPlatform(bot_token=bot_token)
        except ImportError:
            logger.warning("discord.py not installed, skipping Discord platform")

    return None


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    """Short summary of tool input for feed display (like Claude Code)."""
    if not tool_input:
        return ""
    if tool_name == "query_sql":
        return tool_input.get("query", "")[:80]
    elif tool_name == "query_vector":
        return tool_input.get("query", "")[:80]
    elif tool_name == "query_source":
        db = tool_input.get("database", "")
        days = tool_input.get("days", "")
        return f"{db}" + (f" ({days}d)" if days else "")
    elif tool_name == "send_message":
        user = tool_input.get("user", "")
        ch = tool_input.get("channel_id", "")
        target = f"DM {user}" if user else ch
        content = tool_input.get("content", "")
        return f"→ {target}: {content[:60]}" if content else f"→ {target}"
    elif tool_name == "send_email":
        to = tool_input.get("to", "")
        subj = tool_input.get("subject", "")
        return f"→ {to}: {subj[:40]}"
    elif tool_name in ("schedule_self", "schedule_agent_event"):
        s = tool_input.get("summary", "")
        t = tool_input.get("start_time", "")
        return f"'{s[:40]}' at {t}"
    elif tool_name == "web_search":
        return tool_input.get("query", "")[:80]
    elif tool_name == "web_fetch":
        return tool_input.get("url", "")[:80]
    elif tool_name.startswith("notion_"):
        return tool_input.get("query", tool_input.get("filter", ""))[:60] if tool_input else ""
    elif tool_name.startswith("sheets_"):
        ss = tool_input.get("spreadsheet", tool_input.get("title", ""))
        return ss[:40] if ss else ""
    elif tool_name == "start_conversation":
        user = tool_input.get("user", "")
        return f"with {user}"
    # Generic fallback
    for k, v in tool_input.items():
        return f"{str(v)[:60]}"
    return ""


def _make_feed_logger(agent_name: str):
    """Build an on_tool_activity callback that logs for the feed."""

    async def on_tool_activity(tool_name, tool_input=None, completed=False,
                               summary=None, **kwargs):
        tool_input = tool_input or {}
        if tool_name == "__plan__":
            steps = tool_input.get("steps", [])
            logger.info(f"[{agent_name}] Plan: {len(steps)} steps")
            for i, step in enumerate(steps, 1):
                logger.info(f"[{agent_name}]   {i}. {step}")
        elif tool_name == "__plan_step__":
            current = tool_input.get("step", 0)
            total = tool_input.get("total", 0)
            logger.info(f"[{agent_name}] Step {current}/{total}")
        elif tool_name == "__plan_done__":
            logger.info(f"[{agent_name}] Done")
        elif tool_name == "__context_trim__":
            logger.info(f"[{agent_name}] Context trimmed")
        elif not completed:
            params = _summarize_tool_input(tool_name, tool_input)
            if params:
                logger.info(f"[{agent_name}] {tool_name} ({params})")
            else:
                logger.info(f"[{agent_name}] {tool_name}")
        elif summary:
            logger.info(f"[{agent_name}] ✓ {tool_name}: {summary[:200]}")

    return on_tool_activity


# ── Agentic execution (default) ─────────────────────────────────────────

async def _run_agentic(agent_config, goal: str, metadata: dict) -> dict:
    """Run a goal using agentic_turn — same engine as maia chat."""
    from promaia.agents.agentic_turn import (
        agentic_turn, build_tool_definitions, ToolExecutor, _generate_plan,
    )
    from promaia.chat.agentic_adapter import build_agentic_system_prompt

    name = agent_config.name  # shorthand for log prefix

    # 1. Load agent system prompt
    base_prompt = _load_agent_prompt(agent_config)

    # Optionally refresh prompt from Notion
    if agent_config.notion_page_id and agent_config.agent_id:
        try:
            from promaia.agents.notion_config import load_agent_by_id
            notion_agent = await load_agent_by_id(
                agent_config.agent_id, agent_config.workspace
            )
            if notion_agent:
                agent_config = notion_agent
                name = agent_config.name
                base_prompt = _load_agent_prompt(agent_config)
                logger.info(f"[{name}] Loaded system prompt from Notion")
        except Exception as e:
            logger.warning(f"[{name}] Could not load Notion config: {e}")

    # 2. Initialize messaging platform
    platform = _init_messaging_platform(agent_config)
    has_platform = platform is not None
    if has_platform:
        logger.info(f"[{name}] Messaging platform: {platform.platform_name}")

    # 3. Build tool definitions
    tools = build_tool_definitions(agent_config, has_platform=has_platform)
    tool_names = [t["name"] for t in tools]
    logger.info(f"[{name}] Tools: {', '.join(tool_names)}")

    # 4. Create tool executor
    executor = ToolExecutor(
        agent=agent_config,
        workspace=agent_config.workspace,
        platform=platform,
    )

    # 5. Build enhanced system prompt
    enhanced_prompt = build_agentic_system_prompt(
        base_prompt=base_prompt,
        workspace=agent_config.workspace,
        mcp_tools=agent_config.mcp_tools or [],
        databases=agent_config.databases or [],
        agent_calendar_id=agent_config.calendar_id,
    )

    # Add calendar trigger context
    cal_summary = metadata.get("calendar_event_summary", "")
    if cal_summary:
        enhanced_prompt += f"\n\nTriggered by calendar event: {cal_summary}"

    # 6. Generate plan
    plan = await _generate_plan(
        user_message=goal,
        agent=agent_config,
        available_tools=tool_names,
    )

    # 7. Build feed-friendly activity callback
    activity_cb = _make_feed_logger(name)

    # ── Feed lifecycle: signal goal start ──
    logger.info(f"[{name}] Starting goal: {goal}")

    # Emit plan via callback
    if plan and activity_cb:
        await activity_cb(
            tool_name="__plan__",
            tool_input={"steps": plan},
        )

    # 8. Run the agentic loop
    messages = [{"role": "user", "content": goal}]
    result = await agentic_turn(
        system_prompt=enhanced_prompt,
        messages=messages,
        tools=tools,
        tool_executor=executor,
        max_iterations=agent_config.max_iterations or 40,
        on_tool_activity=activity_cb,
        plan=plan,
    )

    logger.info(
        f"[{name}] Agentic turn complete: {result.iterations_used} iterations, "
        f"{len(result.tool_calls_made)} tool calls"
    )
    if result.response_text:
        logger.info(f"[{name}] Response: {result.response_text[:500]}")

    # ── Feed lifecycle: signal goal complete ──
    logger.info(f"[{name}] Goal completed successfully")

    return {
        "success": True,
        "output": result.response_text,
        "iterations": result.iterations_used,
        "tool_calls": len(result.tool_calls_made),
    }


# ── Main entry ───────────────────────────────────────────────────────────

async def _run(agent_name: str, goal: str, metadata: dict, use_orchestrator: bool):
    """Load the agent, start the Slack listener, and execute the goal."""
    from promaia.agents.agent_config import get_agent

    agent_config = get_agent(agent_name)
    if not agent_config:
        logger.error(f"Agent '{agent_name}' not found")
        sys.exit(1)

    # Start the Slack bot listener as a background task so conversation
    # replies (DMs) are processed while the agent waits.
    slack_task = None
    has_messaging = getattr(agent_config, "messaging_enabled", False)
    if use_orchestrator or has_messaging:
        try:
            slack_task = asyncio.create_task(_start_slack_listener())
        except Exception as e:
            logger.warning(f"Could not start Slack listener: {e}")

    try:
        if use_orchestrator:
            from promaia.agents.orchestrator import Orchestrator

            orchestrator = Orchestrator(agent_config)
            result = await orchestrator.run_goal(goal=goal, metadata=metadata)
        else:
            # Default: agentic_turn (same engine as maia chat)
            result = await _run_agentic(agent_config, goal, metadata)

        if not result.get("success"):
            logger.error(f"[{agent_name}] Goal failed: {result.get('error')}")
    finally:
        # Shut down the Slack listener when the goal finishes
        if slack_task and not slack_task.done():
            slack_task.cancel()
            try:
                await slack_task
            except (asyncio.CancelledError, Exception):
                pass


async def _start_slack_listener():
    """Start the Slack bot in the background (if configured)."""
    import os

    # Only start if Slack tokens are available
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")

    if not bot_token or not app_token:
        logger.info("Slack tokens not configured, skipping Slack listener")
        return

    try:
        from promaia.messaging.slack_bot import start_slack_bot_async
        logger.info("Starting Slack listener for conversation replies...")
        await start_slack_bot_async()
    except ImportError:
        logger.info("slack-bolt not installed, skipping Slack listener")
    except Exception as e:
        logger.warning(f"Slack listener error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Run an agent goal (background)")
    parser.add_argument("--agent", required=True, help="Agent name")
    parser.add_argument("--goal", required=True, help="Goal description")
    parser.add_argument("--metadata-json", default="{}", help="JSON metadata string")
    parser.add_argument("--orchestrate", action="store_true",
                        help="Use multi-task orchestrator (for long-horizon goals with async conversations)")
    # Legacy flag — kept for backwards compatibility
    parser.add_argument("--no-orchestrate", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    # --orchestrate is opt-in; --no-orchestrate is now a no-op (already default)
    use_orchestrator = args.orchestrate

    # Load environment variables (needed for Slack tokens, API keys, etc.)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Route all logs to central + per-run log file
    import os
    from promaia.agents.feed_watchers import setup_agent_file_logging
    process_log_name = f"agent-{args.agent}-{os.getpid()}"
    setup_agent_file_logging(process_log_name=process_log_name)

    # Write PID file (also covers direct invocations, not just `maia agent run`)
    from promaia.utils.env_writer import get_data_dir
    pid_file = get_data_dir() / "agents" / f"{args.agent}.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    metadata = json.loads(args.metadata_json)
    try:
        asyncio.run(_run(args.agent, args.goal, metadata, use_orchestrator))
    finally:
        # Clean up PID file only if it still points to us (a newer run may
        # have overwritten it with its own PID).
        try:
            if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
