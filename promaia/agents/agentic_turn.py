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
    """Result of an agentic turn."""
    response_text: str
    tool_calls_made: List[Dict[str, Any]] = field(default_factory=list)
    iterations_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    plan: Optional[List[str]] = None
    signal: Optional[Dict[str, Any]] = None
    # Full tool_use/tool_result message blocks from the agentic loop.
    # These can be appended to conversation history so future turns
    # have access to prior tool calls and results.
    history_messages: List[Dict[str, Any]] = field(default_factory=list)
    # Persistent notepad content (survives across turns)
    notepad_content: Optional[str] = None
    # Context source states (on/off, survives across turns)
    source_states: Optional[Dict[str, Dict]] = None  # kept as source_states for interface.py compat


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
            "Available databases include: agent_journal, gmail, stories, tasks, calendar, "
            "and any Discord/Slack channel sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database": {
                    "type": "string",
                    "description": (
                        "Database name (e.g., 'agent_journal', 'gmail', 'stories', "
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
        "name": "write_agent_journal",
        "description": (
            "Write a note to your agent journal — your private notebook for tracking insights, "
            "learnings, and information across runs. This is YOUR agent journal, not the user's "
            "personal journal database."
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
                    "description": "CC recipients (comma-separated, optional)"
                },
                "bcc": {
                    "type": "string",
                    "description": "BCC recipients (comma-separated, optional)"
                },
                "attachment_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of workspace file paths to attach "
                        "(from drive_download_file or list_workspace_files)"
                    )
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "create_email_draft",
        "description": "Create an email draft (not sent). Supports file attachments from the workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body"},
                "cc": {"type": "string", "description": "CC recipients (optional)"},
                "attachment_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of workspace file paths to attach "
                        "(from drive_download_file or list_workspace_files)"
                    )
                }
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "reply_to_email",
        "description": (
            "Reply to an email thread. Supports file attachments from the workspace. "
            "Use query_sql to find the thread_id and message_id first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Gmail thread ID"},
                "message_id": {"type": "string", "description": "Original message ID"},
                "body": {"type": "string", "description": "Reply body text"},
                "attachment_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of workspace file paths to attach "
                        "(from drive_download_file or list_workspace_files)"
                    )
                }
            },
            "required": ["thread_id", "message_id", "body"]
        }
    },
    {
        "name": "draft_reply_to_email",
        "description": (
            "Create a draft reply to an email thread (NOT sent). "
            "The draft appears in Gmail Drafts, threaded in the original conversation. "
            "Use query_sql to find the thread_id and message_id first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string", "description": "Gmail thread ID"},
                "message_id": {"type": "string", "description": "Original message ID"},
                "body": {"type": "string", "description": "Reply body text"},
                "attachment_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of workspace file paths to attach "
                        "(from drive_download_file or list_workspace_files)"
                    )
                }
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
    {
        "name": "mark_email_read",
        "description": "Mark one or more emails as read.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gmail message IDs to mark as read"
                }
            },
            "required": ["message_ids"]
        }
    },
    {
        "name": "mark_email_unread",
        "description": "Mark one or more emails as unread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gmail message IDs to mark as unread"
                }
            },
            "required": ["message_ids"]
        }
    },
    {
        "name": "label_email",
        "description": "Add or remove labels on emails. Use list_labels to see available labels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gmail message IDs"
                },
                "add_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Label names or IDs to add"
                },
                "remove_labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Label names or IDs to remove"
                }
            },
            "required": ["message_ids"]
        }
    },
    {
        "name": "list_labels",
        "description": "List all Gmail labels (inbox, sent, custom labels, etc.)",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "archive_email",
        "description": "Archive emails (remove from inbox but keep in All Mail).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gmail message IDs to archive"
                }
            },
            "required": ["message_ids"]
        }
    },
    {
        "name": "trash_email",
        "description": "Move emails to trash.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Gmail message IDs to trash"
                }
            },
            "required": ["message_ids"]
        }
    },
    {
        "name": "forward_email",
        "description": "Forward an email to another recipient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "Gmail message ID to forward"},
                "to": {"type": "string", "description": "Recipient email address"},
                "body": {"type": "string", "description": "Optional message to prepend (default: empty)"}
            },
            "required": ["message_id", "to"]
        }
    },
    {
        "name": "delete_draft",
        "description": "Delete an email draft by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "Gmail draft ID"}
            },
            "required": ["draft_id"]
        }
    },
    {
        "name": "gmail_download_attachment",
        "description": (
            "Download an email attachment to the workspace. "
            "Use the attachment_id and message_id from search_emails results. "
            "Returns the workspace path for use with drive_upload_file, "
            "slack_upload_file, send_email attachment_paths, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID containing the attachment."
                },
                "attachment_id": {
                    "type": "string",
                    "description": "Gmail attachment ID from the attachment metadata."
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Filename to save as in workspace "
                        "(use the filename from attachment metadata)."
                    )
                },
            },
            "required": ["message_id", "attachment_id", "filename"]
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
    {
        "name": "end_conversation",
        "description": (
            "End the current conversation gracefully. Use when the user says goodbye, "
            "thanks you, or the conversation has naturally concluded. "
            "You MUST provide a summary of what was discussed — this is saved for future reference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "emoji": {
                    "type": "string",
                    "description": "Optional emoji shortcode to react with (e.g. 'wave', 'thumbsup')"
                },
                "summary": {
                    "type": "string",
                    "description": "1-2 sentence summary of what was discussed in this conversation"
                }
            },
            "required": ["summary"]
        }
    },
    {
        "name": "leave_conversation",
        "description": (
            "Leave a conversation you're no longer needed in. Use when the user "
            "explicitly asks you to leave, or when humans are talking to each other "
            "and don't need your input."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Optional farewell message (e.g. 'See you later!')"
                }
            },
            "required": []
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

CALENDAR_MANAGEMENT_TOOL_DEFINITIONS = [
    {
        "name": "list_calendars",
        "description": "List all calendars the user has access to (owned, subscribed, agent calendars).",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "create_calendar",
        "description": "Create a new calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Calendar name"},
                "description": {"type": "string", "description": "Calendar description (optional)"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_calendar",
        "description": "Delete a calendar by ID. Cannot delete the user's primary calendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string", "description": "Calendar ID to delete"}
            },
            "required": ["calendar_id"]
        }
    },
]

WEB_SEARCH_TOOL_DEFINITIONS = [{
    "type": "web_search_20250305",
    "name": "web_search",
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
            "to-do items (- [ ], - [x]), paragraphs. "
            "Use 'after' to insert after a specific block instead of at the end."
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
                },
                "after": {
                    "type": "string",
                    "description": "Block ID to insert after (omit to append at end)"
                }
            },
            "required": ["page_id", "content"]
        }
    },
    {
        "name": "notion_delete_blocks",
        "description": (
            "Delete (archive) one or more blocks from a Notion page. "
            "Use this to remove blocks, or as part of a delete-and-recreate "
            "flow to change a block's type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of block IDs to delete"
                }
            },
            "required": ["block_ids"]
        }
    },
    {
        "name": "notion_get_page",
        "description": (
            "Retrieve a single Notion page's properties by ID. "
            "Returns all property values (title, status, dates, relations, etc.). "
            "Use notion_get_blocks to read the page's content/body."
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
        "name": "notion_get_database_schema",
        "description": (
            "Get a database's schema — all property names, types, and options. "
            "Use this to understand what fields exist before creating or updating pages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "Notion database ID"
                }
            },
            "required": ["database_id"]
        }
    },
    {
        "name": "notion_add_comment",
        "description": (
            "Add a discussion comment to a Notion page or specific block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID to comment on"
                },
                "text": {
                    "type": "string",
                    "description": "Comment text"
                },
                "block_id": {
                    "type": "string",
                    "description": "Optional: specific block ID to comment on (inline discussion)"
                }
            },
            "required": ["page_id", "text"]
        }
    },
    {
        "name": "notion_get_page_property",
        "description": (
            "Retrieve a specific property value from a Notion page. "
            "Useful for rollups, relations, and formulas that may not be "
            "returned inline by notion_get_page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Notion page ID"
                },
                "property_id": {
                    "type": "string",
                    "description": "Property ID (from the page's properties object)"
                }
            },
            "required": ["page_id", "property_id"]
        }
    },
]


GOOGLE_SHEETS_TOOL_DEFINITIONS = [
    {
        "name": "sheets_read_range",
        "description": (
            "Read a specific cell range from a Google Sheet. Returns both "
            "formulas and display values in inline format: {=FORMULA} value. "
            "Results are auto-saved as a toggleable context source — use the "
            "context tool to turn them on/off. You can load ranges larger than "
            "the default sheet preview if needed."
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
            "sheets). Large sheets are previewed (~first 10k tokens) — use "
            "sheets_read_range to access specific ranges beyond the preview. "
            "The data is cached locally so subsequent sheets tools can "
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

NOTEPAD_TOOL_DEFINITION = {
    "name": "notepad",
    "description": (
        "Your persistent reference notes — facts, context, preferences, and extracted data. "
        "Always visible in your prompt under 'Working Notes'. Notes survive across turns. "
        "NOT for task plans — use act(instructions=[...]) to pass step-by-step instructions to Act mode."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "append", "clear"],
                "description": (
                    "write: replace all notes with new content. "
                    "append: add to existing notes. "
                    "clear: erase all notes."
                )
            },
            "content": {
                "type": "string",
                "description": "Note content (required for write/append)"
            },
        },
        "required": ["action"]
    }
}

MEMORY_TOOL_DEFINITION = {
    "name": "memory",
    "description": (
        "Persistent memory across conversations. Save what you learn about the user, "
        "their preferences, corrections, and projects. Unlike notepad (this conversation "
        "only), memories persist forever across all sessions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "recall", "list", "delete"],
                "description": (
                    "save: create or update a memory. "
                    "recall: load full content of a memory by name. "
                    "list: show all memory entries. "
                    "delete: remove a memory."
                ),
            },
            "name": {
                "type": "string",
                "description": "Memory name (for save/recall/delete). Use descriptive names like 'user_communication_style' or 'project_mitchell_equity'.",
            },
            "content": {
                "type": "string",
                "description": "Memory content (for save action).",
            },
            "type": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
                "description": (
                    "user: who the user is, preferences, role. "
                    "feedback: corrections and confirmed approaches. "
                    "project: ongoing work, goals, decisions. "
                    "reference: where to find things in external systems."
                ),
            },
        },
        "required": ["action"],
    },
}

CONTEXT_TOOL_DEFINITION = {
    "name": "context",
    "description": (
        "Manage your loaded context. Each context source (query results, browser data) "
        "can be toggled ON (visible in your prompt) or OFF (hidden but stored). "
        "Your context index is always visible showing all sources and their state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["on", "off", "all_on", "all_off", "add", "remove"],
                "description": (
                    "on: turn specific sources on (content visible). "
                    "off: turn specific sources off (content hidden). "
                    "all_on: turn ALL sources on. "
                    "all_off: turn ALL sources off. "
                    "add: create a new context source with content. "
                    "remove: delete a context source entirely."
                )
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Source names to toggle (for on/off/remove actions)"
            },
            "name": {
                "type": "string",
                "description": "Name for a new context source (for add action)"
            },
            "content": {
                "type": "string",
                "description": "Content for a new context source (for add action)"
            },
        },
        "required": ["action"]
    }
}

ACT_TOOL_DEFINITION = {
    "name": "act",
    "description": (
        "Enter Act mode to execute actions. You MUST provide step-by-step instructions "
        "for what to do. Instructions stay visible and you mark each step done as you go. "
        "Your notes come with you but context sources are hidden. Call done() when finished."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "suites": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool suites to load (e.g. ['notion', 'google']). Check suite index for available suites."
            },
            "instructions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Step-by-step instructions for what to do in Act mode. Each string is one step."
            }
        },
        "required": ["suites", "instructions"]
    }
}

MARK_STEP_DONE_TOOL_DEFINITION = {
    "name": "mark_step_done",
    "description": (
        "Mark an instruction step as completed. Call this after finishing each step. "
        "Steps are 1-indexed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "step": {
                "type": "integer",
                "description": "Step number to mark as done (1-indexed)"
            }
        },
        "required": ["step"]
    }
}

DONE_TOOL_DEFINITION = {
    "name": "done",
    "description": "Exit Act mode and return to Think mode. Context and search tools become available again.",
    "input_schema": {"type": "object", "properties": {}, "required": []}
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


# ── Google Drive tools ──────────────────────────────────────────────────

GOOGLE_DRIVE_TOOL_DEFINITIONS = [
    {
        "name": "drive_search_files",
        "description": (
            "Search Google Drive for files by name or query. "
            "Pass a plain filename to search by name, or use Drive query syntax "
            "(e.g. \"mimeType='application/pdf'\" or \"'FOLDER_ID' in parents\"). "
            "Returns file IDs, names, types, sizes, and modification dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query. Plain text searches by name. "
                        "For advanced queries use Drive syntax: "
                        "\"name contains 'invoice'\", \"mimeType='application/pdf'\", "
                        "\"'FOLDER_ID' in parents\"."
                    )
                },
                "folder_id": {
                    "type": "string",
                    "description": (
                        "Restrict search to a specific folder by ID. "
                        "If provided, adds \"'folder_id' in parents\" to the query."
                    )
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 10)"
                },
            },
            "required": ["query"]
        }
    },
    {
        "name": "drive_download_file",
        "description": (
            "Download a file from Google Drive to the local workspace. "
            "Use the file ID from drive_search_files. Google-native files "
            "(Docs, Sheets, Slides) are exported to the specified format. "
            "Returns the local workspace path for use with attachment_paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Google Drive file ID from drive_search_files"
                },
                "filename": {
                    "type": "string",
                    "description": "Override the filename in the workspace (optional, defaults to Drive filename)"
                },
                "export_format": {
                    "type": "string",
                    "description": (
                        "Export format for Google-native files: pdf, docx, xlsx, csv, pptx, txt. "
                        "Ignored for non-native files. Default: pdf."
                    )
                },
            },
            "required": ["file_id"]
        }
    },
    {
        "name": "drive_list_folder",
        "description": (
            "List all files and subfolders in a Google Drive folder. "
            "Use to browse Drive directory structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_id": {
                    "type": "string",
                    "description": "Google Drive folder ID. Use 'root' for the top-level Drive."
                },
            },
            "required": ["folder_id"]
        }
    },
    {
        "name": "drive_upload_file",
        "description": (
            "Upload a workspace file to Google Drive. Preserves original format "
            "by default. Set convert_to_google_format=true to convert "
            "(e.g., .docx -> Google Doc, .xlsx -> Google Sheet). "
            "Specify a folder_path like 'Clients/Acme/Quotes' to auto-create "
            "the folder hierarchy, or use folder_id for an existing folder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Workspace file to upload "
                        "(from drive_download_file, slack_download_file, gmail_download_attachment, etc.)"
                    )
                },
                "folder_id": {
                    "type": "string",
                    "description": (
                        "Target Google Drive folder ID. "
                        "Omit if using folder_path or uploading to root."
                    )
                },
                "folder_path": {
                    "type": "string",
                    "description": (
                        "Target folder path like 'Clients/Acme/Quotes'. "
                        "Intermediate folders are created automatically. "
                        "Ignored if folder_id is provided."
                    )
                },
                "drive_filename": {
                    "type": "string",
                    "description": "Override the filename in Drive (optional, defaults to workspace filename)"
                },
                "convert_to_google_format": {
                    "type": "boolean",
                    "description": (
                        "Convert to Google-native format on upload "
                        "(e.g. .docx -> Google Doc, .xlsx -> Google Sheet). Default: false."
                    )
                },
            },
            "required": ["filename"]
        }
    },
    {
        "name": "drive_create_folder",
        "description": (
            "Create a folder in Google Drive. Supports nested paths like "
            "'Clients/Acme/Quotes' — all intermediate folders are created "
            "automatically. Returns the folder ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": (
                        "Folder name or nested path "
                        "(e.g. 'Reports' or 'Clients/Acme/Quotes')."
                    )
                },
                "parent_id": {
                    "type": "string",
                    "description": "Parent folder ID. Defaults to root Drive."
                },
            },
            "required": ["folder_name"]
        }
    },
    {
        "name": "drive_manage_permissions",
        "description": (
            "Manage sharing permissions on a Google Drive file or folder. "
            "Can share with specific users (reader/writer/commenter), "
            "create anyone-with-link sharing, remove permissions, "
            "or list current permissions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Google Drive file or folder ID."
                },
                "action": {
                    "type": "string",
                    "enum": ["share", "unshare", "list"],
                    "description": (
                        "Action to perform: 'share' to grant access, "
                        "'unshare' to remove access, 'list' to show current permissions."
                    )
                },
                "email": {
                    "type": "string",
                    "description": "Email address to share with. Required for action=share with type=user."
                },
                "role": {
                    "type": "string",
                    "enum": ["reader", "writer", "commenter"],
                    "description": "Permission role. Default: reader."
                },
                "type": {
                    "type": "string",
                    "enum": ["user", "anyone"],
                    "description": (
                        "Permission type. 'user' for specific email, "
                        "'anyone' for link sharing. Default: user."
                    )
                },
                "permission_id": {
                    "type": "string",
                    "description": "Permission ID to remove. Required for action=unshare."
                },
                "send_notification": {
                    "type": "boolean",
                    "description": "Send email notification when sharing. Default: true."
                },
            },
            "required": ["file_id", "action"]
        }
    },
]


# ── Config tools (always available) ─────────────────────────────────────

SLACK_FILE_TOOL_DEFINITIONS = [
    {
        "name": "slack_upload_file",
        "description": (
            "Upload a workspace file to a Slack channel or thread. "
            "The file must already exist in the workspace "
            "(from drive_download_file, gmail_download_attachment, etc.). "
            "Optionally include a message alongside the file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Workspace file to upload."
                },
                "channel_id": {
                    "type": "string",
                    "description": (
                        "Slack channel ID to upload to. "
                        "Use 'current' for the current channel."
                    )
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Thread timestamp to upload into (optional)."
                },
                "title": {
                    "type": "string",
                    "description": "File title in Slack (optional, defaults to filename)."
                },
                "initial_comment": {
                    "type": "string",
                    "description": "Message to post alongside the file (optional)."
                },
            },
            "required": ["filename", "channel_id"]
        }
    },
    {
        "name": "slack_download_file",
        "description": (
            "Download a file shared in Slack to the workspace. "
            "Use the file URL from a Slack message's file metadata. "
            "Returns the workspace path for use with drive_upload_file, "
            "send_email attachment_paths, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_url": {
                    "type": "string",
                    "description": (
                        "Slack file URL (url_private from the file object)."
                    )
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Override filename in workspace "
                        "(optional, auto-detected from URL)."
                    )
                },
            },
            "required": ["file_url"]
        }
    },
]


CONFIG_TOOL_DEFINITIONS = [
    {
        "name": "list_source_types",
        "description": (
            "List available data source types that can be added to Promaia. "
            "Returns each type with its description and what identifier is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "list_workspaces",
        "description": (
            "List all configured workspaces and which is the default. "
            "Use to help the user pick a workspace when adding databases or configuring sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "add_workspace",
        "description": "Create a new workspace.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Workspace name (lowercase, no spaces)"
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of the workspace"
                },
            },
            "required": ["name"]
        }
    },
    {
        "name": "discover_source_name",
        "description": (
            "Fetch the human-readable name of a data source from its API. "
            "For example, fetches the Notion database title, Discord server name, etc. "
            "Requires valid credentials for the source type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "description": "Source type (notion, discord, gmail, slack, shopify, google_sheets)"
                },
                "database_id": {
                    "type": "string",
                    "description": "Source identifier (Notion DB ID, Discord server ID, email address, etc.)"
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name to use for credential lookup"
                },
            },
            "required": ["source_type", "database_id", "workspace"]
        }
    },
    {
        "name": "check_credential",
        "description": (
            "Check whether credentials exist for a given integration and workspace. "
            "Returns whether the credential is configured and what to do if not."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "integration": {
                    "type": "string",
                    "description": "Integration name: notion, google, discord, slack, shopify, anthropic, openai"
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace name (some credentials are workspace-scoped)"
                },
            },
            "required": ["integration"]
        }
    },
    {
        "name": "register_database",
        "description": (
            "Register a new data source in Promaia's configuration. "
            "Creates the database entry in promaia.config.json. "
            "The user should confirm the details before you call this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Database nickname (e.g., 'journal', 'team-updates')"
                },
                "source_type": {
                    "type": "string",
                    "description": "Source type (notion, discord, gmail, slack, shopify, google_sheets)"
                },
                "database_id": {
                    "type": "string",
                    "description": "Source identifier (Notion DB ID, Discord server ID, email address, etc.)"
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace to add this database to"
                },
                "description": {
                    "type": "string",
                    "description": "Optional description of what this source contains"
                },
            },
            "required": ["name", "source_type", "database_id", "workspace"]
        }
    },
    {
        "name": "test_connection",
        "description": (
            "Test the connection to a registered database source. "
            "Verifies that credentials work and the source is reachable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Database nickname"
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace the database belongs to"
                },
            },
            "required": ["name", "workspace"]
        }
    },
]


SHOW_SELECTION_TOOL_DEFINITION = {
    "name": "show_selection",
    "description": (
        "Render an interactive selection menu for the user in the terminal. "
        "Use for lists with 4+ items or when multi-select is needed. "
        "For 2-3 simple options, just ask in text instead. "
        "This tool pauses the conversation — the user's selection will "
        "appear in the next message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Menu title displayed at the top"
            },
            "items": {
                "type": "array",
                "description": "Items to select from",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique identifier for this item"},
                        "label": {"type": "string", "description": "Display text"},
                        "description": {"type": "string", "description": "Optional description shown next to label"},
                        "group": {"type": "string", "description": "Optional group header for categorization"},
                    },
                    "required": ["id", "label"]
                }
            },
            "multi_select": {
                "type": "boolean",
                "description": "Allow multiple selections (default: false)",
                "default": False
            },
            "pre_selected": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of item IDs to pre-select (show as already checked)"
            },
        },
        "required": ["title", "items"]
    }
}

SYNC_DATABASE_TOOL_DEFINITION = {
    "name": "sync_database",
    "description": (
        "Sync a database source to pull latest data from the remote service. "
        "Use after registering a new database or when the user wants fresh data. "
        "Equivalent to running `maia database sync <name>`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Database nickname to sync (or 'all' to sync everything)"
            },
            "workspace": {
                "type": "string",
                "description": "Workspace the database belongs to"
            },
            "days": {
                "type": "integer",
                "description": "Number of days of data to sync (default: 7)",
                "default": 7
            },
        },
        "required": ["name"]
    }
}

LIST_DATABASES_TOOL_DEFINITION = {
    "name": "list_databases",
    "description": (
        "List all configured database sources with their sync status. "
        "Equivalent to `maia database list`."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": "Filter by workspace (optional)"
            },
        },
        "required": []
    }
}

RENAME_DATABASE_TOOL_DEFINITION = {
    "name": "rename_database",
    "description": (
        "Rename a database source (change its nickname). "
        "Preserves all configuration, channels, and sync state."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "old_name": {"type": "string", "description": "Current database nickname"},
            "new_name": {"type": "string", "description": "New nickname"},
            "workspace": {"type": "string", "description": "Workspace name"},
        },
        "required": ["old_name", "new_name"]
    }
}

WORKSPACE_FILES_TOOL_DEFINITION = {
    "name": "list_workspace_files",
    "description": (
        "List files in the local workspace (sandbox). Files here may have been "
        "downloaded from Google Drive, generated by MCP tools, or written by the agent. "
        "These files can be attached to emails via attachment_paths."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "name": "create_workflow",
        "description": (
            "Save a repeatable workflow. Provide steps generalized from a task "
            "the user performed, or from a description of what the workflow should do. "
            "Show the workflow definition to the user as an artifact before calling this. "
            "Always confirm with the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short workflow name (e.g., 'glacier-part-reorder')"
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Short topic description (under 50 chars). Say WHAT it's about, "
                        "not HOW it works. E.g., 'Glacier part spec email' or "
                        "'Morning/evening routine walkthrough'. The steps contain the details."
                    ),
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered list of workflow steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "What this step does"},
                            "tool": {"type": "string", "description": "Tool to call (optional — some steps are manual)"},
                            "params_template": {"type": "object", "description": "Fixed/default parameters for the tool"},
                            "variable_params": {
                                "type": "array", "items": {"type": "string"},
                                "description": "Parameter names that change per run (filled from context)"
                            },
                            "notes": {"type": "string", "description": "Guidance for the agent on handling this step"}
                        },
                        "required": ["description"]
                    }
                },
                "workspace": {
                    "type": "string",
                    "description": "Workspace scope (omit for global)"
                },
                "example_run": {
                    "type": "object",
                    "description": "Optional example run from the task just performed",
                    "properties": {
                        "tool_calls": {
                            "type": "array",
                            "description": "Tool calls made during the run: [{tool, params, result_summary}]"
                        },
                        "outcome": {
                            "type": "string",
                            "description": "Run outcome: success, partial, or failed"
                        },
                        "notes": {
                            "type": "string",
                            "description": "Observations about this run"
                        }
                    }
                }
            },
            "required": ["name", "description", "steps"]
        }
    },
    {
        "name": "list_saved_workflows",
        "description": "List all saved workflows with their names and descriptions.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "get_workflow_details",
        "description": (
            "Load a saved workflow's full definition including steps and example runs. "
            "Use this before executing a workflow to get the complete instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "update_workflow",
        "description": (
            "Update an existing workflow. Can change description, steps, "
            "or add a new example run from a just-completed execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name to update"},
                "description": {"type": "string", "description": "New description (optional)"},
                "steps": {
                    "type": "array",
                    "description": "New steps array (optional — replaces all steps)"
                },
                "add_example_run": {
                    "type": "object",
                    "description": "New example run to append",
                    "properties": {
                        "tool_calls": {"type": "array"},
                        "outcome": {"type": "string"},
                        "notes": {"type": "string"}
                    }
                }
            },
            "required": ["name"]
        }
    },
    {
        "name": "delete_workflow",
        "description": (
            "Delete a saved workflow and all its example runs. "
            "This is destructive — always confirm with the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Workflow name to delete"}
            },
            "required": ["name"]
        }
    },
]

AGENT_TOOL_DEFINITIONS = [
    {
        "name": "list_agents",
        "description": "List all scheduled agents with their status, schedule, and databases.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "agent_info",
        "description": "Show detailed information about a specific scheduled agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "enable_agent",
        "description": "Enable a scheduled agent so it runs on its schedule.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "disable_agent",
        "description": "Disable a scheduled agent (stops it from running on schedule).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "rename_agent",
        "description": "Rename a scheduled agent. Updates the agent's name and agent_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "old_name": {"type": "string", "description": "Current agent name"},
                "new_name": {"type": "string", "description": "New agent name"},
            },
            "required": ["old_name", "new_name"]
        }
    },
    {
        "name": "update_agent",
        "description": (
            "Update fields on a scheduled agent. Only provided fields are changed. "
            "Use for editing description, databases, mcp_tools, interval, max_iterations, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name"},
                "description": {"type": "string", "description": "New description"},
                "databases": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Database sources the agent can access (e.g. ['journal', 'gmail', 'slack'])"
                },
                "mcp_tools": {
                    "type": "array", "items": {"type": "string"},
                    "description": "MCP tools to enable (e.g. ['gmail', 'calendar', 'notion'])"
                },
                "interval_minutes": {"type": "integer", "description": "Run interval in minutes"},
                "max_iterations": {"type": "integer", "description": "Max query iterations per run"},
                "prompt": {"type": "string", "description": "New system prompt text"},
                "messaging_enabled": {
                    "type": "boolean",
                    "description": (
                        "Whether this agent can use messaging tools "
                        "(send_message, start_conversation, etc.)."
                    )
                },
                "allowed_channel_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Slack/Discord channel IDs this agent can respond in "
                        "and query messages from. Pass empty array to remove "
                        "restrictions (allow all channels). Omit to leave unchanged."
                    )
                },
            },
            "required": ["name"]
        }
    },
    {
        "name": "remove_agent",
        "description": (
            "Remove a scheduled agent and clean up its resources. "
            "This is destructive — always confirm with the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name to remove"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "run_agent",
        "description": "Trigger an immediate run of a scheduled agent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent name to run"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "create_agent",
        "description": (
            "Create a new scheduled agent. Provide a name and optionally "
            "databases, mcp_tools, description, prompt, schedule, and messaging config. "
            "Use allowed_channel_ids to restrict which Slack/Discord channels "
            "the agent can respond in and read from — get IDs from list_channels first. "
            "Workspace defaults to current. Always confirm with the user first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent display name"},
                "workspace": {
                    "type": "string",
                    "description": "Workspace (defaults to current)"
                },
                "databases": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Database sources the agent can access (e.g. ['journal', 'gmail', 'slack'])"
                },
                "mcp_tools": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Tools to enable (e.g. ['gmail', 'calendar', 'notion'])"
                },
                "prompt": {
                    "type": "string",
                    "description": "System prompt / instructions for the agent"
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this agent does"
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Run interval in minutes (e.g. 60 for hourly)"
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Max iterations per run (default 40)"
                },
                "messaging_enabled": {
                    "type": "boolean",
                    "description": "Whether this agent can use messaging tools"
                },
                "allowed_channel_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Slack/Discord channel IDs (e.g. ['C0ABC123']) this agent "
                        "can respond in and query messages from. Get IDs from "
                        "list_channels. Omit for unrestricted access."
                    )
                },
            },
            "required": ["name"]
        }
    },
]

CHANNEL_TOOL_DEFINITIONS = [
    {
        "name": "list_channels",
        "description": (
            "Fetch all available channels from a Discord or Slack source. "
            "Returns channel IDs and names that the bot has access to. "
            "Use this before show_selection to let the user pick channels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Database nickname"},
                "workspace": {"type": "string", "description": "Workspace name"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "get_configured_channels",
        "description": (
            "Show which channels are currently configured (selected) for a "
            "Discord or Slack database source."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Database nickname"},
                "workspace": {"type": "string", "description": "Workspace name"},
            },
            "required": ["name"]
        }
    },
    {
        "name": "update_channels",
        "description": (
            "Update the channel selection for a Discord or Slack database source. "
            "Replaces the current channel list with the provided one. "
            "Always confirm with the user before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Database nickname"},
                "workspace": {"type": "string", "description": "Workspace name"},
                "channel_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of channel IDs to configure"
                },
                "channel_names": {
                    "type": "object",
                    "description": "Mapping of channel ID to channel name",
                    "additionalProperties": {"type": "string"}
                },
            },
            "required": ["name", "channel_ids", "channel_names"]
        }
    },
]

def _build_interview_tool_definitions():
    """Build interview tool definitions with dynamic workflow enum."""
    try:
        from promaia.chat.workflows import list_workflows
        workflows = list_workflows()
        workflow_names = [wf["name"] for wf in workflows]
    except Exception:
        workflow_names = []
    if not workflow_names:
        workflow_names = ["database_add", "edit_channels"]

    return [
        {
            "name": "start_interview",
            "description": (
                "Start a guided interview workflow to walk the user through a "
                "configuration process. Use when the user wants to set up or "
                "configure something (add a database, create an agent, edit "
                "an agent, etc.). This activates a specialized mode with "
                "step-by-step guidance."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "workflow": {
                        "type": "string",
                        "description": "The workflow to start",
                        "enum": workflow_names,
                    },
                },
                "required": ["workflow"]
            }
        },
        {
            "name": "complete_interview",
            "description": (
                "Signal that the current interview workflow is complete. "
                "Call this when all required steps have been finished and "
                "the user's configuration task is done."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": []
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
        tools.extend(CALENDAR_MANAGEMENT_TOOL_DEFINITIONS)
        if getattr(agent, 'calendar_id', None):
            tools.append(SCHEDULE_SELF_TOOL_DEFINITION)
        # Always include schedule_agent_event — calendar may be created mid-session
        # by create_agent. The executor returns a clear error if no calendars exist.
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
    if "google_drive" in mcp_tools or "google_sheets" in mcp_tools:
        tools.extend(GOOGLE_DRIVE_TOOL_DEFINITIONS)

    if has_platform:
        tools.extend(SLACK_FILE_TOOL_DEFINITIONS)

    # Task queue is always available
    tools.extend(TASK_QUEUE_TOOL_DEFINITIONS)

    # Notepad — always available (persistent notes across turns)
    tools.append(NOTEPAD_TOOL_DEFINITION)

    # Context — always available (context source management)
    tools.append(CONTEXT_TOOL_DEFINITION)

    # Config tools — always available
    tools.extend(CONFIG_TOOL_DEFINITIONS)

    # Agent management tools — always available
    tools.extend(AGENT_TOOL_DEFINITIONS)

    # Channel tools — always available
    tools.extend(CHANNEL_TOOL_DEFINITIONS)

    # Interview tools — disabled outside onboarding flow
    # tools.extend(_build_interview_tool_definitions())

    # UI tools — always available
    tools.append(SHOW_SELECTION_TOOL_DEFINITION)

    # CLI action tools — always available
    tools.append(SYNC_DATABASE_TOOL_DEFINITION)
    tools.append(LIST_DATABASES_TOOL_DEFINITION)
    tools.append(RENAME_DATABASE_TOOL_DEFINITION)

    # Workspace files (sandbox) — always available
    tools.append(WORKSPACE_FILES_TOOL_DEFINITION)

    # Saved workflows — always available
    tools.extend(WORKFLOW_TOOL_DEFINITIONS)

    return tools


# ── Tool suites (Think/Act mode) ─────────────────────────────────────────

def _build_tool_suite_registry(agent, has_platform: bool = False) -> Dict[str, Dict]:
    """Build the registry of available tool suites based on agent config.

    Returns: {suite_name: {"tools": [...], "description": "...", "count": N}}
    """
    mcp_tools = getattr(agent, 'mcp_tools', []) or []
    registry = {}

    # Notion
    if "notion" in mcp_tools:
        tools = list(NOTION_TOOL_DEFINITIONS) + list(NOTION_BLOCK_TOOL_DEFINITIONS)
        registry["notion"] = {
            "tools": tools,
            "description": "search, create/update pages, read/update blocks, comments",
            "count": len(tools),
        }

    # Gmail
    if "gmail" in mcp_tools:
        tools = list(GMAIL_TOOL_DEFINITIONS) + list(GMAIL_READ_TOOL_DEFINITIONS)
        registry["gmail"] = {
            "tools": tools,
            "description": "send, draft, reply, forward, search, threads, labels, archive, trash",
            "count": len(tools),
        }

    # Calendar
    if "calendar" in mcp_tools:
        tools = list(CALENDAR_TOOL_DEFINITIONS) + list(CALENDAR_READ_TOOL_DEFINITIONS) + list(CALENDAR_MANAGEMENT_TOOL_DEFINITIONS)
        tools.append(SCHEDULE_AGENT_EVENT_TOOL_DEFINITION)
        if getattr(agent, 'calendar_id', None):
            tools.append(SCHEDULE_SELF_TOOL_DEFINITION)
        registry["calendar"] = {
            "tools": tools,
            "description": "events, scheduling, calendar management (list/create/delete calendars)",
            "count": len(tools),
        }

    # Google Sheets
    if "google_sheets" in mcp_tools:
        tools = list(GOOGLE_SHEETS_TOOL_DEFINITIONS) + list(GOOGLE_SHEETS_FORMAT_TOOL_DEFINITIONS)
        registry["sheets"] = {
            "tools": tools,
            "description": "read, update, append, create, format, find, ingest",
            "count": len(tools),
        }

    # Google Drive
    if "google_drive" in mcp_tools or "google_sheets" in mcp_tools:
        tools = list(GOOGLE_DRIVE_TOOL_DEFINITIONS)
        registry["drive"] = {
            "tools": tools,
            "description": "search, download, upload, create folders, manage permissions",
            "count": len(tools),
        }

    # Google (combined)
    google_tools = []
    for suite_name in ("gmail", "calendar", "sheets", "drive"):
        if suite_name in registry:
            google_tools.extend(registry[suite_name]["tools"])
    if google_tools:
        registry["google"] = {
            "tools": google_tools,
            "description": "all gmail + calendar + sheets + drive",
            "count": len(google_tools),
        }

    # Web
    if "web_search" in mcp_tools:
        tools = list(WEB_SEARCH_TOOL_DEFINITIONS) + list(WEB_FETCH_TOOL_DEFINITIONS)
        registry["web"] = {
            "tools": tools,
            "description": "search and fetch web pages",
            "count": len(tools),
        }

    # Messaging (platform-dependent)
    if has_platform:
        tools = list(MESSAGING_TOOL_DEFINITIONS) + list(SLACK_FILE_TOOL_DEFINITIONS)
        registry["messaging"] = {
            "tools": tools,
            "description": "send messages, start conversations, upload/download files",
            "count": len(tools),
        }

    # Admin (always available)
    admin_tools = (
        list(CONFIG_TOOL_DEFINITIONS)
        + list(AGENT_TOOL_DEFINITIONS)
        + list(CHANNEL_TOOL_DEFINITIONS)
        + list(WORKFLOW_TOOL_DEFINITIONS)
        # + list(_build_interview_tool_definitions())  # disabled outside onboarding
        + [SHOW_SELECTION_TOOL_DEFINITION]
        + [SYNC_DATABASE_TOOL_DEFINITION, LIST_DATABASES_TOOL_DEFINITION, RENAME_DATABASE_TOOL_DEFINITION]
        + [WORKSPACE_FILES_TOOL_DEFINITION]
        + list(TASK_QUEUE_TOOL_DEFINITIONS)
    )
    registry["admin"] = {
        "tools": admin_tools,
        "description": "config, agents, channels, workflows, interviews, sync",
        "count": len(admin_tools),
    }

    return registry


def _build_suite_index(suite_registry: Dict, mcp_suites: Dict = None, workspace: str = "") -> str:
    """Build the suite index text for Think mode system prompt."""
    lines = [
        "## Tool Suites\n",
        "Use `act(suites=[...], instructions=[...])` to enter Act mode with step-by-step instructions.",
        "Take notes first — context is hidden while acting. Instructions stay visible.\n",
    ]
    for name, info in suite_registry.items():
        lines.append(f"- **{name}** ({info['count']} tools) — {info['description']}")
    if mcp_suites:
        for name, info in mcp_suites.items():
            lines.append(f"- **{name}** ({info['count']} tools) — {info['description']}")

    # Saved workflows (user-created automation recipes)
    try:
        from promaia.tools.workflow_store import list_workflows_for_prompt
        wf_summaries = list_workflows_for_prompt(workspace) if workspace else []
        if wf_summaries:
            lines.append("")
            lines.append("## Saved Workflows\n")
            lines.append("If the user's request matches a saved workflow, **always load and follow it**.")
            lines.append("Do NOT improvise from the description — the workflow has specific steps you must follow.")
            lines.append("Call `get_workflow_details(name=\"...\")` to load the steps, then follow them.\n")
            for wf in wf_summaries:
                # Truncate description to avoid giving enough info to improvise
                desc = wf['description']
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                lines.append(f"- **{wf['name']}**: {desc} *(load for details)*")
    except Exception:
        pass

    # Interview workflows disabled outside onboarding flow.
    # start_interview tool is not available — use create_agent / update_agent directly.

    return "\n".join(lines)


def _get_suite_tools(suite_name: str, suite_registry: Dict, mcp_suites: Dict = None) -> List[Dict]:
    """Get tool definitions for a suite by name."""
    if suite_name in suite_registry:
        return list(suite_registry[suite_name]["tools"])
    if mcp_suites and suite_name in mcp_suites:
        return list(mcp_suites[suite_name]["tools"])
    return []


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
        # Ephemeral sandbox for file operations
        from promaia.tools.sandbox import Sandbox
        self._sandbox = Sandbox()
        # Persistent notepad (survives across turns within a conversation)
        self._notepad = ""
        # Context sources: name → {
        #     "content": str, "on": bool, "page_count": int, "source": str,
        #     "mounted_at_iteration": int,  # set when toggled ON; used by LRU trim
        # }
        self._sources = {}
        self._sources_muted = False
        # Updated each iteration by the agentic loop so source-management
        # paths (shelving, _context_action) can stamp mounted_at_iteration.
        self._current_iteration = 0
        # External MCP server connections
        self._mcp_client = None        # McpClient instance
        self._mcp_tool_map = {}        # namespaced_name → (server_name, original_name)

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
            elif tool_name == "write_agent_journal":
                return await self._write_journal(tool_input)
            # Messaging tools
            elif tool_name == "send_message":
                return await self._send_message(tool_input)
            elif tool_name == "start_conversation":
                return await self._start_conversation(tool_input)
            elif tool_name == "end_conversation":
                emoji = tool_input.get("emoji", "")
                summary = tool_input.get("summary", "")
                return f"__END_CONVERSATION__:{emoji}:{summary}"
            elif tool_name == "leave_conversation":
                message = tool_input.get("message", "")
                return f"__LEAVE_CONVERSATION__:{message}"
            # Gmail tools
            elif tool_name == "send_email":
                return await self._send_email(tool_input)
            elif tool_name == "create_email_draft":
                return await self._create_email_draft(tool_input)
            elif tool_name == "reply_to_email":
                return await self._reply_to_email(tool_input)
            elif tool_name == "draft_reply_to_email":
                return await self._draft_reply_to_email(tool_input)
            # Gmail read tools
            elif tool_name == "search_emails":
                return await self._search_emails(tool_input)
            elif tool_name == "get_email_thread":
                return await self._get_email_thread(tool_input)
            elif tool_name == "mark_email_read":
                return await self._mark_email_read(tool_input)
            elif tool_name == "mark_email_unread":
                return await self._mark_email_unread(tool_input)
            elif tool_name == "label_email":
                return await self._label_email(tool_input)
            elif tool_name == "list_labels":
                return await self._list_labels(tool_input)
            elif tool_name == "archive_email":
                return await self._archive_email(tool_input)
            elif tool_name == "trash_email":
                return await self._trash_email(tool_input)
            elif tool_name == "forward_email":
                return await self._forward_email(tool_input)
            elif tool_name == "delete_draft":
                return await self._delete_draft(tool_input)
            elif tool_name == "gmail_download_attachment":
                return await self._gmail_download_attachment(tool_input)
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
            # Calendar management
            elif tool_name == "list_calendars":
                return await self._list_calendars(tool_input)
            elif tool_name == "create_calendar":
                return await self._create_calendar(tool_input)
            elif tool_name == "delete_calendar":
                return await self._delete_calendar(tool_input)
            # Web search & fetch
            elif tool_name == "web_search":
                return await self._web_search(tool_input)
            elif tool_name == "web_fetch":
                return await self._web_fetch(tool_input)
            # Google Sheets tools
            elif tool_name.startswith("sheets_"):
                return await self._execute_sheets_tool(tool_name, tool_input)
            # Google Drive tools
            elif tool_name.startswith("drive_"):
                return await self._execute_drive_tool(tool_name, tool_input)
            # Slack file tools
            elif tool_name.startswith("slack_"):
                return await self._execute_slack_file_tool(tool_name, tool_input)
            # Notion tools
            elif tool_name.startswith("notion_"):
                return await self._execute_notion_tool(tool_name, tool_input)
            # Task queue
            elif tool_name == "task_queue_add":
                return await self._task_queue_add(tool_input)
            # Notepad (persistent notes across turns)
            elif tool_name == "notepad":
                return self._notepad_action(tool_input)
            # Memory (persistent across conversations)
            elif tool_name == "memory":
                return self._memory_action(tool_input)
            # Context management
            elif tool_name == "context":
                return self._context_action(tool_input)
            # Think/Act mode switching (sentinels — handled by the agentic loop)
            elif tool_name == "act":
                suites = tool_input.get("suites", [])
                if not suites:
                    return "Error: provide at least one suite name."
                instructions = tool_input.get("instructions", [])
                import json as _json_act
                return f"__ACT__:{','.join(suites)}|{_json_act.dumps(instructions)}"
            elif tool_name == "mark_step_done":
                step = tool_input.get("step", 0)
                return f"__MARK_STEP__:{step}"
            elif tool_name == "done":
                return "__DONE__"
            # Config tools
            elif tool_name == "list_source_types":
                return await self._list_source_types()
            elif tool_name == "list_workspaces":
                return await self._list_workspaces()
            elif tool_name == "add_workspace":
                return await self._add_workspace(tool_input)
            elif tool_name == "discover_source_name":
                return await self._discover_source_name(tool_input)
            elif tool_name == "check_credential":
                return await self._check_credential(tool_input)
            elif tool_name == "register_database":
                return await self._register_database(tool_input)
            elif tool_name == "test_connection":
                return await self._test_connection(tool_input)
            # Agent tools
            elif tool_name == "list_agents":
                return await self._list_agents()
            elif tool_name == "agent_info":
                return await self._agent_info(tool_input)
            elif tool_name == "enable_agent":
                return await self._enable_agent(tool_input)
            elif tool_name == "disable_agent":
                return await self._disable_agent(tool_input)
            elif tool_name == "rename_agent":
                return await self._rename_agent(tool_input)
            elif tool_name == "update_agent":
                return await self._update_agent(tool_input)
            elif tool_name == "remove_agent":
                return await self._remove_agent(tool_input)
            elif tool_name == "run_agent":
                return await self._run_agent(tool_input)
            elif tool_name == "create_agent":
                return await self._create_agent(tool_input)
            # Channel tools
            elif tool_name == "list_channels":
                return await self._list_channels(tool_input)
            elif tool_name == "get_configured_channels":
                return await self._get_configured_channels(tool_input)
            elif tool_name == "update_channels":
                return await self._update_channels(tool_input)
            # Interview tools (sentinels — handled by the agentic loop)
            elif tool_name == "start_interview":
                workflow = tool_input.get("workflow", "")
                return f"__INTERVIEW_START__:{workflow}"
            elif tool_name == "complete_interview":
                return "__INTERVIEW_END__"
            # UI tools (sentinels — handled by interface.py)
            elif tool_name == "show_selection":
                import json as _json
                return f"__SHOW_SELECTION__:{_json.dumps(tool_input)}"
            # CLI action tools
            elif tool_name == "sync_database":
                return await self._sync_database(tool_input)
            elif tool_name == "list_databases":
                return await self._list_databases(tool_input)
            elif tool_name == "rename_database":
                return await self._rename_database(tool_input)
            # Workspace files (sandbox)
            elif tool_name == "list_workspace_files":
                return await self._list_workspace_files()
            # Workflow tools
            elif tool_name == "create_workflow":
                return await self._create_workflow(tool_input)
            elif tool_name == "list_saved_workflows":
                return await self._list_saved_workflows()
            elif tool_name == "get_workflow_details":
                return await self._get_workflow_details(tool_input)
            elif tool_name == "update_workflow":
                return await self._update_workflow(tool_input)
            elif tool_name == "delete_workflow":
                return await self._delete_workflow(tool_input)
            # External MCP tools
            elif tool_name.startswith("mcp__") and self._mcp_client:
                return await self._execute_mcp_tool(tool_name, tool_input)
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

        # Store as context source so results persist across turns
        source_name = f"sql_{query[:30].strip().replace(' ', '_').lower()}"
        self._sources[source_name] = {
            "content": formatted,
            "on": True,
            "page_count": total_pages,
            "source": "query_sql",
            "titles": self._extract_titles(loaded_content),
        }

        parts = [f"Found {total_pages} results → source '{source_name}' [ON]"]
        if metadata and metadata.get('generated_query'):
            parts.append(f"SQL: {metadata['generated_query']}")
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

        # Store as context source so results persist across turns
        source_name = f"search_{query[:30].strip().replace(' ', '_').lower()}"
        self._sources[source_name] = {
            "content": formatted,
            "on": True,
            "page_count": total_pages,
            "source": "query_vector",
            "titles": self._extract_titles(loaded_content),
        }
        return f"Found {total_pages} semantically similar results → source '{source_name}' [ON]"

    async def _query_source(self, tool_input: Dict) -> str:
        from promaia.config.databases import get_database_config
        from promaia.storage.files import load_database_pages_with_filters
        from promaia.ai.prompts import format_context_data

        database = tool_input.get("database", "")
        if not database:
            return "Error: missing 'database' parameter"

        # Resolve "agent_journal" alias to this agent's actual journal nickname
        if database == "agent_journal" and hasattr(self.agent, 'agent_id'):
            agent_id = self.agent.agent_id
            if agent_id and agent_id != "terminal-user":
                database = f"{agent_id.replace('-', '_')}_journal"

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

        # Store as context source instead of returning full content
        source_name = database.split(".")[-1] if "." in database else database
        self._sources[source_name] = {
            "content": formatted,
            "on": True,  # ON immediately so agent can read it
            "page_count": len(pages),
            "source": "query_source",
            "titles": self._extract_titles({database: pages}),
        }
        return f"Loaded {len(pages)} pages from '{database}' ({time_range}) → source '{source_name}' [ON]"

    async def _write_journal(self, tool_input: Dict) -> str:
        from promaia.agents.notion_journal import write_journal_entry

        content = tool_input.get("content", "")
        if not content:
            return "Error: missing 'content' parameter"

        note_type = tool_input.get("note_type", "Note")
        agent_id = self.agent.agent_id

        # For terminal chat (no real agent), find the workspace journal database
        # and write directly instead of looking up a non-existent agent config
        if agent_id == "terminal-user":
            try:
                return await self._write_journal_to_workspace(content, note_type)
            except Exception as e:
                return f"Error writing to workspace journal: {e}"

        await write_journal_entry(
            agent_id=agent_id,
            workspace=self.workspace,
            entry_type=note_type,
            content=content,
            execution_id=None,
        )

        return f"Wrote {note_type} to journal successfully."

    async def _write_journal_to_workspace(self, content: str, note_type: str) -> str:
        """Write a journal entry to the workspace's journal database directly."""
        from promaia.config.databases import get_database_manager
        from promaia.notion.client import get_client
        from promaia.agents.notion_journal import _derive_title

        # Find the workspace journal database
        db_manager = get_database_manager()
        journal_db = None
        for db in db_manager.get_workspace_databases(self.workspace):
            if db.nickname == "journal" and db.source_type == "notion":
                journal_db = db
                break

        if not journal_db:
            return "Error: no journal database found for this workspace."

        client = get_client(self.workspace)
        title = _derive_title(content)

        from datetime import datetime
        now = datetime.now()

        properties = {
            "Name": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": now.strftime("%Y-%m-%d")}},
        }

        # Build content blocks (Notion limit: ~2000 chars per block)
        children = []
        chunk_size = 1900
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i + chunk_size]
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": chunk}}]
                }
            })

        response = await client.pages.create(
            parent={"database_id": journal_db.database_id},
            properties=properties,
            children=children
        )

        page_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
        return f"Wrote {note_type} to journal: {title} (page: {page_id})"

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

        meta = await self.platform.send_message(
            channel_id=channel_id,
            content=content,
            thread_id=thread_id,
        )

        # For top-level DMs, register a dormant conversation so replies
        # can be routed back with full context of the original message.
        if user_name and not thread_id:
            await self._register_dormant_dm(
                meta=meta,
                user_info=user_info,
                dm_channel=channel_id,
                content=content,
            )

        if thread_id:
            target_desc += f" (thread {thread_id[:12]})"
        return f"Message sent to {target_desc}"

    async def _register_dormant_dm(
        self, meta, user_info: Dict, dm_channel: str, content: str,
    ) -> None:
        """Create a dormant ConversationState for a DM so replies are routable."""
        try:
            from promaia.agents.conversation_manager import (
                ConversationManager, ConversationState,
            )
            from datetime import datetime, timezone

            conv_manager = ConversationManager()
            platform_name = getattr(self.platform, "platform_name", "slack")

            if platform_name not in conv_manager.platforms:
                conv_manager.register_platform(platform_name, self.platform)

            agent_id = (
                getattr(self.agent, "agent_id", None)
                or getattr(self.agent, "name", "agent")
            )
            real_name = (
                user_info.get("real_name")
                or user_info.get("name")
                or "unknown"
            )

            now = datetime.now(timezone.utc).isoformat()
            msg_ts = str(int(datetime.now(timezone.utc).timestamp()))
            conversation_id = f"{platform_name}_{dm_channel}_{msg_ts}"
            dm_thread_id = meta.message_id

            state = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_id,
                platform=platform_name,
                channel_id=dm_channel,
                user_id=user_info["id"],
                thread_id=dm_thread_id,
                status="dormant",
                last_message_at=now,
                messages=[{
                    "role": "assistant",
                    "content": content,
                    "timestamp": now,
                }],
                context={"is_dm": True, "user_name": real_name},
                timeout_seconds=600,
                max_turns=None,
                turn_count=0,
                created_at=now,
                conversation_type="tag_to_chat",
                is_active=True,
                conversation_partner=real_name,
            )
            await conv_manager._save_state(state)
            logger.info(
                f"Registered dormant DM conversation {conversation_id} "
                f"(thread {dm_thread_id[:12]}) for replies from {real_name}"
            )
        except Exception as e:
            logger.warning(f"Failed to register dormant DM conversation: {e}")

    async def _start_conversation(self, tool_input: Dict) -> str:
        """Start a real interactive DM conversation and wait for it to complete.

        Creates a TagToChatLoop that drives a full back-and-forth conversation
        using the agent's personality. The agentic turn suspends until the
        conversation goes dormant (user stops replying), then resumes with
        the full transcript.
        """
        import asyncio
        if not self.platform:
            return "Error: messaging is not available in this context (no platform)."

        user_name = tool_input.get("user", "")
        message = tool_input.get("message", "")
        timeout_minutes = tool_input.get("timeout_minutes", 60)

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
            from promaia.agents.tag_to_chat import TagToChatLoop
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
            real_name = user_info.get("real_name") or user_info.get("name") or user_name

            # Send message at top-level — this becomes the thread parent
            meta = await self.platform.send_message(
                channel_id=dm_channel, content=message,
            )
            dm_thread_id = meta.message_id

            # Create a real conversation state (not passive — agent personality drives it)
            now = datetime.now(timezone.utc).isoformat()
            msg_ts = str(int(datetime.now(timezone.utc).timestamp()))
            conversation_id = f"{platform_name}_{dm_channel}_{msg_ts}"

            state = ConversationState(
                conversation_id=conversation_id,
                agent_id=agent_id,
                platform=platform_name,
                channel_id=dm_channel,
                user_id=user_info["id"],
                thread_id=dm_thread_id,
                status="active",
                last_message_at=now,
                messages=[{
                    "role": "assistant",
                    "content": message,
                    "timestamp": now,
                }],
                context={"is_dm": True, "user_name": real_name},
                timeout_seconds=timeout_minutes * 60,
                max_turns=None,
                turn_count=0,
                created_at=now,
                conversation_type="tag_to_chat",
                is_active=True,
                conversation_partner=real_name,
            )
            await conv_manager._save_state(state)

            # Start a real TagToChatLoop — agent personality drives the conversation
            loop = TagToChatLoop(
                conversation_id=conversation_id,
                channel_id=dm_channel,
                thread_id=dm_thread_id,
                platform=platform_name,
                agent_id=agent_id,
                platform_impl=self.platform,
                conv_manager=conv_manager,
                is_dm=True,
            )

            # Dormancy signal — fires when the loop exits (dormant/stopped)
            done_event = asyncio.Event()
            loop.on_done(done_event.set)

            # Start the conversation loop
            asyncio.create_task(loop.run())
            logger.info(f"Started interactive conversation {conversation_id} with {real_name}")

            # Suspend the agentic turn until conversation goes dormant or times out
            try:
                await asyncio.wait_for(
                    done_event.wait(),
                    timeout=timeout_minutes * 60,
                )
            except asyncio.TimeoutError:
                logger.info(f"Conversation {conversation_id} timed out after {timeout_minutes}min")
                loop._stop_requested = True

            # Load final transcript
            final_state = await conv_manager._load_state(conversation_id)
            if final_state and final_state.messages:
                transcript_lines = []
                for msg in final_state.messages:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                        content = "\n".join(text_parts)
                    if isinstance(content, str) and content.strip():
                        speaker = real_name if role == "user" else "You"
                        transcript_lines.append(f"[{speaker}]: {content}")
                transcript = "\n".join(transcript_lines)
                return f"Conversation with {real_name} completed.\n\nTranscript:\n{transcript}"
            else:
                return f"Conversation with {real_name} ended (no messages exchanged)."

        except Exception as e:
            logger.error(f"start_conversation error: {e}", exc_info=True)
            return f"Error starting conversation: {e}"

    # ── Gmail tools ──────────────────────────────────────────────────────

    async def _ensure_gmail(self):
        """Lazy-initialize Gmail connector."""
        if self._gmail_connector is not None:
            return
        from promaia.connectors.gmail_connector import GmailConnector
        from promaia.config.databases import get_database_config, get_database_manager

        # Try explicit names first, then search for any gmail source in workspace
        gmail_db = (
            get_database_config(f"{self.workspace}.gmail")
            or get_database_config("gmail")
        )
        if not gmail_db:
            db_manager = get_database_manager()
            for db in db_manager.get_workspace_databases(self.workspace):
                if getattr(db, 'source_type', None) == 'gmail':
                    gmail_db = db
                    break
        if not gmail_db:
            raise RuntimeError(f"No Gmail configured for workspace {self.workspace}")

        if isinstance(gmail_db, dict):
            email = gmail_db.get("database_id")
        else:
            email = getattr(gmail_db, 'database_id', None)
        config = {"database_id": email, "workspace": self.workspace}
        self._gmail_connector = GmailConnector(config)
        if not await self._gmail_connector.connect(allow_interactive=False):
            self._gmail_connector = None
            raise RuntimeError(f"Failed to connect to Gmail: {email}")
        logger.info(f"Gmail connected: {email}")

    def _resolve_attachment_paths(self, tool_input: Dict) -> list[str] | None:
        """Resolve workspace-relative attachment paths to absolute paths."""
        paths = tool_input.get("attachment_paths")
        if not paths:
            return None
        resolved = []
        for rel_path in paths:
            abs_path = self._sandbox.resolve(rel_path)
            if not abs_path.exists():
                raise FileNotFoundError(f"Workspace file not found: {rel_path}")
            resolved.append(str(abs_path))
        return resolved

    async def _send_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        try:
            attachments = self._resolve_attachment_paths(tool_input)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"

        success = await self._gmail_connector.send_email(
            to=tool_input["to"],
            subject=tool_input["subject"],
            body_text=tool_input["body"],
            cc=tool_input.get("cc"),
            bcc=tool_input.get("bcc"),
            attachments=attachments,
        )
        attach_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        if success:
            return f"Email sent to {tool_input['to']}: {tool_input['subject']}{attach_note}"
        return "Failed to send email."

    async def _create_email_draft(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        try:
            attachments = self._resolve_attachment_paths(tool_input)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"

        draft_id = await self._gmail_connector._create_draft(
            to=tool_input["to"],
            subject=tool_input["subject"],
            body=tool_input["body"],
            cc=tool_input.get("cc"),
            attachments=attachments,
        )
        attach_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        if draft_id:
            return f"Draft created{attach_note} (ID: {draft_id})"
        return "Failed to create draft."

    async def _reply_to_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        try:
            attachments = self._resolve_attachment_paths(tool_input)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"

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
            attachments=attachments,
        )
        attach_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        if success:
            return f"Reply sent{attach_note} (thread: {tool_input['thread_id']})"
        return "Failed to send reply."

    async def _draft_reply_to_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        try:
            attachments = self._resolve_attachment_paths(tool_input)
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"

        # Strip msg_ prefix for Gmail API
        raw_message_id = tool_input["message_id"]
        if raw_message_id.startswith("msg_"):
            raw_message_id = raw_message_id[4:]

        original = await self._gmail_connector._get_message(raw_message_id)
        if not original:
            return "Original message not found."

        headers = {
            h['name'].lower(): h['value']
            for h in original.get('payload', {}).get('headers', [])
        }

        subject = headers.get('subject', '')
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        reply_to = headers.get('reply-to') or headers.get('from')

        # Build references chain for threading
        existing_refs = headers.get('references', '')
        msg_id_header = headers.get('message-id', '')
        references = (
            f"{existing_refs} {msg_id_header}".strip()
            if msg_id_header else existing_refs or None
        )

        # Build quoted reply body
        full_body = self._gmail_connector._build_quoted_reply(
            tool_input["body"], original
        )

        draft_id = await self._gmail_connector._create_draft(
            to=reply_to,
            subject=subject,
            body=full_body,
            thread_id=tool_input["thread_id"],
            in_reply_to=msg_id_header,
            references=references,
            attachments=attachments,
        )
        attach_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        if draft_id:
            return f"Draft reply created{attach_note} in thread {tool_input['thread_id']} (ID: {draft_id})"
        return "Failed to create draft reply."

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

    async def _mark_email_read(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        ids = tool_input.get("message_ids", [])
        if not ids:
            return "Error: missing 'message_ids'"
        try:
            for mid in ids:
                self._gmail_connector.service.users().messages().modify(
                    userId='me', id=mid,
                    body={"removeLabelIds": ["UNREAD"]}
                ).execute()
            return f"Marked {len(ids)} email(s) as read."
        except Exception as e:
            return f"Error: {e}"

    async def _mark_email_unread(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        ids = tool_input.get("message_ids", [])
        if not ids:
            return "Error: missing 'message_ids'"
        try:
            for mid in ids:
                self._gmail_connector.service.users().messages().modify(
                    userId='me', id=mid,
                    body={"addLabelIds": ["UNREAD"]}
                ).execute()
            return f"Marked {len(ids)} email(s) as unread."
        except Exception as e:
            return f"Error: {e}"

    async def _label_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        ids = tool_input.get("message_ids", [])
        add = tool_input.get("add_labels", [])
        remove = tool_input.get("remove_labels", [])
        if not ids:
            return "Error: missing 'message_ids'"
        if not add and not remove:
            return "Error: provide 'add_labels' and/or 'remove_labels'"
        try:
            # Resolve label names to IDs
            labels_resp = self._gmail_connector.service.users().labels().list(userId='me').execute()
            all_labels = {l['name'].lower(): l['id'] for l in labels_resp.get('labels', [])}
            all_labels.update({l['id'].lower(): l['id'] for l in labels_resp.get('labels', [])})

            add_ids = [all_labels.get(l.lower(), l) for l in add]
            remove_ids = [all_labels.get(l.lower(), l) for l in remove]

            body = {}
            if add_ids:
                body["addLabelIds"] = add_ids
            if remove_ids:
                body["removeLabelIds"] = remove_ids

            for mid in ids:
                self._gmail_connector.service.users().messages().modify(
                    userId='me', id=mid, body=body
                ).execute()
            parts = []
            if add:
                parts.append(f"added {', '.join(add)}")
            if remove:
                parts.append(f"removed {', '.join(remove)}")
            return f"Labels updated on {len(ids)} email(s): {'; '.join(parts)}."
        except Exception as e:
            return f"Error: {e}"

    async def _list_labels(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        try:
            resp = self._gmail_connector.service.users().labels().list(userId='me').execute()
            labels = resp.get('labels', [])
            system = [l for l in labels if l.get('type') == 'system']
            user = [l for l in labels if l.get('type') == 'user']
            lines = [f"Gmail labels ({len(labels)} total):\n"]
            if user:
                lines.append("Custom labels:")
                for l in sorted(user, key=lambda x: x['name']):
                    lines.append(f"  - {l['name']} (id: {l['id']})")
            if system:
                lines.append("\nSystem labels:")
                for l in sorted(system, key=lambda x: x['name']):
                    lines.append(f"  - {l['name']} (id: {l['id']})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    async def _archive_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        ids = tool_input.get("message_ids", [])
        if not ids:
            return "Error: missing 'message_ids'"
        try:
            for mid in ids:
                self._gmail_connector.service.users().messages().modify(
                    userId='me', id=mid,
                    body={"removeLabelIds": ["INBOX"]}
                ).execute()
            return f"Archived {len(ids)} email(s)."
        except Exception as e:
            return f"Error: {e}"

    async def _trash_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        ids = tool_input.get("message_ids", [])
        if not ids:
            return "Error: missing 'message_ids'"
        try:
            for mid in ids:
                self._gmail_connector.service.users().messages().trash(
                    userId='me', id=mid
                ).execute()
            return f"Trashed {len(ids)} email(s)."
        except Exception as e:
            return f"Error: {e}"

    async def _forward_email(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        message_id = tool_input.get("message_id", "")
        to = tool_input.get("to", "")
        prepend_body = tool_input.get("body", "")
        if not message_id or not to:
            return "Error: 'message_id' and 'to' are required"
        try:
            # Get original message
            msg = self._gmail_connector.service.users().messages().get(
                userId='me', id=message_id, format='full'
            ).execute()
            headers = {
                h['name'].lower(): h['value']
                for h in msg.get('payload', {}).get('headers', [])
            }
            original_body = self._gmail_connector._extract_message_body(msg)
            subject = headers.get('subject', '')
            if not subject.lower().startswith('fwd:'):
                subject = f"Fwd: {subject}"

            fwd_body = ""
            if prepend_body:
                fwd_body = f"{prepend_body}\n\n"
            fwd_body += (
                f"---------- Forwarded message ----------\n"
                f"From: {headers.get('from', '?')}\n"
                f"Date: {headers.get('date', '?')}\n"
                f"Subject: {headers.get('subject', '')}\n\n"
                f"{original_body}"
            )

            return await self._send_email({
                "to": to,
                "subject": subject,
                "body": fwd_body,
            })
        except Exception as e:
            return f"Error forwarding: {e}"

    async def _delete_draft(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        draft_id = tool_input.get("draft_id", "")
        if not draft_id:
            return "Error: missing 'draft_id'"
        try:
            self._gmail_connector.service.users().drafts().delete(
                userId='me', id=draft_id
            ).execute()
            return f"Draft {draft_id} deleted."
        except Exception as e:
            return f"Error: {e}"

    async def _gmail_download_attachment(self, tool_input: Dict) -> str:
        await self._ensure_gmail()
        message_id = tool_input.get("message_id", "").strip()
        attachment_id = tool_input.get("attachment_id", "").strip()
        filename = tool_input.get("filename", "").strip()

        if not message_id or not attachment_id or not filename:
            return "Error: message_id, attachment_id, and filename are all required."

        # Strip msg_ prefix if present (internal convention)
        if message_id.startswith("msg_"):
            message_id = message_id[4:]

        try:
            out_path = self._sandbox.resolve(filename)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except ValueError as e:
            return f"Error: {e}"

        try:
            import base64
            from promaia.utils.rate_limiter import google_api_execute_async

            result = await google_api_execute_async(
                self._gmail_connector.service.users().messages().attachments().get(
                    userId='me', messageId=message_id, id=attachment_id
                )
            )

            data = result.get("data", "")
            # Gmail uses URL-safe base64 without padding
            file_bytes = base64.urlsafe_b64decode(data + "==")
            out_path.write_bytes(file_bytes)

            import mimetypes
            mime, _ = mimetypes.guess_type(str(out_path))

            return (
                f"Downloaded attachment to workspace: {filename}\n"
                f"  Size: {len(file_bytes) / 1024:.1f} KB\n"
                f"  Type: {mime or 'application/octet-stream'}"
            )
        except Exception as e:
            return f"Error downloading attachment: {e}"

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

    # ── Calendar management tools ────────────────────────────────────────

    async def _list_calendars(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        try:
            resp = await asyncio.to_thread(
                self._calendar_service.calendarList().list().execute
            )
            calendars = resp.get("items", [])
            lines = [f"Calendars ({len(calendars)}):\n"]
            for cal in calendars:
                primary = " (primary)" if cal.get("primary") else ""
                access = cal.get("accessRole", "")
                lines.append(
                    f"- **{cal.get('summary', '(untitled)')}**{primary}\n"
                    f"  ID: {cal['id']}\n"
                    f"  Access: {access}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing calendars: {e}"

    async def _create_calendar(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: calendar name is required."
        description = tool_input.get("description", "")
        try:
            body = {"summary": name}
            if description:
                body["description"] = description
            cal = await asyncio.to_thread(
                self._calendar_service.calendars().insert(body=body).execute
            )
            return f"Calendar created: **{cal['summary']}** (ID: {cal['id']})"
        except Exception as e:
            return f"Error creating calendar: {e}"

    async def _delete_calendar(self, tool_input: Dict) -> str:
        await self._ensure_calendar()
        calendar_id = tool_input.get("calendar_id", "").strip()
        if not calendar_id:
            return "Error: calendar_id is required."
        if calendar_id == "primary":
            return "Cannot delete the primary calendar."
        try:
            await asyncio.to_thread(
                self._calendar_service.calendars().delete(calendarId=calendar_id).execute
            )
            return f"Calendar deleted: {calendar_id}"
        except Exception as e:
            return f"Error deleting calendar: {e}"

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
        from promaia.utils.rate_limiter import google_api_execute_async
        formula_result = await google_api_execute_async(
            self._sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueRenderOption='FORMULA',
            )
        )
        display_result = await google_api_execute_async(
            self._sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueRenderOption='FORMATTED_VALUE',
            )
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
        result = f"Range {range_str} ({max_rows} rows, starting row {start_row}):\n\n{out.getvalue()}"

        # Auto-save as toggleable context source
        # TODO: may want to add opt-in pin feature in future version
        source_name = f"sheet_{range_str.replace('!', '_').replace(':', '-')}"
        self._sources[source_name] = {
            "content": result,
            "on": True,
            "source": "sheets_read_range",
        }
        return f"Range loaded → source '{source_name}' [ON]\n\n{result}"

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
            from promaia.utils.rate_limiter import google_api_execute_async
            result = await google_api_execute_async(
                self._sheets_service.spreadsheets().values().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={
                        "valueInputOption": value_input,
                        "data": data,
                    },
                )
            )
            updated = result.get("totalUpdatedCells", 0)
            return f"Updated {updated} cells across {len(data)} ranges."

        # Single range update
        range_str = tool_input.get("range", "")
        values = self._coerce_values(tool_input.get("values", []))
        if not range_str or not values:
            return "Error: 'range' and 'values' required (or use 'ranges' for batch)"

        from promaia.utils.rate_limiter import google_api_execute_async
        result = await google_api_execute_async(
            self._sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption=value_input,
                body={"values": values},
            )
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

        from promaia.utils.rate_limiter import google_api_execute_async
        result = await google_api_execute_async(
            self._sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_str,
                valueInputOption=value_input,
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
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

        from promaia.utils.rate_limiter import google_api_execute_async
        ss = await google_api_execute_async(
            self._sheets_service.spreadsheets().create(body=body)
        )
        ss_id = ss["spreadsheetId"]
        ss_url = ss["spreadsheetUrl"]

        # Optionally move to a folder
        folder_id = tool_input.get("folder_id")
        if folder_id:
            try:
                # Get current parents, then move
                file_info = await google_api_execute_async(
                    self._drive_service.files().get(
                        fileId=ss_id, fields="parents"
                    )
                )
                current_parents = ",".join(file_info.get("parents", []))
                await google_api_execute_async(
                    self._drive_service.files().update(
                        fileId=ss_id,
                        addParents=folder_id,
                        removeParents=current_parents,
                        fields="id, parents",
                    )
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
                await google_api_execute_async(
                    self._sheets_service.spreadsheets().values().batchUpdate(
                        spreadsheetId=ss_id,
                        body={"valueInputOption": "USER_ENTERED", "data": data},
                    )
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
        from promaia.utils.rate_limiter import google_api_execute_async
        meta = await google_api_execute_async(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties",
            )
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
        from promaia.utils.rate_limiter import google_api_execute_async
        meta = await google_api_execute_async(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="sheets.properties",
            )
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

        await google_api_execute_async(
            self._sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
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
            from promaia.utils.rate_limiter import google_api_execute_async
            meta = await google_api_execute_async(
                self._sheets_service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields="sheets.properties",
                )
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

        from promaia.utils.rate_limiter import google_api_execute_async
        await google_api_execute_async(
            self._sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            )
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
        from promaia.utils.rate_limiter import google_api_execute_async
        if sheet_name:
            sheet_id = await self._get_sheet_id_by_name(spreadsheet_id, sheet_name)
            if sheet_id is None:
                return f"Error: tab '{sheet_name}' not found"
        else:
            meta = await google_api_execute_async(
                self._sheets_service.spreadsheets().get(
                    spreadsheetId=spreadsheet_id,
                    fields="sheets.properties",
                )
            )
            sheets = meta.get("sheets", [])
            if not sheets:
                return "Error: spreadsheet has no sheets"
            sheet_id = sheets[0]["properties"]["sheetId"]
            sheet_name = sheets[0]["properties"]["title"]

        # Insert blank rows (0-indexed: row 6 in sheet = startIndex 5)
        await google_api_execute_async(
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
            )
        )

        # Optionally fill inserted rows with data
        values = tool_input.get("values")
        if values:
            values = self._coerce_values(values)
            write_range = f"'{sheet_name}'!A{row}"
            await google_api_execute_async(
                self._sheets_service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=write_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": values},
                )
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
        from promaia.utils.rate_limiter import google_api_execute_async
        results = await google_api_execute_async(
            self._drive_service.files().list(
                q=q,
                fields="files(id, name, modifiedTime, webViewLink)",
                pageSize=20,
                orderBy="modifiedTime desc",
            )
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
        from promaia.utils.rate_limiter import google_api_execute_async
        if not spreadsheet_id:
            safe_query = identifier.replace("'", "\\'")
            q = (
                f"mimeType='application/vnd.google-apps.spreadsheet' "
                f"and name contains '{safe_query}' and trashed=false"
            )
            results = await google_api_execute_async(
                self._drive_service.files().list(
                    q=q,
                    fields="files(id, name)",
                    pageSize=1,
                    orderBy="modifiedTime desc",
                )
            )
            files = results.get("files", [])
            if files:
                spreadsheet_id = files[0]["id"]

        if not spreadsheet_id:
            return f"Error: could not find spreadsheet '{identifier}'. Try sheets_find first."

        # Fetch metadata
        meta = await google_api_execute_async(
            self._sheets_service.spreadsheets().get(
                spreadsheetId=spreadsheet_id,
                fields="properties.title,sheets.properties.title,spreadsheetUrl",
            )
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
            formula_resp = await google_api_execute_async(
                self._sheets_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=safe_range,
                    valueRenderOption='FORMULA',
                )
            )
            display_resp = await google_api_execute_async(
                self._sheets_service.spreadsheets().values().get(
                    spreadsheetId=spreadsheet_id,
                    range=safe_range,
                    valueRenderOption='FORMATTED_VALUE',
                )
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
        from promaia.connectors.google_sheets_connector import truncate_sheet_content
        ingest_properties = {"sheet_names": sheet_names, "row_counts": row_counts}
        content = truncate_sheet_content(content, ingest_properties, title)
        return header + content

    # ── Google Drive tools ───────────────────────────────────────────────

    async def _ensure_drive(self):
        """Lazy-initialize Google Drive service (reuses Sheets init if available)."""
        if self._drive_service is not None:
            return
        # If Sheets is already initialized, Drive comes with it
        if self._sheets_service is not None:
            return
        from googleapiclient.discovery import build
        creds = self._get_google_creds()
        self._drive_service = await asyncio.to_thread(
            build, 'drive', 'v3', credentials=creds
        )
        logger.info("Google Drive authenticated")

    async def _execute_drive_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Route and execute Google Drive tool calls."""
        await self._ensure_drive()

        if tool_name == "drive_search_files":
            return await self._drive_search_files(tool_input)
        elif tool_name == "drive_download_file":
            return await self._drive_download_file(tool_input)
        elif tool_name == "drive_list_folder":
            return await self._drive_list_folder(tool_input)
        elif tool_name == "drive_upload_file":
            return await self._drive_upload_file(tool_input)
        elif tool_name == "drive_create_folder":
            return await self._drive_create_folder(tool_input)
        elif tool_name == "drive_manage_permissions":
            return await self._drive_manage_permissions(tool_input)
        else:
            return f"Unknown Drive tool: {tool_name}"

    async def _drive_search_files(self, tool_input: Dict) -> str:
        query = tool_input.get("query", "").strip()
        folder_id = tool_input.get("folder_id", "").strip()
        max_results = tool_input.get("max_results", 10)

        if not query and not folder_id:
            return "Error: query or folder_id is required."

        # Build Drive query
        q_parts = []
        if query:
            # If it looks like raw Drive syntax, use as-is
            if "'" in query or "=" in query:
                q_parts.append(query)
            else:
                q_parts.append(f"name contains '{query}'")
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        q_parts.append("trashed = false")
        drive_query = " and ".join(q_parts)

        try:
            from promaia.utils.rate_limiter import google_api_execute_async
            results = await google_api_execute_async(
                self._drive_service.files().list(
                    q=drive_query,
                    pageSize=max_results,
                    fields="files(id, name, mimeType, size, modifiedTime, parents)",
                    orderBy="modifiedTime desc",
                )
            )

            files = results.get("files", [])
            if not files:
                return f"No files found for query: {query or folder_id}"

            lines = [f"Found {len(files)} file(s):\n"]
            for f in files:
                is_native = f["mimeType"].startswith("application/vnd.google-apps.")
                size = f.get("size")
                size_str = f"{int(size) / 1024:.1f} KB" if size else "Google native"
                lines.append(
                    f"  - **{f['name']}**\n"
                    f"    ID: {f['id']}\n"
                    f"    Type: {f['mimeType']}\n"
                    f"    Size: {size_str}\n"
                    f"    Modified: {f.get('modifiedTime', 'unknown')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error searching Drive: {e}"

    async def _drive_download_file(self, tool_input: Dict) -> str:
        file_id = tool_input.get("file_id", "").strip()
        if not file_id:
            return "Error: file_id is required."

        filename = tool_input.get("filename", "").strip() or None
        export_format = tool_input.get("export_format", "pdf").strip()

        # Google-native MIME → export MIME mapping
        export_mime_map = {
            "application/vnd.google-apps.document": {
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "txt": "text/plain",
            },
            "application/vnd.google-apps.spreadsheet": {
                "pdf": "application/pdf",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "csv": "text/csv",
            },
            "application/vnd.google-apps.presentation": {
                "pdf": "application/pdf",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            },
        }
        export_extensions = {
            "pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx",
            "csv": ".csv", "pptx": ".pptx", "txt": ".txt",
        }

        try:
            import io
            from googleapiclient.http import MediaIoBaseDownload
            from pathlib import Path

            # Get file metadata
            from promaia.utils.rate_limiter import google_api_execute_async
            meta = await google_api_execute_async(
                self._drive_service.files().get(
                    fileId=file_id, fields="name, mimeType"
                )
            )

            native_mime = meta["mimeType"]
            is_native = native_mime in export_mime_map

            if is_native:
                export_mime = export_mime_map[native_mime].get(export_format, "application/pdf")
                request = self._drive_service.files().export_media(
                    fileId=file_id, mimeType=export_mime
                )
                ext = export_extensions.get(export_format, ".pdf")
                out_name = filename or (Path(meta["name"]).stem + ext)
            else:
                request = self._drive_service.files().get_media(fileId=file_id)
                out_name = filename or meta["name"]

            # Download to buffer
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = await asyncio.to_thread(downloader.next_chunk)

            # Write to sandbox
            out_path = self._sandbox.resolve(out_name)
            out_path.write_bytes(buf.getvalue())

            import mimetypes
            mime, _ = mimetypes.guess_type(str(out_path))

            return (
                f"Downloaded to workspace: {out_name}\n"
                f"  Size: {out_path.stat().st_size / 1024:.1f} KB\n"
                f"  Type: {mime or 'application/octet-stream'}"
            )
        except Exception as e:
            return f"Error downloading file: {e}"

    async def _drive_list_folder(self, tool_input: Dict) -> str:
        folder_id = tool_input.get("folder_id", "root").strip()

        try:
            from promaia.utils.rate_limiter import google_api_execute_async
            results = await google_api_execute_async(
                self._drive_service.files().list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    pageSize=50,
                    fields="files(id, name, mimeType, size, modifiedTime)",
                    orderBy="folder,name",
                )
            )

            files = results.get("files", [])
            if not files:
                return "Folder is empty."

            folders = []
            docs = []
            for f in files:
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    folders.append(f)
                else:
                    docs.append(f)

            lines = []
            if folders:
                lines.append(f"Folders ({len(folders)}):")
                for f in folders:
                    lines.append(f"  📁 {f['name']}  (ID: {f['id']})")
            if docs:
                lines.append(f"\nFiles ({len(docs)}):")
                for f in docs:
                    size = f.get("size")
                    size_str = f"{int(size) / 1024:.1f} KB" if size else "Google native"
                    lines.append(
                        f"  📄 {f['name']}  ({size_str}, ID: {f['id']})"
                    )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing folder: {e}"

    async def _drive_ensure_folder_path(self, path: str, parent_id: str = "root") -> str:
        """Walk/create a nested folder path in Drive, returning the leaf folder ID."""
        from promaia.utils.rate_limiter import google_api_execute_async

        segments = [s.strip() for s in path.split("/") if s.strip()]
        if not segments:
            return parent_id

        current_parent = parent_id
        for segment in segments:
            # Search for existing folder
            q = (
                f"name = '{segment}' and '{current_parent}' in parents "
                f"and mimeType = 'application/vnd.google-apps.folder' "
                f"and trashed = false"
            )
            results = await google_api_execute_async(
                self._drive_service.files().list(
                    q=q, pageSize=1,
                    fields="files(id, name)",
                    orderBy="createdTime",
                )
            )
            found = results.get("files", [])
            if found:
                current_parent = found[0]["id"]
            else:
                # Create the folder
                metadata = {
                    "name": segment,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [current_parent],
                }
                folder = await google_api_execute_async(
                    self._drive_service.files().create(
                        body=metadata, fields="id, name"
                    )
                )
                current_parent = folder["id"]
                logger.info(f"[drive] Created folder '{segment}' (ID: {current_parent})")
        return current_parent

    async def _drive_create_folder(self, tool_input: Dict) -> str:
        folder_name = tool_input.get("folder_name", "").strip()
        if not folder_name:
            return "Error: folder_name is required."
        parent_id = tool_input.get("parent_id", "root").strip() or "root"

        try:
            folder_id = await self._drive_ensure_folder_path(folder_name, parent_id)
            return (
                f"Folder ready: {folder_name}\n"
                f"  ID: {folder_id}\n"
                f"  Parent: {parent_id}"
            )
        except Exception as e:
            return f"Error creating folder: {e}"

    async def _drive_upload_file(self, tool_input: Dict) -> str:
        filename = tool_input.get("filename", "").strip()
        if not filename:
            return "Error: filename is required."

        try:
            abs_path = self._sandbox.resolve(filename)
            if not abs_path.exists():
                return f"Error: workspace file not found: {filename}"
        except ValueError as e:
            return f"Error: {e}"

        folder_id = tool_input.get("folder_id", "").strip()
        folder_path = tool_input.get("folder_path", "").strip()
        drive_filename = tool_input.get("drive_filename", "").strip() or abs_path.name
        convert = tool_input.get("convert_to_google_format", False)

        try:
            # Resolve target folder
            if folder_id:
                target_folder = folder_id
            elif folder_path:
                target_folder = await self._drive_ensure_folder_path(folder_path)
            else:
                target_folder = "root"

            from googleapiclient.http import MediaFileUpload
            import mimetypes as _mt

            mime_type, _ = _mt.guess_type(str(abs_path))
            mime_type = mime_type or "application/octet-stream"

            file_metadata = {
                "name": drive_filename,
                "parents": [target_folder],
            }

            # Conversion MIME mapping (upload original -> Google native)
            upload_conversion_map = {
                ".docx": "application/vnd.google-apps.document",
                ".doc": "application/vnd.google-apps.document",
                ".xlsx": "application/vnd.google-apps.spreadsheet",
                ".xls": "application/vnd.google-apps.spreadsheet",
                ".csv": "application/vnd.google-apps.spreadsheet",
                ".pptx": "application/vnd.google-apps.presentation",
                ".ppt": "application/vnd.google-apps.presentation",
                ".txt": "application/vnd.google-apps.document",
            }

            if convert:
                from pathlib import Path as _Path
                ext = _Path(abs_path).suffix.lower()
                google_mime = upload_conversion_map.get(ext)
                if google_mime:
                    file_metadata["mimeType"] = google_mime

            file_size = abs_path.stat().st_size
            media = MediaFileUpload(
                str(abs_path),
                mimetype=mime_type,
                resumable=file_size > 5 * 1024 * 1024,
            )

            from promaia.utils.rate_limiter import google_api_execute_async
            result = await google_api_execute_async(
                self._drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, name, webViewLink, mimeType",
                )
            )

            location = folder_path or folder_id or "root"
            return (
                f"Uploaded '{result['name']}' to Drive\n"
                f"  ID: {result['id']}\n"
                f"  Location: {location}\n"
                f"  Size: {file_size / 1024:.1f} KB\n"
                f"  Type: {result.get('mimeType', mime_type)}\n"
                f"  Link: {result.get('webViewLink', 'N/A')}"
            )
        except Exception as e:
            return f"Error uploading file: {e}"

    async def _drive_manage_permissions(self, tool_input: Dict) -> str:
        file_id = tool_input.get("file_id", "").strip()
        action = tool_input.get("action", "").strip()
        if not file_id:
            return "Error: file_id is required."
        if action not in ("share", "unshare", "list"):
            return "Error: action must be 'share', 'unshare', or 'list'."

        try:
            from promaia.utils.rate_limiter import google_api_execute_async

            if action == "list":
                result = await google_api_execute_async(
                    self._drive_service.permissions().list(
                        fileId=file_id,
                        fields="permissions(id, type, role, emailAddress, displayName)",
                    )
                )
                perms = result.get("permissions", [])
                if not perms:
                    return "No permissions found."
                lines = [f"Permissions for {file_id}:\n"]
                for p in perms:
                    email = p.get("emailAddress", "")
                    name = p.get("displayName", "")
                    label = f"{name} ({email})" if email else p.get("type", "unknown")
                    lines.append(f"  - {label}: {p['role']} (ID: {p['id']})")
                return "\n".join(lines)

            elif action == "share":
                perm_type = tool_input.get("type", "user").strip()
                role = tool_input.get("role", "reader").strip()
                email = tool_input.get("email", "").strip()
                send_notification = tool_input.get("send_notification", True)

                if perm_type == "user" and not email:
                    return "Error: email is required when sharing with type=user."

                permission_body = {"type": perm_type, "role": role}
                if email:
                    permission_body["emailAddress"] = email

                result = await google_api_execute_async(
                    self._drive_service.permissions().create(
                        fileId=file_id,
                        body=permission_body,
                        sendNotificationEmail=send_notification,
                        fields="id, type, role, emailAddress",
                    )
                )
                target = email or "anyone with link"
                return (
                    f"Shared with {target} as {role}\n"
                    f"  Permission ID: {result['id']}"
                )

            elif action == "unshare":
                permission_id = tool_input.get("permission_id", "").strip()
                if not permission_id:
                    return "Error: permission_id is required for unshare. Use action=list to find it."
                await google_api_execute_async(
                    self._drive_service.permissions().delete(
                        fileId=file_id, permissionId=permission_id
                    )
                )
                return f"Removed permission {permission_id} from {file_id}"

        except Exception as e:
            return f"Error managing permissions: {e}"

    # ── Slack file tools ────────────────────────────────────────────────

    async def _execute_slack_file_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Route and execute Slack file tool calls."""
        if tool_name == "slack_upload_file":
            return await self._slack_upload_file(tool_input)
        elif tool_name == "slack_download_file":
            return await self._slack_download_file(tool_input)
        else:
            return f"Unknown Slack file tool: {tool_name}"

    async def _slack_upload_file(self, tool_input: Dict) -> str:
        if not self.platform or getattr(self.platform, "platform_name", "") != "slack":
            return "Error: Slack file upload requires a Slack platform connection."

        filename = tool_input.get("filename", "").strip()
        if not filename:
            return "Error: filename is required."

        channel_id = tool_input.get("channel_id", "").strip()
        if not channel_id:
            return "Error: channel_id is required."

        # Resolve 'current' channel
        if channel_id == "current":
            if not self.channel_context:
                return "Error: no current channel context available."
            channel_id = self.channel_context["channel_id"]

        try:
            abs_path = self._sandbox.resolve(filename)
            if not abs_path.exists():
                return f"Error: workspace file not found: {filename}"
        except ValueError as e:
            return f"Error: {e}"

        thread_ts = tool_input.get("thread_ts", "").strip() or None
        # Default to current thread if in a threaded conversation
        if not thread_ts and self.channel_context:
            thread_ts = self.channel_context.get("thread_id")

        title = tool_input.get("title", "").strip() or abs_path.name
        initial_comment = tool_input.get("initial_comment", "").strip() or None

        try:
            import asyncio
            response = await asyncio.to_thread(
                self.platform.client.files_upload_v2,
                channel=channel_id,
                file=str(abs_path),
                title=title,
                thread_ts=thread_ts,
                initial_comment=initial_comment,
            )
            file_info = response.get("file", {})
            permalink = file_info.get("permalink", "uploaded")
            return (
                f"Uploaded '{title}' to Slack\n"
                f"  Channel: {channel_id}\n"
                f"  Size: {abs_path.stat().st_size / 1024:.1f} KB\n"
                f"  Link: {permalink}"
            )
        except Exception as e:
            return f"Error uploading file to Slack: {e}"

    async def _slack_download_file(self, tool_input: Dict) -> str:
        if not self.platform or getattr(self.platform, "platform_name", "") != "slack":
            return "Error: Slack file download requires a Slack platform connection."

        file_url = tool_input.get("file_url", "").strip()
        if not file_url:
            return "Error: file_url is required."

        filename = tool_input.get("filename", "").strip()
        if not filename:
            # Auto-detect from URL (last path segment)
            from urllib.parse import urlparse
            path = urlparse(file_url).path
            filename = path.split("/")[-1] if path else "slack_file"

        try:
            out_path = self._sandbox.resolve(filename)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        except ValueError as e:
            return f"Error: {e}"

        try:
            import aiohttp
            headers = {"Authorization": f"Bearer {self.platform.bot_token}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url, headers=headers) as resp:
                    if resp.status != 200:
                        return f"Error: Slack returned HTTP {resp.status} downloading file."
                    data = await resp.read()
                    out_path.write_bytes(data)

            import mimetypes
            mime, _ = mimetypes.guess_type(str(out_path))
            return (
                f"Downloaded to workspace: {filename}\n"
                f"  Size: {len(data) / 1024:.1f} KB\n"
                f"  Type: {mime or 'application/octet-stream'}"
            )
        except Exception as e:
            return f"Error downloading Slack file: {e}"

    # ── Notion tools ────────────────────────────────────────────────────

    async def _web_search(self, tool_input: Dict) -> str:
        """Stub — web_search is now an Anthropic server-side tool."""
        return "Error: web_search is handled server-side by the Anthropic API. This method should not be called."

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
        elif tool_name == "notion_delete_blocks":
            return await self._notion_delete_blocks(tool_input)
        elif tool_name == "notion_get_page":
            return await self._notion_get_page(tool_input)
        elif tool_name == "notion_get_database_schema":
            return await self._notion_get_database_schema(tool_input)
        elif tool_name == "notion_add_comment":
            return await self._notion_add_comment(tool_input)
        elif tool_name == "notion_get_page_property":
            return await self._notion_get_page_property(tool_input)
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
        after = tool_input.get("after")
        if not page_id or not content:
            return "Error: 'page_id' and 'content' are required"

        try:
            blocks = _markdown_to_notion_blocks(content)
            if not blocks:
                return "No blocks parsed from content."

            kwargs = {"block_id": page_id, "children": blocks}
            if after:
                kwargs["after"] = after
            await self._notion_client.blocks.children.append(**kwargs)
            return f"Appended {len(blocks)} blocks to page {page_id}" + (f" after {after}" if after else "")
        except Exception as e:
            return f"Error appending blocks: {e}"

    async def _notion_delete_blocks(self, tool_input: Dict) -> str:
        block_ids = tool_input.get("block_ids", [])
        if not block_ids:
            return "Error: 'block_ids' is required"

        deleted = 0
        errors = []
        for bid in block_ids:
            try:
                await self._notion_client.blocks.delete(block_id=bid)
                deleted += 1
            except Exception as e:
                errors.append(f"{bid}: {e}")

        parts = [f"Deleted {deleted}/{len(block_ids)} blocks"]
        if errors:
            parts.append("Errors: " + "; ".join(errors))
        return ". ".join(parts)

    async def _notion_get_page(self, tool_input: Dict) -> str:
        import json as _json
        page_id = tool_input.get("page_id", "")
        if not page_id:
            return "Error: 'page_id' is required"
        try:
            page = await self._notion_client.pages.retrieve(page_id=page_id)
            # Extract property values into a readable format
            props = {}
            for name, prop in page.get("properties", {}).items():
                prop_type = prop.get("type", "")
                if prop_type == "title":
                    props[name] = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                elif prop_type == "rich_text":
                    props[name] = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
                elif prop_type == "number":
                    props[name] = prop.get("number")
                elif prop_type == "select":
                    sel = prop.get("select")
                    props[name] = sel.get("name") if sel else None
                elif prop_type == "multi_select":
                    props[name] = [s.get("name") for s in prop.get("multi_select", [])]
                elif prop_type == "date":
                    d = prop.get("date")
                    props[name] = d.get("start") if d else None
                elif prop_type == "checkbox":
                    props[name] = prop.get("checkbox")
                elif prop_type == "url":
                    props[name] = prop.get("url")
                elif prop_type == "email":
                    props[name] = prop.get("email")
                elif prop_type == "phone_number":
                    props[name] = prop.get("phone_number")
                elif prop_type == "status":
                    st = prop.get("status")
                    props[name] = st.get("name") if st else None
                elif prop_type == "relation":
                    props[name] = [r.get("id") for r in prop.get("relation", [])]
                elif prop_type == "formula":
                    f = prop.get("formula", {})
                    props[name] = f.get(f.get("type", ""), None)
                elif prop_type == "rollup":
                    r = prop.get("rollup", {})
                    props[name] = f"(rollup: {r.get('type', 'unknown')})"
                elif prop_type == "people":
                    props[name] = [p.get("name", p.get("id", "")) for p in prop.get("people", [])]
                else:
                    props[name] = f"({prop_type})"
            result = {"id": page.get("id"), "url": page.get("url"), "properties": props}
            return _json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"Error retrieving page: {e}"

    async def _notion_get_database_schema(self, tool_input: Dict) -> str:
        import json as _json
        database_id = tool_input.get("database_id", "")
        if not database_id:
            return "Error: 'database_id' is required"
        try:
            db = await self._notion_client.databases.retrieve(database_id=database_id)
            title = "".join(t.get("plain_text", "") for t in db.get("title", []))
            schema = {}
            for name, prop in db.get("properties", {}).items():
                prop_info = {"type": prop.get("type", "unknown"), "id": prop.get("id", "")}
                if prop.get("type") == "select":
                    prop_info["options"] = [o.get("name") for o in prop.get("select", {}).get("options", [])]
                elif prop.get("type") == "multi_select":
                    prop_info["options"] = [o.get("name") for o in prop.get("multi_select", {}).get("options", [])]
                elif prop.get("type") == "status":
                    prop_info["options"] = [o.get("name") for o in prop.get("status", {}).get("options", [])]
                    prop_info["groups"] = [g.get("name") for g in prop.get("status", {}).get("groups", [])]
                schema[name] = prop_info
            result = {"title": title, "id": db.get("id"), "properties": schema}
            return _json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"Error retrieving database schema: {e}"

    async def _notion_add_comment(self, tool_input: Dict) -> str:
        page_id = tool_input.get("page_id", "")
        text = tool_input.get("text", "")
        block_id = tool_input.get("block_id")
        if not page_id or not text:
            return "Error: 'page_id' and 'text' are required"
        try:
            body = {
                "parent": {"page_id": page_id},
                "rich_text": [{"type": "text", "text": {"content": text}}],
            }
            if block_id:
                body["discussion_id"] = block_id
            await self._notion_client.comments.create(**body)
            return f"Comment added to page {page_id}"
        except Exception as e:
            return f"Error adding comment: {e}"

    async def _notion_get_page_property(self, tool_input: Dict) -> str:
        import json as _json
        page_id = tool_input.get("page_id", "")
        property_id = tool_input.get("property_id", "")
        if not page_id or not property_id:
            return "Error: 'page_id' and 'property_id' are required"
        try:
            result = await self._notion_client.pages.properties.retrieve(
                page_id=page_id, property_id=property_id
            )
            return _json.dumps(result, indent=2, default=str)
        except Exception as e:
            return f"Error retrieving property: {e}"

    # ── Config tools ────────────────────────────────────────────────────

    async def _list_source_types(self) -> str:
        source_types = [
            {"type": "notion", "description": "Notion databases and pages", "id_format": "Database ID (from the URL)"},
            {"type": "discord", "description": "Discord server channels", "id_format": "Server ID (right-click server → Copy Server ID)"},
            {"type": "gmail", "description": "Gmail email threads", "id_format": "Gmail address (e.g. user@gmail.com)"},
            {"type": "slack", "description": "Slack workspace channels", "id_format": "Auto-generated (no ID needed)"},
            {"type": "shopify", "description": "Shopify store data", "id_format": "Shop domain (e.g. my-store.myshopify.com)"},
            {"type": "google_sheets", "description": "Google Sheets spreadsheets", "id_format": "Sheet/Folder ID or 'root' for all"},
            {"type": "google_calendar", "description": "Google Calendar events", "id_format": "Calendar ID or 'primary'"},
        ]
        lines = ["Available source types:\n"]
        for st in source_types:
            lines.append(f"- **{st['type']}**: {st['description']}")
            lines.append(f"  ID format: {st['id_format']}")
        return "\n".join(lines)

    async def _list_workspaces(self) -> str:
        try:
            from promaia.config.workspaces import WorkspaceManager
            wm = WorkspaceManager()
            workspaces = wm.list_workspaces()
            default_ws = wm.get_default_workspace()
            if not workspaces:
                return "No workspaces configured. Use add_workspace to create one."
            lines = ["Configured workspaces:\n"]
            for ws in workspaces:
                marker = " (default)" if ws == default_ws else ""
                lines.append(f"- {ws}{marker}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing workspaces: {e}"

    async def _add_workspace(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip().lower()
        if not name:
            return "Error: workspace name is required."
        description = tool_input.get("description", "")
        try:
            from promaia.config.workspaces import WorkspaceManager
            wm = WorkspaceManager()
            success = wm.add_workspace(name, description=description)
            if success:
                return f"Workspace '{name}' created successfully."
            else:
                return f"Workspace '{name}' already exists."
        except Exception as e:
            return f"Error creating workspace: {e}"

    async def _discover_source_name(self, tool_input: Dict) -> str:
        source_type = tool_input.get("source_type", "")
        database_id = tool_input.get("database_id", "")
        workspace = tool_input.get("workspace", self.workspace)
        if not source_type or not database_id:
            return "Error: source_type and database_id are required."
        try:
            from promaia.cli.database_commands import _discover_source_name as discover
            name = await discover(source_type, database_id, workspace)
            if name:
                return f"Discovered source name: {name}"
            else:
                return "Could not discover source name (API unreachable or credentials missing). Ask the user for a nickname."
        except Exception as e:
            return f"Error discovering source name: {e}"

    async def _check_credential(self, tool_input: Dict) -> str:
        integration_name = tool_input.get("integration", "")
        workspace = tool_input.get("workspace", self.workspace)
        if not integration_name:
            return "Error: integration name is required."
        try:
            from promaia.auth.registry import get_integration
            integration = get_integration(integration_name)
            if integration is None:
                return f"Unknown integration: {integration_name}. Available: notion, google, discord, slack, shopify, anthropic, openai"

            # Check for credentials based on integration type
            if integration_name == "notion":
                cred = integration.get_notion_credentials(workspace)
            elif integration_name == "google":
                cred = integration.get_default_credential()
            elif integration_name == "discord":
                cred = integration.get_discord_token(workspace) if hasattr(integration, 'get_discord_token') else integration.get_default_credential()
            else:
                cred = integration.get_default_credential()

            if cred:
                return f"Credentials for '{integration_name}' are configured and available."
            else:
                return (
                    f"No credentials found for '{integration_name}'. "
                    f"The user needs to run: maia auth configure {integration_name}"
                )
        except Exception as e:
            return f"Error checking credential: {e}"

    async def _register_database(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        source_type = tool_input.get("source_type", "").strip()
        database_id = tool_input.get("database_id", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        description = tool_input.get("description", "")

        if not all([name, source_type, database_id, workspace]):
            return "Error: name, source_type, database_id, and workspace are all required."

        valid_types = {"notion", "discord", "gmail", "slack", "shopify", "google_sheets", "google_calendar"}
        if source_type not in valid_types:
            return f"Error: invalid source_type '{source_type}'. Must be one of: {', '.join(sorted(valid_types))}"

        config = {
            "source_type": source_type,
            "database_id": database_id,
            "description": description,
            "workspace": workspace,
            "sync_enabled": True,
            "include_properties": True,
            "default_days": 30 if source_type == "shopify" else 7,
            "save_markdown": source_type != "shopify",
        }

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            success = db_manager.add_database(name, config, workspace)
            if success:
                return (
                    f"Database '{name}' registered successfully.\n"
                    f"  Source type: {source_type}\n"
                    f"  ID: {database_id}\n"
                    f"  Workspace: {workspace}\n"
                    f"You can now run `maia database sync {name}` to sync it."
                )
            else:
                return f"Database '{name}' already exists in workspace '{workspace}'. Choose a different name."
        except Exception as e:
            return f"Error registering database: {e}"

    async def _test_connection(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        if not name:
            return "Error: database name is required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            db_config = db_manager.get_database(name, workspace)
            if not db_config:
                return f"Database '{name}' not found in workspace '{workspace}'."

            from promaia.connectors.base import ConnectorRegistry
            source_type = db_config.source_type

            # Build connector config
            connector_config = {
                "database_id": db_config.database_id,
                "workspace": workspace,
                "nickname": name,
            }

            # Add credentials for connector
            if source_type == "notion":
                from promaia.auth.registry import get_integration
                token = get_integration("notion").get_notion_credentials(workspace)
                if token:
                    connector_config["api_key"] = token
            elif source_type == "discord":
                from promaia.auth.registry import get_integration
                discord_int = get_integration("discord")
                if hasattr(discord_int, 'get_discord_token'):
                    token = discord_int.get_discord_token(workspace)
                    if token:
                        connector_config["bot_token"] = token

            connector = ConnectorRegistry.get_connector(source_type, connector_config)
            if connector is None:
                return f"No connector available for source type '{source_type}'. The required package may not be installed."

            result = await connector.test_connection()
            if result:
                return f"Connection test PASSED for '{name}' ({source_type})."
            else:
                return f"Connection test FAILED for '{name}' ({source_type}). Check credentials and source ID."
        except ImportError as e:
            return f"Connector for '{name}' not available (missing dependency): {e}"
        except Exception as e:
            return f"Connection test error for '{name}': {e}"

    async def _sync_database(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        days = tool_input.get("days", 7)
        if not name:
            return "Error: database name is required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()

            if name.lower() == "all":
                # Sync all databases in workspace
                db_names = db_manager.list_databases(workspace=workspace)
                if not db_names:
                    return f"No databases found in workspace '{workspace}'."
                results = []
                for db_name in db_names:
                    result = await self._sync_single_database(db_manager, db_name, workspace, days)
                    results.append(f"- {db_name}: {result}")
                return f"Sync results:\n" + "\n".join(results)
            else:
                result = await self._sync_single_database(db_manager, name, workspace, days)
                return result
        except Exception as e:
            return f"Error syncing database: {e}"

    async def _sync_single_database(self, db_manager, name: str, workspace: str, days: int) -> str:
        """Sync a single database source."""
        db_config = db_manager.get_database(name, workspace)
        if not db_config:
            return f"Database '{name}' not found in workspace '{workspace}'."

        source_type = db_config.source_type
        database_id = db_config.database_id

        try:
            from promaia.connectors.base import ConnectorRegistry

            # Build connector config
            connector_config = {
                "database_id": database_id,
                "workspace": workspace,
                "nickname": name,
                "default_days": days,
            }

            # Add credentials
            from promaia.auth.registry import get_integration
            if source_type == "notion":
                token = get_integration("notion").get_notion_credentials(workspace)
                if token:
                    connector_config["api_key"] = token
                else:
                    return f"No Notion credentials for workspace '{workspace}'. Run: maia auth configure notion"
            elif source_type in ("gmail", "google_sheets", "google_calendar"):
                cred = get_integration("google").get_default_credential()
                if not cred:
                    return f"No Google credentials found. Run: maia auth configure google"
            elif source_type == "discord":
                discord_int = get_integration("discord")
                if hasattr(discord_int, 'get_discord_token'):
                    token = discord_int.get_discord_token(workspace)
                    if token:
                        connector_config["bot_token"] = token

            connector = ConnectorRegistry.get_connector(source_type, connector_config)
            if connector is None:
                return f"No connector available for '{source_type}'."

            # Run the sync
            from datetime import datetime, timedelta
            start_date = datetime.now() - timedelta(days=days)
            sync_result = await connector.sync(start_date=start_date)

            if sync_result:
                pages_synced = getattr(sync_result, 'pages_synced', None)
                if pages_synced is not None:
                    return f"Synced '{name}' ({source_type}): {pages_synced} pages"
                return f"Synced '{name}' ({source_type}) successfully."
            else:
                return f"Sync of '{name}' returned no result (may have completed with warnings)."
        except Exception as e:
            return f"Sync failed for '{name}': {e}"

    async def _list_databases(self, tool_input: Dict) -> str:
        workspace = tool_input.get("workspace", self.workspace).strip() or None
        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            all_dbs = db_manager.databases

            if not all_dbs:
                return "No databases configured."

            lines = ["Configured databases:\n"]
            for qualified_name, db_config in sorted(all_dbs.items()):
                if workspace and db_config.workspace != workspace:
                    continue
                sync_status = "enabled" if db_config.sync_enabled else "disabled"
                last_sync = getattr(db_config, 'last_sync_time', None)
                sync_info = f", last sync: {last_sync}" if last_sync else ""
                lines.append(
                    f"- **{qualified_name}** ({db_config.source_type}) "
                    f"[{sync_status}{sync_info}]"
                )

            if len(lines) == 1:
                return f"No databases found for workspace '{workspace}'." if workspace else "No databases configured."

            return "\n".join(lines)
        except Exception as e:
            return f"Error listing databases: {e}"

    async def _rename_database(self, tool_input: Dict) -> str:
        old_name = tool_input.get("old_name", "").strip()
        new_name = tool_input.get("new_name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        if not old_name or not new_name:
            return "Error: old_name and new_name are required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            db_config = db_manager.get_database(old_name, workspace)
            if not db_config:
                return f"Database '{old_name}' not found in workspace '{workspace}'."

            # Check new name doesn't exist
            existing = db_manager.get_database(new_name, workspace)
            if existing:
                return f"Database '{new_name}' already exists in workspace '{workspace}'."

            # Build config dict from existing config
            config_data = {
                "source_type": db_config.source_type,
                "database_id": db_config.database_id,
                "description": db_config.description or "",
                "workspace": db_config.workspace,
                "sync_enabled": db_config.sync_enabled,
                "include_properties": db_config.include_properties,
                "default_days": db_config.default_days,
                "save_markdown": db_config.save_markdown,
                "property_filters": db_config.property_filters or {},
                "date_filters": db_config.date_filters or {},
            }

            # Add new, remove old
            success = db_manager.add_database(new_name, config_data, workspace)
            if not success:
                return f"Failed to create '{new_name}'."

            db_manager.remove_database(old_name, workspace)
            return f"Renamed '{old_name}' to '{new_name}' in workspace '{workspace}'."
        except Exception as e:
            return f"Error renaming database: {e}"

    # ── Notepad (persistent working notes) ─────────────────────────────

    def _notepad_action(self, tool_input: Dict) -> str:
        action = tool_input.get("action", "write")
        content = tool_input.get("content", "")

        if action == "write":
            if not content:
                return "Error: content is required for write action."
            self._notepad = content
            return f"Notes updated ({len(content)} chars)."
        elif action == "append":
            if not content:
                return "Error: content is required for append action."
            if self._notepad:
                self._notepad += "\n\n" + content
            else:
                self._notepad = content
            return f"Appended to notes ({len(self._notepad)} chars total)."
        elif action == "clear":
            self._notepad = ""
            return "Notes cleared."
        else:
            return f"Unknown notepad action: {action}"

    def _memory_action(self, tool_input: Dict) -> str:
        from promaia.agents.memory_store import (
            load_memory_index, load_memory_file, save_memory, delete_memory,
        )
        action = tool_input.get("action", "list")
        name = tool_input.get("name", "").strip()

        if action == "save":
            content = tool_input.get("content", "")
            mem_type = tool_input.get("type", "project")
            return save_memory(self.workspace, name, content, mem_type)
        elif action == "recall":
            if not name:
                return "Error: provide a memory name to recall."
            return load_memory_file(self.workspace, name)
        elif action == "list":
            index = load_memory_index(self.workspace)
            return index if index else "No memories saved yet."
        elif action == "delete":
            if not name:
                return "Error: provide a memory name to delete."
            return delete_memory(self.workspace, name)
        else:
            return f"Unknown memory action: {action}"

    # ── Context source management ────────────────────────────────────────

    def _context_action(self, tool_input: Dict) -> str:
        action = tool_input.get("action", "on")

        if action == "on":
            names = tool_input.get("sources", []) or tool_input.get("shelves", [])
            if not names:
                return "Error: provide source names to turn on."
            turned_on = []
            for name in names:
                if name in self._sources:
                    self._sources[name]["on"] = True
                    self._sources[name]["mounted_at_iteration"] = self._current_iteration
                    turned_on.append(name)
            if turned_on:
                return f"Context ON: {', '.join(turned_on)}. Content now visible."
            return f"No matching sources. Available: {', '.join(self._sources.keys())}"

        elif action == "off":
            names = tool_input.get("sources", []) or tool_input.get("shelves", [])
            if not names:
                return "Error: provide source names to turn off."
            turned_off = []
            for name in names:
                if name in self._sources:
                    self._sources[name]["on"] = False
                    turned_off.append(name)
            if turned_off:
                return f"Context OFF: {', '.join(turned_off)}. Content hidden."
            return f"No matching sources. Available: {', '.join(self._sources.keys())}"

        elif action == "all_on":
            for src in self._sources.values():
                src["on"] = True
            return f"All {len(self._sources)} sources turned ON."

        elif action == "all_off":
            for src in self._sources.values():
                src["on"] = False
            return f"All {len(self._sources)} sources turned OFF."

        elif action == "add":
            name = tool_input.get("name", "").strip()
            content = tool_input.get("content", "")
            if not name:
                return "Error: source name is required."
            if not content:
                return "Error: source content is required."
            page_count = content.count("\n**") + 1
            self._sources[name] = {
                "content": content,
                "on": False,
                "page_count": page_count,
                "source": "manual",
            }
            return f"Context source '{name}' added ({len(content)} chars). Turn it ON to include in context."

        elif action == "remove":
            names = tool_input.get("sources", []) or tool_input.get("shelves", [])
            name = tool_input.get("name", "").strip()
            to_remove = names if names else ([name] if name else [])
            if not to_remove:
                return "Error: provide source name(s) to remove."
            removed = []
            for n in to_remove:
                if n in self._sources:
                    del self._sources[n]
                    removed.append(n)
            if removed:
                return f"Sources removed: {', '.join(removed)}"
            return "No matching sources found."

        return f"Unknown context action: {action}"

    @staticmethod
    def _extract_titles(pages_dict: dict) -> list:
        """Extract page titles from a loaded_content dict ({db_name: [page, ...]})."""
        titles = []
        for pages in pages_dict.values():
            for page in pages:
                title = (page.get("title") or page.get("filename")
                         or page.get("name") or "")
                if title:
                    titles.append(title)
        return titles

    def build_context_index(self) -> str:
        """Build the context index string for the system prompt."""
        if self._sources_muted or not self._sources:
            return ""
        lines = [
            "## Your Context\n",
            "Toggle sources ON/OFF with the `context` tool.",
            "Check here BEFORE searching — the data you need may already be loaded.\n",
        ]
        for name, src in self._sources.items():
            state = "ON" if src["on"] else "OFF"
            origin = src.get("source", "unknown")
            count = src.get("page_count", 0)
            chars = len(src.get("content", ""))
            lines.append(f"- [{state}] **{name}** ({count} entries, {chars // 1000}k chars, source: {origin})")
            # OFF sources show titles so agent knows what's available without loading
            if not src["on"]:
                titles = src.get("titles", [])
                for t in titles:
                    lines.append(f"  - {t}")
        return "\n".join(lines)

    def build_active_source_content(self) -> str:
        """Build the combined content of all ON sources for the system prompt."""
        if self._sources_muted:
            return ""
        parts = []
        for name, src in self._sources.items():
            if src["on"] and src.get("content"):
                parts.append(src["content"])
        return "\n\n".join(parts)

    # ── Act-mode tool result shelving ───────────────────────────────────
    #
    # When act mode exits via __DONE__, all tool_result blocks produced
    # during the burst are moved into the _sources shelf system. The
    # full payload is registered as an ON source (so think mode sees it
    # immediately via build_active_source_content) and the inline
    # tool_result block is replaced with a short stub. The matching
    # tool_use block is left untouched, so workflow capture
    # (all_tool_calls[]) is unaffected.
    #
    # Tiny results (control acks, "done", error strings) stay inline.

    _SHELVE_MIN_CHARS = 500

    def shelve_act_results(
        self,
        tool_use_ids: List[str],
        internal_messages: List[Dict],
        current_iteration: int,
    ) -> int:
        """Shelve every tool_result whose id is in tool_use_ids.

        Returns the number of results shelved. Stubs replace the inline
        content; the registered source is ON so think mode sees it
        immediately on its next turn.
        """
        if not tool_use_ids:
            return 0

        id_set = set(tool_use_ids)
        shelved = 0

        # Walk newest → oldest so multiple bursts behave predictably.
        for msg in reversed(internal_messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                tu_id = block.get("tool_use_id")
                if tu_id not in id_set:
                    continue
                result_text = block.get("content", "")
                if not isinstance(result_text, str):
                    continue
                if len(result_text) < self._SHELVE_MIN_CHARS:
                    continue
                # Skip if already a stub (idempotent)
                if result_text.startswith("[tool result shelved]"):
                    continue

                tool_name = self._lookup_tool_name(internal_messages, tu_id)
                source_id = self._make_act_source_id(tool_name, tu_id)
                title = self._make_act_source_title(tool_name, tu_id, internal_messages)

                self._sources[source_id] = {
                    "content": result_text,
                    "on": True,
                    "page_count": 1,
                    "source": tool_name or "act_tool",
                    "titles": [title] if title else [],
                    "mounted_at_iteration": current_iteration,
                }

                stub = (
                    f"[tool result shelved] source_id={source_id} "
                    f"tool={tool_name or 'unknown'} size={len(result_text)} chars\n"
                    f"Call turn_on_source if you need to re-read this."
                )
                block["content"] = stub
                shelved += 1

        if shelved:
            logger.info(
                f"[shelve_act_results] Shelved {shelved} act-mode tool result(s) "
                f"into _sources at iteration {current_iteration}"
            )
        return shelved

    @staticmethod
    def _lookup_tool_name(internal_messages: List[Dict], tool_use_id: str) -> str:
        """Find the tool_use block matching tool_use_id and return its name."""
        for msg in internal_messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id
                ):
                    return block.get("name", "")
        return ""

    @staticmethod
    def _make_act_source_id(tool_name: str, tool_use_id: str) -> str:
        suffix = (tool_use_id or "").split("_")[-1][:8] or "x"
        safe_name = (tool_name or "act_tool").replace("__", "_")
        return f"act_{safe_name}_{suffix}"

    @staticmethod
    def _make_act_source_title(
        tool_name: str, tool_use_id: str, internal_messages: List[Dict]
    ) -> str:
        """Build a short human-readable title from the tool's input args."""
        for msg in internal_messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id
                ):
                    inp = block.get("input") or {}
                    if isinstance(inp, dict):
                        for key in ("query", "q", "name", "range", "thread_id", "id"):
                            if key in inp and inp[key]:
                                return f"{tool_name}({inp[key]!s:.60})"
                        if inp:
                            first_k, first_v = next(iter(inp.items()))
                            return f"{tool_name}({first_k}={first_v!s:.40})"
                    return tool_name or ""
        return tool_name or ""

    # ── Workspace file tools ────────────────────────────────────────────

    async def _list_workspace_files(self) -> str:
        try:
            files = self._sandbox.list_files()
            if not files:
                return "Workspace is empty — no files yet."
            lines = [f"Workspace files ({len(files)}):\n"]
            for f in files:
                size_kb = f["size_bytes"] / 1024
                lines.append(
                    f"  - {f['path']} ({size_kb:.1f} KB, {f['mime_type']})"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing workspace files: {e}"

    # ── Workflow tools ───────────────────────────────────────────────────

    async def _create_workflow(self, tool_input: Dict) -> str:
        try:
            from promaia.tools.workflow_store import create_workflow

            name = tool_input.get("name", "").strip()
            description = tool_input.get("description", "").strip()
            steps = tool_input.get("steps", [])

            if not name:
                return "Error: workflow name is required."
            if not description:
                return "Error: workflow description is required."
            if not steps:
                return "Error: at least one step is required."

            result = create_workflow(
                name=name,
                description=description,
                steps=steps,
                workspace=tool_input.get("workspace", self.workspace),
                example_run=tool_input.get("example_run"),
            )

            msg = f"Workflow '{name}' created (ID: {result['id']})."
            if result.get("example_run_id"):
                msg += f" Example run saved."
            return msg
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error creating workflow: {e}"

    async def _list_saved_workflows(self) -> str:
        try:
            from promaia.tools.workflow_store import list_saved_workflows

            workflows = list_saved_workflows(self.workspace)
            if not workflows:
                return "No saved workflows."

            lines = [f"Saved workflows ({len(workflows)}):\n"]
            for wf in workflows:
                ws = f" [{wf['workspace']}]" if wf.get("workspace") else " [global]"
                lines.append(f"  - **{wf['name']}**{ws}: {wf['description']}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing workflows: {e}"

    async def _get_workflow_details(self, tool_input: Dict) -> str:
        try:
            from promaia.tools.workflow_store import get_workflow_details
            import json

            name = tool_input.get("name", "").strip()
            if not name:
                return "Error: workflow name is required."

            wf = get_workflow_details(name)
            if not wf:
                return f"Workflow '{name}' not found."

            lines = [
                f"**{wf['name']}**",
                f"Description: {wf['description']}",
                f"Workspace: {wf.get('workspace') or 'global'}",
                f"Created: {wf['created_at']}",
                f"Updated: {wf['updated_at']}",
                "",
                f"**Steps ({len(wf['steps'])}):**",
            ]
            for i, step in enumerate(wf["steps"], 1):
                tool = step.get("tool", "manual")
                lines.append(f"  {i}. {step['description']} (tool: {tool})")
                if step.get("params_template"):
                    lines.append(f"     Params: {json.dumps(step['params_template'])}")
                if step.get("variable_params"):
                    lines.append(f"     Variable: {', '.join(step['variable_params'])}")
                if step.get("notes"):
                    lines.append(f"     Notes: {step['notes']}")

            runs = wf.get("example_runs", [])
            if runs:
                lines.append(f"\n**Example runs ({len(runs)}):**")
                for run in runs:
                    lines.append(f"  - [{run['outcome']}] {run.get('notes', 'No notes')}")
                    for tc in run.get("tool_calls", []):
                        lines.append(f"    → {tc['tool']}: {tc.get('result_summary', '')}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error getting workflow details: {e}"

    async def _update_workflow(self, tool_input: Dict) -> str:
        try:
            from promaia.tools.workflow_store import update_workflow

            name = tool_input.get("name", "").strip()
            if not name:
                return "Error: workflow name is required."

            return update_workflow(
                name=name,
                description=tool_input.get("description"),
                steps=tool_input.get("steps"),
                add_example_run=tool_input.get("add_example_run"),
            )
        except Exception as e:
            return f"Error updating workflow: {e}"

    async def _delete_workflow(self, tool_input: Dict) -> str:
        try:
            from promaia.tools.workflow_store import delete_workflow

            name = tool_input.get("name", "").strip()
            if not name:
                return "Error: workflow name is required."

            return delete_workflow(name)
        except Exception as e:
            return f"Error deleting workflow: {e}"

    # ── External MCP server tools ────────────────────────────────────────

    # Names of built-in tool servers to skip when connecting external MCP servers
    _BUILTIN_SERVER_NAMES = {"notion", "gmail", "calendar", "query_tools", "promaia"}

    async def connect_mcp_servers(self):
        """Connect to enabled external MCP servers and discover their tools."""
        try:
            from promaia.mcp.client import McpClient
            from promaia.agents.mcp_loader import _find_mcp_servers_json
            from promaia.config.mcp_servers import McpServerManager

            config_path = _find_mcp_servers_json()
            if not config_path:
                logger.debug("No mcp_servers.json found, skipping MCP connections")
                return

            manager = McpServerManager(str(config_path))
            enabled = manager.get_enabled_servers()
            if not enabled:
                return

            self._mcp_client = McpClient()

            for name, config in enabled.items():
                # Skip built-in servers — those are hardcoded in ToolExecutor
                if name.lower() in self._BUILTIN_SERVER_NAMES:
                    continue

                # Inject sandbox path so MCP servers can write files there
                if config.env is None:
                    config.env = {}
                config.env["PROMAIA_SANDBOX_DIR"] = str(self._sandbox.root)

                try:
                    connected = await self._mcp_client.connect_to_server(config)
                    if connected:
                        logger.info(f"MCP server connected: {name}")
                    else:
                        logger.warning(f"MCP server failed to connect: {name}")
                except Exception as e:
                    logger.warning(f"MCP server {name} connection error: {e}")

        except Exception as e:
            logger.warning(f"MCP server setup failed (continuing without): {e}")

    async def get_mcp_tool_definitions(self) -> list:
        """Return Anthropic-formatted tool definitions from connected MCP servers."""
        if not self._mcp_client:
            return []

        tools = self._mcp_client.get_all_tools()
        if not tools:
            return []

        definitions = []
        for tool in tools:
            namespaced = f"mcp__{tool.server_name}__{tool.name}"
            self._mcp_tool_map[namespaced] = (tool.server_name, tool.name)
            definitions.append({
                "name": namespaced,
                "description": f"[{tool.server_name}] {tool.description}",
                "input_schema": tool.input_schema,
            })

        logger.info(f"Discovered {len(definitions)} MCP tools from external servers")
        return definitions

    async def _execute_mcp_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Execute a tool on an external MCP server."""
        mapping = self._mcp_tool_map.get(tool_name)
        if not mapping:
            return f"Error: unknown MCP tool '{tool_name}'"

        server_name, original_name = mapping
        protocol_client = self._mcp_client.connected_servers.get(server_name)
        if not protocol_client:
            return f"Error: MCP server '{server_name}' is not connected"

        try:
            result = await protocol_client.call_tool(original_name, tool_input)
            if result is None:
                return "MCP tool returned no result."

            # Extract text from content blocks
            content_blocks = result.get("content", [])
            texts = []
            for block in content_blocks:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))

            output = "\n".join(texts) if texts else "MCP tool returned empty result."

            if result.get("isError"):
                return f"Error from {server_name}: {output}"

            return output
        except Exception as e:
            return f"Error calling MCP tool {original_name} on {server_name}: {e}"

    async def disconnect_mcp_servers(self):
        """Clean up all MCP server connections."""
        if self._mcp_client:
            try:
                await self._mcp_client.disconnect_all()
            except Exception as e:
                logger.debug(f"MCP disconnect error (non-critical): {e}")
            self._mcp_client = None
            self._mcp_tool_map = {}

    # ── Agent tools ─────────────────────────────────────────────────────

    async def _list_agents(self) -> str:
        try:
            from promaia.agents.agent_config import load_agents
            agents = load_agents()
            if not agents:
                return "No scheduled agents configured."
            lines = ["Scheduled agents:\n"]
            for a in agents:
                status = "enabled" if a.enabled else "disabled"
                schedule = f"every {a.interval_minutes}min" if a.interval_minutes else "grid schedule"
                dbs = ", ".join(a.databases[:3]) if a.databases else "none"
                if len(a.databases) > 3:
                    dbs += f" (+{len(a.databases) - 3} more)"
                last_run = a.last_run_at or "never"
                lines.append(
                    f"- **{a.name}** [{status}] — {schedule}\n"
                    f"  Workspace: {a.workspace} | Sources: {dbs} | Last run: {last_run}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing agents: {e}"

    async def _agent_info(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        try:
            from promaia.agents.agent_config import get_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."
            lines = [
                f"**{agent.name}**",
                f"  ID: {agent.agent_id}",
                f"  Workspace: {agent.workspace}",
                f"  Status: {'enabled' if agent.enabled else 'disabled'}",
                f"  Databases: {', '.join(agent.databases) if agent.databases else 'none'}",
                f"  Tools: {', '.join(agent.mcp_tools) if agent.mcp_tools else 'none'}",
                f"  Max iterations: {agent.max_iterations}",
            ]
            # Append connected external MCP servers
            try:
                from promaia.config.mcp_servers import McpServerManager
                mgr = McpServerManager()
                ext_names = [name for name, srv in mgr.servers.items() if srv.enabled]
                if ext_names:
                    lines.append(f"  External MCP servers: {', '.join(ext_names)}")
            except Exception:
                pass
            if agent.interval_minutes:
                lines.append(f"  Interval: every {agent.interval_minutes} minutes")
            if agent.schedule:
                lines.append(f"  Schedule: {agent.schedule}")
            if agent.description:
                lines.append(f"  Description: {agent.description}")
            if agent.last_run_at:
                lines.append(f"  Last run: {agent.last_run_at}")
            if agent.calendar_id:
                lines.append(f"  Calendar: {agent.calendar_id}")
            if agent.prompt_file:
                prompt_preview = agent.prompt_file[:200]
                if len(agent.prompt_file) > 200:
                    prompt_preview += "..."
                lines.append(f"  Prompt: {prompt_preview}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting agent info: {e}"

    def _refuse_self_edit(self, target_name: str) -> Optional[str]:
        """Block agents from editing themselves.

        Returns a refusal message if `target_name` resolves to the currently-
        running agent, else None. Keys on `agent_id` (the immutable identity);
        falls back to `name` comparison if either id is empty as a safety net
        during backfill.

        TODO: drop the name-fallback branch once all environments have
        non-empty agent_id on every agent.
        """
        try:
            from promaia.agents.agent_config import get_agent
            target = get_agent(target_name)
        except Exception:
            return None
        if not target:
            return None
        self_id = (getattr(self.agent, "agent_id", "") or "").strip()
        target_id = (getattr(target, "agent_id", "") or "").strip()
        is_self = False
        if self_id and target_id:
            is_self = self_id == target_id
        else:
            is_self = getattr(self.agent, "name", None) == getattr(target, "name", None)
        if is_self:
            return (
                "Refused: agents cannot edit themselves. "
                "Only the user can modify this agent directly."
            )
        return None

    async def _enable_agent(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        refusal = self._refuse_self_edit(name)
        if refusal:
            return refusal
        try:
            from promaia.agents.agent_config import get_agent, save_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."
            if agent.enabled:
                return f"Agent '{name}' is already enabled."
            agent.enabled = True
            save_agent(agent)
            return f"Agent '{name}' enabled."
        except Exception as e:
            return f"Error enabling agent: {e}"

    async def _disable_agent(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        refusal = self._refuse_self_edit(name)
        if refusal:
            return refusal
        try:
            from promaia.agents.agent_config import get_agent, save_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."
            if not agent.enabled:
                return f"Agent '{name}' is already disabled."
            agent.enabled = False
            save_agent(agent)
            return f"Agent '{name}' disabled."
        except Exception as e:
            return f"Error disabling agent: {e}"

    async def _rename_agent(self, tool_input: Dict) -> str:
        old_name = tool_input.get("old_name", "").strip()
        new_name = tool_input.get("new_name", "").strip()
        if not old_name or not new_name:
            return "Error: old_name and new_name are required."
        refusal = self._refuse_self_edit(old_name)
        if refusal:
            return refusal
        try:
            from promaia.agents.agent_config import get_agent, save_agent, load_agents
            agent = get_agent(old_name)
            if not agent:
                return f"Agent '{old_name}' not found."
            existing = get_agent(new_name)
            if existing:
                return f"Agent '{new_name}' already exists."

            # agent_id stays the same (UUID or legacy) — only display name changes
            old_display = agent.name
            agent.name = new_name

            # Save updated config (save_agent matches by name, so we need to
            # remove old entry and add new one)
            import json as _json
            from promaia.agents.agent_config import _get_agents_file, load_config, save_config

            # Update agents.json
            agents_file = _get_agents_file()
            if agents_file.exists():
                with open(agents_file, 'r') as f:
                    agents_data = _json.load(f)
                agents_list = agents_data.get('agents', [])
                for i, a in enumerate(agents_list):
                    if a.get('name') == old_display:
                        agents_list[i] = agent.to_dict()
                        break
                with open(agents_file, 'w') as f:
                    _json.dump(agents_data, f, indent=2)

            # Update promaia.config.json
            config = load_config()
            for i, a in enumerate(config.get('agents', [])):
                if a.get('name') == old_display:
                    config['agents'][i] = agent.to_dict()
                    break
            save_config(config)

            updates = [f"Config: '{old_display}' → '{new_name}'"]

            # Update Notion page title if we have a page ID
            if agent.notion_page_id:
                try:
                    from promaia.notion.client import get_client
                    client = get_client(agent.workspace)
                    await client.pages.update(
                        page_id=agent.notion_page_id,
                        properties={
                            "Name": {"title": [{"text": {"content": new_name}}]},
                        }
                    )
                    updates.append("Notion page title updated")
                except Exception as e:
                    updates.append(f"Notion update failed: {e}")

            # Update Google Calendar name if we have a calendar
            if agent.calendar_id:
                try:
                    from promaia.gcal.google_calendar import GoogleCalendarManager
                    gcal = GoogleCalendarManager()
                    if gcal.authenticate():
                        gcal.service.calendars().patch(
                            calendarId=agent.calendar_id,
                            body={
                                'summary': new_name,
                                'description': f"Automated schedule for {new_name} agent",
                            }
                        ).execute()
                        updates.append("Google Calendar renamed")
                except Exception as e:
                    updates.append(f"Calendar rename failed: {e}")

            return f"Agent renamed: {old_display} → {new_name}\n" + "\n".join(f"  - {u}" for u in updates)
        except Exception as e:
            return f"Error renaming agent: {e}"

    async def _update_agent(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        refusal = self._refuse_self_edit(name)
        if refusal:
            return refusal
        try:
            from promaia.agents.agent_config import get_agent, save_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."

            changes = []
            if "description" in tool_input:
                agent.description = tool_input["description"]
                changes.append("description")
            if "databases" in tool_input:
                agent.databases = tool_input["databases"]
                changes.append("databases")
            if "mcp_tools" in tool_input:
                agent.mcp_tools = tool_input["mcp_tools"]
                changes.append("mcp_tools")
            if "interval_minutes" in tool_input:
                agent.interval_minutes = tool_input["interval_minutes"]
                changes.append("interval")
            if "max_iterations" in tool_input:
                agent.max_iterations = tool_input["max_iterations"]
                changes.append("max_iterations")
            if "prompt" in tool_input:
                agent.prompt_file = tool_input["prompt"]
                changes.append("prompt")
            if "messaging_enabled" in tool_input:
                agent.messaging_enabled = bool(tool_input["messaging_enabled"])
                changes.append("messaging_enabled")
            if "allowed_channel_ids" in tool_input:
                ids = tool_input["allowed_channel_ids"]
                agent.allowed_channel_ids = ids if ids else None
                changes.append("allowed_channel_ids")

            if not changes:
                return "No fields to update were provided."

            save_agent(agent)
            return f"Agent '{name}' updated: {', '.join(changes)}"
        except Exception as e:
            return f"Error updating agent: {e}"

    async def _remove_agent(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        refusal = self._refuse_self_edit(name)
        if refusal:
            return refusal
        try:
            from promaia.agents.agent_config import get_agent, delete_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."
            if getattr(agent, 'is_default_agent', False):
                return f"Cannot delete '{name}' — it's the default system agent. You can edit it with update_agent instead."
            deleted = delete_agent(name)
            if deleted:
                return f"Agent '{name}' removed and resources cleaned up."
            else:
                return f"Failed to remove agent '{name}'."
        except Exception as e:
            return f"Error removing agent: {e}"

    async def _run_agent(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."
        try:
            from promaia.agents.agent_config import get_agent
            agent = get_agent(name)
            if not agent:
                return f"Agent '{name}' not found."

            from promaia.agents.executor import AgentExecutor
            executor = AgentExecutor(agent)
            result = await executor.execute()

            if result.get("success"):
                metrics = result.get("metrics", {})
                return (
                    f"Agent '{name}' ran successfully.\n"
                    f"  Iterations: {metrics.get('iterations_used', '?')}\n"
                    f"  Tokens: {metrics.get('tokens_used', '?')}\n"
                    f"  Duration: {metrics.get('duration_seconds', '?')}s"
                )
            else:
                return f"Agent '{name}' run failed: {result.get('output', 'unknown error')}"
        except Exception as e:
            return f"Error running agent: {e}"

    async def _create_agent(self, tool_input: Dict) -> str:
        # Only the default agent (maia) can create other agents.
        if not getattr(self.agent, 'is_default_agent', False):
            return (
                "Refused: only the default agent (maia) can create other agents."
            )
        name = tool_input.get("name", "").strip()
        if not name:
            return "Error: agent name is required."

        try:
            from datetime import datetime
            from promaia.agents.agent_config import (
                AgentConfig, save_agent, get_agent, load_agents,
            )
            from promaia.agents.notion_setup import generate_agent_id

            # Check for name collision
            existing = get_agent(name)
            if existing:
                return f"Error: agent '{name}' already exists."

            workspace = tool_input.get("workspace", self.workspace).strip()
            databases = tool_input.get("databases", [])
            mcp_tools = tool_input.get("mcp_tools", [])
            description = tool_input.get("description", "")
            max_iterations = tool_input.get("max_iterations", 40)
            interval_minutes = tool_input.get("interval_minutes")

            # Generate unique agent_id
            agent_id = generate_agent_id(name, load_agents())

            # Default prompt
            prompt = tool_input.get("prompt", "")
            if not prompt:
                prompt = f"You are {name}."
                if description:
                    prompt += f" {description}"

            # Build config
            agent_config = AgentConfig(
                name=name,
                workspace=workspace,
                databases=databases,
                prompt_file=prompt,
                mcp_tools=mcp_tools,
                agent_id=agent_id,
                description=description or None,
                max_iterations=max_iterations,
                interval_minutes=interval_minutes,
                enabled=True,
                created_at=datetime.now().isoformat(),
            )

            # Set messaging permission if provided
            if tool_input.get("messaging_enabled"):
                agent_config.messaging_enabled = True

            # Set channel restrictions if provided
            if "allowed_channel_ids" in tool_input:
                ids = tool_input["allowed_channel_ids"]
                agent_config.allowed_channel_ids = ids if ids else None

            # Validate
            errors = agent_config.validate()
            if errors:
                return "Validation errors:\n" + "\n".join(f"  - {e}" for e in errors)

            # Save
            save_agent(agent_config)

            result_parts = [
                f"Agent '{name}' created successfully.",
                f"  Agent ID: @{agent_id}",
                f"  Workspace: {workspace}",
            ]
            if databases:
                result_parts.append(f"  Databases: {', '.join(databases)}")
            if mcp_tools:
                result_parts.append(f"  MCP tools: {', '.join(mcp_tools)}")
            if interval_minutes:
                result_parts.append(f"  Interval: every {interval_minutes} minutes")

            # Attempt Notion setup (non-blocking)
            try:
                from promaia.agents.notion_setup import create_agent_in_notion
                notion_page_id = await create_agent_in_notion(agent_config, workspace)
                if notion_page_id:
                    agent_config.notion_page_id = notion_page_id
                    save_agent(agent_config)
                    page_id_clean = notion_page_id.replace("-", "")
                    result_parts.append(
                        f"  Notion page: https://www.notion.so/{workspace}/{page_id_clean}"
                    )
            except Exception as e:
                logger.debug(f"Notion setup skipped for agent {name}: {e}")

            # Attempt Calendar creation (non-blocking)
            try:
                from promaia.gcal import get_calendar_manager, google_account_for_workspace
                calendar_mgr = get_calendar_manager(
                    account=google_account_for_workspace(workspace)
                )
                cal_desc = f"Automated schedule for {name} agent"
                if description:
                    cal_desc += f"\n\n{description}"
                calendar_id = calendar_mgr.create_agent_calendar(
                    agent_name=name, description=cal_desc
                )
                if calendar_id:
                    agent_config.calendar_id = calendar_id
                    save_agent(agent_config)
                    # Make schedule_agent_event available for this agent immediately
                    self._agent_calendars[name] = calendar_id
                    result_parts.append(f"  Calendar created: {name}")
                    result_parts.append(
                        "  You can now schedule recurring events on this agent's calendar "
                        "using schedule_agent_event."
                    )
            except Exception as e:
                logger.debug(f"Calendar setup skipped for agent {name}: {e}")

            return "\n".join(result_parts)

        except Exception as e:
            return f"Error creating agent: {e}"

    # ── Channel tools ───────────────────────────────────────────────────

    async def _list_channels(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        if not name:
            return "Error: database name is required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            db_config = db_manager.get_database(name, workspace)
            if not db_config:
                return f"Database '{name}' not found in workspace '{workspace}'."

            source_type = db_config.source_type
            if source_type not in ("discord", "slack"):
                return f"Channel listing is only available for Discord and Slack sources (this is {source_type})."

            if source_type == "discord":
                return await self._list_discord_channels(db_config, workspace)
            else:
                return await self._list_slack_channels(db_config, workspace)
        except Exception as e:
            return f"Error listing channels: {e}"

    async def _list_discord_channels(self, db_config, workspace: str) -> str:
        try:
            from promaia.auth.registry import get_integration
            discord_int = get_integration("discord")
            bot_token = discord_int.get_discord_token(workspace) if hasattr(discord_int, 'get_discord_token') else None
            if not bot_token:
                return "No Discord bot token found. Run: maia auth configure discord"

            from promaia.connectors.discord_connector import DiscordConnector
            connector = DiscordConnector({
                "database_id": db_config.database_id,
                "workspace": workspace,
                "bot_token": bot_token,
            })
            channel_data = await connector.get_cached_accessible_channels()
            channels = channel_data.get("channels", [])
            server_name = channel_data.get("server_name", "Unknown")

            if not channels:
                return f"No accessible channels found in {server_name}."

            import json
            result = {
                "server_name": server_name,
                "channels": [{"id": ch["id"], "name": ch["name"]} for ch in channels]
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error fetching Discord channels: {e}"

    async def _list_slack_channels(self, db_config, workspace: str) -> str:
        try:
            from promaia.connectors.slack_connector import SlackConnector
            connector = SlackConnector({
                "database_id": db_config.database_id,
                "workspace": workspace,
            })
            channel_data = await connector.discover_accessible_channels()
            channels = channel_data.get("channels", [])

            if not channels:
                return "No accessible channels found."

            import json
            result = {
                "channels": [
                    {"id": ch["id"], "name": ch["name"], "is_private": ch.get("is_private", False)}
                    for ch in channels
                ]
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error fetching Slack channels: {e}"

    async def _get_configured_channels(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        if not name:
            return "Error: database name is required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            db_config = db_manager.get_database(name, workspace)
            if not db_config:
                return f"Database '{name}' not found in workspace '{workspace}'."

            filters = db_config.property_filters or {}
            channel_ids = filters.get("channel_id", [])
            channel_names = filters.get("channel_names", {})

            if not channel_ids:
                return f"No channels configured for '{name}'. All channels will be synced."

            lines = [f"Configured channels for '{name}':\n"]
            for cid in channel_ids:
                cname = channel_names.get(cid, cid)
                lines.append(f"- #{cname} ({cid})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading channel config: {e}"

    async def _update_channels(self, tool_input: Dict) -> str:
        name = tool_input.get("name", "").strip()
        workspace = tool_input.get("workspace", self.workspace).strip()
        channel_ids = tool_input.get("channel_ids", [])
        channel_names = tool_input.get("channel_names", {})

        if not name:
            return "Error: database name is required."
        if not channel_ids:
            return "Error: channel_ids list is required."

        try:
            from promaia.config.databases import DatabaseManager
            db_manager = DatabaseManager()
            db_config = db_manager.get_database(name, workspace)
            if not db_config:
                return f"Database '{name}' not found in workspace '{workspace}'."

            if db_config.source_type not in ("discord", "slack"):
                return f"Channel configuration is only for Discord/Slack (this is {db_config.source_type})."

            # Update property_filters
            if not db_config.property_filters:
                db_config.property_filters = {}
            db_config.property_filters["channel_id"] = channel_ids
            db_config.property_filters["channel_names"] = channel_names

            # Persist
            db_manager.save_database_field(db_config, "property_filters")

            names_list = [channel_names.get(cid, cid) for cid in channel_ids]
            return (
                f"Updated channels for '{name}': {len(channel_ids)} channels configured.\n"
                f"Channels: {', '.join(names_list)}"
            )
        except Exception as e:
            return f"Error updating channels: {e}"


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


# ── Anthropic API retry with exponential backoff ─────────────────────
#
# 529 overloaded_error / 429 rate_limit_error / 500 api_error /
# 503 service_unavailable are transient and should be retried invisibly
# before surfacing anything to the user. Non-retryable errors
# (bad_request, authentication, permission, not_found) re-raise.

_RETRYABLE_ERROR_MARKERS = (
    "overloaded_error",
    "overloaded",
    "529",
    "rate_limit_error",
    "429",
    "api_error",
    "500",
    "service_unavailable",
    "503",
)

_API_RETRY_MAX_ATTEMPTS = 5  # 1 initial + 4 retries
_API_RETRY_BASE_DELAY = 2.0  # seconds; schedule is 2, 4, 8, 16


class _OverloadExhausted(Exception):
    """Raised when Anthropic API retries are exhausted on a retryable error."""


def _is_retryable_api_error(err: Exception) -> bool:
    err_type = type(err).__name__.lower()
    err_str = str(err).lower()
    blob = f"{err_type} {err_str}"
    return any(marker in blob for marker in _RETRYABLE_ERROR_MARKERS)


async def _call_with_retry(
    client: Any,
    api_kwargs: Dict[str, Any],
    on_tool_activity: Optional["ToolActivityCallback"] = None,
) -> Any:
    """Call client.messages.create with exponential backoff on transient errors.

    Raises _OverloadExhausted after _API_RETRY_MAX_ATTEMPTS retryable failures.
    Non-retryable errors re-raise immediately.
    """
    last_err: Optional[Exception] = None
    for attempt in range(_API_RETRY_MAX_ATTEMPTS):
        try:
            return await asyncio.to_thread(client.messages.create, **api_kwargs)
        except Exception as err:
            if not _is_retryable_api_error(err):
                raise
            last_err = err
            if attempt == _API_RETRY_MAX_ATTEMPTS - 1:
                break
            delay = _API_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                f"[agentic] Anthropic API transient error "
                f"(attempt {attempt + 1}/{_API_RETRY_MAX_ATTEMPTS}): {err}. "
                f"Retrying in {delay:.0f}s"
            )
            if on_tool_activity:
                try:
                    await on_tool_activity(
                        tool_name="__api_retry__",
                        tool_input={},
                        completed=False,
                        summary=(
                            f"Anthropic overloaded, retrying in {delay:.0f}s "
                            f"(attempt {attempt + 2}/{_API_RETRY_MAX_ATTEMPTS})"
                        ),
                    )
                except Exception:
                    pass
            await asyncio.sleep(delay)

    raise _OverloadExhausted(
        f"Anthropic API retries exhausted after {_API_RETRY_MAX_ATTEMPTS} attempts: {last_err}"
    )


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



# Planning layer removed — the agent uses Think/Act mode and notepad
# to plan its own actions. External regex-based planning was too eager
# and fired on casual conversation.


def _serialize_content_blocks(content):
    """Convert Anthropic SDK content blocks to serializable dicts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for block in content:
            if hasattr(block, "type"):
                if block.type == "text":
                    result.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    result.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif block.type == "tool_result":
                    result.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                    })
                elif block.type in ("server_tool_use", "web_search_tool_result"):
                    # Server-side tool blocks must be preserved verbatim
                    # for multi-turn replay (encrypted_content, caller, etc.)
                    result.append(block.model_dump())
            elif isinstance(block, dict):
                result.append(block)
        return result
    return content


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
    suite_registry: Optional[Dict] = None,
    mcp_suites: Optional[Dict] = None,
) -> AgenticTurnResult:
    """
    Run a self-contained agentic turn with tool use.

    Operates in Think/Act modes:
    - Think mode: query tools + notepad + context + suite index (no action tool schemas)
    - Act mode: loaded suite tools + notepad only (no context, no queries)

    Args:
        system_prompt: Base system prompt (without context data)
        messages: Conversation history (plain text messages only)
        tools: Anthropic tool definitions (used as fallback if no suite_registry)
        tool_executor: Executes tool calls
        max_iterations: Max loop iterations (from agent config)
        on_tool_activity: Optional callback for UX activity updates
        plan: Optional list of plan steps to inject into the system prompt
        context_data_block: Loaded database pages block (used for browser-loaded source parsing)
        suite_registry: Tool suite registry for Think/Act mode switching
        mcp_suites: External MCP tool suites (dynamically discovered)

    Returns:
        AgenticTurnResult with plain text response and metadata
    """
    from promaia.utils.ai import get_anthropic_client

    client, prefix = get_anthropic_client()
    if not client:
        return AgenticTurnResult(
            response_text="I'm sorry, I couldn't generate a response (missing API key).",
        )

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
    _initial_msg_count = len(internal_messages)  # Track where tool messages start

    # Step progress tracking (for plan step callbacks)
    _step_marker_seen = False
    _current_step = 0  # 0 = not started yet

    # Think/Act mode state
    act_mode = False
    act_suites: List[str] = []
    act_instructions: List[str] = []
    act_step_status: List[str] = []  # "pending" | "done"
    act_tool_use_ids: List[str] = []  # tool_use ids produced in the current act burst (shelved on __DONE__)
    use_think_act = suite_registry is not None  # Feature flag: only use Think/Act if registry provided
    _retried_for_empty_text = False  # One-shot: nudge model to produce text if end_turn had none

    for iteration in range(max_iterations):
        # Stamp the current iteration on the executor so source-management
        # paths (shelving, _context_action) can record mounted_at_iteration.
        if tool_executor is not None:
            tool_executor._current_iteration = iteration

        budget_note = (
            f"\n\n[Tool budget: {max_iterations - iteration}/{max_iterations} "
            f"iterations remaining]"
        )

        # ── Build effective prompt and tool list per mode ──────────────
        # base_prompt = everything EXCEPT the active-source content block.
        # The active-source block is appended via _compose_prompt() so the
        # context trimmer can rebuild after LRU-off'ing sources.
        base_prompt = system_prompt

        if use_think_act and not act_mode:
            # THINK MODE: suite index first, then context index + active content
            _ws = tool_executor.workspace if tool_executor else ""
            base_prompt += "\n\n" + _build_suite_index(suite_registry, mcp_suites, workspace=_ws)

            if tool_executor and hasattr(tool_executor, 'build_context_index'):
                ctx_index = tool_executor.build_context_index()
                if ctx_index:
                    base_prompt += "\n\n" + ctx_index

            # Think mode tools: query + notepad + memory + context + workflows (read) + act
            iteration_tools = list(QUERY_TOOL_DEFINITIONS)
            iteration_tools.append(NOTEPAD_TOOL_DEFINITION)
            iteration_tools.append(MEMORY_TOOL_DEFINITION)
            iteration_tools.append(CONTEXT_TOOL_DEFINITION)
            # Workflow read tools available in Think mode (planning, not acting)
            for td in WORKFLOW_TOOL_DEFINITIONS:
                if td["name"] in ("list_saved_workflows", "get_workflow_details"):
                    iteration_tools.append(td)
            # Interview tools disabled outside onboarding flow
            # iteration_tools.extend(_build_interview_tool_definitions())
            iteration_tools.append(ACT_TOOL_DEFINITION)

        elif use_think_act and act_mode:
            # ACT MODE: no context, no suite index — just notes + memory + loaded suites + instructions
            budget_note += f"\n\n[ACT MODE: {', '.join(act_suites)}. Call done() when finished.]"

            # Inject instructions checklist into the prompt
            if act_instructions:
                instr_lines = ["\n\n## Instructions\n"]
                for i, step in enumerate(act_instructions):
                    status = act_step_status[i] if i < len(act_step_status) else "pending"
                    checkbox = "[x]" if status == "done" else "[ ]"
                    instr_lines.append(f"{i+1}. {checkbox} {step}")
                budget_note += "\n".join(instr_lines)

            # Act mode tools: loaded suites + notepad + memory + mark_step_done + done
            iteration_tools = []
            for suite_name in act_suites:
                iteration_tools.extend(_get_suite_tools(suite_name, suite_registry, mcp_suites))
            iteration_tools.append(NOTEPAD_TOOL_DEFINITION)
            iteration_tools.append(MEMORY_TOOL_DEFINITION)
            if act_instructions:
                iteration_tools.append(MARK_STEP_DONE_TOOL_DEFINITION)
            iteration_tools.append(DONE_TOOL_DEFINITION)

        else:
            # Legacy mode (no suite registry): all tools, all context
            iteration_tools = tools
            if tool_executor and hasattr(tool_executor, 'build_context_index'):
                ctx_index = tool_executor.build_context_index()
                if ctx_index:
                    base_prompt += "\n\n" + ctx_index

        # Compose final prompt = base + active-source block + budget_note.
        # The active-source block is recomputed from current _sources state
        # so it reflects post-LRU shelving when called from the trimmer.
        def _compose_prompt() -> str:
            active = ""
            # Note: in act mode, build_active_source_content returns "" because
            # _sources_muted is True — preserved automatically.
            if tool_executor and hasattr(tool_executor, "build_active_source_content"):
                active = tool_executor.build_active_source_content() or ""
            parts = [base_prompt]
            if active:
                parts.append(active)
            return "\n\n".join(parts) + budget_note

        effective_prompt = _compose_prompt()

        from promaia.agents.context_trimmer import trim_context_to_fit
        trimmed_system, internal_messages = await trim_context_to_fit(
            effective_prompt,
            internal_messages,
            tools=iteration_tools,
            tool_executor=tool_executor,
            current_iteration=iteration,
            rebuild_system_prompt=_compose_prompt,
        )

        # Log the effective prompt (first iteration and on mode switches)
        if iteration == 0:
            try:
                from promaia.utils.env_writer import get_data_dir
                import datetime as _dt_log
                log_dir = get_data_dir() / "context_logs" / "agentic_turn_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = _dt_log.datetime.now().strftime("%Y%m%d-%H%M%S")
                log_path = log_dir / f"{ts}_agentic_prompt.md"
                log_path.write_text(trimmed_system)
                logger.info(f"Agentic prompt logged to {log_path}")
            except Exception as log_err:
                logger.debug(f"Failed to log agentic prompt: {log_err}")

        # Build API call kwargs
        api_kwargs = dict(
            model=f"{prefix}claude-sonnet-4-6",
            system=trimmed_system,
            messages=internal_messages,
            max_tokens=4096,
        )
        if iteration_tools:
            api_kwargs["tools"] = iteration_tools

        try:
            response = await _call_with_retry(client, api_kwargs, on_tool_activity)
        except _OverloadExhausted as exhaust_err:
            logger.warning(f"[agentic] {exhaust_err}")
            last_text = "\n".join(text_parts) if text_parts else ""
            friendly = (
                "Claude is currently overloaded and I couldn't get a response "
                "after several retries. Please try again in a moment."
            )
            response_text = (last_text + "\n\n" + friendly) if last_text else friendly
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
                response_text=response_text,
                tool_calls_made=all_tool_calls,
                iterations_used=iteration + 1,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                plan=plan,
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
                        response = await _call_with_retry(
                            client, api_kwargs, on_tool_activity
                        )
                        break  # Success
                    except _OverloadExhausted:
                        # Overload on the trimmed retry — bail to the outer
                        # exhaustion path by re-raising as a tagged sentinel.
                        response = None
                        break
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
            elif block.type == "server_tool_use":
                # Server-side tool (e.g. web_search) — already resolved by
                # the API, no local execution needed.  Fire UX callbacks and
                # record in all_tool_calls for history.
                query = block.input.get("query", "") if isinstance(block.input, dict) else ""
                if on_tool_activity:
                    try:
                        await on_tool_activity(
                            tool_name="web_search",
                            tool_input={"query": query},
                            completed=False,
                        )
                    except Exception:
                        pass
                all_tool_calls.append({
                    "name": block.name,
                    "input": block.input if isinstance(block.input, dict) else {},
                    "summary": f'Web search "{query}"',
                })
            elif block.type == "web_search_tool_result":
                # Results already consumed by the model — log errors.
                if hasattr(block, "content") and hasattr(block.content, "error_code"):
                    logger.warning(f"[agentic] Web search error: {block.content.error_code}")
                if on_tool_activity:
                    try:
                        await on_tool_activity(
                            tool_name="web_search",
                            tool_input={},
                            completed=True,
                            summary="Web search completed",
                        )
                    except Exception:
                        pass

        # If no tool calls, we're done — return the final text
        if response.stop_reason == "end_turn" or not tool_uses:
            # If the model ended the turn with zero text but DID execute tools,
            # nudge it once to produce a plain-English summary for the user.
            if not text_parts and all_tool_calls and not _retried_for_empty_text:
                _retried_for_empty_text = True
                internal_messages.append({
                    "role": "assistant",
                    "content": _serialize_content_blocks(response.content),
                })
                internal_messages.append({
                    "role": "user",
                    "content": (
                        "You completed tool actions but didn't provide a response "
                        "to the user. Please summarize what you did in plain English."
                    ),
                })
                continue

            if plan and on_tool_activity:
                try:
                    await on_tool_activity(
                        tool_name="__plan_done__",
                        tool_input={"total": len(plan)},
                        completed=True,
                    )
                except Exception:
                    pass
            # Capture tool interaction messages for conversation history
            new_msgs = internal_messages[_initial_msg_count:]
            # Add the final assistant text as the last message
            if text_parts:
                new_msgs.append({"role": "assistant", "content": "\n".join(text_parts)})
            return AgenticTurnResult(
                response_text="\n".join(text_parts),
                tool_calls_made=all_tool_calls,
                iterations_used=iteration + 1,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                plan=plan,
                history_messages=new_msgs,
            )

        # ── Tool execution ────────────────────────────────────────────
        internal_messages.append({
            "role": "assistant",
            "content": _serialize_content_blocks(response.content),
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

            # Validate tool is in current iteration's tool list
            valid_tool_names = {t["name"] for t in iteration_tools} if iteration_tools else set()
            if valid_tool_names and tool_use.name not in valid_tool_names:
                result_text = (
                    f"Error: tool '{tool_use.name}' is not available in the current mode. "
                    f"Available tools: {', '.join(sorted(valid_tool_names))}"
                )
                logger.warning(f"[think/act] Rejected tool '{tool_use.name}' — not in current mode")
            else:
                # Execute tool
                result_text = await tool_executor.execute(tool_use.name, tool_use.input)

            # Handle Think/Act mode switching sentinels (stay in loop)
            if result_text.startswith("__ACT__:"):
                # Parse: __ACT__:suite1,suite2|["step1","step2"]
                payload = result_text.split(":", 1)[1]
                if "|" in payload:
                    suites_part, instructions_json = payload.split("|", 1)
                    suite_names = [s.strip() for s in suites_part.split(",")]
                    try:
                        import json as _json_parse
                        act_instructions = _json_parse.loads(instructions_json)
                        act_step_status = ["pending"] * len(act_instructions)
                    except Exception:
                        act_instructions = []
                        act_step_status = []
                else:
                    suite_names = [s.strip() for s in payload.split(",")]
                    act_instructions = []
                    act_step_status = []
                act_mode = True
                act_suites = suite_names
                act_tool_use_ids = []  # fresh burst
                # Mute context (preserves individual on/off states for restore)
                if tool_executor:
                    tool_executor._sources_muted = True
                instr_count = f" {len(act_instructions)} steps." if act_instructions else ""
                result_text = f"Act mode. Suites loaded: {', '.join(suite_names)}.{instr_count} Context muted. Follow your instructions."
                logger.info(f"[think/act] Entered Act mode with suites: {suite_names}, instructions: {len(act_instructions)} steps")
                # Fire plan step callback for UX
                if act_instructions and on_tool_activity:
                    try:
                        await on_tool_activity(
                            tool_name="__plan_step__",
                            tool_input={"step": 1, "total": len(act_instructions), "steps": act_instructions},
                            completed=False,
                        )
                    except Exception:
                        pass
            elif result_text.startswith("__MARK_STEP__:"):
                step_num = int(result_text.split(":")[1])
                if 0 < step_num <= len(act_step_status):
                    act_step_status[step_num - 1] = "done"
                    result_text = f"Step {step_num} marked done."
                    logger.info(f"[think/act] Marked step {step_num}/{len(act_instructions)} done")
                    # Fire UX callback
                    if on_tool_activity:
                        try:
                            # Advance to next pending step for display
                            next_step = step_num + 1 if step_num < len(act_instructions) else step_num
                            await on_tool_activity(
                                tool_name="__plan_step__",
                                tool_input={"step": next_step, "total": len(act_instructions)},
                                completed=True,
                            )
                        except Exception:
                            pass
                else:
                    result_text = f"Invalid step number: {step_num}"
            elif result_text == "__DONE__":
                # Shelve all act-burst tool results into _sources before
                # returning to Think mode. This frees ~all of the bloat
                # while keeping the data lossly recoverable via turn_on_source.
                if tool_executor and act_tool_use_ids:
                    try:
                        tool_executor.shelve_act_results(
                            act_tool_use_ids,
                            internal_messages,
                            current_iteration=iteration,
                        )
                    except Exception as shelve_err:
                        logger.warning(
                            f"[think/act] shelve_act_results failed: {shelve_err}"
                        )
                act_mode = False
                act_suites = []
                act_instructions = []
                act_step_status = []
                act_tool_use_ids = []
                if tool_executor:
                    tool_executor._sources_muted = False
                result_text = "Back to Think mode. Context restored."
                logger.info("[think/act] Returned to Think mode")
                # Fire plan done callback
                if on_tool_activity:
                    try:
                        await on_tool_activity(
                            tool_name="__plan_done__",
                            tool_input={},
                            completed=True,
                        )
                    except Exception:
                        pass

            # Handle interview sentinels — break out of loop and return with signal
            elif result_text.startswith("__INTERVIEW_START__:"):
                workflow_name = result_text[len("__INTERVIEW_START__:"):]
                return AgenticTurnResult(
                    response_text="\n".join(text_parts),
                    tool_calls_made=all_tool_calls,
                    iterations_used=iteration + 1,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    plan=plan,
                    signal={"type": "interview_start", "workflow": workflow_name},
                )
            elif result_text == "__INTERVIEW_END__":
                return AgenticTurnResult(
                    response_text="\n".join(text_parts),
                    tool_calls_made=all_tool_calls,
                    iterations_used=iteration + 1,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    plan=plan,
                    signal={"type": "interview_end"},
                )
            elif result_text.startswith("__SHOW_SELECTION__:"):
                import json as _json
                payload = _json.loads(result_text[len("__SHOW_SELECTION__:"):])
                return AgenticTurnResult(
                    response_text="\n".join(text_parts),
                    tool_calls_made=all_tool_calls,
                    iterations_used=iteration + 1,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    plan=plan,
                    signal={"type": "show_selection", "payload": payload},
                )
            elif result_text.startswith("__END_CONVERSATION__:"):
                # Format: __END_CONVERSATION__:emoji:summary
                parts = result_text[len("__END_CONVERSATION__:"):].split(":", 1)
                emoji = parts[0].strip() if parts else ""
                summary = parts[1].strip() if len(parts) > 1 else ""
                result_text = "Conversation ended."
                return AgenticTurnResult(
                    response_text="\n".join(text_parts),
                    tool_calls_made=all_tool_calls,
                    iterations_used=iteration + 1,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    plan=plan,
                    signal={"type": "end_conversation", "emoji": emoji or None, "summary": summary or None},
                )
            elif result_text.startswith("__LEAVE_CONVERSATION__:"):
                message = result_text[len("__LEAVE_CONVERSATION__:"):].strip()
                result_text = message or "Goodbye!"
                return AgenticTurnResult(
                    response_text="\n".join(text_parts) + ("\n" + message if message else ""),
                    tool_calls_made=all_tool_calls,
                    iterations_used=iteration + 1,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    plan=plan,
                    signal={"type": "leave_conversation"},
                )

            # Build summary for logging and UX
            summary = _summarize_tool_result(
                tool_use.name, tool_use.input, result_text
            )

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })
            # Track tool_use ids produced inside an act burst so they can
            # be shelved on __DONE__. Recorded *after* mode-switch sentinels
            # are handled above so __ACT__/__DONE__ themselves are excluded.
            if act_mode:
                act_tool_use_ids.append(tool_use.id)
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

    new_msgs = internal_messages[_initial_msg_count:]
    return AgenticTurnResult(
        response_text=last_text,
        tool_calls_made=all_tool_calls,
        iterations_used=max_iterations,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        plan=plan,
        history_messages=new_msgs,
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

    elif tool_name == "write_agent_journal":
        return "Wrote agent journal entry"

    elif tool_name == "send_email":
        to = tool_input.get("to", "")
        subj = tool_input.get("subject", "")
        return f'Sent email to {to}: "{subj}"'

    elif tool_name == "create_email_draft":
        subj = tool_input.get("subject", "")
        return f'Created draft: "{subj}"'

    elif tool_name == "reply_to_email":
        return f"Replied to thread {tool_input.get('thread_id', '')[:12]}"

    elif tool_name == "draft_reply_to_email":
        return f"Draft reply created in thread {tool_input.get('thread_id', '')[:12]}"

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
