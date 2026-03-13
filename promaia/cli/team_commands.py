"""
CLI commands for team/user management.

Allows syncing users from Slack/Discord and managing the team list.
"""

import asyncio
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def handle_team_sync(args):
    """
    Sync team members from connected platforms.

    Usage:
        maia team sync              # Sync from all configured platforms
        maia team sync --slack      # Sync from Slack only
        maia team sync --discord    # Sync from Discord only
    """
    from rich.console import Console
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    sync_slack = getattr(args, 'slack', False) or not (getattr(args, 'slack', False) or getattr(args, 'discord', False))
    sync_discord = getattr(args, 'discord', False) or not (getattr(args, 'slack', False) or getattr(args, 'discord', False))

    console.print("\n🔄 Syncing team members...\n", style="bold cyan")

    total_added = 0
    total_updated = 0

    total_channels = 0

    # Sync from Slack
    if sync_slack:
        slack_token = os.environ.get('SLACK_BOT_TOKEN')
        if slack_token:
            console.print("📱 Syncing from Slack...", style="dim")
            try:
                result = await team.sync_from_slack(slack_token)
                console.print(f"   ✅ Slack members: {result['added']} added, {result['updated']} updated", style="green")
                total_added += result['added']
                total_updated += result['updated']
            except Exception as e:
                console.print(f"   ❌ Slack member sync failed: {e}", style="red")

            # Also sync channels
            try:
                ch_result = await team.sync_channels_from_slack(slack_token)
                console.print(f"   ✅ Slack channels: {ch_result['added']} added, {ch_result['updated']} updated", style="green")
                total_channels = ch_result['total']
            except Exception as e:
                console.print(f"   ❌ Slack channel sync failed: {e}", style="red")
        else:
            console.print("   ⏭️  Skipping Slack (SLACK_BOT_TOKEN not set)", style="yellow")

    # Sync from Discord
    if sync_discord:
        discord_token = os.environ.get('DISCORD_BOT_TOKEN')
        discord_guild = os.environ.get('DISCORD_GUILD_ID')
        if discord_token and discord_guild:
            console.print("🎮 Syncing from Discord...", style="dim")
            try:
                result = await team.sync_from_discord(discord_token, discord_guild)
                console.print(f"   ✅ Discord: {result['added']} added, {result['updated']} updated", style="green")
                total_added += result['added']
                total_updated += result['updated']
            except Exception as e:
                console.print(f"   ❌ Discord sync failed: {e}", style="red")
        else:
            if not discord_token:
                console.print("   ⏭️  Skipping Discord (DISCORD_BOT_TOKEN not set)", style="yellow")
            elif not discord_guild:
                console.print("   ⏭️  Skipping Discord (DISCORD_GUILD_ID not set)", style="yellow")

    # Summary
    members = team.list_members()
    channels = team.list_channels()
    console.print(f"\n✅ Sync complete!", style="green")
    console.print(f"   Added: {total_added}")
    console.print(f"   Updated: {total_updated}")
    console.print(f"   Total team members: {len(members)}")
    console.print(f"   Total channels: {len(channels)}")


async def handle_team_list(args):
    """
    List all team members.

    Usage:
        maia team list
        maia team list --bots    # Include bots
    """
    from rich.console import Console
    from rich.table import Table
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    include_bots = getattr(args, 'bots', False)
    members = team.list_members(include_bots=include_bots)

    if not members:
        console.print("\n⚠️  No team members found.", style="yellow")
        console.print("   Run 'maia team sync' to sync from Slack/Discord", style="dim")
        return

    # Create table
    table = Table(title=f"👥 Team Members ({len(members)})")
    table.add_column("Name", style="cyan")
    table.add_column("Slack", style="dim")
    table.add_column("Discord", style="dim")
    table.add_column("Email", style="dim")
    table.add_column("Timezone", style="dim")

    for member in members:
        slack = f"@{member.slack_username}" if member.slack_username else "-"
        discord = f"@{member.discord_username}" if member.discord_username else "-"
        email = member.email or "-"
        tz = member.timezone or "-"

        # Truncate long values
        if len(email) > 25:
            email = email[:22] + "..."
        if len(tz) > 15:
            tz = tz[:12] + "..."

        table.add_row(member.name, slack, discord, email, tz)

    console.print()
    console.print(table)
    console.print()


async def handle_team_channels(args):
    """
    List all known Slack channels.

    Usage:
        maia team channels
        maia team channels --member-only    # Only channels the bot is a member of
    """
    from rich.console import Console
    from rich.table import Table
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    member_only = getattr(args, 'member_only', False)
    channels = team.list_channels(member_only=member_only)

    if not channels:
        console.print("\n⚠️  No channels found.", style="yellow")
        console.print("   Run 'maia team sync' to sync from Slack", style="dim")
        return

    # Create table
    label = "bot-member" if member_only else "all"
    table = Table(title=f"📺 Slack Channels ({len(channels)}, {label})")
    table.add_column("Name", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Private", style="dim")
    table.add_column("Bot Member", style="dim")
    table.add_column("Topic", style="dim", max_width=40)

    for ch in channels:
        topic = (ch.topic or "-")[:40]
        table.add_row(
            f"#{ch.name}",
            ch.id,
            "yes" if ch.is_private else "no",
            "yes" if ch.is_member else "no",
            topic,
        )

    console.print()
    console.print(table)
    console.print()


async def handle_team_find(args):
    """
    Find a team member by name.

    Usage:
        maia team find "Koii"
        maia team find "john"
    """
    from rich.console import Console
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    query = args.name
    member = team.find_member(query)

    if not member:
        console.print(f"\n❌ No team member found matching '{query}'", style="red")

        # Suggest similar names
        members = team.list_members()
        suggestions = [m.name for m in members if query.lower() in m.name.lower()][:5]
        if suggestions:
            console.print(f"   Did you mean: {', '.join(suggestions)}?", style="dim")
        return

    console.print(f"\n👤 Found: {member.name}\n", style="bold cyan")
    console.print(f"   ID: {member.id}")
    if member.slack_id:
        console.print(f"   Slack: @{member.slack_username} ({member.slack_id})")
    if member.discord_id:
        console.print(f"   Discord: @{member.discord_username} ({member.discord_id})")
    if member.email:
        console.print(f"   Email: {member.email}")
    if member.timezone:
        console.print(f"   Timezone: {member.timezone}")
    if member.role:
        console.print(f"   Role: {member.role}")
    if member.aliases:
        console.print(f"   Aliases: {', '.join(member.aliases)}")
    if member.notes:
        console.print(f"   Notes: {member.notes}")
    console.print()


async def handle_team_add_alias(args):
    """
    Add an alias for a team member.

    Usage:
        maia team alias "Koii Benvenutto" "Koii"
        maia team alias "John Smith" "Johnny" "JS"
    """
    from rich.console import Console
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    name = args.name
    aliases = args.aliases

    member = team.find_member(name)
    if not member:
        console.print(f"\n❌ No team member found matching '{name}'", style="red")
        return

    # Add aliases
    for alias in aliases:
        if alias not in member.aliases:
            member.aliases.append(alias)

    team._save()

    console.print(f"\n✅ Added aliases to {member.name}:", style="green")
    console.print(f"   Aliases: {', '.join(member.aliases)}")


async def handle_team_set_note(args):
    """
    Set a note for a team member.

    Usage:
        maia team note "Koii" "Prefers async communication"
    """
    from rich.console import Console
    from promaia.config.team import get_team_manager

    console = Console()
    team = get_team_manager()

    name = args.name
    note = args.note

    member = team.find_member(name)
    if not member:
        console.print(f"\n❌ No team member found matching '{name}'", style="red")
        return

    member.notes = note
    team._save()

    console.print(f"\n✅ Updated note for {member.name}:", style="green")
    console.print(f"   Note: {member.notes}")


def add_team_commands(subparsers):
    """Add team management commands to the CLI."""

    # Create team subparser
    team_parser = subparsers.add_parser('team', help='Manage team members')
    team_subparsers = team_parser.add_subparsers(dest='team_command')

    # Sync command
    sync_parser = team_subparsers.add_parser('sync', help='Sync team from Slack/Discord')
    sync_parser.add_argument('--slack', action='store_true', help='Sync from Slack only')
    sync_parser.add_argument('--discord', action='store_true', help='Sync from Discord only')
    sync_parser.set_defaults(func=handle_team_sync)

    # List command
    list_parser = team_subparsers.add_parser('list', help='List all team members')
    list_parser.add_argument('--bots', action='store_true', help='Include bots')
    list_parser.set_defaults(func=handle_team_list)

    # Channels command
    channels_parser = team_subparsers.add_parser('channels', help='List Slack channels')
    channels_parser.add_argument('--member-only', action='store_true', help='Only show channels the bot is a member of')
    channels_parser.set_defaults(func=handle_team_channels)

    # Find command
    find_parser = team_subparsers.add_parser('find', help='Find a team member')
    find_parser.add_argument('name', help='Name to search for')
    find_parser.set_defaults(func=handle_team_find)

    # Alias command
    alias_parser = team_subparsers.add_parser('alias', help='Add alias for a team member')
    alias_parser.add_argument('name', help='Team member name')
    alias_parser.add_argument('aliases', nargs='+', help='Aliases to add')
    alias_parser.set_defaults(func=handle_team_add_alias)

    # Note command
    note_parser = team_subparsers.add_parser('note', help='Set note for a team member')
    note_parser.add_argument('name', help='Team member name')
    note_parser.add_argument('note', help='Note text')
    note_parser.set_defaults(func=handle_team_set_note)
