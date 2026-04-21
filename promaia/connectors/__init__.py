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

# OCR connector (always available - no external dependencies for base functionality)
try:
    from .ocr_connector import OCRConnector
    ConnectorRegistry.register("ocr", OCRConnector)
    ocr_available = True
except ImportError:
    ocr_available = False

# Try to import Shopify connector (requires aiohttp)
try:
    from .shopify_connector import ShopifyConnector
    ConnectorRegistry.register("shopify", ShopifyConnector)
    shopify_available = True
except ImportError:
    shopify_available = False

# Google Calendar connector removed — calendar data is now accessed exclusively
# via live Google API through the list_calendar_events / list_self_calendar_events
# / list_agent_calendar_events chat tools. No sync, no local calendar_events table.

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

if ocr_available:
    __all__.append('OCRConnector')

if shopify_available:
    __all__.append('ShopifyConnector')

if google_sheets_available:
    __all__.append('GoogleSheetsConnector')