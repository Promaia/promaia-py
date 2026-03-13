"""
Notion API client initialization and configuration.

All credential resolution goes through the unified auth module:
  get_integration("notion").get_notion_credentials(workspace)

Resolution order: workspace-specific token → global token → None.
"""

# notion_client is optional in some environments (e.g. minimal schedulers/tests).
# Importing it at module import time makes *any* promaia import fail if the
# dependency isn't installed. Keep imports resilient; raise only when used.
try:
    from notion_client import AsyncClient  # type: ignore
except ImportError:  # pragma: no cover
    AsyncClient = None  # type: ignore[assignment]


def get_client(workspace: str = None):
    """Initialize and return an async Notion client for a specific workspace."""
    if AsyncClient is None:  # pragma: no cover
        raise ImportError(
            "notion_client is not installed. Install it to use Notion features "
            "(e.g. `pip install notion-client`)."
        )

    from promaia.auth import get_integration

    token = get_integration("notion").get_notion_credentials(workspace)
    if not token:
        raise ValueError(
            "No Notion credentials found. "
            "Run 'maia auth configure notion' to set up authentication."
        )

    return AsyncClient(auth=token)


def get_client_for_database(database_name: str):
    """Get a Notion client configured for a specific database's workspace."""
    from promaia.config.databases import get_database_config

    db_config = get_database_config(database_name)
    if db_config and db_config.workspace:
        return get_client(db_config.workspace)

    # Fall back to default client
    return get_client()


# Legacy support - use default workspace
def get_default_client():
    """Get a client using the default workspace."""
    return get_client()


# Create the default client lazily to avoid import-time errors
notion_client = None


def ensure_default_client():
    """Ensure the default client is initialized."""
    global notion_client
    if notion_client is None:
        notion_client = get_default_client()
    return notion_client
