"""
OCR management commands for Promaia CLI.

Provides commands for setting up OCR, processing images,
checking status, and reviewing results.
"""
import asyncio
import argparse
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.text import Text

from promaia.config.ocr import get_ocr_config_manager, validate_ocr_config
from promaia.config.workspaces import get_workspace_manager
from promaia.ocr.processor import OCRProcessor
from promaia.storage.ocr_storage import OCRStorage, get_ocr_stats
from promaia.ocr.notion_sync import (
    extract_database_id_from_url,
    verify_database_access,
    get_or_create_ocr_properties,
    get_recommended_database_schema,
    sync_ocr_results_to_notion
)

logger = logging.getLogger(__name__)
console = Console()


async def cmd_ocr_setup(args: argparse.Namespace):
    """
    Set up OCR for a workspace.

    Creates necessary directories and configures OCR settings.
    """
    # Use specified workspace or fall back to default workspace
    workspace = args.workspace
    if not workspace:
        from promaia.config.workspaces import get_default_workspace
        workspace = get_default_workspace() or "default"

    console.print(f"\n[bold blue]Setting up OCR for workspace: {workspace}[/bold blue]\n")

    # Validate workspace exists
    workspace_manager = get_workspace_manager()
    if not workspace_manager.validate_workspace(workspace):
        console.print(f"[red]Error: Workspace '{workspace}' not found or invalid[/red]")
        return

    # Get/create OCR config
    config_manager = get_ocr_config_manager()
    config = config_manager.get_config()

    # Display current configuration
    console.print(Panel.fit(
        f"[bold]Current OCR Configuration[/bold]\n\n"
        f"Engine: {config.engine}\n"
        f"Uploads Directory: {config.resolve_path(config.uploads_directory)}\n"
        f"Processed Directory: {config.resolve_path(config.processed_directory)}\n"
        f"Failed Directory: {config.resolve_path(config.failed_directory)}\n"
        f"Batch Size: {config.batch_size}\n"
        f"Confidence Threshold: {config.confidence_threshold}",
        title="OCR Settings"
    ))

    # Create directories
    console.print("\n[bold]Creating directories...[/bold]")
    if config.validate_directories(create_if_missing=True):
        console.print("[green]✓[/green] Directories created successfully")
    else:
        console.print("[red]✗[/red] Failed to create some directories")
        return

    # Validate OCR engine configuration
    console.print("\n[bold]Validating OCR engine...[/bold]")
    if validate_ocr_config(create_directories=False):
        console.print(f"[green]✓[/green] OCR engine '{config.engine}' configured correctly")
    else:
        console.print(f"[red]✗[/red] OCR engine validation failed")
        console.print("\n[yellow]Tips:[/yellow]")
        if config.engine == "openai_vision":
            console.print("  - Set OPENAI_API_KEY in .env")
            console.print("  - Or provide api_key in config.api_settings")
        elif config.engine == "google_cloud_vision":
            console.print("  - Set GOOGLE_CLOUD_VISION_API_KEY in .env")
            console.print("  - Or provide credentials_path in config")
        return

    # Save configuration
    config_manager.save_config()

    console.print("\n[bold green]✓ OCR setup complete![/bold green]")
    console.print(f"\nYou can now upload images to: [cyan]{config.uploads_directory}[/cyan]")
    console.print(f"Then run: [cyan]promaia ocr process[/cyan]")


async def cmd_ocr_process(args: argparse.Namespace):
    """
    Process images through OCR pipeline.

    Processes all images in uploads directory or a specific file/directory.
    """
    file_path = args.file
    directory = args.directory
    batch_size = args.batch_size

    # Use specified workspace or fall back to default workspace
    workspace = args.workspace
    if not workspace:
        from promaia.config.workspaces import get_default_workspace
        workspace = get_default_workspace() or "default"

    console.print("\n[bold blue]Starting OCR Processing[/bold blue]\n")

    # Get OCR config
    config = get_ocr_config_manager().get_config()

    # Validate configuration
    if not validate_ocr_config():
        console.print("[red]Error: OCR not configured properly. Run 'promaia ocr setup' first.[/red]")
        return

    # Initialize processor with workspace
    processor = OCRProcessor(workspace=workspace)

    try:
        if file_path:
            # Process single file
            image_path = Path(file_path)
            if not image_path.exists():
                console.print(f"[red]Error: File not found: {file_path}[/red]")
                return

            console.print(f"Processing: [cyan]{image_path.name}[/cyan]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                task = progress.add_task("Extracting text...", total=None)
                result = await processor.process_image(image_path)

            if result.status == "completed":
                console.print(f"[green]✓[/green] Success! Confidence: {result.ocr_result.confidence:.2%}")
                console.print(f"  Markdown saved to: {result.markdown_path}")
            elif result.status == "review_needed":
                console.print(f"[yellow]⚠[/yellow] Low confidence: {result.ocr_result.confidence:.2%} (review needed)")
                console.print(f"  Markdown saved to: {result.markdown_path}")
            else:
                console.print(f"[red]✗[/red] Failed: {result.error}")

        else:
            # Process directory
            process_dir = Path(directory) if directory else config.resolve_path(config.uploads_directory)

            if not process_dir.exists():
                console.print(f"[red]Error: Directory not found: {process_dir}[/red]")
                return

            # Find images
            image_files = list(process_dir.glob("*"))
            supported_formats = processor.engine.get_supported_formats()
            image_files = [
                f for f in image_files
                if f.is_file() and f.suffix.lower() in supported_formats
            ]

            if not image_files:
                console.print(f"[yellow]No images found in {process_dir}[/yellow]")
                return

            console.print(f"Found [cyan]{len(image_files)}[/cyan] images to process\n")

            # Process with progress bar
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task("Processing images...", total=len(image_files))

                results = []
                for image_path in image_files:
                    progress.update(task, description=f"Processing {image_path.name}...")
                    result = await processor.process_image(image_path)
                    results.append(result)
                    progress.advance(task)

            # Display summary
            completed = sum(1 for r in results if r.status == "completed")
            review_needed = sum(1 for r in results if r.status == "review_needed")
            failed = sum(1 for r in results if r.status == "failed")

            table = Table(title="OCR Processing Summary")
            table.add_column("Status", style="bold")
            table.add_column("Count", justify="right")

            table.add_row("[green]Completed[/green]", str(completed))
            table.add_row("[yellow]Review Needed[/yellow]", str(review_needed))
            table.add_row("[red]Failed[/red]", str(failed))
            table.add_row("[cyan]Total[/cyan]", str(len(results)))

            console.print()
            console.print(table)

            if review_needed > 0:
                console.print(f"\n[yellow]Tip: Run 'promaia ocr review' to see low-confidence results[/yellow]")

    except Exception as e:
        logger.error(f"Error during OCR processing: {e}")
        console.print(f"[red]Error: {e}[/red]")


async def cmd_ocr_status(args: argparse.Namespace):
    """
    Show OCR processing status and statistics.
    """
    # Use specified workspace or fall back to default workspace
    workspace = args.workspace
    if not workspace:
        from promaia.config.workspaces import get_default_workspace
        workspace = get_default_workspace() or "default"

    console.print("\n[bold blue]OCR Status[/bold blue]\n")

    # Get configuration
    config = get_ocr_config_manager().get_config()

    # Display configuration
    config_table = Table(title="Configuration")
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value")

    config_table.add_row("Enabled", "✓" if config.enabled else "✗")
    config_table.add_row("Engine", config.engine)
    config_table.add_row("Uploads Directory", str(config.resolve_path(config.uploads_directory)))
    config_table.add_row("Batch Size", str(config.batch_size))
    config_table.add_row("Confidence Threshold", f"{config.confidence_threshold:.0%}")

    console.print(config_table)

    # Get statistics
    stats = get_ocr_stats(workspace)

    if stats["total"] == 0:
        console.print("\n[yellow]No OCR documents processed yet[/yellow]")
        console.print(f"Upload images to: [cyan]{config.uploads_directory}[/cyan]")
        console.print(f"Then run: [cyan]promaia ocr process[/cyan]")
        return

    # Display statistics
    console.print()
    stats_table = Table(title="Processing Statistics")
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", justify="right")

    stats_table.add_row("Total Processed", str(stats["total"]))

    for status, count in stats["status_counts"].items():
        style = {
            "completed": "green",
            "review_needed": "yellow",
            "failed": "red",
            "pending": "blue"
        }.get(status, "")
        stats_table.add_row(f"  {status.replace('_', ' ').title()}", f"[{style}]{count}[/{style}]")

    stats_table.add_row("", "")
    stats_table.add_row("Avg Confidence", f"{stats['avg_confidence']:.2%}")
    stats_table.add_row("Min Confidence", f"{stats['min_confidence']:.2%}")
    stats_table.add_row("Max Confidence", f"{stats['max_confidence']:.2%}")

    console.print(stats_table)

    # Check uploads directory
    uploads_dir = config.resolve_path(config.uploads_directory)
    if uploads_dir.exists():
        pending_images = list(uploads_dir.glob("*"))
        supported_formats = ['.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.heic']
        pending_images = [
            f for f in pending_images
            if f.is_file() and f.suffix.lower() in supported_formats
        ]

        if pending_images:
            console.print(f"\n[yellow]⚠ {len(pending_images)} image(s) pending in uploads directory[/yellow]")
            console.print("Run [cyan]promaia ocr process[/cyan] to process them")


async def cmd_ocr_review(args: argparse.Namespace):
    """
    Review OCR results that need attention (low confidence).
    """
    # Use specified workspace or fall back to default workspace
    workspace = args.workspace
    if not workspace:
        from promaia.config.workspaces import get_default_workspace
        workspace = get_default_workspace() or "default"

    threshold = args.threshold

    console.print(f"\n[bold blue]Reviewing OCR Results Below {threshold:.0%} Confidence[/bold blue]\n")

    # Get low-confidence results
    storage = OCRStorage()
    uploads = storage.get_by_workspace(workspace, status="review_needed")

    # Filter by threshold
    uploads = [u for u in uploads if u.get("ocr_confidence", 0) < threshold]

    if not uploads:
        console.print("[green]No results need review![/green]")
        return

    console.print(f"Found [yellow]{len(uploads)}[/yellow] results needing review\n")

    # Display table
    table = Table()
    table.add_column("Title", style="cyan")
    table.add_column("Confidence", justify="right")
    table.add_column("Language")
    table.add_column("Date")

    for upload in uploads:
        confidence = upload.get("ocr_confidence", 0)
        color = "red" if confidence < 0.5 else "yellow"

        table.add_row(
            upload.get("title", ""),
            f"[{color}]{confidence:.2%}[/{color}]",
            upload.get("language", ""),
            upload.get("processing_date", "")[:10]
        )

    console.print(table)

    console.print(f"\n[cyan]Tip: Review markdown files in data/md/ocr/[/cyan]")
    console.print(f"[cyan]Source images are in: {get_ocr_config_manager().get_config().processed_directory}[/cyan]")


async def cmd_ocr_database_add(args: argparse.Namespace):
    """
    Add a Notion database for OCR uploads.
    """
    url = args.url
    workspace = args.workspace

    console.print(f"\n[bold blue]Adding Notion Database for OCR[/bold blue]\n")

    # Extract database ID from URL
    database_id = extract_database_id_from_url(url)
    if not database_id:
        console.print("[red]✗ Invalid Notion database URL or ID[/red]")
        console.print("\n[yellow]Expected format:[/yellow]")
        console.print("  https://notion.so/workspace/DATABASE_ID")
        console.print("  or just: DATABASE_ID")
        return

    console.print(f"Database ID: [cyan]{database_id}[/cyan]")

    # Verify access
    console.print("\n[bold]Verifying access...[/bold]")
    if not await verify_database_access(database_id, workspace):
        console.print("[red]✗ Cannot access database. Check:[/red]")
        console.print("  - Database URL is correct")
        console.print("  - Notion integration has access to the database")
        console.print("  - Your API key is correct in .env")
        return

    console.print("[green]✓[/green] Database accessible")

    # Get/create properties
    console.print("\n[bold]Checking database schema...[/bold]")
    property_map = await get_or_create_ocr_properties(database_id, workspace)

    required_props = ["title", "status"]
    missing = [p for p in required_props if p not in property_map]

    if missing:
        console.print(f"[yellow]⚠[/yellow] Database is missing some recommended properties")
        console.print("\nRecommended schema:")
        schema = get_recommended_database_schema()
        for prop_name, prop_config in schema.items():
            status = "[green]✓[/green]" if prop_name in [property_map.get(k) for k in property_map] else "[yellow]?[/yellow]"
            console.print(f"  {status} {prop_name} ({prop_config['type']})")

        console.print("\n[cyan]Tip: Run 'maia ocr database setup' to configure properties[/cyan]")
    else:
        console.print("[green]✓[/green] Database schema looks good!")

    # Save configuration
    config_manager = get_ocr_config_manager()
    config = config_manager.get_config()

    # Update config with database info
    from promaia.config.databases import get_database_manager
    db_manager = get_database_manager()

    db_config = {
        "source_type": "ocr",
        "database_id": database_id,
        "workspace": workspace,
        "sync_enabled": True,
        "markdown_directory": f"data/md/ocr/{workspace}/uploads"
    }

    # Save to databases config
    import json
    from promaia.utils.env_writer import get_config_path
    config_file = str(get_config_path())
    try:
        with open(config_file, 'r') as f:
            full_config = json.load(f)

        if "databases" not in full_config:
            full_config["databases"] = {}

        full_config["databases"]["ocr_uploads"] = db_config

        with open(config_file, 'w') as f:
            json.dump(full_config, f, indent=2)

        console.print(f"\n[bold green]✓ Database added successfully![/bold green]")
        console.print(f"\nConfiguration saved to: [cyan]{config_file}[/cyan]")
        console.print(f"\nNext steps:")
        console.print(f"  1. Process some images: [cyan]maia ocr process[/cyan]")
        console.print(f"  2. Sync to Notion: [cyan]maia ocr sync[/cyan]")

    except Exception as e:
        console.print(f"[red]Error saving configuration: {e}[/red]")


async def cmd_ocr_database_setup(args: argparse.Namespace):
    """
    Setup/verify database schema for OCR.
    """
    workspace = args.workspace or "default"

    console.print(f"\n[bold blue]OCR Database Schema Setup[/bold blue]\n")

    # Get database ID from config
    import json
    from promaia.utils.env_writer import get_config_path
    try:
        with open(str(get_config_path()), 'r') as f:
            config = json.load(f)
            db_config = config.get("databases", {}).get("ocr_uploads", {})
            database_id = db_config.get("database_id")

        if not database_id:
            console.print("[red]No OCR database configured[/red]")
            console.print("Run: [cyan]maia ocr database add <url>[/cyan]")
            return

    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        return

    # Show recommended schema
    console.print("[bold]Recommended Database Schema:[/bold]\n")

    schema = get_recommended_database_schema()
    table = Table()
    table.add_column("Property Name", style="cyan")
    table.add_column("Type", style="yellow")
    table.add_column("Description")

    descriptions = {
        "Title": "Page title (auto-generated from filename)",
        "Upload Date": "When image was uploaded",
        "Processing Date": "When OCR processing completed",
        "OCR Confidence": "Confidence score (0-100%)",
        "Status": "Processing status",
        "Source Image": "Original image file",
        "Language": "Detected language",
        "Text Length": "Character count of extracted text",
        "Notes": "Manual notes or corrections"
    }

    for prop_name, prop_config in schema.items():
        table.add_row(
            prop_name,
            prop_config["type"],
            descriptions.get(prop_name, "")
        )

    console.print(table)

    console.print("\n[yellow]Note:[/yellow] You need to manually create these properties in Notion.")
    console.print("Or use a pre-made template (coming soon).")


async def cmd_ocr_database_info(args: argparse.Namespace):
    """
    Show information about configured OCR database.
    """
    console.print(f"\n[bold blue]OCR Database Information[/bold blue]\n")

    # Get database config
    import json
    from promaia.utils.env_writer import get_config_path
    try:
        with open(str(get_config_path()), 'r') as f:
            config = json.load(f)
            db_config = config.get("databases", {}).get("ocr_uploads", {})

        if not db_config:
            console.print("[yellow]No OCR database configured[/yellow]")
            console.print("Run: [cyan]maia ocr database add <url>[/cyan]")
            return

        # Display config
        table = Table(title="Database Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value")

        table.add_row("Database ID", db_config.get("database_id", "Not set"))
        table.add_row("Workspace", db_config.get("workspace", "Not set"))
        table.add_row("Sync Enabled", "✓" if db_config.get("sync_enabled") else "✗")
        table.add_row("Markdown Directory", db_config.get("markdown_directory", "Not set"))

        console.print(table)

        # Check if accessible
        database_id = db_config.get("database_id")
        workspace = db_config.get("workspace", "default")
        if database_id:
            console.print("\n[bold]Testing access...[/bold]")
            if await verify_database_access(database_id, workspace):
                console.print("[green]✓[/green] Database is accessible")

                # Show property mapping
                property_map = await get_or_create_ocr_properties(database_id, workspace)
                if property_map:
                    console.print("\n[bold]Found Properties:[/bold]")
                    for purpose, prop_name in property_map.items():
                        console.print(f"  [green]✓[/green] {purpose}: {prop_name}")
            else:
                console.print("[red]✗[/red] Cannot access database")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


async def cmd_ocr_sync(args: argparse.Namespace):
    """
    Sync OCR results to Notion database.
    """
    # Use specified workspace or fall back to default workspace
    workspace = args.workspace
    if not workspace:
        from promaia.config.workspaces import get_default_workspace
        workspace = get_default_workspace() or "default"
    limit = args.limit

    console.print(f"\n[bold blue]Syncing OCR Results to Notion[/bold blue]\n")

    # Get database ID from config
    import json
    from promaia.utils.env_writer import get_config_path
    try:
        with open(str(get_config_path()), 'r') as f:
            config = json.load(f)
            db_config = config.get("databases", {}).get("ocr_uploads", {})
            database_id = db_config.get("database_id")

        if not database_id:
            console.print("[red]No OCR database configured[/red]")
            console.print("Run: [cyan]maia ocr database add <url>[/cyan]")
            return

    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        return

    # Sync results
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Syncing to Notion...", total=None)

        stats = await sync_ocr_results_to_notion(database_id, workspace, limit)

    # Display results
    console.print()
    table = Table(title="Sync Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right")

    table.add_row("Total Processed", str(stats["total"]))
    table.add_row("[green]Created in Notion[/green]", f"[green]{stats['created']}[/green]")
    table.add_row("[yellow]Skipped[/yellow]", f"[yellow]{stats['skipped']}[/yellow]")
    table.add_row("[red]Failed[/red]", f"[red]{stats['failed']}[/red]")

    console.print(table)

    if stats["created"] > 0:
        console.print(f"\n[bold green]✓ Successfully synced {stats['created']} pages to Notion![/bold green]")


def register_ocr_commands(subparsers):
    """
    Register OCR commands with the CLI parser.

    Args:
        subparsers: Subparsers from main CLI
    """
    # Main OCR command
    ocr_parser = subparsers.add_parser(
        "ocr",
        help="OCR processing and management"
    )
    ocr_subparsers = ocr_parser.add_subparsers(dest="ocr_command", required=True)

    # Setup command
    setup_parser = ocr_subparsers.add_parser(
        "setup",
        help="Set up OCR for a workspace"
    )
    setup_parser.add_argument(
        "--workspace",
        help="Workspace name (default: uses default workspace)"
    )
    setup_parser.set_defaults(func=cmd_ocr_setup)

    # Process command
    process_parser = ocr_subparsers.add_parser(
        "process",
        help="Process images through OCR"
    )
    process_parser.add_argument(
        "--file",
        help="Process a single image file"
    )
    process_parser.add_argument(
        "--directory",
        help="Process all images in directory"
    )
    process_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for processing (default: from config)"
    )
    process_parser.add_argument(
        "--workspace",
        help="Workspace name (default: uses default workspace from config)"
    )
    process_parser.set_defaults(func=cmd_ocr_process)

    # Status command
    status_parser = ocr_subparsers.add_parser(
        "status",
        help="Show OCR status and statistics"
    )
    status_parser.add_argument(
        "--workspace",
        help="Filter by workspace"
    )
    status_parser.set_defaults(func=cmd_ocr_status)

    # Review command
    review_parser = ocr_subparsers.add_parser(
        "review",
        help="Review low-confidence OCR results"
    )
    review_parser.add_argument(
        "--workspace",
        help="Filter by workspace"
    )
    review_parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Confidence threshold (default: 0.7)"
    )
    review_parser.set_defaults(func=cmd_ocr_review)

    # Database commands
    database_parser = ocr_subparsers.add_parser(
        "database",
        help="Manage Notion database for OCR"
    )
    database_subparsers = database_parser.add_subparsers(dest="database_action", required=True)

    # Database add command
    db_add_parser = database_subparsers.add_parser(
        "add",
        help="Add Notion database for OCR uploads"
    )
    db_add_parser.add_argument(
        "url",
        help="Notion database URL or ID"
    )
    db_add_parser.add_argument(
        "--workspace",
        default="default",
        help="Workspace name (default: default)"
    )
    db_add_parser.set_defaults(func=cmd_ocr_database_add)

    # Database setup command
    db_setup_parser = database_subparsers.add_parser(
        "setup",
        help="Show recommended database schema"
    )
    db_setup_parser.add_argument(
        "--workspace",
        help="Workspace name"
    )
    db_setup_parser.set_defaults(func=cmd_ocr_database_setup)

    # Database info command
    db_info_parser = database_subparsers.add_parser(
        "info",
        help="Show OCR database information"
    )
    db_info_parser.set_defaults(func=cmd_ocr_database_info)

    # Sync command
    sync_parser = ocr_subparsers.add_parser(
        "sync",
        help="Sync OCR results to Notion"
    )
    sync_parser.add_argument(
        "--workspace",
        help="Workspace to sync (default: default)"
    )
    sync_parser.add_argument(
        "--limit",
        type=int,
        help="Max number of results to sync"
    )
    sync_parser.set_defaults(func=cmd_ocr_sync)
