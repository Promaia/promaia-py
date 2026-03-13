"""
Self-contained agentic turn for Promaia conversations.

Manages an internal LLM tool-use loop but only returns plain text to the
conversation manager. No tool_use/tool_result blocks leak into stored
conversation history — the ConversationManager continues storing
{'role': str, 'content': str} messages with no serialization changes.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class AgenticTurnResult:
    """Result of an agentic turn — only plain text goes back to conversation."""
    response_text: str
    tool_calls_made: List[Dict[str, Any]] = field(default_factory=list)
    iterations_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    plan: Optional[List[str]] = None  # Plan steps if planning was used


# ── Tool definitions (Anthropic native format) ──────────────────────────

QUERY_TOOL_DEFINITIONS = [
    {
        "name": "query_sql",
        "description": (
            "Search your data sources using natural language (converted to SQL). "
            "Use for exact text/keyword searches when you know what you're looking for. "
            "Examples: 'emails from Federico this week', 'tasks due today', "
            "'calendar events tomorrow'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query (searches for exact text/keywords)"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why you need this data"
                }
            },
            "required": ["query", "reasoning"]
        }
    },
    {
        "name": "query_vector",
        "description": (
            "Semantic search across all data sources using embeddings. "
            "Use for conceptual/fuzzy searches when exact keywords won't work. "
            "Examples: 'discussions about team morale', "
            "'content about project deadlines and pressure'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Semantic search query (conceptual matching)"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why you need this data"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default: 50)",
                    "default": 50
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimum similarity 0-1 (default: 0.2)",
                    "default": 0.2
                }
            },
            "required": ["query", "reasoning"]
        }
    },
    {
        "name": "query_source",
        "description": (
            "Load pages from a specific database with time filtering. "
            "Use to expand context or load different time ranges. "
            "Available databases include: journal, gmail, stories, tasks, calendar, "
            "and any Discord/Slack channel sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "description": (
                        "Database name (e.g., 'journal', 'gmail', 'stories', "
                        "'tasks', 'calendar')"
                    )
                },
                "days": {
                    "type": "integer",
                    "description": "Days to look back (0 or omit for all)"
                }
            },
            "required": ["database"]
        }
    },
    {
        "name": "write_journal",
        "description": (
            "Write a personal note to your journal. "
            "Use to record insights, learnings, or important information you want to remember."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Journal note content"
                },
                "note_type": {
                    "type": "string",
                    "description": "Type: 'Note', 'Insight', 'Learning', or 'Change'",
                    "enum": ["Note", "Insight", "Learning", "Change"]
                }
            },
            "required": ["content"]
        }
    },
]


GMAIL_TOOL_DEFINITIONS = [
    {
        "name": "send_email",
        "description": (
            "Send a new email message. "
            "Use query_sql to search emails first if you need to find addresses or threads."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient email (comma-separated for multiple)"
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject"
                },
                "body": {
                    "type": "string",
                    "description": "Email body (plain text or HTML)"
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients (optional)"
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "create_email_draft",
        "description": "Create an email draft (not sent).",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "reply_to_email",
        "description": (
            "Reply to an email thread. "
            "Use query_sql to find the thread_id and message_id first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Gmail thread ID"},
                "message_id": {"type": "string", "description": "Original message ID"},
                "body": {"type": "string", "description": "Reply body text"}
            },
            "required": ["thread_id", "message_id", "body"]
        }
    },
]

GMAIL_READ_TOOL_DEFINITIONS = [
    {
        "name": "search_emails",
        "description": (
            "Search Gmail for emails matching a query. "
            "Use Gmail search syntax (from:, to:, subject:, after:, before:, "
            "has:attachment, etc.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to return (default 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_email_thread",
        "description": (
            "Get the full thread of emails by thread ID. "
            "Use after search_emails to read a full conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Gmail thread ID"
                }
            },
            "required": ["thread_id"]
        }
    },
]

MESSAGING_TOOL_DEFINITIONS = [
    {
        "name": "send_message",
        "description": (
            "Send a one-way message (no reply expected). "
            "Use 'user' to DM someone by name, or 'channel_id' for a channel. "
            "For back-and-forth conversations, use start_conversation instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": (
                        "The channel ID to send the message to. "
                        "Use 'current' to send to the current conversation's channel. "
                        "Omit if using 'user' to send a DM instead."
                    )
                },
                "user": {
                    "type": "string",
                    "description": (
                        "The user's name to send a direct message to. "
                        "Looks up the user by display name, real name, or username "
                        "and opens a DM channel automatically."
                    )
                },
                "content": {
                    "type": "string",
                    "description": "The message content to send."
                },
                "thread_id": {
                    "type": "string",
                    "description": (
                        "Optional thread ID to reply in a specific thread. "
                        "Omit to post in the channel."
                    )
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "start_conversation",
        "description": (
            "Start a back-and-forth DM conversation with a user. "
            "Sends the initial message, then waits for their reply. "
            "Returns the user's response so you can continue the conversation. "
            "Use this when you need a reply (e.g. asking a question, confirming something). "
            "For one-way notifications, use send_message instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "The user's name to DM."
                },
                "message": {
                    "type": "string",
                    "description": "The initial message to send."
                },
                "timeout_minutes": {
                    "type": "integer",
                    "description": "How long to wait for a reply (default: 15 minutes)."
                }
            },
            "required": ["user", "message"]
        }
    },
]

NOTION_TOOL_DEFINITIONS = [
    {
        "name": "notion_search",
        "description": (
            "Search Notion for pages and databases by title. "
            "Use this to find the ID of a database or page before "
            "creating/updating content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (page or database title)"
                },
                "filter": {
                    "type": "string",
                    "enum": ["page", "database"],
                    "description": "Filter results to only pages or only databases"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "notion_create_page",
        "description": (
            "Create a new page in a Notion database. "
            "First use notion_search to find the database_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database to create the page in"
                },
                "title": {
                    "type": "string",
                    "description": "Page title"
                },
                "icon": {
                    "type": "string",
                    "description": "Emoji icon for the page (e.g. '🎟️', '📋', '🚀')"
                },
                "properties": {
                    "type": "object",
                    "description": (
                        "Additional page properties (key-value pairs matching "
                        "the database schema)"
                    )
                },
                "content": {
                    "type": "string",
                    "description": "Page body content as markdown"
                }
            },
            "required": ["database_id", "title"]
        }
    },
    {
        "name": "notion_update_page",
        "description": (
            "Update properties or icon of an existing Notion page. "
            "For adding structured content (headings, lists, to-dos), "
            "use notion_append_blocks instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "The page ID to update"
                },
                "icon": {
                    "type": "string",
                    "description": "Emoji icon for the page (e.g. '🎟️', '📋', '🚀')"
                },
                "properties": {
                    "type": "object",
                    "description": "Properties to update"
                },
                "content": {
                    "type": "string",
                    "description": "New content to append as markdown"
                }
            },
            "required": ["page_id"]
        }
    },
    {
        "name": "notion_query_database",
        "description": (
            "Query a Notion database with filters to find specific pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database ID to query"
                },
                "filter": {
                    "type": "object",
                    "description": "Notion filter object"
                },
                "sorts": {
                    "type": "array",
                    "description": "Sort specifications"
                }
            },
            "required": ["database_id"]
        }
    },
]

CALENDAR_TOOL_DEFINITIONS = [
    {
        "name": "create_calendar_event",
        "description": (
            "Create a new calendar event. "
            "Use query_sql with calendar source to check for conflicts first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title/summary"},
                "description": {
                    "type": "string",
                    "description": "Event description (optional)"
                },
                "start_time": {
                    "type": "string",
                    "description": "Start time (ISO 8601: 2026-03-01T14:00:00)"
                },
                "end_time": {
                    "type": "string",
                    "description": "End time (ISO 8601: 2026-03-01T15:00:00)"
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID (default: primary)"
                },
                "recurrence": {
                    "type": "string",
                    "description": (
                        "Recurrence rule (RRULE format, optional). "
                        "Examples: 'RRULE:FREQ=DAILY', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR', "
                        "'RRULE:FREQ=MONTHLY;BYMONTHDAY=1', 'RRULE:FREQ=WEEKLY;COUNT=4'"
                    )
                }
            },
            "required": ["summary", "start_time", "end_time"]
        }
    },
    {
        "name": "update_calendar_event",
        "description": (
            "Update an existing calendar event. "
            "Use query_sql with calendar source to find the event_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to update"},
                "summary": {"type": "string", "description": "New title (optional)"},
                "description": {
                    "type": "string", "description": "New description (optional)"
                },
                "start_time": {
                    "type": "string", "description": "New start time (ISO 8601, optional)"
                },
                "end_time": {
                    "type": "string", "description": "New end time (ISO 8601, optional)"
                },
                "calendar_id": {
                    "type": "string", "description": "Calendar ID (default: primary)"
                }
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "Delete a calendar event. "
            "Use query_sql with calendar source to find the event_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to delete"},
                "calendar_id": {
                    "type": "string", "description": "Calendar ID (default: primary)"
                }
            },
            "required": ["event_id"]
        }
    },
]

SCHEDULE_SELF_TOOL_DEFINITION = {
    "name": "schedule_self",
    "description": (
        "Schedule a future task for yourself. Creates an event on your own "
        "calendar that will trigger you to run at the specified time. Use this "
        "for reminders, follow-ups, and multi-step workflows that span hours "
        "or days."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "What you'll do when triggered"},
            "start_time": {
                "type": "string",
                "description": "When to trigger (ISO 8601: 2026-03-01T14:00:00)"
            },
            "end_time": {
                "type": "string",
                "description": "End time (ISO 8601, optional — defaults to start + 30min)"
            },
            "description": {
                "type": "string",
                "description": "Context and instructions for your future self (optional)"
            },
            "recurrence": {
                "type": "string",
                "description": (
                    "Recurrence rule (RRULE format, optional). "
                    "Examples: 'RRULE:FREQ=DAILY', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR', "
                    "'RRULE:FREQ=WEEKLY;COUNT=4'"
                )
            },
        },
        "required": ["summary", "start_time"]
    },
}

SCHEDULE_AGENT_EVENT_TOOL_DEFINITION = {
    "name": "schedule_agent_event",
    "description": (
        "Schedule an event on an agent's dedicated calendar. Use this when "
        "the user wants to put something on the agent calendar (not their "
        "personal calendar). The event will appear on the agent's own calendar."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title / what to do"},
            "start_time": {
                "type": "string",
                "description": "Start time (ISO 8601: 2026-03-01T14:00:00)"
            },
            "end_time": {
                "type": "string",
                "description": "End time (ISO 8601, optional — defaults to start + 30min)"
            },
            "description": {
                "type": "string",
                "description": "Additional details or context (optional)"
            },
            "agent": {
                "type": "string",
                "description": "Agent name (required when multiple agents have calendars, optional when only one)"
            },
            "recurrence": {
                "type": "string",
                "description": (
                    "Recurrence rule (RRULE format, optional). "
                    "Examples: 'RRULE:FREQ=DAILY', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR'"
                )
            },
        },
        "required": ["summary", "start_time"]
    },
}

CALENDAR_READ_TOOL_DEFINITIONS = [
    {
        "name": "list_calendar_events",
        "description": (
            "List upcoming calendar events. "
            "Optionally filter by date range or search query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to look (default 7)",
                    "default": 7
                },
                "days_back": {
                    "type": "integer",
                    "description": "Number of days back to look (default 0)",
                    "default": 0
                },
                "query": {
                    "type": "string",
                    "description": "Optional search text to filter events"
                }
            }
        }
    },
    {
        "name": "get_calendar_event",
        "description": "Get details of a specific calendar event by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Calendar event ID"
                }
            },
            "required": ["event_id"]
        }
    },
]

WEB_SEARCH_TOOL_DEFINITIONS = [{
    "name": "web_search",
    "description": (
        "Search the internet for current information. "
        "Use for real-time info, recent news, facts not in local data, or verification."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "reasoning": {
                "type": "string",
                "description": "Why you need to search the web",
            },
        },
        "required": ["query", "reasoning"],
    },
}]


WEB_FETCH_TOOL_DEFINITIONS = [{
    "name": "web_fetch",
    "description": (
        "Fetch and read the content of a specific web page URL. "
        "Use after web_search to read a full page, or when given a URL directly. "
        "Returns the page content as clean text."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch",
            },
            "reasoning": {
                "type": "string",
                "description": "Why you need to read this page",
            },
        },
        "required": ["url", "reasoning"],
    },
}]


NOTION_BLOCK_TOOL_DEFINITIONS = [
    {
        "name": "notion_get_blocks",
        "description": (
            "Get the block content of a Notion page. Returns all blocks "
            "(paragraphs, to-dos, lists, headings, etc.) with their IDs "
            "so you can update specific blocks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID"
                }
            },
            "required": ["page_id"]
        }
    },
    {
        "name": "notion_update_blocks",
        "description": (
            "Update specific blocks on a Notion page. "
            "Can check/uncheck to-do items, change text content, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": "List of block updates",
                    "items": {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string"},
                            "checked": {
                                "type": "boolean",
                                "description": "For to_do blocks: check/uncheck"
                            },
                            "text": {
                                "type": "string",
                                "description": "New text content for the block"
                            }
                        },
                        "required": ["block_id"]
                    }
                }
            },
            "required": ["updates"]
        }
    },
    {
        "name": "notion_append_blocks",
        "description": (
            "Append structured blocks to a Notion page. Supports markdown: "
            "headings (#, ##), bullet lists (-), numbered lists (1.), "
            "to-do items (- [ ], - [x]), paragraphs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID"
                },
                "content": {
                    "type": "string",
                    "description": "Markdown content to append as individual blocks"
                }
            },
            "required": ["page_id", "content"]
        }
    },
]


GOOGLE_SHEETS_TOOL_DEFINITIONS = [
    {
        "name": "sheets_read_range",
        "description": (
            "Read a specific cell range from a Google Sheet. Returns both "
            "formulas and display values in inline format: {=FORMULA} value. "
            "Use for on-demand reads, verifying edits, or checking formula correctness. "
            "For bulk reads of entire synced sheets, prefer query_sql instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "range": {
                    "type": "string",
                    "description": "A1 notation range, e.g. 'Sheet1!A1:D10' or 'A1:D10'"
                },
            },
            "required": ["spreadsheet", "range"]
        }
    },
    {
        "name": "sheets_update_cells",
        "description": (
            "Write values to one or more ranges in a Google Sheet. "
            "Values are 2D arrays: [[row1col1, row1col2], [row2col1, row2col2]]. "
            "Formulas are entered as strings starting with '='."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "range": {
                    "type": "string",
                    "description": "A1 notation range for a single update, e.g. 'Sheet1!B3'"
                },
                "values": {
                    "type": "array",
                    "description": "2D array of values: [[r1c1, r1c2], [r2c1, r2c2]]",
                    "items": {"type": "array", "items": {}}
                },
                "ranges": {
                    "type": "array",
                    "description": "For batch updates: list of {range, values} objects",
                    "items": {
                        "type": "object",
                        "properties": {
                            "range": {"type": "string"},
                            "values": {"type": "array", "items": {"type": "array", "items": {}}}
                        },
                        "required": ["range", "values"]
                    }
                },
                "value_input": {
                    "type": "string",
                    "enum": ["USER_ENTERED", "RAW"],
                    "description": "How to interpret input (default: USER_ENTERED, interprets formulas)"
                },
            },
            "required": ["spreadsheet"]
        }
    },
    {
        "name": "sheets_append_rows",
        "description": (
            "Append rows after existing data in a Google Sheet. "
            "Values are 2D arrays: [[row1col1, row1col2], [row2col1, row2col2]]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "range": {
                    "type": "string",
                    "description": "A1 range indicating the table to append to, e.g. 'Sheet1!A:D'"
                },
                "values": {
                    "type": "array",
                    "description": "2D array of rows to append",
                    "items": {"type": "array", "items": {}}
                },
                "value_input": {
                    "type": "string",
                    "enum": ["USER_ENTERED", "RAW"],
                    "description": "How to interpret input (default: USER_ENTERED)"
                },
            },
            "required": ["spreadsheet", "range", "values"]
        }
    },
    {
        "name": "sheets_create_spreadsheet",
        "description": (
            "Create a new Google Spreadsheet. Optionally place it in a Drive folder "
            "and populate it with initial data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Spreadsheet title"
                },
                "sheets": {
                    "type": "array",
                    "description": "Tab names to create (default: one 'Sheet1' tab)",
                    "items": {"type": "string"}
                },
                "folder_id": {
                    "type": "string",
                    "description": "Google Drive folder ID to move the spreadsheet into"
                },
                "initial_data": {
                    "type": "object",
                    "description": "Map of 'SheetName!A1' ranges to 2D value arrays for initial population",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "array", "items": {}}
                    }
                },
            },
            "required": ["title"]
        }
    },
    {
        "name": "sheets_manage_sheets",
        "description": (
            "Add, delete, or rename tabs (sheets) within a spreadsheet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "add": {
                    "type": "array",
                    "description": "Tab names to add",
                    "items": {"type": "string"}
                },
                "delete": {
                    "type": "array",
                    "description": "Tab names to delete",
                    "items": {"type": "string"}
                },
                "rename": {
                    "type": "array",
                    "description": "Tabs to rename: [{from, to}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "to": {"type": "string"}
                        },
                        "required": ["from", "to"]
                    }
                },
            },
            "required": ["spreadsheet"]
        }
    },
    {
        "name": "sheets_find",
        "description": (
            "Search Google Drive for spreadsheets by name. Returns matching "
            "spreadsheet id, title, and URL. Use this to discover sheets that "
            "aren't already in context. After finding a sheet, use "
            "sheets_ingest to load its content before reading/writing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — matches against spreadsheet title"
                },
            },
            "required": ["query"]
        }
    },
    {
        "name": "sheets_ingest",
        "description": (
            "One-time ingest of a Google Sheet into context. Fetches all tabs "
            "and formats them as CSV with inline formulas (same format as synced "
            "sheets). The data is cached locally so subsequent sheets tools can "
            "resolve the sheet by name. Only ingest once per sheet per "
            "conversation — if you already ingested it or it's in synced "
            "sources, skip this step."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
            },
            "required": ["spreadsheet"]
        }
    },
    {
        "name": "sheets_insert_rows",
        "description": (
            "Insert blank rows at a specific position in a sheet, then "
            "optionally fill them with data. Use this instead of "
            "sheets_append_rows when you need to add rows in the middle "
            "of a table (e.g. before a totals row). Existing rows shift "
            "down and formulas referencing them update automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "sheet": {
                    "type": "string",
                    "description": "Tab name (default: first sheet)"
                },
                "row": {
                    "type": "integer",
                    "description": "Row number to insert before (1-based, e.g. 6 inserts before row 6)"
                },
                "count": {
                    "type": "integer",
                    "description": "Number of rows to insert (default: 1)"
                },
                "values": {
                    "type": "array",
                    "description": "Optional 2D array of values to fill into the inserted rows",
                    "items": {
                        "type": "array",
                        "items": {}
                    }
                },
            },
            "required": ["spreadsheet", "row"]
        }
    },
]

GOOGLE_SHEETS_FORMAT_TOOL_DEFINITIONS = [
    {
        "name": "sheets_format_cells",
        "description": (
            "Apply formatting to cells: bold, colors, number format, column width, merge. "
            "Hex colors like '#FF0000' are supported."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet": {
                    "type": "string",
                    "description": "Spreadsheet name, URL, or ID"
                },
                "sheet": {
                    "type": "string",
                    "description": "Tab name (default: first sheet)"
                },
                "formats": {
                    "type": "array",
                    "description": "List of format operations",
                    "items": {
                        "type": "object",
                        "properties": {
                            "range": {
                                "type": "string",
                                "description": "A1 range, e.g. 'A1:D1' or '1:1' for whole row"
                            },
                            "bold": {"type": "boolean"},
                            "italic": {"type": "boolean"},
                            "font_size": {"type": "integer"},
                            "fg_color": {
                                "type": "string",
                                "description": "Text color as hex, e.g. '#FFFFFF'"
                            },
                            "bg_color": {
                                "type": "string",
                                "description": "Background color as hex, e.g. '#4285F4'"
                            },
                            "number_format": {
                                "type": "string",
                                "description": "Number format pattern, e.g. '#,##0.00', '0%', '$#,##0'"
                            },
                            "h_align": {
                                "type": "string",
                                "enum": ["LEFT", "CENTER", "RIGHT"],
                            },
                            "merge": {
                                "type": "boolean",
                                "description": "Merge cells in range"
                            },
                            "column_width": {
                                "type": "integer",
                                "description": "Set column width in pixels"
                            },
                        },
                        "required": ["range"]
                    }
                },
            },
            "required": ["spreadsheet", "formats"]
        }
    },
]

COMPACT_CONTEXT_TOOL_DEFINITION = {
    "name": "compact_context",
    "description": (
        "Replace the full loaded context with task-specific notes (contract phase), "
        "or restore the original context block (expand phase). Use after reading "
        "through loaded data — write down only what matters for the work you're "
        "about to do. Query tools (query_sql, query_vector, query_source) still "
        "work in compact mode — they hit the database directly. Restore when the "
        "task shifts and your notes don't cover it. Context auto-resets each new "
        "user message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "notes": {
                "type": "string",
                "description": (
                    "Task-relevant notes that replace the full context block. "
                    "Write down what you need for the work you're about to do — "
                    "names, dates, key facts, thread IDs, etc. Not a generic "
                    "summary — mission-driven notes."
                ),
            },
            "restore": {
                "type": "boolean",
                "description": (
                    "Set to true to restore the original full context block "
                    "(expand phase). Ignores notes when true."
                ),
                "default": False,
            },
        },
        "required": []
    }
}


TASK_QUEUE_TOOL_DEFINITIONS = [
    {
        "name": "task_queue_add",
        "description": (
            "Add a task to the user's task queue. Use when the user says "
            "'add to my queue', 'remind me to', 'do this later', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task description"
                },
            },
            "required": ["task"]
        }
    },
]


def _resolve_spreadsheet_id(identifier: str, workspace: str) -> str:
    """Resolve a spreadsheet name, URL, or ID to a Google Sheets spreadsheet ID.

    1. Google Sheets URL → extract ID from /spreadsheets/d/{ID}/
    2. Long alphanumeric string → passthrough as raw ID
    3. Name → look up in google_sheets SQLite table
    """
    if not identifier:
        raise ValueError("Spreadsheet identifier is required")

    # 1. URL pattern
    url_match = re.match(
        r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)',
        identifier,
    )
    if url_match:
        return url_match.group(1)

    # 2. Looks like a raw ID (long alphanumeric string with hyphens/underscores)
    if len(identifier) > 20 and re.match(r'^[a-zA-Z0-9_-]+$', identifier):
        return identifier

    # 3. Name lookup in SQLite
    import sqlite3 as _sqlite3
    from promaia.utils.env_writer import get_db_path

    db_file = str(get_db_path())
    conn = _sqlite3.connect(db_file)
    try:
        cursor = conn.cursor()
        # Exact match first
        cursor.execute(
            "SELECT page_id FROM google_sheets WHERE title = ? AND workspace = ?",
            (identifier, workspace),
        )
        row = cursor.fetchone()
        if row:
            return row[0]

        # Case-insensitive fuzzy match
        cursor.execute(
            "SELECT page_id FROM google_sheets WHERE title LIKE ? AND workspace = ? LIMIT 1",
            (f"%{identifier}%", workspace),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    finally:
        conn.close()

    raise ValueError(f"Could not resolve spreadsheet: '{identifier}'")


def _hex_to_rgb(hex_color: str) -> Dict[str, float]:
    """Convert '#RRGGBB' to Google Sheets RGB float dict."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return {"red": r / 255.0, "green": g / 255.0, "blue": b / 255.0}


def _a1_to_grid_range(a1: str, sheet_id: int) -> Dict:
    """Convert an A1 notation range to a GridRange dict for batchUpdate requests.

    Supports: 'A1:D5', 'A1', 'A:D', '1:5', 'A1:D'
    """
    def _col_to_index(col_str: str) -> int:
        result = 0
        for ch in col_str.upper():
            result = result * 26 + (ord(ch) - ord('A') + 1)
        return result - 1  # 0-based

    grid: Dict[str, Any] = {"sheetId": sheet_id}

    parts = a1.split(":")
    start = parts[0]
    end = parts[1] if len(parts) > 1 else start

    # Parse start
    start_match = re.match(r'^([A-Za-z]*)(\d*)$', start)
    end_match = re.match(r'^([A-Za-z]*)(\d*)$', end)
    if not start_match or not end_match:
        return grid

    s_col, s_row = start_match.group(1), start_match.group(2)
    e_col, e_row = end_match.group(1), end_match.group(2)

    if s_col:
        grid["startColumnIndex"] = _col_to_index(s_col)
    if e_col:
        grid["endColumnIndex"] = _col_to_index(e_col) + 1
    if s_row:
        grid["startRowIndex"] = int(s_row) - 1
    if e_row:
        grid["endRowIndex"] = int(e_row)

    return grid


def build_tool_definitions(agent, has_platform: bool = False) -> List[Dict[str, Any]]:
    """Build tool definitions for an agent.

    Always includes query tools. Messaging tools included when a platform
    is available (i.e. the agent is in a conversation). Gmail/calendar/notion
    tools included when the agent has them in mcp_tools.
    """
    tools = list(QUERY_TOOL_DEFINITIONS)

    if has_platform:
        tools.extend(MESSAGING_TOOL_DEFINITIONS)

    mcp_tools = getattr(agent, 'mcp_tools', []) or []
    if "gmail" in mcp_tools:
        tools.extend(GMAIL_TOOL_DEFINITIONS)
        tools.extend(GMAIL_READ_TOOL_DEFINITIONS)
    if "calendar" in mcp_tools:
        tools.extend(CALENDAR_TOOL_DEFINITIONS)
        tools.extend(CALENDAR_READ_TOOL_DEFINITIONS)
        if getattr(agent, 'calendar_id', None):
            tools.append(SCHEDULE_SELF_TOOL_DEFINITION)
        if getattr(agent, 'agent_calendars', None):
            tools.append(SCHEDULE_AGENT_EVENT_TOOL_DEFINITION)
    if "notion" in mcp_tools:
        tools.extend(NOTION_TOOL_DEFINITIONS)
        tools.extend(NOTION_BLOCK_TOOL_DEFINITIONS)
    if "web_search" in mcp_tools:
        tools.extend(WEB_SEARCH_TOOL_DEFINITIONS)
        tools.extend(WEB_FETCH_TOOL_DEFINITIONS)
    if "google_sheets" in mcp_tools:
        tools.extend(GOOGLE_SHEETS_TOOL_DEFINITIONS)
        tools.extend(GOOGLE_SHEETS_FORMAT_TOOL_DEFINITIONS)

    # Task queue is always available
    tools.extend(TASK_QUEUE_TOOL_DEFINITIONS)

    # Context compact — always available
    tools.append(COMPACT_CONTEXT_TOOL_DEFINITION)

    return tools


# ── Tool executor ────────────────────────────────────────────────────────

class ToolExecutor:
    """Routes tool calls to Promaia backends."""

    def __init__(self, agent, workspace: str, platform=None, channel_context=None):
        self.agent = agent
        self.workspace = workspace
        self.platform = platform  # BaseMessagingPlatform instance (for send_message)
        self.channel_context = channel_context  # {"channel_id": str, "thread_id": str}
        self._agent_calendar_id = getattr(agent, 'calendar_id', None)
        self._agent_calendars = getattr(agent, 'agent_calendars', {}) or {}
        # Lazy-initialized connectors for MCP tools
        self._gmail_connector = None
        self._calendar_service = None
        self._calendar_tz = None
        self._notion_client = None
        self._sheets_service = None
        self._drive_service = None

    async def execute(self, tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Execute a tool and return a plain text result."""
        try:
            # Query tools
            if tool_name == "query_sql":
                return await self._query_sql(tool_input)
            elif tool_name == "query_vector":
                return await self._query_vector(tool_input)
            elif tool_name == "query_source":
                return await self._query_source(tool_input)
            elif tool_name == "write_journal":
                return await self._write_journal(tool_input)
            # Messaging tools
            elif tool_name == "send_message":
                return await self._send_message(tool_input)
            elif tool_name == "start_conversation":
                return await self._start_conversation(tool_input)
            # Gmail tools
            elif tool_name == "send_email":
                return await self._send_email(tool_input)
            elif tool_name == "create_email_draft":
                return await self._create_email_draft(tool_input)
            elif tool_name == "reply_to_email":
                return await self._reply_to_email(tool_input)
            # Gmail read tools
            elif tool_name == "search_emails":
                return await self._search_emails(tool_input)
            elif tool_name == "get_email_thread":
                return await self._get_email_thread(tool_input)
            # Calendar tools
            elif tool_name == "schedule_self":
                return await self._schedule_self(tool_input)
            elif tool_name == "schedule_agent_event":
                return await self._schedule_agent_event(tool_input)
            elif tool_name == "create_calendar_event":
                return await self._create_calendar_event(tool_input)
            elif tool_name == "update_calendar_event":
                return await self._update_calendar_event(tool_input)
            elif tool_name == "delete_calendar_event":
                return await self._delete_calendar_event(tool_input)
            # Calendar read tools
            elif tool_name == "list_calendar_events":
                return await self._list_calendar_events(tool_input)
            elif tool_name == "get_calendar_event":
                return await self._get_calendar_event(tool_input)
            # Web search & fetch
            elif tool_name == "web_search":
                return await self._web_search(tool_input)
            elif tool_name == "web_fetch":
                return await self._web_fetch(tool_input)
            # Google Sheets tools
            elif tool_name.startswith("sheets_"):
                return await self._execute_sheets_tool(tool_name, tool_input)
            # Notion tools
            elif tool_name.startswith("notion_"):
                return await self._execute_notion_tool(tool_name, tool_input)
            # Task queue
            elif tool_name == "task_queue_add":
                return await self._task_queue_add(tool_input)
            # Context compact (sentinel — handled by the agentic loop)
            elif tool_name == "compact_context":
                if tool_input.get("restore", False):
                    return "__CONTEXT_RESTORE__"
                notes = tool_input.get("notes", "")
                if not notes:
                    return "Error: provide either 'notes' to compact or 'restore: true' to expand."
                return f"__CONTEXT_COMPACT__:{notes}"
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
            return f"Error executing {tool_name}: {e}"

    async def _query_sql(self, tool_input: Dict) -> str:
        from promaia.ai.nl_processor_wrapper import process_natural_language_to_content
        from promaia.ai.prompts import format_context_data

        query = tool_input.get("query", "")
        if not query:
            return "Error: missing 'query' parameter"

        result = await asyncio.to_thread(
            process_natural_language_to_content,
            nl_prompt=query,
            workspace=self.workspace,
            verbose=False,
            skip_confirmation=True,
            return_metadata=True,
        )

        if result is None:
            return "Query returned no results."

        if isinstance(result, tuple) and len(result) == 2:
            loaded_content, metadata = result
        else:
            loaded_content = result if isinstance(result, dict) else {}
            metadata = {}

        if not loaded_content:
            return "Query returned no results."

        total_pages = sum(len(pages) for pages in loaded_content.values() if pages)
        formatted = format_context_data(loaded_content)

        parts = [f"Found {total_pages} results"]
        if metadata and metadata.get('generated_query'):
            parts.append(f"SQL: {metadata['generated_query']}")
        parts.append("")
        parts.append(formatted)
        return "\n".join(parts)

    async def _query_vector(self, tool_input: Dict) -> str:
        from promaia.ai.nl_processor_wrapper import process_vector_search_to_content
        from promaia.ai.prompts import format_context_data

        query = tool_input.get("query", "")
        if not query:
            return "Error: missing 'query' parameter"

        top_k = tool_input.get("top_k", 50)
        min_similarity = tool_input.get("min_similarity", 0.2)

        loaded_content = await asyncio.to_thread(
            process_vector_search_to_content,
            vs_prompt=query,
            workspace=self.workspace,
            n_results=top_k,
            min_similarity=min_similarity,
            verbose=False,
            skip_confirmation=True,
        )

        if not loaded_content:
            return "Semantic search returned no results."

        total_pages = sum(len(pages) for pages in loaded_content.values() if pages)
        formatted = format_context_data(loaded_content)
        return f"Found {total_pages} semantically similar results\n\n{formatted}"

    async def _query_source(self, tool_input: Dict) -> str:
        from promaia.config.databases import get_database_config
        from promaia.storage.files import load_database_pages_with_filters
        from promaia.ai.prompts import format_context_data

        database = tool_input.get("database", "")
        if not database:
            return "Error: missing 'database' parameter"

        days = tool_input.get("days")
        if days == 0:
            days = None

        db_config = get_database_config(database, self.workspace)
        if not db_config:
            return f"Database '{database}' not found in workspace '{self.workspace}'"

        pages = await asyncio.to_thread(
            load_database_pages_with_filters,
            database_config=db_config,
            days=days,
        )

        time_range = f"last {days} days" if days else "all time"
        formatted = format_context_data({database: pages})
        return f"Loaded {len(pages)} pages from '{database}' ({time_range})\n\n{formatted}"

    async def _write_journal(self, tool_input: Dict) -> str:
        from promaia.agents.notion_journal import write_journal_entry

        content = tool_input.get("content", "")
        if not content:
            return "Error: missing 'content' parameter"

        note_type = tool_input.get("note_type", "Note")

        await write_journal_entry(
            agent_id=self.agent.agent_id,
            workspace=self.workspace,
            entry_type=note_type,
            content=content,
            execution_id=None,
        )

        return f"Wrote {note_type} to journal successfully."

    # ── Messaging tools ───────────────────────────────────────────────────

    async def _resolve_user(self, user_name: str) -> Optional[Dict]:
        """Resolve a user name to {id, name, real_name} using synced team data.

        Falls back to live Slack API if team data doesn't have the user.
        """
        import os

        # 1. Try synced team data (fast, no API call)
        try:
            from promaia.config.team import get_team_manager
            team = get_team_manager()

            # Auto-sync if stale
            if team.is_stale(max_age_hours=24):
                bot_token = os.environ.get("SLACK_BOT_TOKEN")
                if bot_token:
                    await team.sync_from_slack(bot_token)

            member = team.find_member(user_name)
            if member and member.slack_id:
                return {
                    "id": member.slack_id,
                    "name": member.slack_username or member.name,
                    "real_name": member.name,
                }
        except Exception as e:
            logger.debug(f"Team lookup failed: {e}")

        # 2. Fallback to live API
        if hasattr(self.platform, "find_user_by_name"):
            return await self.platform.find_user_by_name(user_name)

        return None

    async def _send_message(self, tool_input: Dict) -> str:
        if not self.platform:
            return "Error: messaging is not available in this context (no platform)."

        content = tool_input.get("content", "")
        if not content:
            return "Error: missing 'content' parameter"

        user_name = tool_input.get("user")
        channel_id = tool_input.get("channel_id")
        thread_id = tool_input.get("thread_id")

        # DM by user name — resolve to channel via conversations.open
        if user_name:
            user_info = await self._resolve_user(user_name)
            if not user_info:
                return f"Error: could not find user matching '{user_name}'"
            dm_channel = await self.platform.open_dm(user_info["id"])
            if not dm_channel:
                return f"Error: could not open DM with {user_info['real_name'] or user_name}"
            channel_id = dm_channel
            target_desc = f"DM to {user_info.get('real_name') or user_info.get('name', user_name)}"
        elif channel_id == "current" or (not channel_id and not user_name):
            if not self.channel_context:
                return "Error: no current channel context available. Use 'user' to DM someone or provide a channel_id."
            channel_id = self.channel_context["channel_id"]
            thread_id = thread_id or self.channel_context.get("thread_id")
            target_desc = f"channel {channel_id}"
        else:
            target_desc = f"channel {channel_id}"

        if not channel_id:
            return "Error: must provide either 'user' or 'channel_id'"

        await self.platform.send_message(
            channel_id=channel_id,
            content=content,
            thread_id=thread_id,
        )
        if thread_id:
            target_desc += f" (thread {thread_id[:12]})"
        return f"Message sent to {target_desc}"

    async def _start_conversation(self, tool_input: Dict) -> str:
        """Start a DM conversation, wait for the user's reply, and return it.

        Creates a passive 'agentic' conversation — the Slack bot stores
        incoming messages but does NOT generate AI responses.  The agentic
        loop is the brain; this tool is just the ears.
        """
        import asyncio
        if not self.platform:
            return "Error: messaging is not available in this context (no platform)."

        user_name = tool_input.get("user", "")
        message = tool_input.get("message", "")
        timeout_minutes = tool_input.get("timeout_minutes", 15)

        if not user_name:
            return "Error: missing 'user' parameter"
        if not message:
            return "Error: missing 'message' parameter"

        # Resolve user
        user_info = await self._resolve_user(user_name)
        if not user_info:
            return f"Error: could not find user matching '{user_name}'"

        # Open DM channel
        dm_channel = await self.platform.open_dm(user_info["id"])
        if not dm_channel:
            return f"Error: could not open DM with {user_info['real_name'] or user_name}"

        try:
            from promaia.agents.conversation_manager import (
                ConversationManager, ConversationState,
            )
            from datetime import datetime, timezone

            conv_manager = ConversationManager()
            platform_name = getattr(self.platform, "platform_name", "slack")

            # Register platform so the Slack bot can route replies
            if platform_name not in conv_manager.platforms:
                conv_manager.register_platform(platform_name, self.platform)

            agent_id = (
                getattr(self.agent, "agent_id", None)
                or getattr(self.agent, "name", "agent")
            )

            # Send message directly (no agent-name formatting from ConversationManager)
            await self.platform.send_message(
                channel_id=dm_channel, content=message,
            )

            # Create a passive conversation record — the Slack bot will find
            # this via get_active_conversation and store user replies, but
            # handle_user_message won't generate AI responses for type='agentic'.
            now = datetime.now(timezone.utc).isoformat()
            msg_ts = str(int(datetime.now(timezone.utc).timestamp()))
            conversation_id = f"{platform_name}_{dm_channel}_{msg_ts}"

            state = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_id,
                platform=platform_name,
                channel_id=dm_channel,
                user_id=user_info["id"],
                thread_id=None,  # DMs don't use threads
                status="active",
                last_message_at=now,
                messages=[{
                    "role": "assistant",
                    "content": message,
                    "timestamp": now,
                }],
                context={},
                timeout_seconds=timeout_minutes * 60,
                max_turns=None,
                turn_count=0,
                created_at=now,
                conversation_type="agentic",  # passive — no auto-response
            )
            await conv_manager._save_state(state)

            # Poll for user reply (stored by Slack bot → handle_user_message)
            poll_interval = 3
            max_wait = timeout_minutes * 60
            elapsed = 0

            while elapsed < max_wait:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                state = await conv_manager._load_state(conversation_id)
                if not state:
                    return "Error: conversation state lost"

                # Check for user messages
                user_msgs = [m for m in state.messages if m["role"] == "user"]
                if user_msgs:
                    last_msg = user_msgs[-1]["content"]
                    # End the conversation so the channel is free
                    await conv_manager.end_conversation(
                        conversation_id, "handed_to_agent",
                    )
                    return f"User replied: {last_msg}"

            # Timeout
            await conv_manager.end_conversation(conversation_id, "timeout")
            real_name = user_info.get("real_name", user_name)
            return f"No reply from {real_name} after {timeout_minutes} minutes."

        except Exception as e:
            logger.error(f"start_conversation error: {e}", exc_info=True)
            return f"Error starting conversation: {e}"

    # ── Gmail tools ──────────────────────────────────────────────────────

    async def _ensure_gmail(self):
        """Lazy-initialize Gmail connector."""
        if self._gmail_connector is not None:
            return
        from promaia.connectors.gmail_connector import GmailConnector
        from promaia.config.databases import get_database_config

        gmail_db = (
            get_database_config(f"{self.workspace}.gmail")
            or get_database_config("gmail")
        )
        if not gmail_db:
            raise RuntimeError(f"No Gmail configured for workspace {self.workspace}")

        email = gmail_db.get("database_id")
        config = {"database_id": email, "workspace": self.workspace}
        self._gmail_connector = GmailConnector(config)
        if not await self._gmail_connector.connect(allow_interactive=False):
            self._gmail_connector = None
            raise RuntimeError(f"Failed to connect to Gmail: {email}")
        logger.info(f"Gmail connected: {email}")

    async def _send_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        success = await self._gmail_connector.send_email(
            to=tool_input["to"],
            subject=tool_input["subject"],
            body_text=tool_input["body"],
            cc=tool_input.get("cc"),
        )
        if success:
            return f"Email sent to {tool_input['to']}: {tool_input['subject']}"
        return "Failed to send email."

    async def _create_email_draft(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        draft_id = await self._gmail_connector._create_draft(
            to=tool_input["to"],
            subject=tool_input["subject"],
            body=tool_input["body"],
        )
        if draft_id:
            return f"Draft created (ID: {draft_id})"
        return "Failed to create draft."

    async def _reply_to_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        original = await self._gmail_connector._get_message(tool_input["message_id"])
        if not original:
            return "Original message not found."

        headers = original.get('payload', {}).get('headers', [])
        subject = next(
            (h['value'] for h in headers if h['name'].lower() == 'subject'), ''
        )
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        success = await self._gmail_connector.send_reply(
            thread_id=tool_input["thread_id"],
            message_id=tool_input["message_id"],
            subject=subject,
            body_text=tool_input["body"],
        )
        if success:
            return f"Reply sent (thread: {tool_input['thread_id']})"
        return "Failed to send reply."

    # ── Gmail read tools ─────────────────────────────────────────────────

    async def _search_emails(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        query = tool_input.get("query", "")
        if not query:
            return "Error: missing 'query' parameter"
        max_results = tool_input.get("max_results", 10)

        try:
            # Search for messages
            resp = self._gmail_connector.service.users().messages().list(
                userId='me', q=query, maxResults=max_results
            ).execute()
            messages = resp.get('messages', [])
            if not messages:
                return f"No emails found for query: {query}"

            # Fetch details for each message
            summaries = []
            for msg_ref in messages:
                msg = self._gmail_connector.service.users().messages().get(
                    userId='me', id=msg_ref['id'], format='metadata',
                    metadataHeaders=['From', 'To', 'Subject', 'Date'],
                ).execute()
                headers = {
                    h['name'].lower(): h['value']
                    for h in msg.get('payload', {}).get('headers', [])
                }
                snippet = msg.get('snippet', '')[:120]
                summaries.append(
                    f"- **{headers.get('subject', '(no subject)')}**\n"
                    f"  From: {headers.get('from', '?')} | Date: {headers.get('date', '?')}\n"
                    f"  ID: {msg_ref['id']} | Thread: {msg.get('threadId', '')}\n"
                    f"  {snippet}"
                )
            return f"Found {len(messages)} emails:\n\n" + "\n\n".join(summaries)
        except Exception as e:
            return f"Error searching emails: {e}"

    async def _get_email_thread(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        thread_id = tool_input.get("thread_id", "")
        if not thread_id:
            return "Error: missing 'thread_id' parameter"

        try:
            raw_id = self._gmail_connector._get_raw_thread_id(thread_id)
            thread = self._gmail_connector.service.users().threads().get(
                userId='me', id=raw_id, format='full'
            ).execute()
            messages = thread.get('messages', [])
            if not messages:
                return f"Thread {thread_id} has no messages."

            parts = [f"Thread: {thread_id} ({len(messages)} messages)\n"]
            for msg in messages:
                headers = {
                    h['name'].lower(): h['value']
                    for h in msg.get('payload', {}).get('headers', [])
                }
                body = self._gmail_connector._extract_message_body(msg)
                # Truncate very long bodies
                if len(body) > 2000:
                    body = body[:2000] + "\n[...truncated]"
                parts.append(
                    f"---\n"
                    f"From: {headers.get('from', '?')}\n"
                    f"Date: {headers.get('date', '?')}\n"
                    f"Subject: {headers.get('subject', '')}\n"
                    f"Message-ID: {msg.get('id', '')}\n\n"
                    f"{body}"
                )
            return "\n".join(parts)
        except Exception as e:
            return f"Error fetching thread: {e}"

    # ── Calendar tools ───────────────────────────────────────────────────

    @staticmethod
    def _get_google_creds():
        """Get Google credentials, trying legacy global then authenticated accounts."""
        from promaia.auth.registry import get_integration

        google_int = get_integration("google")
        creds = google_int.get_google_credentials()
        if not creds:
            for acct in google_int.list_authenticated_accounts():
                creds = google_int.get_google_credentials(acct)
                if creds:
                    break
        if not creds:
            raise RuntimeError(
                "Google not configured. Run: maia auth configure google"
            )
        return creds

    async def _ensure_calendar(self):
        """Lazy-initialize Google Calendar service."""
        if self._calendar_service is not None:
            return
        from googleapiclient.discovery import build

        creds = self._get_google_creds()
        self._calendar_service = await asyncio.to_thread(
            build, 'calendar', 'v3', credentials=creds
        )
        # Fetch the user's primary calendar timezone
        try:
            cal = await asyncio.to_thread(
                self._calendar_service.calendars().get(calendarId='primary').execute
            )
            self._calendar_tz = cal.get('timeZone', 'UTC')
        except Exception:
            self._calendar_tz = 'UTC'
        logger.info(f"Google Calendar authenticated (tz: {self._calendar_tz})")

    async def _schedule_self(self, tool_input: Dict) -> str:
        if not self._agent_calendar_id:
            return "Error: No dedicated calendar configured for this agent. Cannot schedule self."
        # Default end_time to start + 30 minutes
        start = tool_input["start_time"]
        if not tool_input.get("end_time"):
            from datetime import datetime as dt, timedelta
            try:
                parsed = dt.fromisoformat(start)
                tool_input["end_time"] = (parsed + timedelta(minutes=30)).isoformat()
            except ValueError:
                tool_input["end_time"] = start  # fallback: same as start
        tool_input["calendar_id"] = self._agent_calendar_id
        result = await self._create_calendar_event(tool_input)
        summary = tool_input.get("summary", "")
        return f"Scheduled: '{summary}' at {start} (on your calendar)\n{result}"

    async def _schedule_agent_event(self, tool_input: Dict) -> str:
        if not self._agent_calendars:
            return "Error: No agent calendars available."

        # Resolve which agent calendar to use
        agent_name = tool_input.get("agent")
        if len(self._agent_calendars) == 1:
            # Implicit — only one agent has a calendar
            agent_name = next(iter(self._agent_calendars))
        elif not agent_name:
            names = ", ".join(sorted(self._agent_calendars.keys()))
            return f"Error: Multiple agent calendars available ({names}). Please specify the 'agent' parameter."
        if agent_name not in self._agent_calendars:
            names = ", ".join(sorted(self._agent_calendars.keys()))
            return f"Error: No calendar for agent '{agent_name}'. Available: {names}"

        calendar_id = self._agent_calendars[agent_name]

        # Default end_time to start + 30 minutes
        start = tool_input["start_time"]
        if not tool_input.get("end_time"):
            from datetime import datetime as dt, timedelta
            try:
                parsed = dt.fromisoformat(start)
                tool_input["end_time"] = (parsed + timedelta(minutes=30)).isoformat()
            except ValueError:
                tool_input["end_time"] = start

        tool_input["calendar_id"] = calendar_id
        result = await self._create_calendar_event(tool_input)
        summary = tool_input.get("summary", "")
        return f"Scheduled on {agent_name}'s calendar: '{summary}' at {start}\n{result}"

    async def _create_calendar_event(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        calendar_id = tool_input.get("calendar_id", "primary")
        tz = self._calendar_tz or 'UTC'
        event_body = {
            'summary': tool_input['summary'],
            'start': {'dateTime': tool_input['start_time'], 'timeZone': tz},
            'end': {'dateTime': tool_input['end_time'], 'timeZone': tz},
        }
        if tool_input.get('description'):
            event_body['description'] = tool_input['description']
        if tool_input.get('recurrence'):
            event_body['recurrence'] = [tool_input['recurrence']]

        event = await asyncio.to_thread(
            self._calendar_service.events().insert(
                calendarId=calendar_id, body=event_body
            ).execute
        )
        return (
            f"Event created: {event.get('summary')}\n"
            f"ID: {event.get('id')}\n"
            f"Link: {event.get('htmlLink')}"
        )

    async def _update_calendar_event(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        event_id = tool_input['event_id']
        calendar_id = tool_input.get("calendar_id", "primary")

        event = await asyncio.to_thread(
            self._calendar_service.events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute
        )

        if 'summary' in tool_input:
            event['summary'] = tool_input['summary']
        if 'description' in tool_input:
            event['description'] = tool_input['description']
        tz = self._calendar_tz or 'UTC'
        if 'start_time' in tool_input:
            event['start'] = {'dateTime': tool_input['start_time'], 'timeZone': tz}
        if 'end_time' in tool_input:
            event['end'] = {'dateTime': tool_input['end_time'], 'timeZone': tz}

        await asyncio.to_thread(
            self._calendar_service.events().update(
                calendarId=calendar_id, eventId=event_id, body=event
            ).execute
        )
        return f"Event updated: {event_id}"

    async def _delete_calendar_event(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        event_id = tool_input['event_id']
        calendar_id = tool_input.get("calendar_id", "primary")

        await asyncio.to_thread(
            self._calendar_service.events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute
        )
        return f"Event deleted: {event_id}"

    # ── Calendar read tools ──────────────────────────────────────────────

    async def _list_calendar_events(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        days_ahead = tool_input.get("days_ahead", 7)
        days_back = tool_input.get("days_back", 0)
        query = tool_input.get("query")

        now = _dt.now(_tz.utc)
        time_min = (now - _td(days=days_back)).isoformat()
        time_max = (now + _td(days=days_ahead)).isoformat()

        kwargs = {
            "calendarId": "primary",
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 50,
        }
        if query:
            kwargs["q"] = query

        try:
            result = await asyncio.to_thread(
                self._calendar_service.events().list(**kwargs).execute
            )
            events = result.get('items', [])
            if not events:
                return "No calendar events found for the specified range."

            lines = [f"Found {len(events)} events:\n"]
            for ev in events:
                start = ev.get('start', {})
                start_str = start.get('dateTime') or start.get('date', '?')
                end = ev.get('end', {})
                end_str = end.get('dateTime') or end.get('date', '?')
                summary = ev.get('summary', '(No title)')
                location = ev.get('location', '')
                loc_str = f" | Location: {location}" if location else ""
                lines.append(
                    f"- **{summary}**\n"
                    f"  {start_str} to {end_str}{loc_str}\n"
                    f"  ID: {ev.get('id', '')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing calendar events: {e}"

    async def _get_calendar_event(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        event_id = tool_input.get("event_id", "")
        if not event_id:
            return "Error: missing 'event_id' parameter"

        try:
            event = await asyncio.to_thread(
                self._calendar_service.events().get(
                    calendarId="primary", eventId=event_id
                ).execute
            )
            start = event.get('start', {})
            end = event.get('end', {})
            attendees = event.get('attendees', [])
            attendee_list = ', '.join(a.get('email', '') for a in attendees)

            parts = [
                f"**{event.get('summary', '(No title)')}**",
                f"Start: {start.get('dateTime') or start.get('date', '?')}",
                f"End: {end.get('dateTime') or end.get('date', '?')}",
            ]
            if event.get('location'):
                parts.append(f"Location: {event['location']}")
            if attendee_list:
                parts.append(f"Attendees: {attendee_list}")
            if event.get('description'):
                parts.append(f"Description: {event['description']}")
            parts.append(f"Status: {event.get('status', '?')}")
            parts.append(f"Link: {event.get('htmlLink', '')}")
            return "\n".join(parts)
        except Exception as e:
            return f"Error getting calendar event: {e}"

    # ── Google Sheets tools ─────────────────────────────────────────────

    async def _ensure_sheets(self):
        """Lazy-initialize Google Sheets and Drive services."""
        if self._sheets_service is not None:
            return
        from googleapiclient.discovery import build

        creds = self._get_google_creds()
        self._sheets_service = await asyncio.to_thread(
            build, 'sheets', 'v4', credentials=creds
        )
        self._drive_service = await asyncio.to_thread(
            build, 'drive', 'v3', credentials=creds
        )
        logger.info("Google Sheets & Drive authenticated")

    async def _execute_sheets_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Route and execute Google Sheets tool calls."""
        await self._ensure_sheets()

        if tool_name == "sheets_read_range":
            return await self._sheets_read_range(tool_input)
        elif tool_name == "sheets_update_cells":
            return await self._sheets_update_cells(tool_input)
        elif tool_name == "sheets_append_rows":
            return await self._sheets_append_rows(tool_input)
        elif tool_name == "sheets_create_spreadsheet":
            return await self._sheets_create_spreadsheet(tool_input)
        elif tool_name == "sheets_manage_sheets":
            return await self._sheets_manage_sheets(tool_input)
        elif tool_name == "sheets_format_cells":
            return await self._sheets_format_cells(tool_input)
        elif tool_name == "sheets_find":
            return await self._sheets_find(tool_input)
        elif tool_name == "sheets_ingest":
            return await self._sheets_ingest(tool_input)
        elif tool_name == "sheets_insert_rows":
            return await self._sheets_insert_rows(tool_input)
        else:
            return f"Unknown Sheets tool: {tool_name}"

    async def _sheets_read_range(self, tool_input: Dict) -> str:
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )
        range_str = tool_input.get("range", "")
        if not range_str:
            return "Error: missing 'range' parameter"

        from promaia.connectors.google_sheets_connector import GoogleSheetsConnector

        # Fetch both formula and display values for inline format
        formula_result = await asyncio.to_thread(
            self._sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueRenderOption='FORMULA',
            ).execute
        )
        display_result = await asyncio.to_thread(
            self._sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueRenderOption='FORMATTED_VALUE',
            ).execute
        )
        formula_rows = formula_result.get("values", [])
        display_rows = display_result.get("values", [])
        if not formula_rows and not display_rows:
            return f"Range {range_str} is empty."

        # Determine starting row number from the range
        import re as _re
        start_row = 1
        cell_part = range_str.split("!")[-1] if "!" in range_str else range_str
        m = _re.search(r'[A-Za-z](\d+)', cell_part)
        if m:
            start_row = int(m.group(1))

        # Build inline CSV with {=formula} display_value format + row numbers
        import csv
        import io
        max_rows = max(len(formula_rows), len(display_rows))
        out = io.StringIO()
        writer = csv.writer(out)
        for i in range(max_rows):
            f_row = formula_rows[i] if i < len(formula_rows) else []
            d_row = display_rows[i] if i < len(display_rows) else []
            max_cols = max(len(f_row), len(d_row))
            cells = [f"[row {start_row + i}]"]
            for j in range(max_cols):
                f_val = str(f_row[j]) if j < len(f_row) else ""
                d_val = str(d_row[j]) if j < len(d_row) else ""
                if f_val.startswith("=") and f_val != d_val:
                    cells.append(f"{{{f_val}}} {d_val}")
                else:
                    cells.append(d_val)
            writer.writerow(cells)
        return f"Range {range_str} ({max_rows} rows, starting row {start_row}):\n\n{out.getvalue()}"

    @staticmethod
    def _coerce_values(values):
        """Ensure values is a 2D list (model sometimes sends a JSON string)."""
        if isinstance(values, str):
            values = json.loads(values)
        return values

    async def _sheets_update_cells(self, tool_input: Dict) -> str:
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )
        value_input = tool_input.get("value_input", "USER_ENTERED")

        # Batch update
        batch_ranges = tool_input.get("ranges")
        if batch_ranges:
            data = [
                {
                    "range": r["range"],
                    "values": self._coerce_values(r["values"]),
                }
                for r in batch_ranges
            ]
            result = await asyncio.to_thread(
                self._sheets_service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "valueInputOption": value_input,
                        "data": data,
                    },
                ).execute
            )
            updated = result.get("totalUpdatedCells", 0)
            return f"Updated {updated} cells across {len(data)} ranges."

        # Single range update
        range_str = tool_input.get("range", "")
        values = self._coerce_values(tool_input.get("values", []))
        if not range_str or not values:
            return "Error: 'range' and 'values' required (or use 'ranges' for batch)"

        result = await asyncio.to_thread(
            self._sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption=value_input,
                body={"values": values},
            ).execute
        )
        updated = result.get("updatedCells", 0)
        return f"Updated {updated} cells in {range_str}."

    async def _sheets_append_rows(self, tool_input: Dict) -> str:
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )
        range_str = tool_input.get("range", "")
        values = self._coerce_values(tool_input.get("values", []))
        if not range_str or not values:
            return "Error: 'range' and 'values' required"

        value_input = tool_input.get("value_input", "USER_ENTERED")

        result = await asyncio.to_thread(
            self._sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption=value_input,
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            ).execute
        )
        updates = result.get("updates", {})
        updated_rows = updates.get("updatedRows", len(values))
        return f"Appended {updated_rows} rows to {range_str}."

    async def _sheets_create_spreadsheet(self, tool_input: Dict) -> str:
        title = tool_input.get("title", "")
        if not title:
            return "Error: 'title' required"

        sheet_names = tool_input.get("sheets") or ["Sheet1"]
        body = {
            "properties": {"title": title},
            "sheets": [
                {"properties": {"title": name}} for name in sheet_names
            ],
        }

        ss = await asyncio.to_thread(
            self._sheets_service.spreadsheets().create(body=body).execute
        )
        ss_id = ss["spreadsheetId"]
        ss_url = ss["spreadsheetUrl"]

        # Optionally move to a folder
        folder_id = tool_input.get("folder_id")
        if folder_id:
            try:
                # Get current parents, then move
                file_info = await asyncio.to_thread(
                    self._drive_service.files().get(
                        fileId=ss_id, fields="parents"
                    ).execute
                )
                current_parents = ",".join(file_info.get("parents", []))
                await asyncio.to_thread(
                    self._drive_service.files().update(
                        fileId=ss_id,
                        addParents=folder_id,
                        removeParents=current_parents,
                        fields="id, parents",
                    ).execute
                )
            except Exception as e:
                logger.warning(f"Could not move spreadsheet to folder: {e}")

        # Optionally populate initial data
        initial_data = tool_input.get("initial_data")
        if initial_data:
            data = [
                {"range": rng, "values": vals}
                for rng, vals in initial_data.items()
            ]
            if data:
                await asyncio.to_thread(
                    self._sheets_service.spreadsheets().values().batchUpdate(
                        spreadsheetId=ss_id,
                        body={"valueInputOption": "USER_ENTERED", "data": data},
                    ).execute
                )

        # Auto-register so _resolve_spreadsheet_id can find it by name
        try:
            import sqlite3 as _sqlite3
            from datetime import datetime, timezone
            from promaia.utils.env_writer import get_db_path
            from promaia.connectors.google_sheets_connector import GoogleSheetsConnector

            db_file = str(get_db_path())
            conn = _sqlite3.connect(db_file)
            try:
                GoogleSheetsConnector._ensure_tables(conn)
                now_str = datetime.now(timezone.utc).isoformat()
                properties = json.dumps({"sheet_names": sheet_names})
                conn.execute("""
                    INSERT INTO google_sheets (
                        page_id, workspace, database_id, file_path,
                        title, content, properties, source_type,
                        created_at, updated_at, last_synced
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'google_sheets_ingest', ?, ?, ?)
                    ON CONFLICT(page_id, workspace) DO UPDATE SET
                        title = excluded.title,
                        properties = excluded.properties,
                        source_type = excluded.source_type,
                        updated_at = excluded.updated_at
                """, (
                    ss_id, self.workspace, "", title,
                    "", properties, now_str, now_str, now_str,
                ))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"Could not auto-register created spreadsheet: {e}")

        tabs = ", ".join(sheet_names)
        return f"Created spreadsheet '{title}' (tabs: {tabs})\nID: {ss_id}\nURL: {ss_url}"

    async def _get_sheet_id_by_name(
        self, spreadsheet_id: str, sheet_name: str
    ) -> Optional[int]:
        """Resolve a tab name to its numeric sheetId."""
        meta = await asyncio.to_thread(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties",
            ).execute
        )
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title", "").lower() == sheet_name.lower():
                return props.get("sheetId")
        return None

    async def _sheets_manage_sheets(self, tool_input: Dict) -> str:
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )

        # Fetch current sheet metadata for name → sheetId resolution
        meta = await asyncio.to_thread(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties",
            ).execute
        )
        name_to_id = {
            s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta.get("sheets", [])
        }

        requests = []
        summaries = []

        # Add tabs
        for name in (tool_input.get("add") or []):
            requests.append({"addSheet": {"properties": {"title": name}}})
            summaries.append(f"Added tab '{name}'")

        # Delete tabs
        for name in (tool_input.get("delete") or []):
            sheet_id = name_to_id.get(name)
            if sheet_id is None:
                summaries.append(f"Tab '{name}' not found, skipped delete")
                continue
            requests.append({"deleteSheet": {"sheetId": sheet_id}})
            summaries.append(f"Deleted tab '{name}'")

        # Rename tabs
        for rename in (tool_input.get("rename") or []):
            old_name = rename.get("from", "")
            new_name = rename.get("to", "")
            sheet_id = name_to_id.get(old_name)
            if sheet_id is None:
                summaries.append(f"Tab '{old_name}' not found, skipped rename")
                continue
            requests.append({
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "title": new_name},
                    "fields": "title",
                }
            })
            summaries.append(f"Renamed '{old_name}' → '{new_name}'")

        if not requests:
            return "No sheet operations to perform."

        await asyncio.to_thread(
            self._sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute
        )
        return "\n".join(summaries)

    async def _sheets_format_cells(self, tool_input: Dict) -> str:
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )
        sheet_name = tool_input.get("sheet")

        # Resolve sheet ID
        if sheet_name:
            sheet_id = await self._get_sheet_id_by_name(spreadsheet_id, sheet_name)
            if sheet_id is None:
                return f"Error: tab '{sheet_name}' not found"
        else:
            # Use first sheet
            meta = await asyncio.to_thread(
                self._sheets_service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields="sheets.properties",
                ).execute
            )
            sheets = meta.get("sheets", [])
            if not sheets:
                return "Error: spreadsheet has no sheets"
            sheet_id = sheets[0]["properties"]["sheetId"]

        formats = tool_input.get("formats", [])
        if not formats:
            return "Error: 'formats' list is required"

        requests = []
        for fmt in formats:
            grid_range = _a1_to_grid_range(fmt["range"], sheet_id)

            # Merge request
            if fmt.get("merge"):
                requests.append({
                    "mergeCells": {
                        "range": grid_range,
                        "mergeType": "MERGE_ALL",
                    }
                })

            # Column width
            if fmt.get("column_width"):
                start_col = grid_range.get("startColumnIndex", 0)
                end_col = grid_range.get("endColumnIndex", start_col + 1)
                requests.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_col,
                            "endIndex": end_col,
                        },
                        "properties": {"pixelSize": fmt["column_width"]},
                        "fields": "pixelSize",
                    }
                })

            # Cell formatting (bold, colors, number format, alignment)
            cell_format = {}
            fields = []

            text_format = {}
            if fmt.get("bold") is not None:
                text_format["bold"] = fmt["bold"]
                fields.append("userEnteredFormat.textFormat.bold")
            if fmt.get("italic") is not None:
                text_format["italic"] = fmt["italic"]
                fields.append("userEnteredFormat.textFormat.italic")
            if fmt.get("font_size"):
                text_format["fontSize"] = fmt["font_size"]
                fields.append("userEnteredFormat.textFormat.fontSize")
            if fmt.get("fg_color"):
                text_format["foregroundColorStyle"] = {
                    "rgbColor": _hex_to_rgb(fmt["fg_color"])
                }
                fields.append("userEnteredFormat.textFormat.foregroundColorStyle")
            if text_format:
                cell_format["textFormat"] = text_format

            if fmt.get("bg_color"):
                cell_format["backgroundColorStyle"] = {
                    "rgbColor": _hex_to_rgb(fmt["bg_color"])
                }
                fields.append("userEnteredFormat.backgroundColorStyle")

            if fmt.get("number_format"):
                cell_format["numberFormat"] = {
                    "type": "NUMBER",
                    "pattern": fmt["number_format"],
                }
                fields.append("userEnteredFormat.numberFormat")

            if fmt.get("h_align"):
                cell_format["horizontalAlignment"] = fmt["h_align"]
                fields.append("userEnteredFormat.horizontalAlignment")

            if cell_format and fields:
                requests.append({
                    "repeatCell": {
                        "range": grid_range,
                        "cell": {"userEnteredFormat": cell_format},
                        "fields": ",".join(fields),
                    }
                })

        if not requests:
            return "No format operations to perform."

        await asyncio.to_thread(
            self._sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute
        )
        return f"Applied {len(requests)} format operations."

    async def _sheets_insert_rows(self, tool_input: Dict) -> str:
        """Insert blank rows at a position, optionally filling them with data."""
        spreadsheet_id = _resolve_spreadsheet_id(
            tool_input.get("spreadsheet", ""), self.workspace
        )
        row = tool_input.get("row")
        if not row or row < 1:
            return "Error: 'row' must be a positive integer (1-based)"
        count = tool_input.get("count", 1)
        sheet_name = tool_input.get("sheet")

        # Resolve sheet ID
        if sheet_name:
            sheet_id = await self._get_sheet_id_by_name(spreadsheet_id, sheet_name)
            if sheet_id is None:
                return f"Error: tab '{sheet_name}' not found"
        else:
            meta = await asyncio.to_thread(
                self._sheets_service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields="sheets.properties",
                ).execute
            )
            sheets = meta.get("sheets", [])
            if not sheets:
                return "Error: spreadsheet has no sheets"
            sheet_id = sheets[0]["properties"]["sheetId"]
            sheet_name = sheets[0]["properties"]["title"]

        # Insert blank rows (0-indexed: row 6 in sheet = startIndex 5)
        await asyncio.to_thread(
            self._sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "startIndex": row - 1,
                            "endIndex": row - 1 + count,
                        },
                        "inheritFromBefore": True,
                    }
                }]},
            ).execute
        )

        # Optionally fill inserted rows with data
        values = tool_input.get("values")
        if values:
            values = self._coerce_values(values)
            write_range = f"'{sheet_name}'!A{row}"
            await asyncio.to_thread(
                self._sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=write_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                ).execute
            )
            return f"Inserted {count} row(s) at row {row} and wrote {len(values)} row(s) of data."

        return f"Inserted {count} blank row(s) at row {row}."

    async def _sheets_find(self, tool_input: Dict) -> str:
        """Search Google Drive for spreadsheets by name."""
        query = tool_input.get("query", "")
        if not query:
            return "Error: missing 'query' parameter"

        # Sanitize single quotes in query
        safe_query = query.replace("'", "\\'")
        q = (
            f"mimeType='application/vnd.google-apps.spreadsheet' "
            f"and name contains '{safe_query}' and trashed=false"
        )
        results = await asyncio.to_thread(
            self._drive_service.files().list(
                q=q,
                fields="files(id, name, modifiedTime, webViewLink)",
                pageSize=20,
                orderBy="modifiedTime desc",
            ).execute
        )
        files = results.get("files", [])
        if not files:
            return f"No spreadsheets found matching '{query}'."

        lines = [f"Found {len(files)} spreadsheet(s) matching '{query}':\n"]
        for f in files:
            url = f.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{f['id']}")
            lines.append(
                f"- **{f['name']}**\n"
                f"  ID: {f['id']}\n"
                f"  Modified: {f.get('modifiedTime', '?')}\n"
                f"  URL: {url}"
            )
        return "\n".join(lines)

    async def _sheets_ingest(self, tool_input: Dict) -> str:
        """One-time ingest of a Google Sheet into local DB and return CSV content."""
        identifier = tool_input.get("spreadsheet", "")
        if not identifier:
            return "Error: missing 'spreadsheet' parameter"

        # Resolve identifier to spreadsheet ID
        spreadsheet_id = None

        # Try URL extraction
        url_match = re.match(
            r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)',
            identifier,
        )
        if url_match:
            spreadsheet_id = url_match.group(1)

        # Try raw ID
        if not spreadsheet_id and len(identifier) > 20 and re.match(r'^[a-zA-Z0-9_-]+$', identifier):
            spreadsheet_id = identifier

        # Try existing DB lookup (may already be cached)
        if not spreadsheet_id:
            try:
                spreadsheet_id = _resolve_spreadsheet_id(identifier, self.workspace)
            except ValueError:
                pass

        # Fall back to Drive search by name
        if not spreadsheet_id:
            safe_query = identifier.replace("'", "\\'")
            q = (
                f"mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name contains '{safe_query}' and trashed=false"
            )
            results = await asyncio.to_thread(
                self._drive_service.files().list(
                    q=q,
                    fields="files(id, name)",
                    pageSize=1,
                    orderBy="modifiedTime desc",
                ).execute
            )
            files = results.get("files", [])
            if files:
                spreadsheet_id = files[0]["id"]

        if not spreadsheet_id:
            return f"Error: could not find spreadsheet '{identifier}'. Try sheets_find first."

        # Fetch metadata
        meta = await asyncio.to_thread(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="properties.title,sheets.properties.title,spreadsheetUrl",
            ).execute
        )
        title = meta.get("properties", {}).get("title", "Untitled")
        ss_url = meta.get("spreadsheetUrl", "")
        sheets = meta.get("sheets", [])
        sheet_names = [s.get("properties", {}).get("title", "") for s in sheets]

        # Fetch formula and display values for each tab, build inline CSV
        from promaia.connectors.google_sheets_connector import GoogleSheetsConnector

        sections = []
        row_counts = {}
        for sheet_title in sheet_names:
            safe_range = f"'{sheet_title}'"
            formula_resp = await asyncio.to_thread(
                self._sheets_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=safe_range,
                    valueRenderOption='FORMULA',
                ).execute
            )
            display_resp = await asyncio.to_thread(
                self._sheets_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=safe_range,
                    valueRenderOption='FORMATTED_VALUE',
                ).execute
            )
            formula_rows = formula_resp.get('values', [])
            display_rows = display_resp.get('values', [])
            row_counts[sheet_title] = len(display_rows)

            csv_text = GoogleSheetsConnector._build_inline_csv(formula_rows, display_rows)
            if len(sheet_names) > 1:
                sections.append(f"## Sheet: {sheet_title}\n\n{csv_text}")
            else:
                sections.append(csv_text)

        content = "\n\n".join(sections)

        # Cache in google_sheets SQLite table
        import sqlite3 as _sqlite3
        from datetime import datetime, timezone
        from promaia.utils.env_writer import get_db_path

        db_file = str(get_db_path())
        conn = _sqlite3.connect(db_file)
        try:
            GoogleSheetsConnector._ensure_tables(conn)
            now_str = datetime.now(timezone.utc).isoformat()
            properties = json.dumps({
                "sheet_names": sheet_names,
                "row_counts": row_counts,
            })
            conn.execute("""
                INSERT INTO google_sheets (
                    page_id, workspace, database_id, file_path,
                    title, content, properties, source_type,
                    created_at, updated_at, last_synced
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, 'google_sheets_ingest', ?, ?, ?)
                ON CONFLICT(page_id, workspace) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    properties = excluded.properties,
                    source_type = excluded.source_type,
                    updated_at = excluded.updated_at,
                    last_synced = excluded.last_synced
            """, (
                spreadsheet_id, self.workspace, "", title,
                content, properties, now_str, now_str, now_str,
            ))
            conn.commit()
        finally:
            conn.close()

        tabs_str = ", ".join(sheet_names)
        total_rows = sum(row_counts.values())
        header = (
            f"Ingested '{title}' ({len(sheet_names)} tab(s): {tabs_str}, "
            f"{total_rows} total rows)\n"
            f"ID: {spreadsheet_id}\nURL: {ss_url}\n\n"
        )
        return header + content

    # ── Notion tools ────────────────────────────────────────────────────

    async def _web_search(self, tool_input: Dict) -> str:
        """Search the web via Perplexity API."""
        import json as _json
        import urllib.request
        import urllib.error

        query = tool_input.get("query", "")
        if not query:
            return "Error: missing 'query' parameter"

        from promaia.auth import get_integration

        try:
            perplexity = get_integration("perplexity")
            api_key = perplexity.get_default_credential()
        except Exception:
            api_key = None
        if not api_key:
            return "Error: Perplexity API key not configured. Run `maia auth setup perplexity`."

        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": "sonar-pro",
            "messages": [{"role": "user", "content": query}],
        }

        def _call():
            req = urllib.request.Request(
                url,
                data=_json.dumps(data).encode("utf-8"),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json.loads(resp.read().decode("utf-8"))

        try:
            result = await asyncio.to_thread(_call)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return "Error: Invalid Perplexity API key."
            elif e.code == 429:
                return "Error: Perplexity rate limit exceeded. Try again later."
            return f"Error: Perplexity API returned HTTP {e.code}: {e.reason}"
        except Exception as e:
            return f"Error: Web search failed: {e}"

        # Extract content
        content = ""
        citations = []
        if "choices" in result and result["choices"]:
            content = result["choices"][0]["message"]["content"]
            msg = result["choices"][0]["message"]
            citations = msg.get("citations", result.get("citations", []))

        if not content:
            return "Web search returned no results."

        # Extract search_results array (title + URL + snippet per result)
        search_results = result.get("search_results", [])

        # Format response
        parts = [content]
        if search_results:
            parts.append("\n\nSearch Results:")
            for i, sr in enumerate(search_results, 1):
                title = sr.get("title", "Untitled")
                url = sr.get("url", "")
                snippet = sr.get("snippet", "")
                parts.append(f"  {i}. {title}")
                if url:
                    parts.append(f"     {url}")
                if snippet:
                    parts.append(f"     {snippet}")
        if citations:
            parts.append("\n\nSources:")
            for i, cite in enumerate(citations, 1):
                if isinstance(cite, dict):
                    parts.append(f"  {i}. {cite.get('title', cite.get('url', 'Source'))}")
                    if cite.get("url"):
                        parts.append(f"     {cite['url']}")
                else:
                    parts.append(f"  {i}. {cite}")
        return "\n".join(parts)

    async def _web_fetch(self, tool_input: Dict) -> str:
        """Fetch and extract text content from a URL."""
        import httpx

        url = tool_input.get("url", "")
        if not url:
            return "Error: missing 'url' parameter"

        MAX_CONTENT_CHARS = 50_000

        def _fetch_and_extract():
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; Promaia/1.0; +https://promaia.com)"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            with httpx.Client(
                follow_redirects=True, timeout=30, max_redirects=5
            ) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                html = resp.text

            # Try trafilatura first (best quality extraction)
            try:
                import trafilatura

                text = trafilatura.extract(
                    html,
                    include_links=True,
                    include_tables=True,
                    favor_recall=True,
                )
                if text and len(text.strip()) > 100:
                    return text
            except Exception:
                pass

            # Fallback: BeautifulSoup basic extraction
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                if text:
                    return text
            except Exception:
                pass

            return ""

        try:
            text = await asyncio.to_thread(_fetch_and_extract)
        except httpx.TimeoutException:
            return f"Error: Request timed out fetching {url}"
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} fetching {url}"
        except httpx.ConnectError:
            return f"Error: Could not connect to {url}"
        except Exception as e:
            return f"Error: Failed to fetch {url}: {e}"

        if not text:
            return f"Could not extract readable content from {url}"

        # Truncate if too long
        if len(text) > MAX_CONTENT_CHARS:
            text = text[:MAX_CONTENT_CHARS] + "\n\n[Content truncated at 50,000 characters]"

        return f"Content from {url}:\n\n{text}"

    async def _ensure_notion(self):
        """Lazy-initialize Notion client."""
        if self._notion_client is not None:
            return
        from promaia.notion.client import get_client
        self._notion_client = get_client(self.workspace)

    async def _task_queue_add(self, tool_input: Dict) -> str:
        """Add a task to the local task queue."""
        from promaia.agents.task_queue_file import append_task
        task = tool_input.get("task", "")
        if not task:
            return "Error: task description is required"
        return append_task(task)

    async def _execute_notion_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Route and execute Notion tool calls."""
        import json as _json
        await self._ensure_notion()

        if tool_name == "notion_search":
            return await self._notion_search(tool_input)
        elif tool_name == "notion_create_page":
            return await self._notion_create_page(tool_input)
        elif tool_name == "notion_update_page":
            return await self._notion_update_page(tool_input)
        elif tool_name == "notion_query_database":
            return await self._notion_query_database(tool_input)
        elif tool_name == "notion_get_blocks":
            return await self._notion_get_blocks(tool_input)
        elif tool_name == "notion_update_blocks":
            return await self._notion_update_blocks(tool_input)
        elif tool_name == "notion_append_blocks":
            return await self._notion_append_blocks(tool_input)
        else:
            return f"Unknown Notion tool: {tool_name}"

    async def _notion_search(self, tool_input: Dict) -> str:
        import json as _json
        query = tool_input.get("query", "")
        kwargs = {"query": query}
        filter_type = tool_input.get("filter")
        if filter_type:
            kwargs["filter"] = {"value": filter_type, "property": "object"}

        results = await self._notion_client.search(**kwargs)
        items = results.get("results", [])

        if not items:
            return f"No results found for '{query}'"

        summaries = []
        for item in items[:20]:
            obj_type = item.get("object", "unknown")
            obj_id = item.get("id", "")
            title = _extract_notion_title(item)
            summaries.append(f"- [{obj_type}] {title} (id: {obj_id})")

        return f"Found {len(items)} results for '{query}':\n" + "\n".join(summaries)

    async def _notion_create_page(self, tool_input: Dict) -> str:
        database_id = tool_input.get("database_id", "")
        title = tool_input.get("title", "")
        if not database_id or not title:
            return "Error: 'database_id' and 'title' are required"

        # Build page properties — title is always required
        properties = tool_input.get("properties") or {}
        # Find the title property name from the database schema
        # Default to "Name" which is the most common
        title_prop = properties.pop("_title_property", "Name")
        page_properties = {
            title_prop: {"title": [{"text": {"content": title}}]}
        }

        # Map simplified property values to Notion format
        for key, value in properties.items():
            if isinstance(value, str):
                page_properties[key] = {"rich_text": [{"text": {"content": value}}]}
            elif isinstance(value, bool):
                page_properties[key] = {"checkbox": value}
            elif isinstance(value, (int, float)):
                page_properties[key] = {"number": value}
            else:
                # Pass through as-is (caller used Notion format)
                page_properties[key] = value

        create_kwargs = {
            "parent": {"database_id": database_id},
            "properties": page_properties,
        }

        # Set page icon if provided
        icon = tool_input.get("icon")
        if icon:
            create_kwargs["icon"] = {"type": "emoji", "emoji": icon}

        # Add content as structured blocks if provided
        content = tool_input.get("content")
        if content:
            create_kwargs["children"] = _markdown_to_notion_blocks(content)

        page = await self._notion_client.pages.create(**create_kwargs)
        page_url = page.get('url', '')
        return f"Page created: '{title}' (id: {page['id']})\nURL: {page_url}"

    async def _notion_update_page(self, tool_input: Dict) -> str:
        page_id = tool_input.get("page_id", "")
        if not page_id:
            return "Error: 'page_id' is required"

        icon = tool_input.get("icon")
        properties = tool_input.get("properties")

        # Build update kwargs (icon + properties in one call if both present)
        update_kwargs = {}
        if icon:
            update_kwargs["icon"] = {"type": "emoji", "emoji": icon}
        if properties:
            page_properties = {}
            for key, value in properties.items():
                if isinstance(value, str):
                    page_properties[key] = {"rich_text": [{"text": {"content": value}}]}
                elif isinstance(value, bool):
                    page_properties[key] = {"checkbox": value}
                elif isinstance(value, (int, float)):
                    page_properties[key] = {"number": value}
                else:
                    page_properties[key] = value
            update_kwargs["properties"] = page_properties

        if update_kwargs:
            await self._notion_client.pages.update(
                page_id=page_id, **update_kwargs
            )

        # Append content as structured blocks if provided
        content = tool_input.get("content")
        if content:
            blocks = _markdown_to_notion_blocks(content)
            if blocks:
                await self._notion_client.blocks.children.append(
                    block_id=page_id, children=blocks
                )

        return f"Page updated: {page_id}"

    async def _notion_query_database(self, tool_input: Dict) -> str:
        import json as _json
        database_id = tool_input.get("database_id", "")
        if not database_id:
            return "Error: 'database_id' is required"

        kwargs = {"database_id": database_id}
        if tool_input.get("filter"):
            kwargs["filter"] = tool_input["filter"]
        if tool_input.get("sorts"):
            kwargs["sorts"] = tool_input["sorts"]

        results = await self._notion_client.databases.query(**kwargs)
        pages = results.get("results", [])

        if not pages:
            return "No pages found matching the query."

        summaries = []
        for page in pages[:20]:
            title = _extract_notion_title(page)
            page_id = page.get("id", "")
            summaries.append(f"- {title} (id: {page_id})")

        return f"Found {len(pages)} pages:\n" + "\n".join(summaries)

    # ── Notion block tools ──────────────────────────────────────────────

    async def _notion_get_blocks(self, tool_input: Dict) -> str:
        page_id = tool_input.get("page_id", "")
        if not page_id:
            return "Error: 'page_id' is required"

        try:
            result = await self._notion_client.blocks.children.list(block_id=page_id)
            blocks = result.get("results", [])
            if not blocks:
                return f"Page {page_id} has no blocks."

            lines = [f"Page {page_id} — {len(blocks)} blocks:\n"]
            for block in blocks:
                block_id = block.get("id", "")
                block_type = block.get("type", "unknown")
                text = _extract_block_text(block)
                prefix = ""
                if block_type == "to_do":
                    checked = block.get("to_do", {}).get("checked", False)
                    prefix = "[x] " if checked else "[ ] "
                elif block_type == "bulleted_list_item":
                    prefix = "- "
                elif block_type == "numbered_list_item":
                    prefix = "1. "
                elif block_type.startswith("heading_"):
                    level = block_type[-1]
                    prefix = "#" * int(level) + " "

                lines.append(f"[{block_type}] (id: {block_id}) {prefix}{text}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error getting blocks: {e}"

    async def _notion_update_blocks(self, tool_input: Dict) -> str:
        updates = tool_input.get("updates", [])
        if not updates:
            return "Error: 'updates' array is required"

        results = []
        for update in updates:
            block_id = update.get("block_id", "")
            if not block_id:
                results.append("Skipped: missing block_id")
                continue

            try:
                # Fetch current block to know its type
                current = await self._notion_client.blocks.retrieve(block_id=block_id)
                block_type = current.get("type", "")

                update_data = {}

                if "checked" in update and block_type == "to_do":
                    # Update the checked state
                    to_do = dict(current.get("to_do", {}))
                    to_do["checked"] = update["checked"]
                    update_data["to_do"] = to_do

                if "text" in update and block_type:
                    # Update text content
                    block_content = dict(current.get(block_type, {}))
                    block_content["rich_text"] = [
                        {"type": "text", "text": {"content": update["text"]}}
                    ]
                    update_data[block_type] = block_content

                if update_data:
                    await self._notion_client.blocks.update(
                        block_id=block_id, **update_data
                    )
                    results.append(f"Updated block {block_id}")
                else:
                    results.append(f"No changes for block {block_id}")
            except Exception as e:
                results.append(f"Error updating {block_id}: {e}")

        return "\n".join(results)

    async def _notion_append_blocks(self, tool_input: Dict) -> str:
        page_id = tool_input.get("page_id", "")
        content = tool_input.get("content", "")
        if not page_id or not content:
            return "Error: 'page_id' and 'content' are required"

        try:
            blocks = _markdown_to_notion_blocks(content)
            if not blocks:
                return "No blocks parsed from content."

            await self._notion_client.blocks.children.append(
                block_id=page_id, children=blocks
            )
            return f"Appended {len(blocks)} blocks to page {page_id}"
        except Exception as e:
            return f"Error appending blocks: {e}"


def _extract_block_text(block: Dict) -> str:
    """Extract plain text from a Notion block."""
    block_type = block.get("type", "")
    block_data = block.get(block_type, {})
    rich_text = block_data.get("rich_text", [])
    return "".join(t.get("plain_text", "") for t in rich_text)


def _markdown_to_notion_blocks(content: str) -> List[Dict]:
    """Parse markdown into Notion block objects."""
    import re as _re
    blocks = []
    for line in content.split('\n'):
        stripped = line.strip()
        if not stripped:
            continue

        # Headings
        heading_match = _re.match(r'^(#{1,3})\s+(.+)', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2)
            block_type = f"heading_{level}"
            blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                }
            })
            continue

        # To-do items: - [ ] or - [x]
        todo_match = _re.match(r'^-\s*\[([ xX])\]\s+(.*)', stripped)
        if todo_match:
            checked = todo_match.group(1).lower() == 'x'
            text = todo_match.group(2)
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": [{"type": "text", "text": {"content": text}}],
                    "checked": checked,
                }
            })
            continue

        # Bullet list items
        bullet_match = _re.match(r'^[-*]\s+(.*)', stripped)
        if bullet_match:
            text = bullet_match.group(1)
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                }
            })
            continue

        # Numbered list items
        num_match = _re.match(r'^\d+\.\s+(.*)', stripped)
        if num_match:
            text = num_match.group(1)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                }
            })
            continue

        # Plain paragraph
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": stripped}}]
            }
        })

    return blocks


def _extract_notion_title(item: Dict) -> str:
    """Extract a human-readable title from a Notion page/database object."""
    # Try page title properties
    props = item.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_parts = prop.get("title", [])
            if title_parts:
                return "".join(t.get("plain_text", "") for t in title_parts)

    # Try database title
    title_list = item.get("title", [])
    if isinstance(title_list, list) and title_list:
        return "".join(t.get("plain_text", "") for t in title_list)

    return "(untitled)"


# ── Activity callback type ─────────────────────────────────────────────

# on_tool_activity(tool_name, tool_input, completed, summary)
ToolActivityCallback = Callable[..., Awaitable[None]]


@dataclass
class _PageSection:
    """A parsed section from a formatted tool result."""
    start: int          # char offset in original text
    end: int            # char offset (exclusive)
    header: str         # the header line
    body: str           # body content (everything after header)
    date_str: str       # extracted date or ""
    is_db_header: bool  # True for "### === NAME DATABASE ..." lines


# Patterns matching format_context_data() output
_DB_HEADER_RE = re.compile(r'^### === .+ DATABASE \(\d+ entries\) ===$', re.MULTILINE)
_PAGE_ENTRY_RE = re.compile(
    r'^(?:\*\*[\w\-]+\*\* entry \(|'       # Standard: **db_name** entry (Date: ...
    r'\*\*`.+`\*\*)',                       # Discord: **`timestamp  author  #channel  file`**
    re.MULTILINE,
)
_DATE_RE = re.compile(r'(?:Date:\s*|^|\s)(\d{4}-\d{2}-\d{2})')


def _parse_page_sections(text: str) -> List[_PageSection]:
    """Split formatted tool result text into page sections."""
    import datetime as _dt

    # Find all section boundaries (both db headers and page entries)
    boundaries = []
    for m in _DB_HEADER_RE.finditer(text):
        boundaries.append((m.start(), True))
    for m in _PAGE_ENTRY_RE.finditer(text):
        boundaries.append((m.start(), False))

    if not boundaries:
        return []

    boundaries.sort(key=lambda x: x[0])

    sections = []
    for i, (start, is_db) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        chunk = text[start:end]

        # Split into header line and body
        newline_pos = chunk.find("\n")
        if newline_pos >= 0:
            header = chunk[:newline_pos]
            body = chunk[newline_pos + 1:]
        else:
            header = chunk
            body = ""

        # Extract date
        date_str = ""
        date_match = _DATE_RE.search(header)
        if date_match:
            date_str = date_match.group(1)
        elif not is_db and body:
            date_match = _DATE_RE.search(body[:200])
            if date_match:
                date_str = date_match.group(1)

        sections.append(_PageSection(
            start=start,
            end=end,
            header=header,
            body=body,
            date_str=date_str,
            is_db_header=is_db,
        ))

    return sections


def _smart_trim_text(text: str, max_chars: int) -> str:
    """Proportionally trim a formatted tool result, weighting by page size and recency."""
    import datetime as _dt

    sections = _parse_page_sections(text)
    if not sections:
        # Fallback: hard truncate
        return text[:max_chars] + "\n\n[... results trimmed to fit context limit ...]"

    overflow = len(text) - max_chars
    if overflow <= 0:
        return text

    today = _dt.date.today()

    # Separate trimmable pages from exempt ones
    trimmable = []
    for sec in sections:
        if sec.is_db_header:
            continue  # never trim db headers
        if len(sec.body) < 500:
            continue  # too short to trim meaningfully
        trimmable.append(sec)

    if not trimmable:
        return text[:max_chars] + "\n\n[... results trimmed to fit context limit ...]"

    total_trimmable_size = sum(len(s.body) for s in trimmable)
    if total_trimmable_size <= 0:
        return text[:max_chars] + "\n\n[... results trimmed to fit context limit ...]"

    # Calculate recency weight per page
    weights = []
    for sec in trimmable:
        age_days = 30  # default: moderate trim
        if sec.date_str:
            try:
                page_date = _dt.date.fromisoformat(sec.date_str)
                age_days = max(0, (today - page_date).days)
            except ValueError:
                pass
        # Today: 0.5x (protected), 7-day: ~1.0x, 30+: 2.0x (trim aggressively)
        weight = 0.5 + min(age_days / 15.0, 1.5)
        weights.append(weight)

    # Calculate per-page trim amounts
    raw_shares = [len(s.body) / total_trimmable_size for s in trimmable]
    weighted_shares = [rs * w for rs, w in zip(raw_shares, weights)]
    ws_sum = sum(weighted_shares) or 1.0
    normalized = [ws / ws_sum for ws in weighted_shares]

    trim_amounts = []
    for i, sec in enumerate(trimmable):
        trim = int(overflow * normalized[i])
        # Cap: retain at least 200 chars of body
        max_trim = max(0, len(sec.body) - 200)
        trim_amounts.append(min(trim, max_trim))

    # Check if we covered the overflow
    remaining = overflow - sum(trim_amounts)
    if remaining > 0:
        # Sort by body size descending, distribute remaining to largest pages
        indices = sorted(range(len(trimmable)), key=lambda i: len(trimmable[i].body), reverse=True)
        for idx in indices:
            if remaining <= 0:
                break
            max_extra = max(0, len(trimmable[idx].body) - 200) - trim_amounts[idx]
            take = min(remaining, max_extra)
            trim_amounts[idx] += take
            remaining -= take

    # Build a map: section -> trimmed body
    trim_map = {}
    for i, sec in enumerate(trimmable):
        if trim_amounts[i] > 0:
            keep = len(sec.body) - trim_amounts[i]
            trimmed_body = sec.body[:keep] + "\n[... context was trimmed to avoid context overflow ...]\n"
            trim_map[id(sec)] = trimmed_body

    # Reassemble: text before first section + sections in order
    parts = []
    prev_end = 0
    for sec in sections:
        # Any text between sections (shouldn't happen, but be safe)
        if sec.start > prev_end:
            parts.append(text[prev_end:sec.start])

        parts.append(sec.header + "\n")
        if id(sec) in trim_map:
            parts.append(trim_map[id(sec)])
        else:
            parts.append(sec.body)
        prev_end = sec.end

    # Any trailing text
    if prev_end < len(text):
        parts.append(text[prev_end:])

    result = "".join(parts)

    # Safety: if somehow still too large, hard truncate
    if len(result) > max_chars + 1000:
        result = result[:max_chars] + "\n\n[... results trimmed to fit context limit ...]"

    return result


def _extract_overflow_tokens(error_str: str) -> Optional[int]:
    """Extract overflow token count from Anthropic error message.

    Parses: 'prompt is too long: 210000 tokens > 200000 maximum'
    Returns actual - limit, or None if format doesn't match.
    """
    m = re.search(r'(\d+)\s*tokens?\s*>\s*(\d+)\s*maximum', error_str)
    if m:
        actual = int(m.group(1))
        limit = int(m.group(2))
        return actual - limit
    return None


def _trim_tool_results(messages: List[Dict], max_result_chars: int = 50_000) -> None:
    """Smart-trim large tool_result blocks using proportional recency-weighted trimming.

    Mutates messages in-place. Walks backwards to trim the most recent
    (and likely largest) results first.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                result_text = block.get("content", "")
                if isinstance(result_text, str) and len(result_text) > max_result_chars:
                    block["content"] = _smart_trim_text(result_text, max_result_chars)


# ── Planning layer ─────────────────────────────────────────────────────

# Action verbs that signal a distinct task in a user request
_ACTION_VERBS = re.compile(
    r'\b(draft|reply|respond|write|send|create|make|schedule|'
    r'find|search|look up|figure out|check|review|summarize|'
    r'update|delete|post|notify|remind|book|set up|add|cancel|'
    r'move|reschedule|forward|compose|prepare|compile|gather)\b',
    re.IGNORECASE,
)


def _is_complex_request(message: str) -> bool:
    """Check if a message likely contains multiple distinct tasks.

    Uses a fast heuristic to avoid calling the Planner AI for simple queries.
    Only multi-action requests trigger planning.
    """
    # Find distinct action verbs
    matches = _ACTION_VERBS.findall(message.lower())
    unique_actions = set(matches)
    if len(unique_actions) >= 2:
        return True
    return False


async def _generate_plan(
    user_message: str,
    agent,
    available_tools: List[str],
) -> Optional[List[str]]:
    """Use the Planner AI to decompose a complex request into steps.

    Returns a list of plan step strings, or None if the request is simple.
    """
    if not _is_complex_request(user_message):
        return None

    logger.info("[planning] Complex request detected, generating plan...")

    try:
        import os
        from anthropic import Anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        # Build a lightweight decomposition prompt (reuses Planner's style)
        agent_info = ""
        if agent:
            name = getattr(agent, 'name', 'Agent')
            workspace = getattr(agent, 'workspace', '')
            databases = getattr(agent, 'databases', [])
            mcp_tools = getattr(agent, 'mcp_tools', []) or []
            agent_info = (
                f"\nAgent: {name} (workspace: {workspace})\n"
                f"Databases: {', '.join(databases)}\n"
                f"Tools: {', '.join(available_tools)}\n"
            )

        prompt = f"""You are a task planner. Decompose this user request into distinct executable steps.

User request: {user_message}
{agent_info}
Rules:
- Each step should be a single, focused action
- Steps should be in logical execution order
- Only create steps for actions the user actually requested
- Keep step descriptions concise (one line each)
- Typical requests have 2-5 steps

Return a JSON array of step descriptions (strings only).

Example:
["Search emails for Marina/Open Editions thread", "Draft a contextual reply to Marina", "Create calendar event for tomorrow 12-4pm", "Search for dreamshare Quarterly info and compile findings"]

Return ONLY the JSON array, no other text."""

        client = Anthropic(api_key=api_key)
        response = await asyncio.to_thread(
            client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )

        if not response.content:
            return None

        response_text = response.content[0].text.strip()

        # Parse JSON array from response
        json_match = re.search(r'\[[\s\S]*\]', response_text)
        if json_match:
            steps = json.loads(json_match.group())
            if isinstance(steps, list) and len(steps) >= 2:
                logger.info(f"[planning] Generated {len(steps)} plan steps")
                return [str(s) for s in steps]

        return None

    except Exception as e:
        logger.warning(f"[planning] Plan generation failed: {e}")
        return None


def _format_plan_for_prompt(steps: List[str]) -> str:
    """Format plan steps for injection into the system prompt."""
    numbered = "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps))
    return (
        f"# Execution Plan\n\n"
        f"You identified the following tasks in the user's request:\n\n"
        f"{numbered}\n\n"
        f"Execute each step in order. Use your tools for each step. "
        f"Before starting each step, output [STEP:N] where N is the step number. "
        f"After completing all steps, summarize what you did."
    )


# ── Agentic turn ───────────────────────────────────────────────────────

async def agentic_turn(
    system_prompt: str,
    messages: List[Dict],
    tools: List[Dict],
    tool_executor: ToolExecutor,
    max_iterations: int = 40,
    on_tool_activity: Optional[ToolActivityCallback] = None,
    plan: Optional[List[str]] = None,
    context_data_block: str = "",
) -> AgenticTurnResult:
    """
    Run a self-contained agentic turn with tool use.

    Manages its own internal message history (with tool_use/tool_result blocks)
    but returns only plain text. The conversation manager never sees tool blocks.

    Args:
        system_prompt: Base system prompt (without context data)
        messages: Conversation history (plain text messages only)
        tools: Anthropic tool definitions
        tool_executor: Executes tool calls
        max_iterations: Max loop iterations (from agent config)
        on_tool_activity: Optional callback for UX activity updates
        plan: Optional list of plan steps to inject into the system prompt
        context_data_block: Loaded database pages block, mutable via compact_context tool

    Returns:
        AgenticTurnResult with plain text response and metadata
    """
    import os
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return AgenticTurnResult(
            response_text="I'm sorry, I couldn't generate a response (missing API key).",
        )

    client = Anthropic(api_key=api_key)

    # Inject plan into system prompt if provided
    if plan:
        system_prompt += "\n\n" + _format_plan_for_prompt(plan)

    # Context compact tracking — agent can compact with notes or restore
    context_notes: Optional[str] = None

    # Copy messages — tool_use/tool_result blocks stay internal only
    # Format any messages with images into Anthropic multimodal content blocks
    internal_messages = []
    for m in messages:
        msg = dict(m)
        if msg.get("images") and msg.get("role") == "user":
            from promaia.utils.image_processing import format_image_for_anthropic
            content_blocks = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for img in msg["images"]:
                content_blocks.append(format_image_for_anthropic(img["data"], img["media_type"]))
            msg["content"] = content_blocks
            del msg["images"]
        elif "images" in msg:
            del msg["images"]
        internal_messages.append(msg)
    all_tool_calls = []
    total_input_tokens = 0
    total_output_tokens = 0
    text_parts = []

    # Step progress tracking (for plan step callbacks)
    _step_marker_seen = False
    _current_step = 0  # 0 = not started yet

    for iteration in range(max_iterations):
        budget_note = (
            f"\n\n[Tool budget: {max_iterations - iteration}/{max_iterations} "
            f"iterations remaining]"
        )

        # Build effective prompt: include context block only when not muted
        effective_prompt = system_prompt
        if context_data_block and context_notes is None:
            effective_prompt = system_prompt + context_data_block
        elif context_notes is not None:
            effective_prompt = system_prompt + "\n\n## Context (compacted)\n\n" + context_notes

        # Proactive context trimming before API call
        from promaia.agents.context_trimmer import trim_context_to_fit
        trimmed_system, internal_messages = await trim_context_to_fit(
            effective_prompt + budget_note, internal_messages, tools=tools
        )

        # Build API call kwargs
        api_kwargs = dict(
            model="claude-sonnet-4-6",
            system=trimmed_system,
            messages=internal_messages,
            max_tokens=4096,
        )
        if tools:
            api_kwargs["tools"] = tools

        try:
            response = await asyncio.to_thread(
                client.messages.create,
                **api_kwargs,
            )
        except Exception as api_err:
            err_str = str(api_err)
            if "prompt is too long" in err_str or "too many tokens" in err_str.lower():
                # Context overflow — smart trim at 50k, then 25k, then give up
                overflow_tokens = _extract_overflow_tokens(err_str)
                trim_limits = [50_000, 25_000]
                response = None

                for trim_limit in trim_limits:
                    logger.info(
                        f"[agentic] Context overflow "
                        f"(overflow={overflow_tokens or '?'} tokens), "
                        f"trimming tool results to {trim_limit} chars"
                    )
                    if on_tool_activity:
                        try:
                            await on_tool_activity(
                                tool_name="__context_trim__",
                                tool_input={},
                                completed=True,
                                summary=f"Context too large, trimming to {trim_limit // 1000}k and retrying",
                            )
                        except Exception:
                            pass
                    _trim_tool_results(internal_messages, max_result_chars=trim_limit)
                    try:
                        response = await asyncio.to_thread(
                            client.messages.create,
                            **api_kwargs,
                        )
                        break  # Success
                    except Exception:
                        continue  # Try tighter trim

                if response is None:
                    # Both trim levels failed — return what we have
                    last_text = "\n".join(text_parts) if text_parts else ""
                    if not last_text:
                        last_text = (
                            "I ran into a context limit while processing your request. "
                            "Try asking a more specific question so I can search with "
                            "narrower results."
                        )
                    if plan and on_tool_activity:
                        try:
                            await on_tool_activity(
                                tool_name="__plan_done__",
                                tool_input={"total": len(plan)},
                                completed=True,
                            )
                        except Exception:
                            pass
                    return AgenticTurnResult(
                        response_text=last_text,
                        tool_calls_made=all_tool_calls,
                        iterations_used=iteration + 1,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        plan=plan,
                    )
            else:
                raise

        # Track token usage
        if hasattr(response, 'usage'):
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

        # Separate text and tool_use blocks
        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                raw_text = block.text
                # Detect step markers and fire callbacks
                step_matches = re.findall(r'\[STEP:(\d+)\]', raw_text)
                if step_matches and on_tool_activity and plan:
                    for step_num_str in step_matches:
                        step_num = int(step_num_str)
                        _step_marker_seen = True
                        _current_step = step_num
                        try:
                            await on_tool_activity(
                                tool_name="__plan_step__",
                                tool_input={"step": step_num, "total": len(plan)},
                                completed=True,
                            )
                        except Exception:
                            pass
                # Strip markers from output text
                clean_text = re.sub(r'\[STEP:\d+\]\s*', '', raw_text)
                text_parts.append(clean_text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        # If no tool calls, we're done — return the final text
        if response.stop_reason == "end_turn" or not tool_uses:
            if plan and on_tool_activity:
                try:
                    await on_tool_activity(
                        tool_name="__plan_done__",
                        tool_input={"total": len(plan)},
                        completed=True,
                    )
                except Exception:
                    pass
            return AgenticTurnResult(
                response_text="\n".join(text_parts),
                tool_calls_made=all_tool_calls,
                iterations_used=iteration + 1,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                plan=plan,
            )

        # ── Tool execution (stays internal to this function) ─────────
        internal_messages.append({
            "role": "assistant",
            "content": response.content,
        })

        tool_results = []
        for tool_use in tool_uses:
            # Notify UX: tool starting
            if on_tool_activity:
                try:
                    await on_tool_activity(
                        tool_name=tool_use.name,
                        tool_input=tool_use.input,
                        completed=False,
                    )
                except Exception as e:
                    logger.debug(f"Tool activity callback error: {e}")

            # Execute tool
            result_text = await tool_executor.execute(tool_use.name, tool_use.input)

            # Handle context compact/restore sentinels
            if result_text.startswith("__CONTEXT_COMPACT__:"):
                context_notes = result_text[len("__CONTEXT_COMPACT__:"):]
                result_text = "Context compacted with your notes. Query tools still work normally."
            elif result_text == "__CONTEXT_RESTORE__":
                context_notes = None
                result_text = "Context restored to full loaded data. Query tools still work normally."

            # Build summary for logging and UX
            summary = _summarize_tool_result(
                tool_use.name, tool_use.input, result_text
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })
            all_tool_calls.append({
                "name": tool_use.name,
                "input": tool_use.input,
                "summary": summary,
            })

            logger.info(f"[agentic] Tool {tool_use.name}: {summary}")

            # Notify UX: tool completed
            if on_tool_activity:
                try:
                    await on_tool_activity(
                        tool_name=tool_use.name,
                        tool_input=tool_use.input,
                        completed=True,
                        summary=summary,
                    )
                except Exception as e:
                    logger.debug(f"Tool activity callback error: {e}")

            # Sequential fallback: auto-advance plan steps if LLM didn't emit markers
            if not _step_marker_seen and plan and on_tool_activity:
                estimated_step = min(
                    1 + (len(all_tool_calls) * len(plan)) // max(max_iterations, 1),
                    len(plan),
                )
                if estimated_step > _current_step:
                    _current_step = estimated_step
                    try:
                        await on_tool_activity(
                            tool_name="__plan_step__",
                            tool_input={"step": _current_step, "total": len(plan)},
                            completed=True,
                        )
                    except Exception:
                        pass

        internal_messages.append({"role": "user", "content": tool_results})

    # Exhausted iteration budget — return whatever text we have
    last_text = "\n".join(text_parts) if text_parts else ""
    if not last_text:
        last_text = (
            "I've used all my available tool iterations. "
            "Here's what I found so far based on the queries I ran."
        )

    if plan and on_tool_activity:
        try:
            await on_tool_activity(
                tool_name="__plan_done__",
                tool_input={"total": len(plan)},
                completed=True,
            )
        except Exception:
            pass

    return AgenticTurnResult(
        response_text=last_text,
        tool_calls_made=all_tool_calls,
        iterations_used=max_iterations,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        plan=plan,
    )


def _summarize_tool_result(
    tool_name: str, tool_input: Dict, result: str
) -> str:
    """Create a short human-readable summary of a tool call."""
    if tool_name == "query_sql":
        query = tool_input.get("query", "")
        if "Found" in result:
            count_part = result.split("\n")[0]
            return f'Searched "{query}" ({count_part.lower()})'
        if "no results" in result.lower():
            return f'Searched "{query}" (no results)'
        return f'Searched "{query}"'

    elif tool_name == "query_vector":
        query = tool_input.get("query", "")
        if "Found" in result:
            count_part = result.split("\n")[0]
            return f'Semantic search "{query}" ({count_part.lower()})'
        if "no results" in result.lower():
            return f'Semantic search "{query}" (no results)'
        return f'Semantic search "{query}"'

    elif tool_name == "query_source":
        db = tool_input.get("database", "")
        days = tool_input.get("days", "all")
        if "Loaded" in result:
            count_part = result.split("\n")[0]
            return f"Loaded {db}:{days} ({count_part.lower()})"
        return f"Loaded {db}:{days}"

    elif tool_name == "write_journal":
        return "Wrote journal entry"

    elif tool_name == "send_email":
        to = tool_input.get("to", "")
        subj = tool_input.get("subject", "")
        return f'Sent email to {to}: "{subj}"'

    elif tool_name == "create_email_draft":
        subj = tool_input.get("subject", "")
        return f'Created draft: "{subj}"'

    elif tool_name == "reply_to_email":
        return f"Replied to thread {tool_input.get('thread_id', '')[:12]}"

    elif tool_name == "schedule_self":
        return f"Self-scheduled: {tool_input.get('summary', '')} at {tool_input.get('start_time', '')}"

    elif tool_name == "schedule_agent_event":
        agent = tool_input.get('agent', '')
        agent_label = f" ({agent})" if agent else ""
        return f"Agent-scheduled{agent_label}: {tool_input.get('summary', '')} at {tool_input.get('start_time', '')}"

    elif tool_name == "create_calendar_event":
        return f"Created event: {tool_input.get('summary', '')}"

    elif tool_name == "update_calendar_event":
        return f"Updated event {tool_input.get('event_id', '')[:12]}"

    elif tool_name == "delete_calendar_event":
        return f"Deleted event {tool_input.get('event_id', '')[:12]}"

    elif tool_name == "send_message":
        target = tool_input.get("channel_id", "")
        return f"Sent message to {target}"

    elif tool_name == "notion_search":
        if "Error:" in result:
            return f"Error searching Notion for '{tool_input.get('query', '')}'"
        return f"Searched Notion for '{tool_input.get('query', '')}'"

    elif tool_name == "notion_create_page":
        if "Error:" in result:
            return f"Error creating Notion page (error)"
        return f"Created Notion page: {tool_input.get('title', '')}"

    elif tool_name == "notion_update_page":
        if "Error:" in result:
            return f"Error updating Notion page {tool_input.get('page_id', '')[:12]} (error)"
        return f"Updated Notion page {tool_input.get('page_id', '')[:12]}"

    elif tool_name == "notion_query_database":
        if "Error:" in result:
            return f"Notion database {tool_input.get('database_id', '')[:12]} (error)"
        return f"Queried Notion database {tool_input.get('database_id', '')[:12]}"

    elif tool_name == "web_search":
        query = tool_input.get("query", "")
        if "Error:" in result:
            return f'Web search "{query}" (error)'
        return f'Web search "{query}"'

    elif tool_name == "web_fetch":
        url = tool_input.get("url", "")
        display_url = url[:60] + "..." if len(url) > 60 else url
        if "Error:" in result:
            return f"Fetch {display_url} (error)"
        char_count = len(result)
        return f"Fetched {display_url} ({char_count:,} chars)"

    elif tool_name == "task_queue_add":
        task = tool_input.get("task", "")
        return f'Queued task: "{task}"'

    else:
        if result.startswith("Error") or result.startswith("Error:"):
            return f"{tool_name} (error)"
        return f"{tool_name} completed"
