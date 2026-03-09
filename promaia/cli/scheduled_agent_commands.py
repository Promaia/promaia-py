"""
CLI commands for scheduled agent management.

These commands allow users to create, manage, and monitor interval-based agents
that automatically run queries and write results to Notion.
"""
import argparse
import asyncio
import json
import logging
import os
import random
import signal
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

from promaia.agents import (
    AgentConfig, load_agents, save_agent, delete_agent, get_agent,
    execute_agent_sync, ExecutionTracker,
    run_scheduler_daemon_sync, run_scheduler_daemon, is_scheduler_running, stop_scheduler
)
from promaia.config.workspaces import get_workspace_manager

logger = logging.getLogger(__name__)


async def handle_agent_feed(args):
    """Live activity feed for a specific agent."""
    from promaia.agents.feed_aggregator import FeedAggregator
    from rich.console import Console

    console = Console()
    agent_name = args.name

    console.print(f"[cyan]🐙 Maia Feed — {agent_name}[/cyan]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    filters = {'agent': agent_name}
    verbose = getattr(args, 'verbose', False)
    aggregator = FeedAggregator(verbose=verbose, show_timestamps=getattr(args, 'timestamps', False))
    try:
        await aggregator.start_feed(filters)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        console.print("\n[dim]Feed stopped[/dim]")


async def handle_agent_push(args):
    """Push agent's local markdown changes to Notion."""
    from promaia.storage.notion_push import push_database_changes
    from promaia.config.databases import get_database_manager

    # Get agent name
    agent_name = getattr(args, 'name', None)

    if not agent_name:
        print("❌ Agent name required")
        return

    # Load agent config
    agents = load_agents()
    agent = next((a for a in agents if a.name == agent_name or a.agent_id == agent_name), None)

    if not agent:
        print(f"❌ Agent '{agent_name}' not found")
        return

    print(f"🤖 Agent: {agent.name}")
    print(f"   Workspace: {agent.workspace}")

    # Get database manager and reload config
    db_manager = get_database_manager()
    db_manager.load_config()  # Force reload to pick up any new databases

    # Get agent's personal journal database (the one they write to)
    databases = []

    # Use journal_db_id if available (agent's personal journal)
    if agent.journal_db_id:
        # Find the database config by database_id
        for db_name in db_manager.list_databases(workspace=agent.workspace):
            db_config = db_manager.get_database(db_name)
            if db_config and db_config.database_id == agent.journal_db_id:
                databases.append(db_config.nickname)
                break

    if not databases:
        print("❌ Agent has no journal database configured")
        print(f"   Hint: Set 'journal_db_id' in agent config to enable journal pushing")
        return

    print(f"   Agent's journal: {', '.join(databases)}")
    print()

    # Push each database
    total_created = 0
    total_updated = 0
    total_skipped = 0
    total_failed = 0

    for db_name in databases:
        db_config = db_manager.get_database(db_name, agent.workspace)

        if not db_config:
            print(f"⚠️  Database '{db_name}' not found, skipping")
            continue

        if db_config.source_type != 'notion':
            print(f"⏭️  Skipping {db_name} (not a Notion database)")
            continue

        print(f"📤 Pushing {db_name}...")

        try:
            result = await push_database_changes(
                database_name=db_name,
                workspace=agent.workspace,
                force=getattr(args, 'force', False)
            )

            if result['success']:
                total_created += result['created']
                total_updated += result['updated']
                total_skipped += result['skipped']
                total_failed += result['failed']

                if result['created'] + result['updated'] > 0:
                    print(f"   ✅ Created: {result['created']}, Updated: {result['updated']}")
                else:
                    print(f"   ⏭️  No changes detected")

                # Show conflicts if any
                conflicts = sum(1 for r in result.get('results', []) if r.get('status') == 'conflict')
                if conflicts > 0:
                    print(f"   ⚠️  Conflicts: {conflicts}")
            else:
                print(f"   ❌ Failed: {result.get('error')}")
                total_failed += 1
        except Exception as e:
            print(f"   ❌ Error: {e}")
            total_failed += 1

    # Summary
    print()
    print("=" * 50)
    print("📊 PUSH SUMMARY")
    print(f"   Created: {total_created}")
    print(f"   Updated: {total_updated}")
    print(f"   Skipped: {total_skipped}")
    if total_failed > 0:
        print(f"   Failed: {total_failed}")
    print("=" * 50)


def _generate_placeholder_name() -> str:
    """Generate a random placeholder name in format: noun-####"""
    nouns = [
        "phoenix", "falcon", "eagle", "hawk", "raven", "sparrow",
        "tiger", "wolf", "lion", "bear", "fox", "lynx",
        "comet", "nova", "star", "nebula", "pulsar", "quasar",
        "river", "mountain", "forest", "ocean", "canyon", "valley",
        "storm", "thunder", "lightning", "cloud", "wind", "rain",
        "sage", "mentor", "guide", "scout", "guardian", "watcher",
        "crystal", "diamond", "ruby", "amber", "jade", "opal",
        "compass", "beacon", "anchor", "horizon", "zenith", "atlas"
    ]
    noun = random.choice(nouns)
    number = random.randint(1000, 9999)
    return f"{noun}-{number}"


async def _add_agent_to_calendar(agent_config: AgentConfig) -> bool:
    """Add an agent to Google Calendar."""
    try:
        from promaia.gcal import get_calendar_manager, google_account_for_workspace

        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent_config.workspace))

        if not agent_config.schedule:
            logger.warning(f"Agent {agent_config.name} has no schedule, cannot add to calendar")
            return False

        event_ids = calendar_mgr.create_agent_event(
            agent_name=agent_config.name,
            schedule=agent_config.schedule,
            agent_config=agent_config.to_dict()
        )

        if event_ids:
            # Store event IDs in agent config
            agent_config.calendar_event_ids = event_ids
            save_agent(agent_config)
            return True

        return False

    except Exception as e:
        logger.error(f"Error adding agent to calendar: {e}")
        return False


def _show_agent_summary(agent: AgentConfig, console):
    """Display agent configuration summary before creation."""
    from promaia.utils.display import print_separator
    from promaia.cli.schedule_grid_selector import schedule_to_string

    print()
    print_separator("Agent Configuration Summary")
    console.print(f"Name: [bold white]{agent.name}[/bold white]")
    console.print(f"Agent ID: [bold cyan]@{agent.agent_id}[/bold cyan]")
    console.print(f"Workspace: [cyan]{agent.workspace}[/cyan]")

    # Show databases in a compact format
    db_count = len(agent.databases)
    if db_count <= 3:
        console.print(f"Databases: [cyan]{', '.join(agent.databases)}[/cyan]")
    else:
        db_preview = ', '.join(agent.databases[:3])
        console.print(f"Databases: [cyan]{db_preview}, +{db_count - 3} more[/cyan]")

    # Show schedule or interval
    if agent.schedule:
        schedule_display = schedule_to_string(agent.schedule)
        console.print(f"Schedule: [cyan]{len(agent.schedule)} runs/week[/cyan]")
        console.print(f"  [dim]{schedule_display}[/dim]")
    elif agent.interval_minutes:
        console.print(f"Schedule: [cyan]Every {agent.interval_minutes} minutes[/cyan]")
    else:
        console.print(f"Schedule: [cyan]Calendar events only (tag @{agent.agent_id})[/cyan]")

    console.print(f"Journal Memory: [cyan]{agent.journal_memory_days} days[/cyan]")

    if agent.messaging_enabled and agent.messaging_platform:
        console.print(f"Messaging: [cyan]{agent.messaging_platform.title()}[/cyan]")
    else:
        console.print(f"Messaging: [dim]None[/dim]")

    if agent.mcp_tools:
        console.print(f"MCP Tools: [cyan]{', '.join(agent.mcp_tools)}[/cyan]")
    else:
        console.print(f"MCP Tools: [dim]None[/dim]")

    if agent.description:
        console.print(f"Description: [dim]{agent.description}[/dim]")

    # Show prompt info
    console.print(f"\nSystem Prompt: [dim]Default (edit in Notion after creation)[/dim]")

    print_separator()
    console.print("\n[bold white]What happens when you create this agent:[/bold white]")
    console.print("  • Creates agent page in Notion Agents database", style="dim")
    console.print("  • Sets up System Prompt subpage (editable)", style="dim")
    console.print("  • Creates Instructions and Journal sub-databases", style="dim")
    console.print("  • Opens agent page in browser for easy access", style="dim")
    console.print()


async def handle_agent_add(args):
    """
    Interactively add a new scheduled agent with modern UI.

    Usage:
        maia agent add
    """
    from promaia.utils.display import print_text, print_separator
    from promaia.cli.agent_creation_selector import (
        select_workspace,
        select_databases,
        select_mcp_tools,
        fetch_discord_channels,
    )
    from promaia.config.databases import get_database_manager
    from rich.console import Console

    console = Console()

    print_text("\n🤖 Create New Scheduled Agent\n", style="bold cyan")

    # Step 1: Agent name (simple text input with placeholder)
    placeholder_name = _generate_placeholder_name()
    console.print(f"Agent name (press ENTER for '[cyan]{placeholder_name}[/cyan]'):")
    name = input("› ").strip()

    # Use placeholder if user pressed ENTER without typing
    if not name:
        name = placeholder_name
        console.print(f"✓ Using name: [cyan]{name}[/cyan]", style="dim")

    # Check if already exists
    if get_agent(name):
        console.print(f"❌ Agent '{name}' already exists", style="red")
        return

    # Step 2: Workspace selection (interactive)
    workspace_mgr = get_workspace_manager()
    workspaces = workspace_mgr.list_workspaces()

    if not workspaces:
        console.print("❌ No workspaces configured. Please create a workspace first.", style="red")
        return

    console.print()  # Spacing
    workspace = await select_workspace(workspaces)
    if not workspace:
        console.print("❌ Cancelled", style="red")
        return

    console.print(f"✓ Workspace: [cyan]{workspace}[/cyan]", style="dim")

    # Step 3: Database selection (interactive checkbox with inline days)
    db_manager = get_database_manager()
    workspace_databases = db_manager.get_workspace_databases(workspace)

    # Format databases for selector
    available_databases = []
    for db in workspace_databases:
        if db.browser_include:  # Only show databases marked for browser
            db_config = {
                'name': db.get_qualified_name(),
                'default_days': db.default_days,
                'default_include': db.default_include,
            }
            # Add source_type if it's Discord
            if hasattr(db, 'source_type'):
                db_config['source_type'] = db.source_type
            available_databases.append(db_config)

    if not available_databases:
        console.print(f"❌ No databases available in workspace '{workspace}'", style="red")
        return

    # Enrich Discord databases with channel info
    console.print()  # Spacing
    console.print("⏳ Loading Discord channels...", style="dim")
    available_databases = await fetch_discord_channels(workspace, available_databases)

    selected_dbs = await select_databases(workspace, available_databases)
    if not selected_dbs:
        console.print("❌ Cancelled", style="red")
        return

    # Format databases with days for agent config
    databases = [f"{db_name}:{days}" for db_name, days in selected_dbs]
    console.print(f"✓ Databases: {len(selected_dbs)} selected", style="dim")

    # Step 4: Use default prompt - user edits in Notion System Prompt page after creation
    prompt_content = f"You are {name}, a helpful AI assistant for the {workspace} workspace."
    console.print()  # Spacing
    console.print("✓ Default prompt (edit in Notion System Prompt page after creation)", style="dim")

    # Step 5: Scheduling options
    console.print()  # Spacing
    console.print("📅 Agent Scheduling", style="bold cyan")
    console.print()
    console.print("You can schedule your agent in two ways:", style="dim")
    console.print(f"  1. Interval: Run automatically every N minutes", style="dim")
    console.print(f"  2. Calendar: Tag @agent-name in Google Calendar events", style="dim")
    console.print(f"     (Works in event title or description)", style="dim")
    console.print(f"  Both can be used together!", style="dim")
    console.print()

    schedule = None
    interval_minutes = None

    use_interval = input("Run on interval? (y/N): ").strip().lower()

    if use_interval == 'y':
        interval_input = input("Interval in minutes (leave empty to skip): ").strip()
        if interval_input:
            try:
                interval_minutes = int(interval_input)
                console.print(f"✓ Interval: every {interval_minutes} minutes", style="dim")
            except ValueError:
                console.print("⚠️  Invalid interval, skipping", style="yellow")
        else:
            console.print("✓ No interval (calendar events only)", style="dim")
    else:
        console.print(f"✓ No interval", style="dim")

    # Step 6: Journal memory lookback days
    console.print()  # Spacing
    journal_days_input = input("Journal memory lookback days (default: 7): ").strip()
    journal_memory_days = int(journal_days_input) if journal_days_input else 7

    if journal_memory_days <= 0:
        console.print("❌ Journal memory days must be positive", style="red")
        return

    console.print(f"✓ Journal memory: {journal_memory_days} days", style="dim")

    # Step 7: MCP tools (optional, interactive)
    mcp_tools = []
    console.print()  # Spacing
    console.print("🛠️  MCP Tools (Model Context Protocol)", style="bold cyan")
    console.print("   Enable external tools for your agent:", style="dim")

    # Load available MCP servers + built-in integrations
    available_tools = []
    try:
        import json
        from promaia.agents.mcp_loader import _find_mcp_servers_json
        mcp_config_file = _find_mcp_servers_json()
        if mcp_config_file and mcp_config_file.is_file():
            with open(mcp_config_file, 'r') as f:
                mcp_config = json.load(f)
                servers = mcp_config.get('servers', {})
                available_tools = [
                    name for name, config in servers.items()
                    if config.get('enabled', True)
                ]
    except Exception as e:
        logger.warning(f"Could not load MCP servers: {e}")

    # Add built-in integrations that have tool support
    for builtin in ("gmail", "calendar"):
        if builtin not in available_tools:
            available_tools.append(builtin)

    if available_tools:
        console.print(f"   Available: {', '.join(available_tools)}", style="dim")
        configure_mcp = input("\nConfigure MCP tools? (y/N): ").strip().lower()
        if configure_mcp == 'y':
            mcp_tools = await select_mcp_tools(available_tools)
            if mcp_tools:
                console.print(f"✓ MCP Tools: {', '.join(mcp_tools)}", style="dim")
            else:
                console.print("✓ No MCP tools selected", style="dim")
        else:
            console.print("✓ No MCP tools (can add later)", style="dim")
    else:
        console.print("   No MCP servers configured", style="yellow")
        console.print("   Configure in maia-data/mcp_servers.json", style="dim")

    # Step 8: Messaging platform (auto-detect from env vars)
    messaging_platform = None
    messaging_enabled = False
    console.print()  # Spacing
    console.print("💬 Messaging Platform", style="bold cyan")

    has_slack = bool(os.environ.get('SLACK_BOT_TOKEN'))
    has_discord = bool(os.environ.get('DISCORD_BOT_TOKEN'))

    if has_slack and has_discord:
        console.print("   Detected: Slack, Discord", style="dim")
        platform_choice = input("Select platform (1=Slack, 2=Discord, 3=None): ").strip()
        if platform_choice == '1':
            messaging_platform = 'slack'
            messaging_enabled = True
        elif platform_choice == '2':
            messaging_platform = 'discord'
            messaging_enabled = True
        else:
            console.print("✓ No messaging platform", style="dim")
    elif has_slack:
        console.print("   Detected: Slack", style="dim")
        use_slack = input("Enable Slack messaging? (Y/n): ").strip().lower()
        if use_slack != 'n':
            messaging_platform = 'slack'
            messaging_enabled = True
    elif has_discord:
        console.print("   Detected: Discord", style="dim")
        use_discord = input("Enable Discord messaging? (Y/n): ").strip().lower()
        if use_discord != 'n':
            messaging_platform = 'discord'
            messaging_enabled = True
    else:
        console.print("   No messaging platforms detected (set SLACK_BOT_TOKEN or DISCORD_BOT_TOKEN)", style="dim")

    if messaging_enabled:
        console.print(f"✓ Messaging: {messaging_platform.title()}", style="dim")

    # Step 9: Description (optional, simple text)
    console.print()  # Spacing
    description = input("Description (optional, press ENTER to skip): ").strip()

    # Generate agent ID
    from promaia.agents.notion_setup import generate_agent_id
    existing_agents = load_agents()
    agent_id = generate_agent_id(name, existing_agents)
    console.print(f"✓ Agent ID: [cyan]{agent_id}[/cyan]", style="dim")

    # Create agent config
    agent_config = AgentConfig(
        name=name,
        agent_id=agent_id,
        workspace=workspace,
        databases=databases,
        prompt_file=prompt_content,
        schedule=None,  # No schedule grid - use calendar events or interval
        interval_minutes=interval_minutes,  # Optional interval
        mcp_tools=mcp_tools,
        max_iterations=40,
        journal_memory_days=journal_memory_days,
        messaging_platform=messaging_platform,
        messaging_enabled=messaging_enabled,
        enabled=True,
        description=description,
        created_at=datetime.now().isoformat()
    )

    # Validate
    errors = agent_config.validate()
    if errors:
        console.print("\n❌ Validation errors:", style="red")
        for error in errors:
            console.print(f"  - {error}", style="red")
        return

    # Show configuration summary
    _show_agent_summary(agent_config, console)

    # Confirm creation
    confirm = input("\nCreate this agent? (Y/n): ").strip().lower()
    if confirm and confirm != 'y':
        console.print("❌ Cancelled", style="yellow")
        return

    # Create agent structure in Notion
    try:
        from promaia.agents.notion_setup import create_agent_in_notion

        notion_page_id = await create_agent_in_notion(agent_config, workspace)
        agent_config.notion_page_id = notion_page_id

    except Exception as e:
        console.print(f"\n⚠️  Could not create Notion structure: {e}", style="yellow")
        console.print("   Agent will be created without Notion integration", style="dim")
        # Continue anyway - agent can still work from JSON

    # Save
    save_agent(agent_config)

    # Auto-create dedicated Google Calendar for this agent
    console.print("\n📅 Creating dedicated Google Calendar...", style="dim")

    try:
        from promaia.gcal import get_calendar_manager, google_account_for_workspace

        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent_config.workspace))

        # Create calendar
        calendar_description = f"Automated schedule for {agent_config.name} agent"
        if agent_config.description:
            calendar_description += f"\n\n{agent_config.description}"

        calendar_id = calendar_mgr.create_agent_calendar(
            agent_name=agent_config.name,
            description=calendar_description
        )

        if calendar_id:
            # Store calendar ID
            agent_config.calendar_id = calendar_id
            save_agent(agent_config)

            console.print(f"   ✅ Created calendar: {agent_config.name}", style="green")
            console.print(f"   Calendar ID: [dim]{calendar_id}[/dim]")

            # Auto-sync agent to calendar if schedule exists
            if agent_config.schedule:
                console.print(f"   Adding {len(agent_config.schedule)} recurring events...", style="dim")
                event_ids = calendar_mgr.create_agent_event(
                    agent_name=agent_config.name,
                    schedule=agent_config.schedule,
                    agent_config=agent_config.to_dict(),
                    calendar_id=calendar_id
                )

                if event_ids:
                    agent_config.calendar_event_ids = event_ids
                    save_agent(agent_config)
                    console.print(f"   ✅ Added {len(agent_config.schedule)} recurring events", style="green")
        else:
            console.print("   ⚠️  Failed to create calendar", style="yellow")

    except Exception as e:
        console.print(f"   ⚠️  Calendar creation failed: {e}", style="yellow")
        logger.warning(f"Could not create calendar for agent {agent_config.name}: {e}")

    console.print(f"\n✅ Agent '{name}' created successfully!", style="green")
    console.print(f"\n   🤖 Agent ID: [bold cyan]@{agent_id}[/bold cyan]", style="white")

    if agent_config.notion_page_id:
        # Use shortest working Notion URL format: workspace/page_id_no_dashes
        page_id_clean = agent_config.notion_page_id.replace("-", "")
        agent_url = f"https://www.notion.so/{workspace}/{page_id_clean}"

        console.print(f"\n   📋 Notion: {agent_url}", style="cyan")
        console.print(f"      💡 Edit System Prompt and configure your agent", style="dim")

    # Show calendar link if created
    if agent_config.calendar_id:
        console.print(f"\n   📆 Google Calendar:", style="bold white")
        console.print(f"      https://calendar.google.com", style="cyan")
        console.print(f"      (Look for '{agent_config.name}' calendar in sidebar)", style="dim")

    # Show scheduling info
    if agent_config.interval_minutes:
        console.print(f"\n   📅 Scheduling:", style="bold white")
        console.print(f"      • Runs every {agent_config.interval_minutes} minutes", style="dim")

    console.print(f"\n   🧪 Test with: 'maia agent run-scheduled {name}'", style="dim")


async def handle_agent_list_scheduled(args):
    """
    List all scheduled agents.

    Usage:
        maia agent list-scheduled
    """
    from promaia.cli.schedule_grid_selector import schedule_to_string

    agents = load_agents()

    if not agents:
        print("No scheduled agents configured")
        return

    print(f"\n🤖 Scheduled Agents ({len(agents)}):\n")

    tracker = ExecutionTracker()

    for agent in agents:
        status_emoji = "✅" if agent.enabled else "⏸️"
        calendar_emoji = " 📅" if agent.calendar_event_ids else ""
        print(f"{status_emoji}{calendar_emoji} {agent.name}")
        print(f"   Workspace: {agent.workspace}")

        # Show schedule or interval
        if agent.schedule:
            print(f"   Schedule: {len(agent.schedule)} runs/week")
        elif agent.interval_minutes:
            print(f"   Interval: every {agent.interval_minutes} minutes")

        if agent.calendar_event_ids:
            print(f"   Calendar: On Google Calendar")

        print(f"   Databases: {', '.join(agent.databases)}")

        if agent.description:
            print(f"   Description: {agent.description}")

        # Show last run
        if agent.last_run_at:
            print(f"   Last run: {agent.last_run_at}")

        # Show stats
        stats = tracker.get_agent_stats(agent.name)
        if stats.get('total_runs', 0) > 0:
            print(f"   Runs: {stats['total_runs']} (success rate: {stats['success_rate']:.1f}%)")
            print(f"   Total cost: ${stats['total_cost']:.4f}")

        print()


async def handle_agent_run_scheduled(args):
    """
    Manually run a scheduled agent.

    Usage:
        maia agent run-scheduled <name>
    """
    from promaia.utils.display import print_text

    agent = get_agent(args.name)

    if not agent:
        print(f"❌ Agent '{args.name}' not found")
        return

    print_text(f"\n🤖 Running agent '{args.name}'...\n", style="bold cyan")

    # Execute agent
    result = execute_agent_sync(agent)

    if result['success']:
        print_text(f"\n✅ Agent completed successfully!", style="green")

        metrics = result.get('metrics', {})
        print(f"\nMetrics:")
        print(f"  Iterations: {metrics.get('iterations_used', 0)}")
        print(f"  Tokens: {metrics.get('tokens_used', 0):,}")
        print(f"  Cost: ${metrics.get('cost_estimate', 0):.4f}")
        print(f"  Duration: {metrics.get('duration_seconds', 0):.1f}s")

        print(f"\n📝 Output generated (logged to journal)")

        # Show preview of output
        if result.get('output'):
            print("\nOutput preview:")
            output_preview = result['output'][:200]
            print(f"  {output_preview}...")

    else:
        print_text(f"\n❌ Agent failed: {result.get('error')}", style="red")


async def handle_agent_logs_scheduled(args):
    """
    View execution logs for a scheduled agent.

    Usage:
        maia agent logs-scheduled <name>
        maia agent logs-scheduled <name> --limit 10
    """
    tracker = ExecutionTracker()

    executions = tracker.list_executions(
        agent_name=args.name,
        limit=args.limit
    )

    if not executions:
        print(f"No execution logs for agent '{args.name}'")
        return

    print(f"\n📜 Execution Logs for '{args.name}' ({len(executions)} recent):\n")

    for exec_record in executions:
        status = exec_record['status']
        status_emoji = {
            'completed': '✅',
            'failed': '❌',
            'running': '⏳',
            'pending': '⏸️'
        }.get(status, '❓')

        print(f"{status_emoji} Execution #{exec_record['id']}")
        print(f"   Started: {exec_record['started_at']}")

        if exec_record['completed_at']:
            print(f"   Completed: {exec_record['completed_at']}")

        print(f"   Status: {status}")

        if exec_record['iterations_used']:
            print(f"   Iterations: {exec_record['iterations_used']}")

        if exec_record['tokens_used']:
            print(f"   Tokens: {exec_record['tokens_used']:,}")

        if exec_record['cost_estimate']:
            print(f"   Cost: ${exec_record['cost_estimate']:.4f}")

        if exec_record['error_message']:
            print(f"   Error: {exec_record['error_message']}")

        if exec_record['context_summary']:
            print(f"   Context: {exec_record['context_summary']}")

        print()


async def handle_agent_remove_scheduled(args):
    """
    Remove an agent and cascade-delete all associated Notion sub-resources.

    Usage:
        maia agent remove <name>
    """
    from rich.console import Console

    console = Console()

    agent = get_agent(args.name)

    if not agent:
        console.print(f"❌ Agent '{args.name}' not found", style="red")
        return

    # Show what will be deleted
    has_notion = any([
        agent.notion_page_id, agent.journal_db_id,
        agent.instructions_db_id, agent.system_prompt_page_id
    ])

    if has_notion:
        console.print(f"\n📋 Notion resources that will be archived:", style="dim")
        if agent.notion_page_id:
            console.print(f"   • Agent page: {agent.notion_page_id}", style="dim")
        if agent.system_prompt_page_id:
            console.print(f"   • System prompt: {agent.system_prompt_page_id}", style="dim")
        if agent.instructions_db_id:
            console.print(f"   • Instructions DB: {agent.instructions_db_id}", style="dim")
        if agent.journal_db_id:
            console.print(f"   • Journal DB: {agent.journal_db_id}", style="dim")

    # Show local resources that will be cleaned up
    if agent.agent_id and agent.workspace:
        db_nickname = f"{agent.agent_id.replace('-', '_')}_journal"
        console.print(f"\n🗂️  Local resources that will be removed:", style="dim")
        console.print(f"   • SQLite table: notion_{agent.workspace}_{db_nickname}", style="dim")
        console.print(f"   • Journal markdown: data/md/notion/{agent.workspace}/{db_nickname}/", style="dim")
        console.print(f"   • System prompt: data/md/notion/{agent.workspace}/pages/{agent.agent_id}-system-prompt.md", style="dim")
        console.print(f"   • Config database entry (if registered)", style="dim")

    if agent.calendar_id:
        console.print(f"   • Calendar: {agent.calendar_id}", style="dim")

    # Single confirmation prompt
    if not args.yes:
        confirm = input(f"\n⚠️  Delete agent '{args.name}' and all Notion resources? (Y/n): ").strip().lower()
        if confirm and confirm != 'y':
            console.print("Cancelled", style="yellow")
            return

    # Cascade-delete Notion sub-resources
    if has_notion:
        try:
            from promaia.notion.client import get_client

            notion = get_client(agent.workspace)

            # Archive journal database
            if agent.journal_db_id:
                try:
                    await notion.blocks.delete(agent.journal_db_id)
                    console.print("   ✅ Archived journal database", style="green")
                except Exception as e:
                    console.print(f"   ⚠️  Journal DB archive failed: {e}", style="yellow")

            # Archive instructions database
            if agent.instructions_db_id:
                try:
                    await notion.blocks.delete(agent.instructions_db_id)
                    console.print("   ✅ Archived instructions database", style="green")
                except Exception as e:
                    console.print(f"   ⚠️  Instructions DB archive failed: {e}", style="yellow")

            # Archive system prompt page
            if agent.system_prompt_page_id:
                try:
                    await notion.pages.update(agent.system_prompt_page_id, archived=True)
                    console.print("   ✅ Archived system prompt", style="green")
                except Exception as e:
                    console.print(f"   ⚠️  System prompt archive failed: {e}", style="yellow")

            # Archive agent page
            if agent.notion_page_id:
                try:
                    await notion.pages.update(agent.notion_page_id, archived=True)
                    console.print("   ✅ Archived agent page", style="green")
                except Exception as e:
                    console.print(f"   ⚠️  Agent page archive failed: {e}", style="yellow")

        except Exception as e:
            console.print(f"   ⚠️  Notion cleanup failed: {e}", style="yellow")

    # Ask about calendar deletion if agent has one
    delete_calendar = False
    if agent.calendar_id:
        console.print(f"\n📅 Agent has dedicated calendar: [cyan]{agent.calendar_id}[/cyan]")

        if not args.yes:
            try:
                from prompt_toolkit import PromptSession
                session = PromptSession()
                delete_cal_input = await session.prompt_async("Delete calendar too? (y/N): ")
                delete_calendar = delete_cal_input.strip().lower() == 'y'
            except (EOFError, KeyboardInterrupt):
                console.print("   Keeping calendar", style="dim")

        if delete_calendar:
            try:
                from promaia.gcal import get_calendar_manager, google_account_for_workspace
                calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))

                if calendar_mgr.delete_agent_calendar(agent.calendar_id):
                    console.print("   ✅ Deleted calendar", style="green")
                else:
                    console.print("   ⚠️  Failed to delete calendar", style="yellow")
            except Exception as e:
                console.print(f"   ⚠️  Calendar deletion failed: {e}", style="yellow")
        else:
            console.print("   ℹ️  Calendar preserved (you can delete manually in Google Calendar)", style="dim")

    # Delete the agent (also cleans up orphaned database entries in config)
    deleted = delete_agent(args.name)

    if deleted:
        console.print(f"\n✅ Agent '{args.name}' removed", style="green")
    else:
        console.print(f"\n❌ Failed to remove agent '{args.name}'", style="red")


async def handle_agent_enable(args):
    """
    Enable a scheduled agent.

    Usage:
        maia agent enable <name>
    """
    agent = get_agent(args.name)

    if not agent:
        print(f"❌ Agent '{args.name}' not found")
        return

    agent.enabled = True
    save_agent(agent)

    print(f"✅ Agent '{args.name}' enabled")


async def handle_agent_disable(args):
    """
    Disable a scheduled agent.

    Usage:
        maia agent disable <name>
    """
    agent = get_agent(args.name)

    if not agent:
        print(f"❌ Agent '{args.name}' not found")
        return

    agent.enabled = False
    save_agent(agent)

    print(f"⏸️  Agent '{args.name}' disabled")


async def handle_agent_info_scheduled(args):
    """
    Show detailed information about a scheduled agent.

    Usage:
        maia agent info-scheduled <name>
    """
    from promaia.cli.schedule_grid_selector import schedule_to_string

    agent = get_agent(args.name)

    if not agent:
        print(f"❌ Agent '{args.name}' not found")
        return

    tracker = ExecutionTracker()
    stats = tracker.get_agent_stats(agent.name)

    print(f"\n🤖 Agent: {agent.name}\n")

    print(f"Status: {'✅ Enabled' if agent.enabled else '⏸️  Disabled'}")
    print(f"Workspace: {agent.workspace}")

    # Show schedule or interval
    if agent.schedule:
        schedule_display = schedule_to_string(agent.schedule)
        print(f"Schedule: {len(agent.schedule)} runs/week")
        print(f"  {schedule_display}")
    elif agent.interval_minutes:
        print(f"Interval: every {agent.interval_minutes} minutes")

    print(f"Max Iterations: {agent.max_iterations}")

    print(f"\nDatabases ({len(agent.databases)}):")
    for db in agent.databases:
        print(f"  - {db}")

    if agent.mcp_tools:
        print(f"\nMCP Tools ({len(agent.mcp_tools)}):")
        for tool in agent.mcp_tools:
            print(f"  - {tool}")

    if agent.messaging_enabled and agent.messaging_platform:
        print(f"\nMessaging: {agent.messaging_platform.title()}")
    print(f"Journal Memory: {agent.journal_memory_days} days")

    if agent.description:
        print(f"\nDescription: {agent.description}")

    # Show calendar info
    if agent.calendar_id:
        print(f"\nGoogle Calendar:")
        print(f"  Calendar ID: {agent.calendar_id}")
        print(f"  URL: https://calendar.google.com")
        print(f"  (Look for '{agent.name}' in sidebar)")
        if agent.calendar_event_ids:
            event_count = len(agent.calendar_event_ids.split(','))
            print(f"  Events: {event_count} recurring event(s)")

    print(f"\nPrompt:")
    prompt_preview = agent.prompt_file[:200] if len(agent.prompt_file) < 500 else agent.prompt_file[:200] + "..."
    print(f"  {prompt_preview}")

    print(f"\nCreated: {agent.created_at}")

    if agent.last_run_at:
        print(f"Last Run: {agent.last_run_at}")

    if stats.get('total_runs', 0) > 0:
        print(f"\n📊 Statistics:")
        print(f"  Total Runs: {stats['total_runs']}")
        print(f"  Successful: {stats['successful_runs']}")
        print(f"  Failed: {stats['failed_runs']}")
        print(f"  Success Rate: {stats['success_rate']:.1f}%")
        print(f"  Avg Cost: ${stats['avg_cost']:.4f}")
        print(f"  Total Cost: ${stats['total_cost']:.4f}")
        print(f"  Last Status: {stats['last_run_status']}")


async def handle_scheduler_start(args):
    """
    Start the agent scheduler daemon.

    Usage:
        maia agent-scheduler-start
    """
    from promaia.utils.display import print_text

    # Check if already running
    if is_scheduler_running():
        print_text("❌ Scheduler is already running", style="red")
        return

    # Check if there are enabled agents
    agents = load_agents()
    enabled_agents = [a for a in agents if a.enabled]

    if not enabled_agents:
        print_text("⚠️  No enabled agents found. Create and enable agents first.", style="yellow")
        return

    print_text(f"\n🚀 Starting scheduler with {len(enabled_agents)} enabled agents...\n", style="bold cyan")

    for agent in enabled_agents:
        print(f"  ✓ {agent.name} (every {agent.interval_minutes} min)")

    print()

    # Start the scheduler
    try:
        await run_scheduler_daemon()
    except KeyboardInterrupt:
        print_text("\n⚠️  Scheduler stopped by user", style="yellow")


async def handle_scheduler_stop(args):
    """
    Stop the agent scheduler daemon.

    Usage:
        maia agent-scheduler-stop
    """
    from promaia.utils.display import print_text

    if not is_scheduler_running():
        print_text("⚠️  Scheduler is not running", style="yellow")
        return

    print_text("🛑 Stopping scheduler...", style="cyan")

    if stop_scheduler():
        print_text("✅ Scheduler stopped", style="green")
    else:
        print_text("❌ Failed to stop scheduler", style="red")


async def handle_scheduler_status(args):
    """
    Check scheduler status.

    Usage:
        maia agent-scheduler-status
    """
    from promaia.utils.display import print_text

    if is_scheduler_running():
        print_text("✅ Scheduler is running", style="green")

        # Show enabled agents
        agents = load_agents()
        enabled_agents = [a for a in agents if a.enabled]

        if enabled_agents:
            print(f"\nEnabled agents ({len(enabled_agents)}):")
            for agent in enabled_agents:
                print(f"  ✓ {agent.name} (every {agent.interval_minutes} min)")
    else:
        print_text("⏸️  Scheduler is not running", style="yellow")

        # Show enabled agents that would run
        agents = load_agents()
        enabled_agents = [a for a in agents if a.enabled]

        if enabled_agents:
            print(f"\nEnabled agents ({len(enabled_agents)}) waiting to run:")
            for agent in enabled_agents:
                print(f"  ⏸️  {agent.name} (every {agent.interval_minutes} min)")
            print("\nUse 'maia agent-scheduler-start' to start the scheduler")
        else:
            print("\nNo enabled agents configured.")


async def handle_calendar_sync(args):
    """
    Sync an agent to Google Calendar.

    Usage:
        maia agent calendar-sync <name>
    """
    from promaia.utils.display import print_text
    from promaia.gcal import get_calendar_manager, google_account_for_workspace
    from rich.console import Console

    console = Console()

    agent = get_agent(args.name)
    if not agent:
        console.print(f"❌ Agent '{args.name}' not found", style="red")
        return

    if not agent.schedule:
        console.print(f"❌ Agent '{args.name}' has no schedule", style="red")
        console.print("   Only schedule-based agents can be synced to calendar", style="dim")
        return

    print_text(f"\n📅 Syncing agent '{args.name}' to Google Calendar...\n", style="bold cyan")

    try:
        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))

        # Ensure agent has a dedicated calendar
        calendar_id = agent.calendar_id
        if not calendar_id:
            console.print("Creating dedicated calendar for this agent...", style="dim")
            calendar_description = f"Automated schedule for {agent.name} agent"
            if agent.description:
                calendar_description += f"\n\n{agent.description}"

            calendar_id = calendar_mgr.create_agent_calendar(
                agent_name=agent.name,
                description=calendar_description
            )

            if calendar_id:
                agent.calendar_id = calendar_id
                save_agent(agent)
                console.print(f"   ✅ Created calendar", style="green")
            else:
                # Fall back to primary calendar
                console.print("   ⚠️  Failed to create calendar, using primary", style="yellow")
                calendar_id = "primary"

        # Remove existing events if any
        if agent.calendar_event_ids:
            console.print("Removing existing calendar events...", style="dim")
            calendar_mgr.delete_agent_events(args.name, calendar_id=calendar_id)

        # Create new events on agent's dedicated calendar
        event_ids = calendar_mgr.create_agent_event(
            agent_name=agent.name,
            schedule=agent.schedule,
            agent_config=agent.to_dict(),
            calendar_id=calendar_id
        )

        if event_ids:
            # Update agent config with event IDs
            agent.calendar_event_ids = event_ids
            save_agent(agent)

            console.print(f"\n✅ Agent '{args.name}' synced to Google Calendar!", style="green")
            console.print(f"   Created {len(agent.schedule)} recurring event(s)", style="dim")

            # Show calendar link
            console.print(f"   View calendar: https://calendar.google.com", style="cyan")
            console.print(f"   (Look for '{agent.name}' in 'My calendars')", style="dim")
        else:
            console.print(f"\n❌ Failed to sync agent to calendar", style="red")

    except Exception as e:
        console.print(f"\n❌ Error: {e}", style="red")
        import traceback
        traceback.print_exc()


async def handle_calendar_remove(args):
    """
    Remove an agent from Google Calendar.

    Usage:
        maia agent calendar-remove <name>
    """
    from promaia.utils.display import print_text
    from promaia.gcal import get_calendar_manager, google_account_for_workspace
    from rich.console import Console

    console = Console()

    agent = get_agent(args.name)
    if not agent:
        console.print(f"❌ Agent '{args.name}' not found", style="red")
        return

    if not agent.calendar_event_ids:
        console.print(f"⚠️  Agent '{args.name}' is not on Google Calendar", style="yellow")
        return

    print_text(f"\n📅 Removing agent '{args.name}' from Google Calendar...\n", style="bold cyan")

    try:
        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))

        # Use agent's dedicated calendar if available
        calendar_id = agent.calendar_id if agent.calendar_id else "primary"

        success = calendar_mgr.delete_agent_events(args.name, calendar_id=calendar_id)

        if success:
            # Clear event IDs from agent config
            agent.calendar_event_ids = None
            save_agent(agent)

            console.print(f"\n✅ Agent '{args.name}' removed from calendar", style="green")
        else:
            console.print(f"\n❌ Failed to remove agent from calendar", style="red")

    except Exception as e:
        console.print(f"\n❌ Error: {e}", style="red")


async def handle_calendar_list(args):
    """
    List agents on Google Calendar.

    Usage:
        maia agent calendar-list           # all accounts
        maia agent calendar-list <name>    # specific agent's account
    """
    from promaia.utils.display import print_text
    from promaia.gcal import get_calendar_manager, google_account_for_workspace
    from rich.console import Console
    from rich.table import Table

    console = Console()

    # If a specific agent was given, scope to that agent's account
    agent_name_filter = getattr(args, 'name', None)
    if agent_name_filter:
        agent = get_agent(agent_name_filter)
        if not agent:
            console.print(f"❌ Agent '{agent_name_filter}' not found", style="red")
            return
        agents_to_check = [agent]
    else:
        agents_to_check = [a for a in load_agents() if a.calendar_id and a.enabled]

    if not agents_to_check:
        console.print("No enabled agents with calendar integration", style="yellow")
        console.print("\nUse 'maia agent calendar-sync <name>' to add an agent", style="dim")
        return

    print_text("\n📅 Agents on Google Calendar\n", style="bold cyan")

    # Collect events across all relevant accounts (deduplicate by account)
    seen_accounts: set[str] = set()
    all_events = []

    for agent in agents_to_check:
        account = google_account_for_workspace(agent.workspace)
        account_key = (account or "").lower()
        if account_key in seen_accounts:
            continue
        seen_accounts.add(account_key)

        try:
            calendar_mgr = get_calendar_manager(account=account)
            events = calendar_mgr.list_agent_events()
            if events:
                all_events.extend(events)
        except Exception as e:
            console.print(f"⚠️  Failed to list events for account {account or 'default'}: {e}", style="yellow")

    if not all_events:
        console.print("No agents found on Google Calendar", style="yellow")
        console.print("\nUse 'maia agent calendar-sync <name>' to add an agent", style="dim")
        return

    table = Table(title=f"Found {len(all_events)} agent event(s)")
    table.add_column("Agent", style="cyan")
    table.add_column("Recurrence", style="white")
    table.add_column("Event ID", style="dim")

    for event in all_events:
        props = event.get('extendedProperties', {}).get('private', {})
        name = props.get('agent_name', 'Unknown')
        recurrence = event.get('recurrence', ['One-time'])[0]
        event_id = event['id'][:12] + "..."

        table.add_row(name, recurrence, event_id)

    console.print(table)
    console.print("\n💡 View full calendar: https://calendar.google.com", style="dim")


async def handle_calendar_monitor(args):
    """
    Run a foreground calendar monitor that triggers agents from calendar events.

    Usage:
        maia agent calendar-monitor
        maia agent calendar-monitor --interval 1 --window 120
    """
    import logging
    from promaia.gcal.agent_calendar_monitor import run_foreground

    # Ensure logs are visible in the console
    logging.basicConfig(
        level=logging.INFO if not getattr(args, "debug", False) else logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    interval = getattr(args, "interval", 1)
    window = getattr(args, "window", 120)

    try:
        await run_foreground(check_interval_minutes=interval, trigger_window_minutes=window)
    except KeyboardInterrupt:
        print("\n🛑 Calendar monitor stopped")


async def handle_run_calendar_event(args):
    """
    Run an upcoming calendar event's agent.  By default runs in the foreground
    with a live activity feed (like ``docker compose up``).  Pass ``-d`` to
    detach and run in the background.

    Usage:
        maia agent run          (foreground — shows live feed, auto-exits on completion)
        maia agent run -d       (detached — launches in background and exits)
        maia agent run 2        (run second upcoming event)
        maia agent run --orchestrate   (use multi-task orchestrator)
    """
    import subprocess
    import sys

    from promaia.utils.display import print_text
    from promaia.gcal import get_calendar_manager, google_account_for_workspace
    from rich.console import Console

    console = Console()

    use_orchestrator = getattr(args, 'orchestrate', False)
    n = getattr(args, 'n', 1) or 1

    print_text("\n🔍 Finding upcoming calendar events...\n", style="bold cyan")

    try:
        agents = load_agents()
        agents_with_calendars = [a for a in agents if a.calendar_id and a.enabled]

        if not agents_with_calendars:
            console.print("❌ No enabled agents with calendar integration", style="red")
            return

        # Collect ALL upcoming events across all agent calendars
        from datetime import datetime
        all_events = []

        for agent in agents_with_calendars:
            calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))
            upcoming = calendar_mgr.get_upcoming_agent_runs(
                hours_ahead=24,
                calendar_id=agent.calendar_id,
            )

            for event in upcoming:
                start_raw = event.get("start")
                if not start_raw:
                    continue

                start_time = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                all_events.append((start_time, event, agent))

        if not all_events:
            console.print("❌ No upcoming calendar events found in the next 24 hours", style="yellow")
            console.print("\nTip: Create an event in your agent's Google Calendar", style="dim")
            return

        # Sort by start time
        all_events.sort(key=lambda x: x[0])

        # Show numbered list of upcoming events
        console.print(f"[bold]Upcoming events ({len(all_events)}):[/bold]\n")
        for i, (start_time, event, agent) in enumerate(all_events, 1):
            summary = event.get("summary") or "No title"
            marker = " 👈" if i == n else ""
            console.print(f"  {i}. [{agent.name}] {summary}  —  {start_time.strftime('%H:%M')}{marker}")

        console.print()

        # Validate n
        if n < 1 or n > len(all_events):
            console.print(f"❌ Event #{n} not found. Choose 1–{len(all_events)}.", style="red")
            return

        # Select the Nth event
        selected_time, selected_event, selected_agent = all_events[n - 1]

        summary = selected_event.get("summary") or "No title"
        description = (selected_event.get("description") or "").strip()
        link = selected_event.get("html_link") or ""
        event_id = selected_event.get("event_id")

        console.print(f"📅 Running: [bold]{summary}[/bold]")
        console.print(f"   Agent: [cyan]{selected_agent.name}[/cyan]")
        console.print(f"   Scheduled: {selected_time.strftime('%Y-%m-%d %H:%M')}")
        if description:
            console.print(f"   Description: {description[:100]}...")

        # Build goal and metadata for the background process
        run_request = description or summary or "Run based on your system instructions."
        run_metadata = {
            "calendar_event_id": event_id,
            "calendar_event_start": selected_event.get("start"),
            "calendar_event_summary": summary,
            "calendar_event_link": link,
        }

        # Spawn a detached background process
        cmd = [
            sys.executable, "-m", "promaia.agents.run_goal",
            "--agent", selected_agent.name,
            "--goal", run_request,
            "--metadata-json", json.dumps(run_metadata),
        ]
        if use_orchestrator:
            cmd.append("--orchestrate")

        detach = getattr(args, 'detach', False)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach — Ctrl-C won't kill the agent
        )

        # Write PID file so `maia agent stop` can find it
        from promaia.utils.env_writer import get_data_dir
        pid_dir = get_data_dir() / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / f"{selected_agent.name}.pid"
        pid_file.write_text(str(process.pid))

        if detach:
            console.print(f"\n🚀 Agent launched (PID {process.pid})")
            console.print(f"\n📡 View activity with: [bold]maia agent feed {selected_agent.name}[/bold]\n")
        else:
            # Foreground mode: show the feed inline until the agent finishes
            console.print(f"\n🚀 Agent running (PID {process.pid})")
            console.print("[dim]Press Ctrl+C to detach (agent keeps running)[/dim]\n")
            from promaia.agents.feed_aggregator import FeedAggregator
            filters = {'agent': selected_agent.name}
            aggregator = FeedAggregator(show_timestamps=False, stop_on_complete=True)
            try:
                await aggregator.start_feed(filters)
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print(f"\n[dim]Detached. Agent still running (PID {process.pid})[/dim]")
                console.print(f"📡 View activity with: [bold]maia agent feed {selected_agent.name}[/bold]\n")

    except Exception as e:
        console.print(f"\n❌ Error: {e}", style="red")
        import traceback
        traceback.print_exc()


async def handle_calendar_share(args):
    """
    Share an agent's calendar with a team member.

    Usage:
        maia agent calendar-share <name>
    """
    from promaia.utils.display import print_text
    from promaia.gcal import get_calendar_manager, google_account_for_workspace
    from rich.console import Console
    from prompt_toolkit import PromptSession

    console = Console()

    agent = get_agent(args.name)
    if not agent:
        console.print(f"❌ Agent '{args.name}' not found", style="red")
        return

    if not agent.calendar_id:
        console.print(f"❌ Agent '{args.name}' doesn't have a dedicated calendar", style="red")
        console.print(f"   Run 'maia agent calendar-sync {args.name}' to create one first", style="dim")
        return

    print_text(f"\n👥 Share Calendar: {agent.name}\n", style="bold cyan")

    session = PromptSession()

    try:
        # Get email address
        console.print("Email address to share with:")
        email = await session.prompt_async("› ")
        email = email.strip()

        if not email or '@' not in email:
            console.print("❌ Invalid email address", style="red")
            return

        # Get permission level
        console.print("\nPermission level:")
        console.print("  1. View only (can see schedule)")
        console.print("  2. Edit (can modify schedule)")
        console.print()

        choice = await session.prompt_async("Select (1-2): ")
        choice = choice.strip()

        if choice not in ['1', '2']:
            console.print("❌ Invalid choice", style="red")
            return

        role = 'writer' if choice == '2' else 'reader'
        role_display = 'Edit' if choice == '2' else 'View only'

        # Share the calendar
        console.print(f"\n📤 Sharing calendar with {email} ({role_display})...", style="dim")

        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))
        success = calendar_mgr.share_calendar(agent.calendar_id, email, role)

        if success:
            console.print(f"\n✅ Calendar shared successfully!", style="green")
            console.print(f"   {email} can now {role_display.lower()} the '{agent.name}' calendar", style="dim")
        else:
            console.print(f"\n❌ Failed to share calendar", style="red")

    except (EOFError, KeyboardInterrupt):
        console.print("\n❌ Cancelled", style="red")
    except Exception as e:
        console.print(f"\n❌ Error: {e}", style="red")


async def handle_sync_prompts(args):
    """
    Sync all Notion-backed prompts with their source pages.

    Usage:
        maia agent sync-prompts
        maia agent sync-prompts --workspace <workspace>
    """
    from promaia.utils.display import print_text
    from promaia.cli.notion_prompt_manager import sync_all_notion_prompts
    from rich.console import Console

    console = Console()

    workspace = getattr(args, 'workspace', None)

    print_text("\n🔄 Syncing Notion-backed prompts...\n", style="bold cyan")

    try:
        results = await sync_all_notion_prompts(workspace=workspace)

        # Display results
        if results['synced']:
            console.print(f"\n✅ Synced {len(results['synced'])} prompt(s):", style="green")
            for filename in results['synced']:
                console.print(f"  ✓ {filename}", style="dim")

        if results['failed']:
            console.print(f"\n❌ Failed to sync {len(results['failed'])} prompt(s):", style="red")
            for filename in results['failed']:
                console.print(f"  ✗ {filename}", style="dim")

        if results['skipped']:
            console.print(f"\n⏭️  Skipped {len(results['skipped'])} file(s) (not Notion-backed or sync disabled)", style="yellow")

        if not results['synced'] and not results['failed']:
            console.print("No Notion-backed prompts found to sync", style="yellow")

    except Exception as e:
        console.print(f"\n❌ Error syncing prompts: {e}", style="red")
        import traceback
        traceback.print_exc()


async def select_agent_for_edit() -> Optional[str]:
    """Interactive selector to choose an agent for editing."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.formatted_text import FormattedText

    agents = load_agents()

    if not agents:
        print("❌ No agents configured")
        return None

    if len(agents) == 1:
        # Only one agent, auto-select
        return agents[0].name

    current_index = [0]
    selected_agent = [None]

    def get_formatted_text():
        """Generate the formatted text for the agent list."""
        lines = [
            ('class:header', '🤖 Select Agent to Edit\n'),
            ('', '\n'),
        ]

        for i, agent in enumerate(agents):
            prefix = '→ ' if i == current_index[0] else '  '
            style = 'class:selected' if i == current_index[0] else 'class:normal'

            schedule_info = ""
            if agent.schedule:
                schedule_info = f" | {len(agent.schedule)} runs/week"
            elif agent.interval_minutes:
                schedule_info = f" | Every {agent.interval_minutes}min"

            status = "✅" if agent.enabled else "⏸️"
            line = f"{prefix}{status} {agent.name} (@{agent.agent_id}) | {agent.workspace}{schedule_info}\n"
            lines.append((style, line))

        lines.append(('', '\n'))
        lines.append(('class:status', '↑↓: Navigate | ENTER: Select | q: Cancel'))

        return FormattedText(lines)

    kb = KeyBindings()

    @kb.add(Keys.Up)
    def move_up(event):
        current_index[0] = max(0, current_index[0] - 1)

    @kb.add(Keys.Down)
    def move_down(event):
        current_index[0] = min(len(agents) - 1, current_index[0] + 1)

    @kb.add(Keys.Enter)
    def select(event):
        selected_agent[0] = agents[current_index[0]].name
        event.app.exit()

    @kb.add('q')
    def cancel(event):
        event.app.exit()

    @kb.add(Keys.ControlC)
    def ctrl_c(event):
        event.app.exit()

    # Create layout
    text_window = Window(
        content=FormattedTextControl(
            get_formatted_text,
            focusable=True
        ),
        always_hide_cursor=True
    )

    layout = Layout(HSplit([text_window]))

    # Create and run application
    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        mouse_support=False
    )

    await app.run_async()

    return selected_agent[0]


async def handle_agent_edit(args):
    """
    Interactively edit an existing scheduled agent.

    Usage:
        maia agent edit [agent_name]
    """
    from promaia.utils.display import print_text
    from promaia.cli.agent_creation_selector import (
        select_databases,
        select_mcp_tools,
        fetch_discord_channels,
    )
    from promaia.cli.schedule_grid_selector import select_schedule, schedule_to_string
    from promaia.config.databases import get_database_manager
    from rich.console import Console

    console = Console()

    # If no name provided, show selector
    agent_name = args.name if hasattr(args, 'name') and args.name else None

    if not agent_name:
        agent_name = await select_agent_for_edit()
        if not agent_name:
            console.print("❌ Cancelled", style="red")
            return

    # Load the agent - try by name first, then by agent_id
    agent = get_agent(agent_name)
    if not agent:
        # Try finding by agent_id
        agents = load_agents()
        for a in agents:
            if a.agent_id == agent_name:
                agent = a
                break

    if not agent:
        console.print(f"❌ Agent '{agent_name}' not found", style="red")
        console.print("   Available agents:", style="dim")
        agents = load_agents()
        for a in agents:
            console.print(f"     • {a.name} (@{a.agent_id})", style="dim")
        return

    print_text(f"\n✏️  Edit Agent: {agent.name}\n", style="bold cyan")

    # Show current configuration
    console.print("[dim]Current configuration:[/dim]")
    console.print(f"  Name: [cyan]{agent.name}[/cyan]")
    console.print(f"  Agent ID: [cyan]@{agent.agent_id}[/cyan]")
    console.print(f"  Workspace: [cyan]{agent.workspace}[/cyan]")
    console.print(f"  Databases: [cyan]{', '.join(agent.databases)}[/cyan]")
    if agent.schedule:
        console.print(f"  Schedule: [cyan]{schedule_to_string(agent.schedule)}[/cyan]")
    console.print()

    # Ask what to edit
    from prompt_toolkit import PromptSession

    console.print("[bold]What would you like to edit?[/bold]")
    console.print("  1. Name")
    console.print("  2. Agent ID")
    console.print("  3. Databases")
    console.print("  4. Schedule")
    console.print("  5. MCP Tools")
    console.print("  6. Max Iterations")
    console.print("  7. Description")
    console.print("  8. All fields (full edit)")
    console.print("  9. Calendar Settings")
    console.print("  0. Cancel")
    console.print()

    session = PromptSession()
    try:
        choice = await session.prompt_async("Select option (0-9): ")
        choice = choice.strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n❌ Cancelled", style="red")
        return

    if choice == "0":
        console.print("❌ Cancelled", style="red")
        return

    # Edit based on choice
    if choice in ["1", "8"]:
        console.print(f"\nCurrent name: [cyan]{agent.name}[/cyan]")
        try:
            new_name = await session.prompt_async("New name (ENTER to keep): ")
            new_name = new_name.strip()
            if new_name:
                agent.name = new_name
                console.print(f"✓ Updated name to: [cyan]{new_name}[/cyan]", style="dim")
        except (EOFError, KeyboardInterrupt):
            pass

    if choice in ["2", "8"]:
        console.print(f"\nCurrent Agent ID: [cyan]@{agent.agent_id}[/cyan]")
        try:
            new_id = await session.prompt_async("New Agent ID (without @, ENTER to keep): ")
            new_id = new_id.strip()
            if new_id:
                agent.agent_id = new_id
                console.print(f"✓ Updated Agent ID to: [cyan]@{new_id}[/cyan]", style="dim")
        except (EOFError, KeyboardInterrupt):
            pass

    if choice in ["3", "8"]:
        console.print("\nSelect databases...")
        db_manager = get_database_manager()
        workspace_databases = db_manager.get_workspace_databases(agent.workspace)

        # Parse agent's current database settings to preserve days values
        agent_db_settings = {}
        for db_spec in agent.databases:
            if ':' in db_spec:
                db_name, days_str = db_spec.split(':', 1)
                agent_db_settings[db_name] = days_str
            else:
                agent_db_settings[db_spec] = None

        # Format databases for selector
        available_databases = []
        for db in workspace_databases:
            if db.browser_include:
                qualified_name = db.get_qualified_name()
                # Use agent's saved days value if this db is selected, otherwise use db default
                if qualified_name in agent_db_settings and agent_db_settings[qualified_name] is not None:
                    saved_days = agent_db_settings[qualified_name]
                    days_value = saved_days if saved_days == 'all' else int(saved_days)
                else:
                    days_value = db.default_days
                db_config = {
                    'name': qualified_name,
                    'default_days': days_value,
                    'default_include': qualified_name in agent_db_settings,
                }
                if hasattr(db, 'source_type'):
                    db_config['source_type'] = db.source_type
                available_databases.append(db_config)

        # Enrich Discord databases (pass preselected channel names for edit flow)
        preselected = set(agent_db_settings.keys())
        available_databases = await fetch_discord_channels(
            agent.workspace, available_databases, preselected_names=preselected
        )

        # select_databases expects (workspace, available_databases)
        selected_databases = await select_databases(agent.workspace, available_databases)
        if selected_databases:
            # Convert selection tuples [("db", "7"), ...] into legacy "db:days" strings
            agent.databases = [f"{db_name}:{days}" for db_name, days in selected_databases]
            console.print(f"✓ Updated databases: [cyan]{', '.join(agent.databases)}[/cyan]", style="dim")

    if choice in ["4", "8"]:
        console.print("\nSelect schedule...")
        new_schedule = await select_schedule()
        if new_schedule:
            agent.schedule = new_schedule
            console.print(f"✓ Updated schedule: [cyan]{schedule_to_string(new_schedule)}[/cyan]", style="dim")

    if choice in ["5", "8"]:
        console.print("\nSelect MCP tools...")
        # Load available MCP servers from mcp_servers.json + built-in integrations
        available_tools = []
        try:
            import json
            from promaia.agents.mcp_loader import _find_mcp_servers_json
            mcp_config_file = _find_mcp_servers_json()
            if mcp_config_file and mcp_config_file.is_file():
                with open(mcp_config_file, "r") as f:
                    mcp_config = json.load(f)
                    servers = mcp_config.get("servers", {})
                    available_tools = [
                        name for name, config in servers.items()
                        if config.get("enabled", True)
                    ]
        except Exception as e:
            logger.warning(f"Could not load MCP servers: {e}")

        # Add built-in integrations that have tool support
        for builtin in ("gmail", "calendar"):
            if builtin not in available_tools:
                available_tools.append(builtin)

        selected_tools = await select_mcp_tools(available_tools, preselected=agent.mcp_tools)
        if selected_tools is not None:  # Allow empty list
            agent.mcp_tools = selected_tools
            tools_display = ', '.join(selected_tools) if selected_tools else "None"
            console.print(f"✓ Updated MCP tools: [cyan]{tools_display}[/cyan]", style="dim")

    if choice in ["6", "8"]:
        console.print(f"\nCurrent max iterations: [cyan]{agent.max_iterations}[/cyan]")
        try:
            new_max = await session.prompt_async("New max iterations (ENTER to keep): ")
            new_max = new_max.strip()
            if new_max and new_max.isdigit():
                agent.max_iterations = int(new_max)
                console.print(f"✓ Updated max iterations: [cyan]{new_max}[/cyan]", style="dim")
        except (EOFError, KeyboardInterrupt):
            pass

    if choice in ["7", "8"]:
        console.print(f"\nCurrent description: [dim]{agent.description}[/dim]")
        try:
            new_desc = await session.prompt_async("New description (ENTER to keep): ")
            new_desc = new_desc.strip()
            if new_desc:
                agent.description = new_desc
                console.print(f"✓ Updated description", style="dim")
        except (EOFError, KeyboardInterrupt):
            pass

    if choice == "9":
        console.print("\n📅 Calendar Settings", style="bold cyan")

        if agent.calendar_id:
            console.print(f"\n   Current calendar ID: [cyan]{agent.calendar_id}[/cyan]")
            console.print(f"   URL: https://calendar.google.com")
            console.print(f"   (Look for '{agent.name}' in sidebar)", style="dim")

            if agent.calendar_event_ids:
                event_count = len(agent.calendar_event_ids.split(','))
                console.print(f"   Events: {event_count} recurring event(s)")

            console.print("\n[bold]Calendar Actions:[/bold]")
            console.print("  1. Share calendar with team member")
            console.print("  2. View calendar URL")
            console.print("  3. Recreate calendar (deletes and creates new)")
            console.print("  0. Back")

            try:
                cal_choice = await session.prompt_async("\nSelect option (0-3): ")
                cal_choice = cal_choice.strip()

                if cal_choice == "1":
                    # Share calendar
                    console.print("\nEmail address to share with:")
                    email = await session.prompt_async("› ")
                    email = email.strip()

                    if email and '@' in email:
                        console.print("\nPermission level:")
                        console.print("  1. View only")
                        console.print("  2. Edit")

                        perm_choice = await session.prompt_async("Select (1-2): ")
                        role = 'writer' if perm_choice.strip() == '2' else 'reader'

                        from promaia.gcal import get_calendar_manager, google_account_for_workspace
                        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))

                        if calendar_mgr.share_calendar(agent.calendar_id, email, role):
                            console.print(f"\n✅ Shared calendar with {email}", style="green")
                        else:
                            console.print(f"\n❌ Failed to share calendar", style="red")

                elif cal_choice == "2":
                    # Show URL
                    console.print(f"\n📆 Calendar URL:", style="bold")
                    console.print(f"   https://calendar.google.com", style="cyan")
                    console.print(f"   (Look for '{agent.name}' in sidebar)", style="dim")

                elif cal_choice == "3":
                    # Recreate calendar
                    console.print("\n⚠️  This will delete the existing calendar and create a new one", style="yellow")
                    confirm = await session.prompt_async("Continue? (y/N): ")

                    if confirm.strip().lower() == 'y':
                        from promaia.gcal import get_calendar_manager, google_account_for_workspace
                        calendar_mgr = get_calendar_manager(account=google_account_for_workspace(agent.workspace))

                        # Delete old calendar
                        calendar_mgr.delete_agent_calendar(agent.calendar_id)

                        # Create new calendar
                        new_calendar_id = calendar_mgr.create_agent_calendar(
                            agent_name=agent.name,
                            description=agent.description or f"Automated schedule for {agent.name}"
                        )

                        if new_calendar_id:
                            agent.calendar_id = new_calendar_id
                            agent.calendar_event_ids = None  # Clear old events
                            console.print(f"\n✅ Calendar recreated", style="green")
                            console.print(f"   New calendar ID: {new_calendar_id}")
                        else:
                            console.print(f"\n❌ Failed to create new calendar", style="red")

            except (EOFError, KeyboardInterrupt):
                console.print("\n❌ Cancelled", style="yellow")

        else:
            console.print(f"\n   ⚠️  No calendar associated with this agent", style="yellow")
            console.print(f"   Run 'maia agent calendar-sync {agent.name}' to create one", style="dim")

    # Save changes
    console.print()
    console.print("💾 Saving changes...")
    save_agent(agent)

    console.print("✅ Agent updated successfully!", style="green")
    console.print()
    console.print("[dim]To sync changes to Notion:[/dim]")
    console.print(f"  [For future implementation: maia agent sync-to-notion '{agent.name}']")


async def handle_agent_stop(args):
    """
    Stop running agent(s) by sending SIGTERM, then SIGKILL if needed.

    Usage:
        maia agent stop              # stop all running agents
        maia agent stop chief-of-staff  # stop a specific agent by name
    """
    import sqlite3
    from rich.console import Console

    console = Console()

    from promaia.utils.env_writer import get_data_dir
    pid_dir = get_data_dir() / "agents"

    if not pid_dir.exists():
        console.print("No agents are running.", style="yellow")
        return

    # Collect PID files
    name_filter = getattr(args, 'name', None)
    if name_filter:
        pid_files = list(pid_dir.glob(f"{name_filter}.pid"))
        if not pid_files:
            console.print(f"No running agent found with name '{name_filter}'", style="yellow")
            return
    else:
        pid_files = list(pid_dir.glob("*.pid"))
        if not pid_files:
            console.print("No agents are running.", style="yellow")
            return

    stopped = 0

    for pid_file in pid_files:
        agent_name = pid_file.stem
        try:
            pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            console.print(f"  ⚠️  Bad PID file for {agent_name}, cleaning up", style="yellow")
            pid_file.unlink(missing_ok=True)
            continue

        # Check if process is alive
        alive = _is_pid_alive(pid)

        if not alive:
            console.print(f"  {agent_name}: process {pid} already exited, cleaning up PID file")
            pid_file.unlink(missing_ok=True)
            _cancel_agent_in_db(agent_name, console)
            stopped += 1
            continue

        # Send SIGTERM for graceful shutdown
        console.print(f"  {agent_name}: sending SIGTERM to PID {pid}...")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            console.print(f"  {agent_name}: process already exited")
            pid_file.unlink(missing_ok=True)
            _cancel_agent_in_db(agent_name, console)
            stopped += 1
            continue
        except PermissionError:
            console.print(f"  ⚠️  {agent_name}: permission denied (PID {pid})", style="red")
            continue

        # Wait up to 5 seconds for graceful exit
        for _ in range(50):
            if not _is_pid_alive(pid):
                break
            time.sleep(0.1)

        if _is_pid_alive(pid):
            console.print(f"  {agent_name}: still alive, sending SIGKILL...")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        pid_file.unlink(missing_ok=True)
        _cancel_agent_in_db(agent_name, console)
        console.print(f"  ✅ {agent_name} stopped", style="green")
        stopped += 1

    if stopped:
        console.print(f"\n🛑 Stopped {stopped} agent(s)", style="bold")
    else:
        console.print("No agents were stopped.", style="yellow")


def _is_pid_alive(pid: int) -> bool:
    """Check whether *pid* refers to a running process."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _cancel_agent_in_db(agent_name: str, console):
    """Cancel active goals and conversations for the agent in the DB."""
    import sqlite3

    agent = get_agent(agent_name)
    if not agent:
        return

    agent_id = agent.agent_id or agent.name

    # Cancel goals
    try:
        from promaia.agents.task_queue import TaskQueue
        tq = TaskQueue()
        active_goals = tq.get_active_goals()
        agent_goals = [g for g in active_goals if g.agent_id == agent_id]
        for goal in agent_goals:
            tq.cancel_goal(goal.id)
        if agent_goals:
            console.print(f"    cancelled {len(agent_goals)} goal(s)", style="dim")
    except Exception as e:
        logger.warning(f"Could not cancel goals for {agent_name}: {e}")

    # Cancel conversations
    try:
        from promaia.utils.env_writer import get_conversations_db_path
        db_path = get_conversations_db_path()
        if db_path.exists():
            from datetime import timezone
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE conversations SET status = 'completed', "
                    "completion_reason = 'stopped', "
                    "completed_at = ? "
                    "WHERE agent_id = ? AND status = 'active'",
                    (now, agent_id),
                )
                cancelled = cursor.rowcount
                conn.commit()
                if cancelled:
                    console.print(f"    cancelled {cancelled} conversation(s)", style="dim")
    except Exception as e:
        logger.warning(f"Could not cancel conversations for {agent_name}: {e}")


def add_scheduled_agent_commands(agent_subparsers):
    """Add scheduled agent commands to the agent subparser.

    Args:
        agent_subparsers: The subparsers object from the 'agent' command
    """
    # Add command
    add_parser = agent_subparsers.add_parser('add', help='Create a new scheduled agent')
    add_parser.set_defaults(func=handle_agent_add)

    # Edit command
    edit_parser = agent_subparsers.add_parser('edit', help='Edit an existing scheduled agent')
    edit_parser.add_argument('name', nargs='?', help='Agent name (optional - will show selector if omitted)')
    edit_parser.set_defaults(func=handle_agent_edit)

    # List command (for scheduled agents)
    list_scheduled_parser = agent_subparsers.add_parser('list-scheduled', help='List all scheduled agents')
    list_scheduled_parser.set_defaults(func=handle_agent_list_scheduled)

    # Run command (for scheduled agents)
    run_scheduled_parser = agent_subparsers.add_parser('run-scheduled', help='Manually run a scheduled agent')
    run_scheduled_parser.add_argument('name', help='Agent name')
    run_scheduled_parser.set_defaults(func=handle_agent_run_scheduled)

    # Logs command (for scheduled agents)
    logs_scheduled_parser = agent_subparsers.add_parser('logs-scheduled', help='View execution logs for scheduled agent')
    logs_scheduled_parser.add_argument('name', help='Agent name')
    logs_scheduled_parser.add_argument('--limit', '-l', type=int, default=20, help='Number of logs to show')
    logs_scheduled_parser.set_defaults(func=handle_agent_logs_scheduled)

    # Remove command (for agents)
    remove_parser = agent_subparsers.add_parser('remove', help='Remove an agent')
    remove_parser.add_argument('name', help='Agent name')
    remove_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation')
    remove_parser.set_defaults(func=handle_agent_remove_scheduled)

    # Enable command
    enable_parser = agent_subparsers.add_parser('enable', help='Enable a scheduled agent')
    enable_parser.add_argument('name', help='Agent name')
    enable_parser.set_defaults(func=handle_agent_enable)

    # Disable command
    disable_parser = agent_subparsers.add_parser('disable', help='Disable a scheduled agent')
    disable_parser.add_argument('name', help='Agent name')
    disable_parser.set_defaults(func=handle_agent_disable)

    # Info command (for scheduled agents)
    info_scheduled_parser = agent_subparsers.add_parser('info-scheduled', help='Show scheduled agent details')
    info_scheduled_parser.add_argument('name', help='Agent name')
    info_scheduled_parser.set_defaults(func=handle_agent_info_scheduled)

    # Scheduler commands
    scheduler_start_parser = agent_subparsers.add_parser('scheduler-start', help='Start the agent scheduler daemon')
    scheduler_start_parser.set_defaults(func=handle_scheduler_start)

    scheduler_stop_parser = agent_subparsers.add_parser('scheduler-stop', help='Stop the agent scheduler daemon')
    scheduler_stop_parser.set_defaults(func=handle_scheduler_stop)

    scheduler_status_parser = agent_subparsers.add_parser('scheduler-status', help='Check scheduler status')
    scheduler_status_parser.set_defaults(func=handle_scheduler_status)

    # Sync prompts command
    sync_prompts_parser = agent_subparsers.add_parser('sync-prompts', help='Sync Notion-backed prompts')
    sync_prompts_parser.add_argument('--workspace', '-w', help='Workspace for auth context')
    sync_prompts_parser.set_defaults(func=handle_sync_prompts)

    # Calendar commands
    calendar_sync_parser = agent_subparsers.add_parser('calendar-sync', help='Sync agent to Google Calendar')
    calendar_sync_parser.add_argument('name', help='Agent name')
    calendar_sync_parser.set_defaults(func=handle_calendar_sync)

    calendar_remove_parser = agent_subparsers.add_parser('calendar-remove', help='Remove agent from Google Calendar')
    calendar_remove_parser.add_argument('name', help='Agent name')
    calendar_remove_parser.set_defaults(func=handle_calendar_remove)

    calendar_list_parser = agent_subparsers.add_parser('calendar-list', help='List agents on Google Calendar')
    calendar_list_parser.add_argument('name', nargs='?', default=None, help='Agent name (lists all accounts if omitted)')
    calendar_list_parser.set_defaults(func=handle_calendar_list)

    calendar_monitor_parser = agent_subparsers.add_parser('calendar-monitor', help='Foreground monitor: trigger agents from calendar events (shows live logs)')
    calendar_monitor_parser.add_argument('--interval', '-i', type=int, default=1, help='Check interval in minutes (default: 1)')
    calendar_monitor_parser.add_argument('--window', '-w', type=int, default=5, help='Trigger window in minutes after start time to allow late triggers (default: 5)')
    calendar_monitor_parser.add_argument('--debug', action='store_true', help='Enable debug logs')
    calendar_monitor_parser.set_defaults(func=handle_calendar_monitor)

    calendar_share_parser = agent_subparsers.add_parser('calendar-share', help='Share agent calendar with team member')
    calendar_share_parser.add_argument('name', help='Agent name')
    calendar_share_parser.set_defaults(func=handle_calendar_share)

    # Stop running agent(s)
    stop_parser = agent_subparsers.add_parser('stop', help='Stop running agent(s)')
    stop_parser.add_argument('name', nargs='?', default=None,
                             help='Agent name to stop (omit to stop all)')
    stop_parser.set_defaults(func=handle_agent_stop)

    # Run Nth upcoming calendar event
    run_parser = agent_subparsers.add_parser('run', help='Run an upcoming calendar event\'s agent (foreground by default)')
    run_parser.add_argument('n', nargs='?', type=int, default=1,
                            help='Which upcoming event to run (1=next, 2=second, etc.). Default: 1')
    run_parser.add_argument('-d', '--detach', action='store_true',
                            help='Detach: launch in background and exit immediately (like docker compose up -d)')
    run_parser.add_argument('--orchestrate', action='store_true',
                            help='Use multi-task orchestrator (for long-horizon goals with async Slack conversations)')
    run_parser.add_argument('--no-orchestrate', action='store_true',
                            help=argparse.SUPPRESS)  # Legacy, now a no-op
    run_parser.set_defaults(func=handle_run_calendar_event)

    # Push command (push agent's local markdown to Notion)
    push_parser = agent_subparsers.add_parser('push', help='Push agent\'s local markdown changes to Notion')
    push_parser.add_argument('name', help='Agent name or agent ID')
    push_parser.add_argument('--force', action='store_true', help='Force push all files regardless of changes')
    push_parser.set_defaults(func=handle_agent_push)

    # Feed command (per-agent live activity feed)
    feed_parser = agent_subparsers.add_parser('feed', help='Live activity feed for an agent')
    feed_parser.add_argument('name', help='Agent name to watch')
    feed_parser.add_argument('--verbose', '-v', action='store_true', help='Show all implementation details')
    feed_parser.add_argument('--timestamps', action='store_true', help='Show timestamps on every line')
    feed_parser.set_defaults(func=handle_agent_feed)
