"""
Intent classification for Promaia agent orchestration.

Determines whether a user query is:
- KNOWLEDGE: Information request (Promaia handles directly)
- ACTION: Task requiring external tools/MCPs (spawn agent)
- CONTEXT: Promaia command (/e, /browse, etc.)
"""

from enum import Enum
from typing import Optional
import re


class IntentType(Enum):
    """Types of user intents."""
    KNOWLEDGE = "knowledge"  # Query, question, information request
    ACTION = "action"        # Task requiring external tools/MCPs
    CONTEXT = "context"      # Promaia commands (/e, /browse, etc.)


class ActionType(Enum):
    """Types of actions that require specific MCPs."""
    EMAIL = "email"          # Gmail MCP
    NOTION = "notion"        # Notion MCP
    CALENDAR = "calendar"    # Calendar operations
    FILES = "files"          # File operations
    CODE = "code"            # Code execution
    WEB = "web"             # Web browsing
    GENERAL = "general"      # General agent task


def classify_intent(query: str) -> IntentType:
    """
    Classify user intent as knowledge, action, or context command.

    Args:
        query: User's input string

    Returns:
        IntentType enum value

    Examples:
        >>> classify_intent("What did I work on yesterday?")
        IntentType.KNOWLEDGE

        >>> classify_intent("Send Federico an email")
        IntentType.ACTION

        >>> classify_intent("/e journal:7")
        IntentType.CONTEXT
    """
    query_lower = query.lower().strip()

    # Check for Promaia commands first
    if query_lower.startswith('/'):
        return IntentType.CONTEXT

    # Action indicators (imperative verbs)
    action_verbs = [
        'send', 'create', 'write', 'draft', 'compose',
        'schedule', 'book', 'add', 'update', 'edit',
        'delete', 'remove', 'run', 'execute',
        'upload', 'download', 'search', 'find',
        'notify', 'remind', 'set', 'configure'
    ]

    # Check if query starts with action verb
    for verb in action_verbs:
        # Match verb at start of query or after "can you", "please", etc.
        patterns = [
            rf'\b{verb}\b',  # Direct: "send email"
            rf'^(can you |could you |please )?{verb}\b',  # Polite: "can you send"
            rf'^(i need to |i want to |help me ){verb}\b',  # Intent: "I need to send"
        ]
        for pattern in patterns:
            if re.search(pattern, query_lower):
                return IntentType.ACTION

    # Knowledge indicators (interrogative patterns)
    knowledge_patterns = [
        r'^what ',
        r'^who ',
        r'^when ',
        r'^where ',
        r'^why ',
        r'^how ',
        r'^which ',
        r'^show me',
        r'^tell me',
        r'^explain',
        r'^describe',
        r'^summarize',
        r'^list',
        r'^\w+\?',  # Ends with question mark
    ]

    for pattern in knowledge_patterns:
        if re.match(pattern, query_lower):
            return IntentType.KNOWLEDGE

    # Default to knowledge for most cases
    # (Promaia's primary use case is information retrieval)
    return IntentType.KNOWLEDGE


def detect_action_type(query: str) -> ActionType:
    """
    If action intent, detect which type of action is needed.

    Args:
        query: User's input string

    Returns:
        ActionType enum value indicating which tools/MCPs are needed

    Examples:
        >>> detect_action_type("Send Federico an email")
        ActionType.EMAIL

        >>> detect_action_type("Create a Notion page")
        ActionType.NOTION
    """
    query_lower = query.lower()

    # Email indicators
    email_keywords = ['email', 'mail', 'send to', 'reply to', 'forward to']
    if any(keyword in query_lower for keyword in email_keywords):
        return ActionType.EMAIL

    # Notion indicators
    notion_keywords = ['notion', 'page', 'database', 'workspace']
    if any(keyword in query_lower for keyword in notion_keywords):
        # But not if it's just asking about Notion content
        if not any(q in query_lower for q in ['what', 'show', 'tell me']):
            return ActionType.NOTION

    # Calendar indicators
    calendar_keywords = ['calendar', 'schedule', 'meeting', 'appointment', 'event']
    if any(keyword in query_lower for keyword in calendar_keywords):
        return ActionType.CALENDAR

    # File operations
    file_keywords = ['file', 'upload', 'download', 'save', 'export']
    if any(keyword in query_lower for keyword in file_keywords):
        return ActionType.FILES

    # Code execution
    code_keywords = ['run', 'execute', 'script', 'command', 'bash']
    if any(keyword in query_lower for keyword in code_keywords):
        return ActionType.CODE

    # Web operations
    web_keywords = ['search', 'browse', 'lookup', 'web', 'internet', 'google']
    if any(keyword in query_lower for keyword in web_keywords):
        return ActionType.WEB

    # Default to general action
    return ActionType.GENERAL


def should_spawn_agent(query: str) -> bool:
    """
    Convenience function to determine if Claude Code agent should be spawned.

    Args:
        query: User's input string

    Returns:
        True if agent should be spawned, False if Promaia should handle directly
    """
    intent = classify_intent(query)
    return intent == IntentType.ACTION


def get_required_mcps(query: str) -> list[str]:
    """
    Determine which MCPs are needed for an action query.

    Args:
        query: User's input string

    Returns:
        List of MCP names needed (e.g., ["gmail", "notion"])
    """
    if classify_intent(query) != IntentType.ACTION:
        return []

    action_type = detect_action_type(query)

    mcp_mapping = {
        ActionType.EMAIL: ["gmail"],
        ActionType.NOTION: ["notion"],
        ActionType.CALENDAR: ["google-calendar"],  # Future
        ActionType.FILES: [],  # Built-in tools
        ActionType.CODE: [],   # Built-in tools
        ActionType.WEB: [],    # Built-in tools
        ActionType.GENERAL: [], # Let agent figure it out
    }

    return mcp_mapping.get(action_type, [])


# For testing
if __name__ == "__main__":
    test_queries = [
        "What did I work on yesterday?",
        "Send Federico an email about the launch",
        "Create a Notion page with my notes",
        "/e journal:7",
        "Show me my recent emails",
        "Draft a response to Maria",
        "Tell me about the API project",
        "Schedule a meeting for tomorrow",
    ]

    print("Intent Classification Tests")
    print("=" * 60)
    for query in test_queries:
        intent = classify_intent(query)
        action_type = detect_action_type(query) if intent == IntentType.ACTION else None
        mcps = get_required_mcps(query)

        print(f"\nQuery: {query}")
        print(f"Intent: {intent.value}")
        if action_type:
            print(f"Action Type: {action_type.value}")
        if mcps:
            print(f"Required MCPs: {mcps}")
