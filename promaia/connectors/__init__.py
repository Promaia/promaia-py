"""
Database connectors for Maia.

This module provides a plugin-based architecture for connecting to different data sources.
"""

from .base import BaseConnector, ConnectorRegistry
from .notion_connector import NotionConnector

# Try to import Gmail connector (optional dependency)
try:
    from .gmail_connector import GmailConnector
    ConnectorRegistry.register("gmail", GmailConnector)
    gmail_available = True
except ImportError:
    gmail_available = False

# Try to import Discord connector (optional dependency)
try:
    from .discord_connector import DiscordConnector
    ConnectorRegistry.register("discord", DiscordConnector)
    discord_available = True
except ImportError:
    discord_available = False


# Try to import Slack connector (optional dependency)
try:
    from .slack_connector import SlackConnector
    ConnectorRegistry.register("slack", SlackConnector)
    slack_available = True
except ImportError:
    slack_available = False

# Conversation connector (always available - no external dependencies)
from .conversation_connector import ConversationConnector
ConnectorRegistry.register("conversation", ConversationConnector)

# Try to import Shopify connector (requires aiohttp)
try:
    from .shopify_connector import ShopifyConnector
    ConnectorRegistry.register("shopify", ShopifyConnector)
    shopify_available = True
except ImportError:
    shopify_available = False

# Try to import Google Calendar connector (requires google-api-python-client)
try:
    from .google_calendar_connector import GoogleCalendarConnector
    ConnectorRegistry.register("google_calendar", GoogleCalendarConnector)
    google_calendar_available = True
except ImportError:
    google_calendar_available = False

# Try to import Google Sheets connector (requires google-api-python-client)
try:
    from .google_sheets_connector import GoogleSheetsConnector
    ConnectorRegistry.register("google_sheets", GoogleSheetsConnector)
    google_sheets_available = True
except ImportError:
    google_sheets_available = False

# Register available connectors
ConnectorRegistry.register("notion", NotionConnector)

__all__ = [
    'BaseConnector',
    'ConnectorRegistry',
    'NotionConnector',
    'ConversationConnector'
]

if gmail_available:
    __all__.append('GmailConnector')

if discord_available:
    __all__.append('DiscordConnector')

if slack_available:
    __all__.append('SlackConnector')

if shopify_available:
    __all__.append('ShopifyConnector')

if google_calendar_available:
    __all__.append('GoogleCalendarConnector')

if google_sheets_available:
    __all__.append('GoogleSheetsConnector')