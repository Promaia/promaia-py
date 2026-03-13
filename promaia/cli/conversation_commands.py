"""
CLI commands for managing conversations.

Provides commands to:
- List active conversations
- View conversation details
- Manually end conversations
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def handle_conversation_list(args):
    """List all active conversations."""
    asyncio.run(_list_conversations())


async def _list_conversations():
    """List all active conversations from database."""
    import sqlite3
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table

    console = Console()

    # Get conversations database
    from promaia.utils.env_writer import get_conversations_db_path
    db_path = get_conversations_db_path()

    if not db_path.exists():
        console.print("[yellow]No conversations database found[/yellow]")
        return

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get active conversations
            cursor.execute("""
                SELECT id, agent_id, platform, channel_id, user_id, status,
                       turn_count, created_at, last_message_at,
                       orchestrator_task_id, completion_reason
                FROM conversations
                WHERE status IN ('active', 'waiting')
                ORDER BY created_at DESC
            """)

            rows = cursor.fetchall()

            if not rows:
                console.print("[green]No active conversations[/green]")
                return

            # Create table
            table = Table(title=f"Active Conversations ({len(rows)})")
            table.add_column("ID", style="cyan", no_wrap=True)
            table.add_column("Agent", style="yellow")
            table.add_column("Platform", style="blue")
            table.add_column("Channel", style="magenta")
            table.add_column("Status", style="green")
            table.add_column("Turns", justify="right")
            table.add_column("Started", style="dim")
            table.add_column("Task ID", style="dim")

            for row in rows:
                conv_id_short = row['id'][:20] + "..."
                created = datetime.fromisoformat(row['created_at']).strftime("%m/%d %H:%M")
                task_id = row['orchestrator_task_id'][:8] + "..." if row['orchestrator_task_id'] else "-"

                table.add_row(
                    conv_id_short,
                    row['agent_id'],
                    row['platform'],
                    row['channel_id'],
                    row['status'],
                    str(row['turn_count']),
                    created,
                    task_id
                )

            console.print(table)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.error(f"Error listing conversations: {e}", exc_info=True)


def handle_conversation_show(args):
    """Show details of a specific conversation."""
    asyncio.run(_show_conversation(args.conversation_id))


async def _show_conversation(conversation_id: str):
    """Show conversation details and transcript."""
    import sqlite3
    import json
    from pathlib import Path
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown

    console = Console()

    # Get conversations database
    from promaia.utils.env_writer import get_conversations_db_path
    db_path = get_conversations_db_path()

    if not db_path.exists():
        console.print("[red]No conversations database found[/red]")
        return

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Find conversation by prefix match
            cursor.execute("""
                SELECT * FROM conversations
                WHERE id LIKE ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (f"{conversation_id}%",))

            row = cursor.fetchone()

            if not row:
                console.print(f"[red]Conversation not found: {conversation_id}[/red]")
                return

            # Display info
            console.print(f"\n[bold cyan]Conversation: {row['id']}[/bold cyan]")
            console.print(f"Agent: {row['agent_id']}")
            console.print(f"Platform: {row['platform']}")
            console.print(f"Channel: {row['channel_id']}")
            console.print(f"User: {row['user_id']}")
            console.print(f"Status: [{'green' if row['status'] == 'active' else 'yellow'}]{row['status']}[/{'green' if row['status'] == 'active' else 'yellow'}]")
            console.print(f"Turns: {row['turn_count']}")
            console.print(f"Created: {row['created_at']}")
            console.print(f"Last message: {row['last_message_at']}")

            if row['completed_at']:
                console.print(f"Completed: {row['completed_at']}")
            if row['completion_reason']:
                console.print(f"Reason: {row['completion_reason']}")
            if row['orchestrator_task_id']:
                console.print(f"Task ID: {row['orchestrator_task_id']}")

            # Display transcript
            messages = json.loads(row['messages'])
            console.print(f"\n[bold]Transcript ({len(messages)} messages):[/bold]\n")

            for i, msg in enumerate(messages):
                role = msg['role']
                content = msg['content']
                timestamp = msg.get('timestamp', '')

                role_color = "cyan" if role == "user" else "green"
                role_label = "User" if role == "user" else "Assistant"

                panel = Panel(
                    content,
                    title=f"[{role_color}]{role_label}[/{role_color}] ({timestamp})",
                    border_style=role_color
                )
                console.print(panel)

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.error(f"Error showing conversation: {e}", exc_info=True)


def handle_conversation_end(args):
    """Manually end a conversation."""
    asyncio.run(_end_conversation(args.conversation_id, args.reason))


async def _end_conversation(conversation_id: str, reason: Optional[str] = None):
    """End a conversation by ID."""
    from promaia.agents.conversation_manager import ConversationManager
    from rich.console import Console

    console = Console()

    try:
        manager = ConversationManager()

        # Find conversation by prefix match
        import sqlite3
        from pathlib import Path

        from promaia.utils.env_writer import get_conversations_db_path
        db_path = get_conversations_db_path()

        if not db_path.exists():
            console.print("[red]No conversations database found[/red]")
            return

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM conversations
                WHERE id LIKE ? AND status IN ('active', 'waiting')
                LIMIT 1
            """, (f"{conversation_id}%",))

            row = cursor.fetchone()

            if not row:
                console.print(f"[red]Active conversation not found: {conversation_id}[/red]")
                return

            full_id = row[0]

        # End the conversation
        end_reason = reason or "manual_cli_end"
        await manager.end_conversation(full_id, reason=end_reason)

        console.print(f"[green]✓ Conversation ended: {full_id[:30]}...[/green]")
        console.print(f"  Reason: {end_reason}")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.error(f"Error ending conversation: {e}", exc_info=True)


def add_conversation_commands(subparsers):
    """Add conversation management commands to CLI."""

    # Main conversation command
    conversation_parser = subparsers.add_parser(
        'conversation',
        help='Manage active conversations'
    )
    conversation_subparsers = conversation_parser.add_subparsers(
        dest='conversation_command',
        help='Conversation commands'
    )

    # List conversations
    list_parser = conversation_subparsers.add_parser(
        'list',
        help='List all active conversations'
    )
    list_parser.set_defaults(func=handle_conversation_list)

    # Show conversation details
    show_parser = conversation_subparsers.add_parser(
        'show',
        help='Show conversation details and transcript'
    )
    show_parser.add_argument(
        'conversation_id',
        help='Conversation ID (can be prefix)'
    )
    show_parser.set_defaults(func=handle_conversation_show)

    # End conversation
    end_parser = conversation_subparsers.add_parser(
        'end',
        help='Manually end a conversation'
    )
    end_parser.add_argument(
        'conversation_id',
        help='Conversation ID (can be prefix)'
    )
    end_parser.add_argument(
        '--reason',
        default='manual_cli_end',
        help='Reason for ending (default: manual_cli_end)'
    )
    end_parser.set_defaults(func=handle_conversation_end)

    return conversation_parser


def add_conversation_commands_to_existing_parser(parser, subparsers):
    """Add conversation commands to an existing parser (for aliases)."""
    add_conversation_commands(subparsers)
