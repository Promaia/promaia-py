"""
Terminal-based chat interface for interacting with AI models.
"""

# Import models
from anthropic import Anthropic
from openai import OpenAI

import os
import sys
import time
import json
import re
import shlex
import logging
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
import datetime
import asyncio
import traceback
from typing import List, Dict, Any, Optional
from pathlib import Path

from promaia.storage.files import load_database_pages_with_filters
from promaia.utils.config import load_environment, get_last_sync_time
from promaia.config.workspaces import get_workspace_manager
from promaia.nlq.prompts import create_system_prompt
from promaia.nlq.models import LLAMA_MODELS, ANTHROPIC_MODELS
from promaia.utils.display import print_markdown, print_code, print_text, print_separator
from promaia.utils.timezone_utils import now_utc
from promaia.storage.chat_history import ChatHistoryManager
from promaia.storage.recents import RecentsManager
from promaia.utils.query_parsing import parse_vs_queries_with_params

import google.generativeai as genai

# Setup logging
logger = logging.getLogger(__name__)

# Load environment variables
load_environment()

# Initialize DEBUG_MODE at the global scope
DEBUG_MODE = os.getenv("MAIA_DEBUG", "0") == "1"

# Configuration file for API preferences
from promaia.utils.env_writer import get_data_dir as _get_data_dir
API_PREFERENCE_FILE = str(_get_data_dir() / ".maia_api_preference")
# Configuration file for browser selection preferences
BROWSER_PREFERENCE_FILE = str(_get_data_dir() / ".maia_browser_selection")


def format_email_preview(body: str, attachments: list, max_body_length: int = 400) -> str:
    """Format email preview showing body and attachments.

    Args:
        body: Email body text
        attachments: List of attachment file paths
        max_body_length: Maximum characters to show from body

    Returns:
        Formatted preview string
    """
    lines = []

    # Show body preview
    if body:
        body_preview = body.strip()
        if len(body_preview) > max_body_length:
            body_preview = body_preview[:max_body_length] + "\n\n[... message continues ...]"
        lines.append(body_preview)

    # Show attachments
    if attachments:
        lines.append("\n---")
        lines.append("📎 Attachments:")
        for attachment in attachments:
            lines.append(f"- {attachment}")

    return "\n".join(lines)


def _resolve_host_path(path: str) -> str:
    """Translate a macOS host path to a Docker-accessible path.

    When running inside Docker with /Users/<user> mounted at /host_home,
    rewrite paths so images and files from the host are accessible.
    Falls back to the original path if not running in Docker or no mount exists.
    """
    if not path.startswith("/Users/"):
        return path
    # Check if we're in Docker with the host home mount
    if os.path.isdir("/host_home"):
        # /Users/<user>/Desktop/foo.jpg → /host_home/Desktop/foo.jpg
        parts = path.split("/", 3)  # ['', 'Users', 'username', 'rest/of/path']
        if len(parts) >= 4:
            return f"/host_home/{parts[3]}"
    return path


def _shell_unescape(path: str) -> str:
    """Remove shell escape backslashes from a file path.

    Handles escaped spaces (\\ ), equals (\\=), parentheses, etc.
    Example: 'foo_ref_\\\\=bar.png' -> 'foo_ref_=bar.png'
    """
    return re.sub(r'\\(.)', r'\1', path)


def detect_urls_in_text(text: str) -> List[str]:
    """Detect URLs in user input text.

    Args:
        text: User input text

    Returns:
        List of URLs found in the text
    """
    # URL pattern that matches http://, https://, and www. URLs
    url_pattern = r'(?:https?://|www\.)[^\s<>"\'{}\[\]\\^`|]+'
    urls = re.findall(url_pattern, text, re.IGNORECASE)
    return urls


def check_and_suggest_web_tools(user_input: str, context_state: dict, style: Style) -> None:
    """Check if user input contains URLs or search requests and suggest enabling web tools.

    Args:
        user_input: The user's input text
        context_state: Current context state
        style: Prompt style for display
    """
    # Skip if this is a command
    if user_input.strip().startswith('/'):
        return

    urls = detect_urls_in_text(user_input)
    has_search_keywords = any(keyword in user_input.lower() for keyword in
                               ['search', 'look up', 'find online', 'search internet', 'search web', 'google'])

    mcp_servers = context_state.get('mcp_servers', []) or []
    agentic_tools = context_state.get('agentic_tools', []) or []

    # In agentic mode with web_search, web_fetch is built-in — no tip needed
    has_agentic_web = "web_search" in agentic_tools

    # Suggest fetch if URLs detected and no web capability
    if urls and 'fetch' not in mcp_servers and not has_agentic_web:
        print_text("💡 Tip: I detected URLs in your query. Enable web fetching with /mcp fetch to visit these URLs.",
                  style="cyan")
        print_text(f"   URLs found: {', '.join(urls[:3])}", style="dim cyan")

    # Suggest search if search keywords detected and no search capability
    if has_search_keywords and 'search' not in mcp_servers and not has_agentic_web and not urls:
        print_text("💡 Tip: I detected a search request. Enable web search with /mcp search to search the internet.",
                  style="cyan")


# --- API Client Initialization ---

def get_api_preference():
    """Get the saved API preference and model ID."""
    try:
        if os.path.exists(API_PREFERENCE_FILE):
            with open(API_PREFERENCE_FILE, 'r') as f:
                content = f.read().strip()
                lines = content.split('\n')
                api_type = lines[0] if lines else "anthropic"
                model_id = lines[1] if len(lines) > 1 else None

                if api_type in ["anthropic", "openai", "gemini", "llama"]:
                    # Set the model ID in environment if available
                    if model_id:
                        os.environ["SELECTED_MODEL_ID"] = model_id
                    return api_type
    except Exception as e:
        debug_print(f"Error reading API preference: {str(e)}")
    return "anthropic"

def save_api_preference(api_type, model_id=None):
    """Save the API preference and optionally the model ID."""
    try:
        with open(API_PREFERENCE_FILE, 'w') as f:
            f.write(api_type)
            if model_id:
                f.write(f'\n{model_id}')
        debug_print(f"API preference saved: {api_type}" + (f" (model: {model_id})" if model_id else ""))
    except Exception as e:
        debug_print(f"Error saving API preference: {str(e)}")

def get_browser_selection():
    """Get the saved browser selection (selected sources)."""
    try:
        if os.path.exists(BROWSER_PREFERENCE_FILE):
            with open(BROWSER_PREFERENCE_FILE, 'r') as f:
                content = f.read().strip()
                if content:
                    # Sources are stored one per line
                    return [line.strip() for line in content.split('\n') if line.strip()]
    except Exception as e:
        debug_print(f"Error reading browser selection: {str(e)}")
    return None

def save_browser_selection(sources):
    """Save the browser selection (selected sources)."""
    try:
        with open(BROWSER_PREFERENCE_FILE, 'w') as f:
            if sources:
                f.write('\n'.join(sources))
        debug_print(f"Browser selection saved: {len(sources)} sources")
    except Exception as e:
        debug_print(f"Error saving browser selection: {str(e)}")

anthropic_client = None
if os.getenv("ANTHROPIC_API_KEY"):
    anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), base_url=os.environ.get("ANTHROPIC_BASE_URL"), max_retries=5)

openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

gemini_client = None
if os.getenv("GOOGLE_API_KEY"):
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    from promaia.nlq.models import get_current_google_model, GOOGLE_MODELS
    # Use selected model ID if available, otherwise use default
    selected_model = os.getenv("SELECTED_MODEL_ID")
    if selected_model and "gemini" in selected_model.lower():
        gemini_client = genai.GenerativeModel(selected_model)
    else:
        gemini_client = genai.GenerativeModel(get_current_google_model())

current_api = get_api_preference()
os.environ["API_TYPE"] = current_api

# --- UI Components ---

# Create key bindings for intuitive chat input
from prompt_toolkit.filters import Condition


class MessageOnlyHistory(FileHistory):
    """
    FileHistory that filters out slash commands from up/down arrow navigation.
    All entries (including commands) are still persisted to the history file.
    """

    def load_history_strings(self):
        """Load history from file, filtering out slash commands."""
        for entry in super().load_history_strings():
            if not entry.startswith('/'):
                yield entry

    def append_string(self, string: str):
        """Persist all entries to file, but only add non-commands to in-memory history."""
        self.store_string(string)
        if not string.startswith('/'):
            self._loaded_strings.insert(0, string)


# Store partially typed input when Ctrl+O is pressed for edit mode
pending_input_text = None

bindings = KeyBindings()

@bindings.add('enter')
def _(event):
    """Enter key sends the message/command."""
    # Get the text from the buffer
    text = event.app.current_buffer.text
    # Manually append to history before exiting (since we're using custom exit)
    if text.strip():  # Only save non-empty inputs
        event.app.current_buffer.append_to_history()
    # Exit with the text as result
    event.app.exit(result=text)

@bindings.add('c-j')
def _(event):
    """Ctrl+J adds a new line. On many terminals, Shift+Enter sends Ctrl+J."""
    event.current_buffer.insert_text('\n')

@bindings.add('c-o')
def _(event):
    """Ctrl+O triggers edit mode, preserving any partially typed input."""
    global pending_input_text
    # Save current buffer text to restore after edit mode
    pending_input_text = event.app.current_buffer.text
    # Exit with /e command to trigger edit mode
    event.app.exit(result='/e')

@bindings.add('c-l')
def _(event):
    """Ctrl+L triggers inline sync, preserving any partially typed input."""
    global pending_input_text
    # Save current buffer text to restore after sync
    pending_input_text = event.app.current_buffer.text
    # Exit with special /sync-inline command to trigger inline sync mode
    event.app.exit(result='/sync-inline')

@bindings.add('c-b')
def _(event):
    """Ctrl+B triggers browser mode, preserving any partially typed input."""
    global pending_input_text
    # Save current buffer text to restore after browser mode
    pending_input_text = event.app.current_buffer.text
    # Exit with special /browser-inline command to trigger browser mode
    event.app.exit(result='/browser-inline')

session = PromptSession(
    history=MessageOnlyHistory(str(_get_data_dir() / ".chat_history")),
    multiline=True,  # Keep multiline for editing capabilities
    key_bindings=bindings
)

style = Style.from_dict({
    'prompt': 'ansicyan bold',
    'input': 'ansiwhite',
    'assistant': 'ansigreen',
    'user': 'ansiblue',
})

def get_local_timestamp():
    """Get current local timestamp formatted as YYYY-MM-DD HH:MM:SS."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _track_agentic_query_sources(tool_calls_made, context_state):
    """Update context_state['sources'] based on query_source tool calls from an agentic turn."""
    from promaia.config.databases import get_database_config
    for tc in tool_calls_made:
        if tc.get('name') == 'query_source':
            tc_input = tc.get('input', {})
            db_name = tc_input.get('database', '')
            days = tc_input.get('days')
            if db_name:
                # Resolve qualified name using the same logic as the browser
                ws = context_state.get('workspace', '')
                try:
                    db_config = get_database_config(db_name, ws)
                    qualified = db_config.get_qualified_name() if db_config else db_name
                except Exception:
                    qualified = db_name
                source_spec = f"{qualified}:{days}" if days else qualified
                current_srcs = context_state.get('sources') or []
                existing_bases = {s.split(':')[0] for s in current_srcs}
                if qualified not in existing_bases:
                    current_srcs.append(source_spec)
                else:
                    current_srcs = [source_spec if s.split(':')[0] == qualified else s for s in current_srcs]
                context_state['sources'] = current_srcs

def debug_print(message):
    """Print debug messages if debug mode is enabled."""
    if DEBUG_MODE:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        caller_name = ""
        try:
            caller_name = f" [{sys._getframe(1).f_code.co_name}]"
        except Exception:
            pass
        print(f"DEBUG ({timestamp}){caller_name}: {message}")

def _preview_title(page: dict, max_len: int = 60) -> str:
    """Build a preview title for a loaded content page, with Shopify-aware formatting."""
    # Shopify order
    if 'order_number' in page:
        parts = [f"#{page['order_number']}"]
        if page.get('total_price'):
            parts.append(f"${page['total_price']}")
        if page.get('financial_status'):
            parts.append(page['financial_status'])
        return ' '.join(parts)[:max_len]
    # Shopify product
    if 'product_type' in page or 'handle' in page:
        return (page.get('title') or page.get('handle') or 'Product')[:max_len]
    # Shopify inventory
    if 'variant_id' in page or 'inventory_item_id' in page:
        parts = []
        if page.get('sku'):
            parts.append(page['sku'])
        if page.get('product_title'):
            parts.append(page['product_title'])
        if page.get('available') is not None:
            parts.append(f"qty {page['available']}")
        return (' — '.join(parts) if parts else 'Inventory')[:max_len]
    # Default
    return (page.get('title') or page.get('name') or 'Untitled')[:max_len]


def has_artifact_tags(text: str) -> bool:
    """
    Check if text contains artifact tags (with or without attributes).

    Handles both:
    - Simple: <artifact>...</artifact>
    - With attributes: <artifact identifier="..." type="..." title="...">...</artifact>

    Args:
        text: Text to check

    Returns:
        True if artifact tags are present
    """
    artifact_pattern = r'<artifact(?:\s+[^>]*)?>(.+?)</artifact>'
    return bool(re.search(artifact_pattern, text, re.DOTALL))

def has_email_draft_tags(text: str) -> bool:
    """
    Check if text contains email_draft tags.

    Args:
        text: Text to check

    Returns:
        True if email_draft tags are present
    """
    email_draft_pattern = r'<email_draft>(.+?)</email_draft>'
    return bool(re.search(email_draft_pattern, text, re.DOTALL))

def extract_email_draft_data(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract email draft data from response text.

    Args:
        text: Text containing <email_draft>...</email_draft> tags

    Returns:
        Dictionary with email draft data, or None if parsing fails
    """
    email_draft_pattern = r'<email_draft>(.+?)</email_draft>'
    match = re.search(email_draft_pattern, text, re.DOTALL)

    if not match:
        return None

    try:
        draft_json = match.group(1).strip()
        draft_data = json.loads(draft_json)
        return draft_data
    except json.JSONDecodeError as e:
        debug_print(f"Failed to parse email draft JSON: {e}")
        return None

# Local Llama client initialization (after debug_print is defined)
def initialize_llama_client():
    """Initialize local Llama client if available."""
    global llama_client
    llama_client = None
    llama_base_url = os.getenv("LLAMA_BASE_URL", "http://localhost:11434")
    if llama_base_url:
        try:
            # Test if local Llama server is available
            import requests
            test_url = f"{llama_base_url.rstrip('/')}/api/tags" if "ollama" in llama_base_url or ":11434" in llama_base_url else f"{llama_base_url.rstrip('/')}/v1/models"
            response = requests.get(test_url, timeout=2)
            if response.status_code == 200:
                # Use OpenAI client with custom base URL for local Llama
                llama_client = OpenAI(
                    base_url=f"{llama_base_url.rstrip('/')}/v1",
                    api_key=os.getenv("LLAMA_API_KEY", "local-llama")  # Many local setups don't need real API keys
                )
                debug_print(f"Local Llama client initialized at {llama_base_url}")
            else:
                debug_print(f"Local Llama server not responding at {llama_base_url}")
        except Exception as e:
            debug_print(f"Could not connect to local Llama server: {e}")

# Initialize llama client (will be initialized lazily when needed)
llama_client = None

def get_current_model_name():
    """Get the display name of the current model based on the current API."""
    global current_api
    from promaia.nlq.models import get_model_display_name, ANTHROPIC_MODELS, GOOGLE_MODELS, LLAMA_MODELS

    # Check if a specific model ID was selected
    selected_model_id = os.getenv("SELECTED_MODEL_ID")
    if selected_model_id:
        return get_model_display_name(selected_model_id, current_api)

    # Get the actual model ID being used for each API type (fallback)
    if current_api == "anthropic":
        model_id = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")
        return get_model_display_name(model_id, "anthropic")
    elif current_api == "openai":
        return get_model_display_name("gpt-4o", "openai")  # default display for OpenAI
    elif current_api == "gemini":
        from promaia.nlq.models import get_current_google_model
        model_id = get_current_google_model()
        return get_model_display_name(model_id, "gemini")
    elif current_api == "llama":
        model_id = os.getenv('LLAMA_DEFAULT_MODEL', 'llama3:latest')
        return get_model_display_name(model_id, "llama")

    return "Unknown Model"

def switch_model(target_model=None):
    """Switch to a different AI model during chat session."""
    global current_api
    from promaia.nlq.models import get_model_display_name, ANTHROPIC_MODELS, GOOGLE_MODELS
    
    # Build available models dynamically
    from promaia.nlq.models import get_current_google_model
    # Build available models list dynamically
    available_choices = {}
    choice_num = 1

    # Add Anthropic models
    if anthropic_client:
        for key, model_id in ANTHROPIC_MODELS.items():
            display_name = get_model_display_name(model_id, "anthropic")
            available_choices[str(choice_num)] = ("anthropic", display_name, model_id)
            choice_num += 1

    # Add OpenAI models
    if openai_client:
        for model_id in ["gpt-5.2", "gpt-4o", "gpt-4o-mini"]:
            display_name = get_model_display_name(model_id, "openai")
            available_choices[str(choice_num)] = ("openai", display_name, model_id)
            choice_num += 1

    # Add Gemini models
    if gemini_client:
        for key in ["flash", "pro"]:
            if key in GOOGLE_MODELS:
                model_id = GOOGLE_MODELS[key]
                display_name = get_model_display_name(model_id, "gemini")
                available_choices[str(choice_num)] = ("gemini", display_name, model_id)
                choice_num += 1

    # Add Llama model
    if os.getenv("LLAMA_BASE_URL"):
        model_id = os.getenv('LLAMA_DEFAULT_MODEL', 'llama3:latest')
        display_name = get_model_display_name(model_id, "llama")
        available_choices[str(choice_num)] = ("llama", display_name, model_id)
        choice_num += 1
    
    if not available_choices:
        print_text("No AI models are available. Check your API keys.", style="bold red")
        return False
    
    # If target_model is specified, try to use it directly
    if target_model:
        target_model = target_model.lower()
        model_map = {
            "claude": "anthropic", "anthropic": "anthropic",
            "opus": "anthropic", "sonnet": "anthropic",
            "gpt": "openai", "openai": "openai",
            "gemini": "gemini", "google": "gemini",
            "flash": "gemini", "pro": "gemini",
            "llama": "llama", "local": "llama"
        }

        if target_model in model_map:
            new_api = model_map[target_model]
            # Check if this model is available - find first matching provider
            for choice_num, (api, name, model_id) in available_choices.items():
                if api == new_api:
                    current_api = new_api
                    os.environ["API_TYPE"] = current_api
                    os.environ["SELECTED_MODEL_ID"] = model_id
                    save_api_preference(current_api, model_id)
                    print_text(f"Switched to {name}", style="bold green")
                    return True

            print_text(f"Model '{target_model}' is not available. Check API keys.", style="bold red")
            return False

    # Interactive model selection
    print_text("Available models:", style="bold")

    # Get currently selected model ID
    current_model_id = os.getenv("SELECTED_MODEL_ID")
    debug_print(f"Current model ID from env: {current_model_id}, current_api: {current_api}")

    for choice, (api, name, model_id) in available_choices.items():
        # Mark as current only if the specific model ID matches
        # If no model ID is saved, mark the first model of the current API as current
        if current_model_id:
            is_current = (model_id == current_model_id)
        else:
            # No specific model selected - show first one of current API as current
            is_current = (api == current_api and not any(
                available_choices[c][0] == current_api and int(c) < int(choice)
                for c in available_choices.keys()
            ))
        current_indicator = " (current)" if is_current else ""
        print_text(f"  {choice}. {name}{current_indicator}", style="cyan")

    try:
        max_choice = len(available_choices)
        choice = input(f"Select model (1-{max_choice}): ").strip()
        if choice in available_choices:
            new_api, model_name, model_id = available_choices[choice]
            current_api = new_api
            os.environ["API_TYPE"] = current_api
            os.environ["SELECTED_MODEL_ID"] = model_id
            save_api_preference(current_api, model_id)
            print_text(f"Switched to {model_name}", style="bold green")
            return True
        else:
            print_text("Invalid choice.", style="bold red")
            return False
    except (KeyboardInterrupt, EOFError):
        print_text("\nModel switch cancelled.", style="bold yellow")
        return False

def display_message_with_timestamp(role, content):
    """Displays a message with a timestamp using copy-friendly Rich display."""
    if role == 'assistant':
        print_markdown(f"**Maia:** {content}")
    elif role == 'user':
        print_text(f"You: {content}", style="bold cyan")
    else:
        print_text(content, style="yellow")

def generate_source_breakdown(multi_source_data):
    """Generate a dictionary of source names to page counts.
    
    Always uses qualified names (workspace.database) for clarity.
    """
    if not multi_source_data:
        return None
    
    breakdown = {}
    for source_key, pages in multi_source_data.items():
        # Always use the full qualified name (workspace.database)
        # This provides clarity about which workspace each database belongs to
        source_name = source_key
            
        breakdown[source_name] = len(pages)
    
    return breakdown


def print_help_message(query_command, total_pages, model_name=None, source_breakdown=None):
    """Prints the detailed help message with command explanations."""
    print_text("🐙 maia chat", style="bold magenta")
    print_text(f"Query: {query_command}", style="dim")
    if total_pages > 0:
        print_text(f"Pages loaded: {total_pages}", style="dim")
        
        # Show breakdown by source if available
        if source_breakdown:
            for source_name, page_count in source_breakdown.items():
                print_text(f"{source_name}: {page_count}", style="dim")
                
    if model_name:
        print_text(f"Model: {model_name}", style="dim")
    print_text("Available commands: /quit /debug /push /help /s /e /save /model /temp /mail /queries /clear /mute /unmute", style="dim")
    print_text("  /s - Sync databases in current context", style="dim")
    print_text("  Ctrl+L - Quick inline sync (all or specific databases)", style="dim")
    print_text("  /clear - Clear all context (blank slate)", style="dim")
    print_text("  /mute - Temporarily hide context from AI", style="dim")
    print_text("  /unmute - Restore muted context", style="dim")
    print_text("  /e - Edit context (sources, filters, natural language)", style="dim")
    print_text("  /save - Save current conversation to history", style="dim")
    print_text("  /model - Switch AI model (Claude, GPT-4o, Gemini, Llama)", style="dim")
    print_text("  /temp - Adjust creativity (0.0=focused, 2.0=creative)", style="dim")
    print_text("  /m [n] - Manually edit artifact [n] with keyboard (defaults to latest)", style="dim")
    print_text("  /mail - Toggle AI-assisted email sending", style="dim")
    print_text("  /mcp search - Toggle internet search capabilities", style="dim")
    print_text("  /mcp fetch - Toggle URL fetching capabilities", style="dim")
    print_text("  /queries - List AI-generated queries in this session", style="dim")
    print_text("  /remove-query N - Remove query #N from context", style="dim")
    print_text("  /agent - Switch to agent mode (autonomous multi-tool execution)", style="dim")
    print_text("  /chat - Switch to chat mode (single response, no tools)", style="dim")
    print_text("")


def print_welcome_message(query_command, total_pages, model_name=None, source_breakdown=None):
    """Prints the welcome message for the chat interface."""
    print_text("🐙 maia chat", style="bold magenta")
    print_text(f"Query: {query_command}", style="dim")
    if total_pages > 0:
        print_text(f"Pages loaded: {total_pages}", style="dim")
        
        # Show breakdown by source if available
        if source_breakdown:
            for source_name, page_count in source_breakdown.items():
                print_text(f"{source_name}: {page_count}", style="dim")
                
    if model_name:
        print_text(f"Model: {model_name}", style="dim")
    print_text("Available commands: /quit /debug /push /help /s /e /save /model /temp /m /mail /queries /clear /mute /unmute", style="dim")
    print_text("Keyboard shortcuts: Ctrl+O (edit context) • Ctrl+L (quick sync) • Ctrl+B (browser)", style="dim")
    print_text("")


# --- Core Chat Logic ---

async def push_chat_to_notion(messages):
    """Pushes the current chat history to a new Notion page."""
    # This is a placeholder for the actual implementation
    print_text("\n[Pushing chat to Notion...]", style="yellow")
    await asyncio.sleep(1) # Simulate async operation
    return "Successfully pushed chat to Notion."

def call_anthropic(client, system_prompt, messages, max_tokens=4096, temperature=0.7, max_retries=3):
    """Call the Anthropic API with automatic model selection.

    Retries are handled by the SDK client (max_retries on the Anthropic constructor).
    """
    from promaia.nlq.models import ANTHROPIC_MODELS

    selected_model_id = os.getenv("SELECTED_MODEL_ID")
    if selected_model_id and "claude" in selected_model_id.lower():
        model_to_use = selected_model_id
    else:
        current_model_name = get_current_model_name()
        if "Opus" in current_model_name:
            model_to_use = ANTHROPIC_MODELS.get("opus", "claude-opus-4-5")
        else:
            model_to_use = ANTHROPIC_MODELS.get("sonnet", "claude-sonnet-4-5")

    debug_print(f"Using Anthropic model: {model_to_use}")

    response = client.messages.create(
        model=model_to_use,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response

def run_non_interactive_chat(messages: List[Dict[str, Any]], system_prompt: str, for_api: str):
    """Handles a single, non-interactive chat exchange."""
    pass

def _split_respecting_escaped_spaces(text: str) -> list[str]:
    """
    Split text on spaces while respecting escaped spaces (backslash followed by space).
    
    Args:
        text: Text to split
        
    Returns:
        List of parts with escaped spaces properly handled
    """
    import re
    
    # Replace escaped spaces with a placeholder
    placeholder = "__ESCAPED_SPACE__"
    text_with_placeholder = text.replace('\\ ', placeholder)
    
    # Split on regular spaces
    parts = text_with_placeholder.split()
    
    # Restore escaped spaces by removing backslash and replacing placeholder
    restored_parts = []
    for part in parts:
        restored_part = part.replace(placeholder, ' ')
        restored_parts.append(restored_part)
    
    return restored_parts

def _parse_image_paths_and_message(input_text: str) -> tuple[list[str], str]:
    """
    Parse multiple image paths and message text from input.
    
    Args:
        input_text: The text after '/image' command
        
    Returns:
        Tuple of (image_paths, message_text)
    """
    import re
    from pathlib import Path
    
    # Use smart splitting that respects escaped spaces
    parts = _split_respecting_escaped_spaces(input_text)
    image_paths = []
    message_parts = []
    
    for part in parts:
        # Check if this looks like a file path and has an image extension
        if _is_likely_image_path(part):
            image_paths.append(part)
        else:
            message_parts.append(part)
    
    message_text = ' '.join(message_parts)
    return image_paths, message_text

def _is_likely_file_path(text: str) -> tuple[bool, str]:
    """
    Check if a string looks like a file path and determine its type.

    Args:
        text: String to check

    Returns:
        Tuple of (is_file_path, file_type) where file_type is 'image', 'document', or ''
    """
    # Common file extensions by category
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg'}
    document_extensions = {'.pdf', '.doc', '.docx', '.txt', '.md', '.csv', '.xls', '.xlsx'}

    # Remove escaped spaces to check the actual path structure
    # Escaped spaces (\ ) should be treated as part of the filename, not word boundaries
    text_unescaped = text.replace('\\ ', '_SPACE_')

    # Early exit: Exclude URLs from file path detection
    # URLs should not be treated as local file paths
    if text.startswith(('http://', 'https://', 'ftp://', 'ftps://', 'www.')):
        return (False, '')

    # Exclude any string with protocol indicators (e.g., custom protocols)
    if '://' in text:
        return (False, '')

    # Exclude email addresses
    if '@' in text and '/' not in text.split('@')[0]:
        return (False, '')

    # Check if it has a known file extension
    path = Path(text.lower())
    if path.suffix in image_extensions:
        return (True, 'image')
    if path.suffix in document_extensions:
        return (True, 'document')

    # Check if it looks like a path (contains / or \ and doesn't look like a sentence)
    if ('/' in text or '\\' in text) and not text.endswith('.'):
        # Must be a single word/path, not a sentence
        # Use the unescaped version for word counting to handle escaped spaces properly
        if len(text_unescaped.split()) != 1:
            return (False, '')

        # Additional validation to avoid false positives like "VAT/EORI"

        # 1. Check if it starts with path indicators (relative/absolute paths)
        if text.startswith(('./', '../', '~/', '/', '\\')):
            # Check extension if it has one
            if path.suffix in image_extensions:
                return (True, 'image')
            if path.suffix in document_extensions:
                return (True, 'document')
            # Generic file path
            return (True, 'unknown')

        # 2. Check if it has multiple path components (not just "word/word")
        path_parts = [p for p in text.replace('\\', '/').split('/') if p]
        if len(path_parts) >= 3:  # At least something like "dir/subdir/file"
            # Check extension
            if path.suffix in image_extensions:
                return (True, 'image')
            if path.suffix in document_extensions:
                return (True, 'document')
            return (True, 'unknown')

        # 3. Check if any component has a file extension
        for part in path_parts:
            if '.' in part and not part.startswith('.'):
                # Has an extension, could be a file path
                part_path = Path(part.lower())
                if part_path.suffix in image_extensions:
                    return (True, 'image')
                if part_path.suffix in document_extensions:
                    return (True, 'document')
                return (True, 'unknown')

        # 4. Avoid acronym patterns (e.g., "VAT/EORI" - uppercase words with slash)
        if all(part.isupper() or part.isdigit() for part in path_parts if part):
            return (False, '')

        # 5. Check if it's a common path pattern with current directory
        if len(path_parts) == 2:
            # Could be "dir/file" - check if second part looks like a filename
            last_part = path_parts[-1]
            if '.' in last_part or any(char.isdigit() for char in last_part):
                # Check extension
                if path.suffix in image_extensions:
                    return (True, 'image')
                if path.suffix in document_extensions:
                    return (True, 'document')
                return (True, 'unknown')

    return (False, '')

def _is_likely_image_path(text: str) -> bool:
    """
    Check if a string looks like an image file path.

    Backward compatibility wrapper for _is_likely_file_path.

    Args:
        text: String to check

    Returns:
        True if it looks like an image path
    """
    is_file, file_type = _is_likely_file_path(text)
    return is_file and file_type == 'image'

def _is_likely_document_path(text: str) -> bool:
    """
    Check if a string looks like a document file path (PDF, DOCX, etc.).

    Args:
        text: String to check

    Returns:
        True if it looks like a document path
    """
    is_file, file_type = _is_likely_file_path(text)
    return is_file and file_type == 'document'

# Preserve old function name for compatibility - now checks all file types
def _is_likely_any_file_path(text: str) -> bool:
    """
    Check if a string looks like any supported file path (image or document).

    Args:
        text: String to check

    Returns:
        True if it looks like a supported file path
    """
    is_file, file_type = _is_likely_file_path(text)
    return is_file and file_type in ('image', 'document')

def _detect_file_paths_in_message(user_input: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Detect file paths (images and documents) in a regular message.

    Args:
        user_input: The full user message

    Returns:
        Tuple of (cleaned_message, file_paths) where file_paths is list of (path, type) tuples
    """
    # Use smart splitting that respects escaped spaces
    words = _split_respecting_escaped_spaces(user_input)
    file_paths = []
    remaining_words = []

    for word in words:
        is_file, file_type = _is_likely_file_path(word)
        if is_file and file_type in ('image', 'document'):
            file_paths.append((word, file_type))
        else:
            remaining_words.append(word)

    cleaned_message = ' '.join(remaining_words)
    return cleaned_message, file_paths

def _detect_image_paths_in_message(user_input: str) -> tuple[str, list[str]]:
    """
    Detect image paths in a regular message without /image prefix.

    Backward compatibility wrapper for _detect_file_paths_in_message.

    Args:
        user_input: The full user message

    Returns:
        Tuple of (cleaned_message, image_paths)
    """
    cleaned_message, file_paths = _detect_file_paths_in_message(user_input)
    # Filter only images
    image_paths = [path for path, file_type in file_paths if file_type == 'image']
    return cleaned_message, image_paths

def safe_split_command(user_input):
    """
    Safely split command arguments, handling natural language and vector search queries.

    NOTE: This implementation must stay in sync with the edit mode parsing.
    Must support multiple -nl and -vs arguments (e.g., "-nl query1 -vs query2").
    See also: promaia/cli.py for the CLI-side implementation.
    """
    # Clean up whitespace first
    cleaned = ' '.join(user_input.split())

    # For natural language or vector search queries, handle them specially
    # IMPORTANT: Must support multiple -nl and -vs arguments
    if '-nl' in cleaned or '-vs' in cleaned:
        # Find all flag positions (both -nl and -vs)
        result = []
        current_pos = 0

        while current_pos < len(cleaned):
            # Find next -nl or -vs flag
            nl_pos = cleaned.find('-nl', current_pos)
            vs_pos = cleaned.find('-vs', current_pos)

            # Determine which flag comes next
            next_flag_pos = None
            next_flag = None
            next_flag_len = 0

            if nl_pos != -1 and vs_pos != -1:
                # Both found, use whichever comes first
                if nl_pos < vs_pos:
                    next_flag_pos = nl_pos
                    next_flag = '-nl'
                    next_flag_len = 3
                else:
                    next_flag_pos = vs_pos
                    next_flag = '-vs'
                    next_flag_len = 3
            elif nl_pos != -1:
                next_flag_pos = nl_pos
                next_flag = '-nl'
                next_flag_len = 3
            elif vs_pos != -1:
                next_flag_pos = vs_pos
                next_flag = '-vs'
                next_flag_len = 3

            if next_flag_pos is None:
                # No more flags found
                if not result or result[-1] in ['-nl', '-vs']:
                    # We're in a query section, add the rest as-is
                    remaining = cleaned[current_pos:].strip()
                    if remaining:
                        result.extend(remaining.split())
                else:
                    # Process remaining non-query arguments
                    remaining = cleaned[current_pos:].strip()
                    if remaining:
                        try:
                            result.extend(shlex.split(remaining))
                        except ValueError:
                            result.extend(remaining.split())
                break

            # Check if this is a real flag (preceded by space or at start, followed by space or end)
            is_real_flag = (next_flag_pos == 0 or cleaned[next_flag_pos-1].isspace()) and \
                          (next_flag_pos + next_flag_len >= len(cleaned) or cleaned[next_flag_pos+next_flag_len].isspace())

            if not is_real_flag:
                # Not a real flag, keep searching after this position
                current_pos = next_flag_pos + 1
                continue

            # Process the part before this flag
            before_flag = cleaned[current_pos:next_flag_pos].strip()

            if before_flag:
                if not result or result[-1] in ['-nl', '-vs']:
                    # Previous section was a query, add as-is (split on spaces)
                    result.extend(before_flag.split())
                else:
                    # This is regular arguments before first query flag
                    try:
                        result.extend(shlex.split(before_flag))
                    except ValueError:
                        result.extend(before_flag.split())

            # Add the flag
            result.append(next_flag)

            # Move past this flag
            current_pos = next_flag_pos + next_flag_len

        return result
    
    # For non-natural language commands, try normal shlex first
    try:
        return shlex.split(cleaned)
    except ValueError:
        # Fall back to simple split if shlex fails
        return cleaned.split()

def save_context_log(context_state, system_prompt, total_pages_loaded, current_api, log_type="session_init"):
    """Save a context log file with current session information."""
    try:
        from promaia.config.databases import get_database_manager
        db_manager = get_database_manager()
        should_save_context = db_manager.global_settings.get("savecontexts", True)
    except Exception as e:
        debug_print(f"Could not load savecontexts config, defaulting to True: {e}")
        should_save_context = True
    
    if not should_save_context:
        return
    
    try:
        from promaia.utils.env_writer import get_data_dir
        log_dir = get_data_dir() / "context_logs" / "chat_context_logs"

        timestamp = now_utc().strftime("%Y%m%d-%H%M%S")
        context_filename = str(log_dir / f"{timestamp}_{log_type}_prompt.md")

        # Ensure context logs directory exists
        os.makedirs(log_dir, exist_ok=True)

        # Write context file with session info
        with open(context_filename, 'w', encoding='utf-8') as f:
            if log_type == "session_init":
                f.write("=== MAIA CHAT SESSION INITIALIZATION ===\n")
            elif log_type == "context_update":
                f.write("=== MAIA CHAT CONTEXT UPDATE ===\n")
            else:
                f.write(f"=== MAIA CHAT {log_type.upper()} ===\n")
                
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"API Type: {current_api}\n")
            f.write(f"Workspace: {context_state.get('workspace')}\n")
            f.write(f"Resolved Workspace: {context_state.get('resolved_workspace')}\n")
            f.write(f"Sources: {context_state.get('sources')}\n")
            f.write(f"Filters: {context_state.get('filters')}\n")
            f.write(f"Natural Language Prompt: {context_state.get('sql_query_prompt')}\n")
            f.write(f"Query Command: {context_state.get('query_command')}\n")
            f.write(f"Agentic Tools: {context_state.get('agentic_tools', [])}\n")
            f.write(f"Agentic Databases: {context_state.get('agentic_databases', [])}\n")
            f.write(f"Total Pages Loaded: {total_pages_loaded}\n")
            f.write(f"System Prompt Length: {len(system_prompt)} characters\n")
            f.write("\n" + "="*50 + "\n")
            f.write("SYSTEM PROMPT:\n")
            f.write("="*50 + "\n")
            f.write(system_prompt)

        debug_print(f"Context log saved: {context_filename}")
        return context_filename
    except Exception as e:
        debug_print(f"Failed to save context log: {e}")
        return None


def process_browser_selections(selected_sources):
    processed_sources = []
    processed_filters = []
    channel_db_groups = {}  # (db_name, days_part) -> list of channel names

    from promaia.config.databases import get_database_manager
    db_manager = get_database_manager()

    # Group channel-based sources by database AND days combination
    for source in selected_sources:
        if '#' in source:
            db_channel, days_part = source.rsplit(':', 1)
            db_name, channel_name = db_channel.split('#', 1)

            # Group by (database, days) tuple so each unique day value creates separate source
            group_key = (db_name, days_part)

            if group_key not in channel_db_groups:
                channel_db_groups[group_key] = []

            channel_db_groups[group_key].append(channel_name)
        else:
            processed_sources.append(source)

    # Create separate source per database:days combination with channel filter
    for (db_name, days_part), channels in channel_db_groups.items():
        # Build source spec with specific days value
        db_spec = f"{db_name}:{days_part}"

        processed_sources.append(db_spec)

        # Determine the correct channel property name based on source type
        db_config = db_manager.get_database_by_qualified_name(db_name)
        if db_config and db_config.source_type == 'slack':
            channel_prop = 'slack_channel_name'
        else:
            channel_prop = 'discord_channel_name'

        # Build filter with OR logic for all channels in this day group
        if len(channels) == 1:
            # Single channel - use period separator
            filter_spec = f"{db_spec}.{channel_prop}={channels[0]}"
            processed_filters.append(filter_spec)
        else:
            # Multiple channels - use OR logic with colon separator
            channel_conditions = [f"{channel_prop}={ch}" for ch in channels]
            combined_filter = " or ".join(channel_conditions)
            filter_spec = f"{db_spec}:({combined_filter})"
            processed_filters.append(filter_spec)

    return processed_sources, processed_filters


def build_system_prompt_with_mode(multi_source_data, mcp_tools_info, mode_system_prompt=None, mode=None, include_query_tools=True, workspace=None):
    """
    Build system prompt, respecting mode-specific prompts while including context.

    Args:
        multi_source_data: Dict of database_name -> pages
        mcp_tools_info: MCP tools info string
        mode_system_prompt: Optional mode-specific base prompt
        mode: Optional ChatMode instance to check if it handles its own context
        include_query_tools: Whether to include built-in query tools (default: True)
        workspace: Current workspace for database preview (default: None)

    Returns:
        Complete system prompt with context
    """
    from promaia.nlq.prompts import create_system_prompt, format_context_data

    if mode_system_prompt:
        # Check if mode handles its own context formatting
        if mode and hasattr(mode, 'handles_own_context') and mode.handles_own_context():
            # Mode builds complete prompt with context - don't append generic format
            return mode_system_prompt
        else:
            # Start with mode prompt, append context so AI has access to both
            return mode_system_prompt + format_context_data(multi_source_data, mcp_tools_info)
    else:
        # Standard prompt with context
        return create_system_prompt(multi_source_data, mcp_tools_info, include_query_tools, workspace)


def chat(sources=None, filters=None, workspace=None, resolved_workspace=None, non_interactive=False, initial_messages=None, current_thread_id=None, sql_query_content=None, sql_query_prompt=None, original_browse_command=None, browse_selections=None, browse_databases=None, mcp_servers=None, is_vector_search=False, initial_nl_prompt=None, initial_nl_content=None, initial_vs_prompt=None, initial_vs_content=None, mode=None, mode_config=None, draft_id=None, auto_respond_to_initial=False, top_k=None, threshold=None, vector_search_queries=None, initial_vs_per_query_cache=None):
    """
    Main chat function with simplified, unified logic.

    Args:
        mode: ChatMode instance for specialized behavior
        mode_config: Additional mode configuration dict
        auto_respond_to_initial: If True, automatically trigger AI response to initial user message before interactive loop
        ... (other existing args)
    """
    global current_api, DEBUG_MODE
    
    # Debug logging for mode activation
    if mode:
        logger.info(f"🎭 Chat called with mode: {type(mode).__name__}")
        logger.info(f"   Workspace: {workspace}")
        logger.info(f"   Natural language content: {bool(sql_query_content)}")
        logger.info(f"   Initial messages: {len(initial_messages) if initial_messages else 0}")
        
        # Prevent browser auto-launch when mode is active (e.g., draft mode has pre-loaded context)
        logger.info("🎭 Mode active - skipping browser auto-launch")
        browse_databases = None

    # Detect mixed commands: when user provides both sources and browse arguments
    has_regular_sources = bool(sources)
    has_browse_command = bool(browse_databases) or bool(original_browse_command and '-b' in original_browse_command)
    has_natural_language = bool(sql_query_prompt)

    
    # Detect mixed browse+NL commands from CLI: sources from browser + natural language
    # These should use OR logic (independent operation) not AND logic (filtering)
    # CLI mixed commands have: sources (from browser) + sql_query_prompt + original_browse_command + browse_databases=None
    is_cli_mixed_command = has_regular_sources and has_natural_language and browse_selections and not browse_databases
    debug_print(f"🐛 Mixed command check: has_regular_sources={has_regular_sources}, has_natural_language={has_natural_language}, browse_selections={bool(browse_selections)}, has_browse_command={has_browse_command}, browse_databases={bool(browse_databases)}")
    is_mixed_browse_nl_command = is_cli_mixed_command
    
    if is_mixed_browse_nl_command:
        debug_print(f"🔍 Detected mixed browse+NL command: sources={bool(sources)}, nl={bool(sql_query_prompt)}, browse_sel={bool(browse_selections)}, browse_cmd={has_browse_command}")
    else:
        debug_print(f"🚫 NOT detected as mixed browse+NL command")
    
    # Mixed command flow: -s sources + -b browse + -nl (optional)
    # This should: 1) Load -s sources first, 2) Launch browser with sources as context, 3) Process -nl last
    # Handle both cases: CLI launching browser first, or CLI already processed browser and calling with results
    if has_regular_sources and has_browse_command and not browse_selections:
        debug_print("🔄 Detected mixed command: sources + browse. Processing in sequence...")
        print_text("🔄 Processing mixed command: loading sources first, then launching browser...", style="cyan")
        
        
        # Step 1: Parse and prepare regular sources first
        print_text(f"📦 Preparing {len(sources)} regular sources for browser context...", style="cyan")
        
        # Import unified browser and workspace management
        from promaia.cli.workspace_browser import launch_unified_browser
        from promaia.config.workspaces import get_workspace_manager
        from promaia.config.databases import get_database_manager
        
        workspace_manager = get_workspace_manager()
        db_manager = get_database_manager()
        
        # Determine workspace and database filter from browse_databases
        database_filter = None
        default_days = None
        browse_workspace = resolved_workspace or workspace
        
        if browse_databases:
            database_filter = []
            workspace_names = []
            
            for browse_spec in browse_databases:
                if ':' in browse_spec:
                    db_name, days_str = browse_spec.rsplit(':', 1)
                    try:
                        days = int(days_str)
                        if default_days is None:
                            default_days = days
                        
                        # Check if db_name is a workspace
                        if workspace_manager.validate_workspace(db_name):
                            # Don't expand workspace - let browser handle it
                            workspace_names.append(db_name)
                            if not browse_workspace:
                                browse_workspace = db_name
                        else:
                            database_filter.append(browse_spec)  # Keep the full spec with days
                    except ValueError:
                        database_filter.append(browse_spec)
                else:
                    # Check if this is a workspace name
                    if workspace_manager.validate_workspace(browse_spec):
                        # Don't expand workspace - let browser handle it
                        workspace_names.append(browse_spec)
                        if not browse_workspace:
                            browse_workspace = browse_spec
                    else:
                        # It's a specific database name
                        database_filter.append(browse_spec)
            
            # If we only have workspace names, clear the database filter
            if workspace_names and not database_filter:
                database_filter = None  # Let browser show all databases in the workspace
        
        # Step 2: Launch browser with regular sources as context
        print_text("🔍 Launching browser with regular sources as context...", style="cyan")
        
        # For workspace browse commands, we want to default to ALL workspace sources selected
        # Plus include the regular sources (like journal:30)
        browser_current_sources = list(sources)  # Start with regular sources like journal:30
        
        # Check if this is a workspace browse by seeing if we have a workspace but no specific database filter
        is_workspace_browse = browse_workspace and database_filter is None
        
        if is_workspace_browse:
            # For workspace browse, add all workspace databases as selected by default
            workspace_databases = db_manager.get_workspace_databases(browse_workspace)
            for db in workspace_databases:
                if db.browser_include:  # Only include databases visible in browser
                    if default_days:
                        source_with_days = f"{db.get_qualified_name()}:{default_days}"
                    else:
                        source_with_days = f"{db.get_qualified_name()}:7"  # Default to 7 days
                    browser_current_sources.append(source_with_days)
        
        # Launch unified browser with existing sources as context
        browser_result = launch_unified_browser(
            workspace=browse_workspace,
            default_days=default_days,
            database_filter=database_filter,
            current_sources=browser_current_sources  # Pre-populate with all workspace sources + regular sources
        )
        selected_sources = browser_result.sources if not browser_result.cancelled else None

        if browser_result.cancelled:
            # User cancelled (ESC) - keep current context unchanged
            if not sources or len(sources) == 0:
                print_text("Browser cancelled. Only prompt, no context.", style="yellow")
                browse_selections = []
                combined_multi_source_data = {}
                initial_multi_source_data = {}
                context_state['sources'] = []
                total_pages_loaded = 0
            else:
                print_text("Browser cancelled. Using only the regular sources.", style="yellow")
                browse_selections = []
        elif not selected_sources:
            # User confirmed with nothing selected - clear browser context
            print_text("No sources selected. Browser context cleared.", style="yellow")
            browse_selections = []
            combined_multi_source_data = {}
            initial_multi_source_data = {}
            context_state['sources'] = [] if not sources else sources  # Keep regular -s sources
            total_pages_loaded = 0
        else:
            # Store ALL browser selections for persistence (not just additional ones)
            browse_selections = selected_sources.copy()  # Store all selected sources

            print_text(f"Browser selections: {len(browse_selections)} sources selected", style="green")

            # Store in context_state for /e functionality
            context_state['browse_selections'] = browse_selections
        
        # Step 3: Store the original mixed command format and browser selections
        # Build the original command format to preserve -s and -b structure
        query_parts = ["maia", "chat"]
        if sources:
            for source in sources:
                query_parts.extend(["-s", source])
        if browse_databases:
            query_parts.append("-b")
            query_parts.extend(browse_databases)
        if filters:
            for filter_expr in filters:
                query_parts.extend(["-f", f'"{filter_expr}"'])
        if workspace:
            query_parts.extend(["-ws", workspace])
        if sql_query_prompt:
            query_parts.extend(["-sql", sql_query_prompt])
        if mcp_servers:
            for server in mcp_servers:
                query_parts.extend(["-mcp", server])
        
        # Store the original command format and browser selections
        original_mixed_command = " ".join(query_parts)
        print_text(f"📝 Preserving original command format: {original_mixed_command}", style="dim")
        
        # Step 4: Process browser selections to handle Discord channels correctly
        # This separates Discord channels into database+filter format
        processed_browser_sources = []
        processed_browser_filters = []
        if selected_sources:
            processed_browser_sources, processed_browser_filters = process_browser_selections(selected_sources)
            print_text(f"🔍 Processed browser selections: {len(processed_browser_sources)} sources, {len(processed_browser_filters)} filters", style="dim")
        
        # Step 5: Combine all sources and filters for the main chat flow
        all_sources = list(sources)  # Start with regular sources like journal:30
        all_filters = list(filters) if filters else []
        
        # Add processed browser sources (Discord channels converted to database specs)
        for processed_source in processed_browser_sources:
            # Check if not already in sources
            source_base = processed_source.split(':')[0]
            already_in_sources = any(source_base in existing.split(':')[0] for existing in all_sources)
            if not already_in_sources:
                all_sources.append(processed_source)
        
        # Add processed browser filters (Discord channel filters)
        all_filters.extend(processed_browser_filters)
        
        # Update sources and filters for the main chat flow
        sources = all_sources
        filters = all_filters if all_filters else None
        print_text(f"🔗 Combined sources: {len(sources)} sources, {len(filters) if filters else 0} filters", style="blue")
        
        # Set these for context state initialization
        original_browse_command = f"maia chat {original_browse_command}" if original_browse_command else None
        
        # Clear browse_databases since we've processed it and stored the results
        browse_databases = None
        print_text("🚀 Proceeding with normal chat flow preserving original format...", style="green")
        
        # Continue to normal chat processing below with the combined sources

    # Parse original_browse_command if provided (from history loading)
    if original_browse_command and not sources and not filters:
        try:
            # Parse the browse command to extract sources and filters
            import shlex
            import argparse
            
            # Extract arguments from the command
            if original_browse_command.startswith("maia chat "):
                command_args = original_browse_command[10:]  # Remove "maia chat "
            else:
                command_args = original_browse_command
            
            # Parse the command
            args_list = safe_split_command(command_args)
            
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("-s", "--source", action="append", dest="sources")
            parser.add_argument("-f", "--filter", action="append", dest="filters")
            parser.add_argument("-w", "--workspace", dest="workspace")
            parser.add_argument("-b", "--browse", nargs="*", dest="browse")
            parser.add_argument("-sql", "--sql-query", nargs="*", dest="sql_query")
            parser.add_argument("-nl", nargs="*", dest="sql_query", help=argparse.SUPPRESS)  # Deprecated alias
            parser.add_argument("-vs", "--vector-search", nargs="*", dest="vector_search")
            parser.add_argument("-tk", "--top-k", type=int, dest="top_k")
            parser.add_argument("-th", "--threshold", type=float, dest="threshold")
            parser.add_argument("-mcp", action="append", dest="mcp_servers")
            parser.add_argument("-dc", "--draft-context", action="store_true", dest="draft_context")
            parsed_args, unknown = parser.parse_known_args(args_list)
            
            # Extract parsed values
            if parsed_args.sources:
                sources = parsed_args.sources
            if parsed_args.filters:
                filters = parsed_args.filters
            if parsed_args.workspace and not workspace:
                workspace = parsed_args.workspace
            if parsed_args.sql_query and not sql_query_prompt:
                sql_query_prompt = " ".join(parsed_args.sql_query)
            if parsed_args.mcp_servers and not mcp_servers:
                mcp_servers = parsed_args.mcp_servers
                
            debug_print(f"Parsed browse command: sources={sources}, filters={filters}, workspace={workspace}")
            
        except Exception as e:
            debug_print(f"Error parsing original_browse_command: {e}")
            # Continue with original values

    # Context state tracking for dynamic changes
    context_state = {
        'sources': sources,
        'filters': filters,
        'artifact_manager': None,  # Lazy-initialized artifact manager
        'workspace': workspace,
        'resolved_workspace': resolved_workspace,
        'initial_multi_source_data': {},
        'total_pages_loaded': 0,
        'system_prompt': None,
        'query_command': None,
        'current_thread_id': current_thread_id,  # Track if we're continuing a thread
        'sql_query_content': None,  # Will be set from initial_nl_content if provided
        'vector_search_content': None,  # Store VS content separately for independent tracking
        'vector_search_per_query_cache': initial_vs_per_query_cache if initial_vs_per_query_cache else {},  # Per-query cache for efficient -vs editing
        'vector_search_queries': vector_search_queries if vector_search_queries else [],  # Store individual -vs queries as list
        'browse_selections': browse_selections if browse_selections is not None else [],  # Store browser selections from CLI
        'sql_query_prompt': sql_query_prompt,  # Store the original NL prompt
        'is_vector_search': is_vector_search,  # Track if using vector search instead of natural language
        'mcp_servers': mcp_servers,  # Store MCP server names to include
        'mcp_tools_info': None,  # Store MCP tools information for prompt
        'original_browse_mode': bool(original_browse_command),  # Track if session started with browse mode
        'enable_search': False,  # Store search functionality flag (starts disabled)
        'enable_email_send': False,  # Store email sending functionality flag (starts disabled)
        'original_query_format': original_browse_command,  # Store the original query format for display
        'is_mixed_browse_nl_command': is_mixed_browse_nl_command,  # Flag for OR logic in NL processing
        'mode': mode,  # Store chat mode for specialized behavior
        'mode_config': mode_config or {},  # Store mode configuration
        'top_k': top_k if top_k is not None else 60,  # Maximum vector search results
        'threshold': threshold if threshold is not None else 0.2,  # Minimum similarity threshold
        'ai_queries': [],  # Track AI-generated queries: [{'id': str, 'type': str, 'query': str, 'params': dict, 'timestamp': str}]
        'query_iteration_count': 0,  # Track iterations per user message for loop control
        'loaded_image_paths': [],  # Track successfully loaded image file paths for email attachments
        'agentic_mode': True,  # Agentic loop mode (multi-tool autonomous execution) — on by default
        'agentic_tools': [],  # Detected MCP tools for agentic mode
        'agentic_databases': [],  # Databases available for agentic mode
    }
    
    # Detect agentic tools and databases (agent mode is on by default for Anthropic)
    if current_api == "anthropic":
        from promaia.chat.agentic_adapter import detect_available_tools, _resolve_workspace
        from promaia.config.databases import get_database_manager
        ws = _resolve_workspace(workspace or resolved_workspace or "")
        detected_tools = detect_available_tools(ws)
        try:
            db_mgr = get_database_manager()
            detected_dbs = db_mgr.list_databases(workspace=ws) if ws else []
        except Exception:
            detected_dbs = []
        context_state['agentic_tools'] = detected_tools
        context_state['agentic_databases'] = detected_dbs
    else:
        # Non-Anthropic API: disable agentic mode
        context_state['agentic_mode'] = False

    # Mode-specific setup
    mode_system_prompt = None
    mode_commands = {}
    if mode:
        # Get mode-specific system prompt
        mode_system_prompt = mode.get_system_prompt()
        if mode_system_prompt:
            logger.info(f"Using system prompt from mode: {type(mode).__name__}")
        
        # Get mode-specific commands
        mode_commands = mode.get_additional_commands()
        if mode_commands:
            logger.info(f"Mode {type(mode).__name__} added commands: {list(mode_commands.keys())}")
        
        # Initialize artifact manager immediately for mode (e.g., draft mode needs it for initial draft)
        from promaia.chat.artifacts import ArtifactManager
        context_state['artifact_manager'] = ArtifactManager()
        logger.info("🎨 Initialized artifact manager for mode")

    # Initialize NL and VS content separately from CLI
    if initial_nl_content:
        context_state['sql_query_content'] = initial_nl_content
        debug_print(f"🔧 Initialized NL content from CLI: {len(initial_nl_content)} databases, {sum(len(pages) for pages in initial_nl_content.values() if pages is not None)} pages")
    elif sql_query_content:
        # Backwards compatibility: if sql_query_content parameter is provided (old code path)
        context_state['sql_query_content'] = sql_query_content
        debug_print(f"🔧 Initialized NL content from parameter: {len(sql_query_content)} databases")

    if initial_vs_content:
        context_state['vector_search_content'] = initial_vs_content
        debug_print(f"🔧 Initialized VS content from CLI: {len(initial_vs_content)} databases, {sum(len(pages) for pages in initial_vs_content.values() if pages is not None)} pages")

    # Set up separate caches for NL and VS if provided by CLI
    if initial_nl_prompt or initial_nl_content:
        context_state['cached_sql_query_prompt'] = initial_nl_prompt or ''
        context_state['cached_sql_query_content'] = initial_nl_content or {}
        debug_print(f"🔧 Set up NL cache from CLI: prompt='{initial_nl_prompt}'")

    if initial_vs_prompt or initial_vs_content:
        context_state['cached_vector_search_prompt'] = initial_vs_prompt or ''
        context_state['cached_vector_search_content'] = initial_vs_content or {}
        debug_print(f"🔧 Set up VS cache from CLI: prompt='{initial_vs_prompt}'")

    # Update context_state with browse_selections if they were set during browser interaction
    # This handles the case where browse_selections were set locally but not captured in the parameter
    if 'browse_selections' in locals() and browse_selections:
        context_state['browse_selections'] = browse_selections
    # Also store browse_selections if passed as parameter (for CLI mixed commands)
    elif browse_selections:
        context_state['browse_selections'] = browse_selections
    
    # Debug: Show what browse_selections were stored
    # if browse_selections:
    #     debug_print(f"STORED browse_selections in context_state: {browse_selections}")

    def update_query_command():
        """Update the query command display based on current context state."""
        # If we have an original query format (mixed commands, browse commands, etc.), show it
        if context_state.get('original_query_format'):
            context_state['query_command'] = context_state['original_query_format']
            return
        
        # Otherwise, build the command from current state
        query_parts = ["maia", "chat"]
        if context_state['sources']:
            if len(context_state['sources']) == 1:
                query_parts.extend(["-s", context_state['sources'][0]])
            else:
                for source in context_state['sources']:
                    query_parts.extend(["-s", source])
        if context_state['filters']:
            for filter_expr in context_state['filters']:
                query_parts.extend(["-f", f'"{filter_expr}"'])
        if context_state['workspace']:  # Only show workspace if explicitly provided by user
            query_parts.extend(["-ws", context_state['workspace']])
        # Check for vector search queries first (new format with multiple -vs)
        has_structured_queries = False
        if context_state.get('vector_search_queries'):
            for vs_query in context_state['vector_search_queries']:
                # Handle both dict format (new) and string format (old/backward compatibility)
                if isinstance(vs_query, dict):
                    has_structured_queries = True
                    query_parts.extend(["-vs", vs_query['query']])
                    # Include per-query parameters if they differ from defaults
                    if vs_query.get('top_k', 20) != 20:
                        query_parts.extend(["-tk", str(vs_query['top_k'])])
                    if vs_query.get('threshold', 0.75) != 0.75:
                        query_parts.extend(["-th", str(vs_query['threshold'])])
                else:
                    # Old format: just a string
                    query_parts.extend(["-vs", vs_query])
        elif context_state['sql_query_prompt']:
            nl_prompt = context_state['sql_query_prompt']
            # Don't add quotes - the -sql argument parser handles multiple words with nargs="*"
            # Adding quotes is redundant and makes commands harder to read and copy
            query_parts.extend(["-sql", nl_prompt])
        if context_state['mcp_servers']:
            for server in context_state['mcp_servers']:
                query_parts.extend(["-mcp", server])

        # Note: Don't include top_k/threshold in display unless explicitly set by user
        # These are config defaults and shouldn't clutter the command display

        # Update query_command but preserve original_query_format if it exists
        built_command = " ".join(query_parts)
        context_state['query_command'] = built_command
        # Only update original_query_format if it doesn't exist
        if not context_state.get('original_query_format'):
            context_state['original_query_format'] = built_command

    # Initial query command setup - capture original format for regular commands too
    if not context_state.get('original_query_format'):
        if original_browse_command:
            # Use the provided original browse command
            context_state['original_query_format'] = original_browse_command
        elif sources or filters or workspace or sql_query_prompt or mcp_servers or vector_search_queries:
            # Build and store the original query format for regular commands to preserve day specifications
            query_parts = ["maia", "chat"]
            if sources:
                for source in sources:
                    query_parts.extend(["-s", source])
            if filters:
                for filter_expr in filters:
                    query_parts.extend(["-f", f'"{filter_expr}"'])
            if workspace:
                query_parts.extend(["-ws", workspace])
            # Check for vector search queries first (multiple -vs format)
            if vector_search_queries:
                for vs_query in vector_search_queries:
                    # Handle both dict format (new) and string format (old/backward compatibility)
                    if isinstance(vs_query, dict):
                        query_parts.extend(["-vs", vs_query['query']])
                        # Include per-query parameters if they differ from defaults
                        if vs_query.get('top_k', 20) != 20:
                            query_parts.extend(["-tk", str(vs_query['top_k'])])
                        if vs_query.get('threshold', 0.75) != 0.75:
                            query_parts.extend(["-th", str(vs_query['threshold'])])
                    else:
                        # Old format: just a string
                        query_parts.extend(["-vs", vs_query])
            elif sql_query_prompt:
                query_parts.extend(["-sql", sql_query_prompt])
            if mcp_servers:
                for server in mcp_servers:
                    query_parts.extend(["-mcp", server])
            context_state['original_query_format'] = " ".join(query_parts)
    
    update_query_command()
    query_command = context_state['query_command']

    def reload_context(skip_nl_cache_messages=False):
        """Reload the chat context with current state configuration."""
        nonlocal initial_multi_source_data, total_pages_loaded, system_prompt, query_command, sql_query_content, sources

        # Debug: Log reload_context entry
        debug_print(f"\n🔄 reload_context() called:")
        debug_print(f"  context_state['sources']: {context_state.get('sources', [])}")
        debug_print(f"  context_state['sql_query_prompt']: {bool(context_state.get('sql_query_prompt'))}")
        debug_print(f"  context_state['sql_query_content']: {len(context_state.get('sql_query_content', {})) if context_state.get('sql_query_content') else 0} databases")
        debug_print(f"  context_state['vector_search_content']: {len(context_state.get('vector_search_content', {})) if context_state.get('vector_search_content') else 0} databases")
        debug_print(f"  context_state['is_mixed_browse_nl_command']: {context_state.get('is_mixed_browse_nl_command')}")

        # Initialize combined data container
        combined_multi_source_data = {}

        # Check if we have pre-processed NL content (from CLI) - separate from VS content
        # This happens when CLI processes NL queries before calling chat()
        if context_state.get('sql_query_content') and not context_state.get('sql_query_prompt'):
            debug_print(f"🔍 Using pre-processed NL content from CLI")
            nl_content = context_state.get('sql_query_content', {})
            if nl_content:
                debug_print(f"  Found {len(nl_content)} databases with {sum(len(pages) for pages in nl_content.values() if pages is not None)} NL pages")
                for db_name, pages in nl_content.items():
                    debug_print(f"    {db_name}: {len(pages)} pages")
                combined_multi_source_data.update(nl_content)

        # Check if we have pre-processed VS content (from CLI) - separate from NL content
        # This happens when CLI processes VS queries before calling chat()
        if context_state.get('vector_search_content'):
            debug_print(f"🔍 Using pre-processed VS content from CLI")
            vs_content = context_state.get('vector_search_content', {})
            if vs_content:
                debug_print(f"  Found {len(vs_content)} databases with {sum(len(pages) for pages in vs_content.values() if pages is not None)} VS pages")
                for db_name, pages in vs_content.items():
                    debug_print(f"    {db_name}: {len(pages)} pages")
                    # Merge VS content with NL content (union)
                    if db_name not in combined_multi_source_data:
                        combined_multi_source_data[db_name] = []
                    combined_multi_source_data[db_name].extend(pages)

        # Process natural language query if present (prompt needs processing)
        natural_language_data = {}
        if context_state.get('sql_query_prompt'):
            debug_print(f"🔍 Processing natural language prompt: '{context_state.get('sql_query_prompt')}'")
            debug_print(f"🔍 Vector search mode: {context_state.get('is_vector_search', False)}")
            nl_prompt = context_state['sql_query_prompt']
            
            # Check if we already have content from CLI (first time) or cached results
            existing_nl_content = context_state.get('sql_query_content', {})
            cached_nl_prompt = context_state.get('cached_sql_query_prompt', '')
            
            # DEBUG: Add logging to understand cache behavior
            debug_print(f"🔍 NL Cache Debug:")
            debug_print(f"  Current prompt: '{nl_prompt}'")
            debug_print(f"  Cached prompt: '{cached_nl_prompt}'")
            debug_print(f"  Prompts match: {nl_prompt == cached_nl_prompt}")
            debug_print(f"  Has existing content: {bool(existing_nl_content)}")
            debug_print(f"  Content count: {len(existing_nl_content) if existing_nl_content else 0}")
            
            # If we have content and no cached prompt yet, this is CLI-provided content (first time)
            if existing_nl_content and not cached_nl_prompt:
                natural_language_data = existing_nl_content
                # Set up cache for future reloads
                context_state['cached_sql_query_prompt'] = nl_prompt
                debug_print(f"  → Using CLI content, caching prompt")
            # If we have cached content for this exact prompt, reuse it (no re-processing needed)
            elif nl_prompt == cached_nl_prompt and existing_nl_content:
                if not skip_nl_cache_messages:
                    print_text("🔄 Using cached natural language results (browse context changed, query unchanged)", style="dim")
                natural_language_data = existing_nl_content
                debug_print(f"  → Using cached content (cache hit)")
                # IMPORTANT: Don't re-process the query, just use cached results
            # Otherwise, process fresh query
            else:
                debug_print(f"  → Cache miss, will re-process query")
                
                try:
                    from promaia.storage.unified_query import get_query_interface
                    
                    # Determine workspace to use - preserve from original context
                    workspace = context_state.get('resolved_workspace') or context_state.get('workspace')
                    
                    # If no explicit workspace, try to infer from original sources
                    if not workspace and context_state.get('sources'):
                        # Try to extract workspace from source names (e.g., "acme.gmail" -> "acme")
                        for source in context_state['sources']:
                            if '.' in source:
                                potential_workspace = source.split('.')[0]
                                workspace = potential_workspace
                                break
                    
                    # Fall back to default workspace
                    if not workspace:
                        from promaia.config.workspaces import get_workspace_manager
                        workspace_manager = get_workspace_manager()
                        workspace = workspace_manager.get_default_workspace()
                    
                    if not workspace:
                        print_text("Error: No workspace available for natural language query.", style="bold red")
                        return False
                    
                    # Process natural language query fresh
                    query_interface = get_query_interface()
                    
                    # For OR logic: Natural language searches ALL databases (not restricted to browser selections)
                    # Browser selections will be loaded separately and combined with NL results
                    database_names = None  # Search all databases for maximum content discovery
                    
                    # IMPORTANT: For mixed browse+NL commands, always use None to ensure OR logic
                    # Don't extract database names from current sources - that would create AND logic
                    if context_state.get('is_mixed_browse_nl_command'):
                        database_names = None  # Explicit OR logic for browse + NL independence
                        debug_print("🔄 Mixed browse+NL command: using database_names=None for OR logic")
                    
                    # Check if this is vector search mode
                    if context_state.get('is_vector_search'):
                        # Use vector search processor instead of natural language SQL query
                        from promaia.nlq.nl_processor_wrapper import process_vector_search_to_content
                        sql_query_content = process_vector_search_to_content(
                            nl_prompt,
                            workspace=None,  # Allow cross-workspace searches
                            verbose=True,  # Show detailed processing steps
                            n_results=context_state.get('top_k', 20),
                            min_similarity=context_state.get('threshold', 0.2)
                        )
                    else:
                        # Always allow cross-workspace queries for natural language
                        # Workspace is just a classifier/tag, not a mandatory constraint
                        sql_query_content = query_interface.natural_language_query(nl_prompt, None, database_names)
                    
                    if not sql_query_content:
                        print_text("❌ No content found for natural language query", style="bold red")
                        return False
                    
                    # Cache both the results and prompt for future use
                    context_state['sql_query_content'] = sql_query_content
                    context_state['cached_sql_query_prompt'] = nl_prompt
                    
                    # IMPORTANT: Set natural_language_data for integration with combined_multi_source_data
                    natural_language_data = sql_query_content
                    
                except Exception as e:
                    print_text(f"Error processing natural language content: {e}", style="bold red")
                    # Continue with regular sources even if NL fails
                    # Clear cache on error
                    context_state['sql_query_content'] = {}
                    context_state['cached_sql_query_prompt'] = ''
            
            # Add natural language data to combined results (whether cached or fresh)
            if natural_language_data:
                debug_print(f"🔍 Adding NL data to combined_multi_source_data: {len(natural_language_data)} databases, {sum(len(pages) for pages in natural_language_data.values() if pages is not None)} total pages")
                # Merge NL content with existing content (don't use .update() as it replaces!)
                for db_name, pages in natural_language_data.items():
                    if db_name not in combined_multi_source_data:
                        combined_multi_source_data[db_name] = []
                    # Avoid duplicates by checking page IDs
                    existing_ids = {p.get('id') for p in combined_multi_source_data[db_name] if isinstance(p, dict) and 'id' in p}
                    for page in pages:
                        if not isinstance(page, dict) or 'id' not in page or page['id'] not in existing_ids:
                            combined_multi_source_data[db_name].append(page)
                            if isinstance(page, dict) and 'id' in page:
                                existing_ids.add(page['id'])
                debug_print(f"🔍 After merge: combined_multi_source_data has {len(combined_multi_source_data)} databases")
            else:
                debug_print(f"⚠️  natural_language_data is empty, skipping merge")
        
        # Process MCP servers if present
        mcp_tools_info = ""
        if context_state.get('mcp_servers'):
            try:
                print_text("🔧 Connecting to MCP servers...", style="white")
                
                # Load environment variables for MCP servers
                from dotenv import load_dotenv
                load_dotenv()
                
                from promaia.config.mcp_servers import get_mcp_manager
                from promaia.mcp.client import McpClient
                from promaia.mcp.execution import McpToolExecutor
                
                mcp_manager = get_mcp_manager()
                mcp_client = McpClient()
                
                connected_servers = []
                for server_name in context_state['mcp_servers']:
                    server_config = mcp_manager.get_server(server_name)
                    if server_config:
                        if server_config.enabled:
                            print(f"  Connecting to {server_name}...")
                            # For now, simulate connection - in real implementation this would be async
                            import asyncio
                            success = asyncio.run(mcp_client.connect_to_server(server_config))
                            if success:
                                connected_servers.append(server_name)
                                print(f"  ✅ Connected to {server_name}")
                            else:
                                print(f"  ❌ Failed to connect to {server_name}")
                        else:
                            print(f"  ⚠️ Server {server_name} is disabled in config")
                    else:
                        print(f"  ❌ Server {server_name} not found in config")
                
                if connected_servers:
                    # Use compact format if we have other content to avoid prompt issues
                    # Also use compact format when we have no content at all to prevent content filtering
                    has_other_content = bool(sources or sql_query_content)
                    compact_format = has_other_content or (not sources and not sql_query_content)
                    
                    # Format tools information for the system prompt
                    mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=compact_format)
                    context_state['mcp_tools_info'] = mcp_tools_info
                    context_state['mcp_client'] = mcp_client
                    context_state['mcp_executor'] = McpToolExecutor(mcp_client)
                    print(f"🔧 Connected to {len(connected_servers)} MCP server(s): {', '.join(connected_servers)}")
                else:
                    print("⚠️ No MCP servers were successfully connected")
                    
            except Exception as e:
                print_text(f"Error processing MCP servers: {e}", style="bold red")
                # Continue even if MCP fails
        
        # Use current state - ensure sources is always a list
        current_sources = context_state.get('sources', []) or []
        
        # Debug: Check if we have browser selections that should be loaded as regular sources
        if DEBUG_MODE and current_sources:
            print_text(f"🔍 Current sources to load: {current_sources}", style="dim cyan")
        current_filters = context_state.get('filters', []) or []
        current_workspace = context_state.get('workspace')
        current_resolved_workspace = context_state.get('resolved_workspace')
        
        # 1. Determine Workspace (use resolved_workspace if provided, otherwise fallback)
        if current_resolved_workspace:
            actual_workspace = current_resolved_workspace
        else:
            from promaia.config.workspaces import get_workspace_manager
            workspace_manager = get_workspace_manager()
            if not current_workspace:
                actual_workspace = workspace_manager.get_default_workspace()
            else:
                actual_workspace = current_workspace
        
        if not actual_workspace:
            print_text("ERROR: No workspace available. Please configure one.", style="bold red")
            return False

        # 2. Determine and Process Sources
        from promaia.config.databases import get_database_manager
        from promaia.cli.database_commands import parse_source_specs, parse_filter_expression

        db_manager = get_database_manager()
        # Initialize new data container (don't include natural language yet to avoid duplication)
        new_multi_source_data = {}
        # Don't calculate total here - calculate it from final data to ensure consistency

        # Only auto-load workspace databases if user provided NO arguments at all
        # Also check for pre-loaded sql_query_content (e.g., from draft mode) or if a mode is active
        user_provided_args = bool(sources or filters or sql_query_prompt or context_state.get('browse_selections') or mcp_servers or context_state.get('sql_query_content') or mode)
        
        # Check if user provided workspace but no sources (workspace browse mode)
        # BUT don't launch browser if we already have sources (e.g., from edit context)
        # Only launch browser if user explicitly provided a workspace (not defaulted)
        # Also don't launch browser if we're in a mode (e.g., draft mode)
        user_provided_workspace_only = bool(current_workspace and not sources and not filters and not sql_query_prompt)

        if user_provided_workspace_only and not current_sources and not mode:
            debug_print(f"Opening workspace browser for '{actual_workspace}'.")
            print_text(f"🔍 Launching unified browser for '{actual_workspace}'...", style="bold cyan")

            # Launch the workspace browser
            # Use launch_unified_browser to support pre-selecting sources from history
            from promaia.cli.workspace_browser import launch_unified_browser

            # Check if we have saved browse_selections from history
            saved_selections = context_state.get('browse_selections')
            if saved_selections:
                debug_print(f"Restoring {len(saved_selections)} browse selections from history: {saved_selections}")

            browser_result = launch_unified_browser(
                workspace=actual_workspace,
                current_sources=saved_selections if saved_selections else None
            )
            selected_sources = browser_result.sources if not browser_result.cancelled else None

            if browser_result.cancelled:
                # User cancelled (ESC)
                print_text(f"Browser cancelled for workspace '{actual_workspace}'. Only prompt, no context.", style="bold yellow")
                # Continue with blank slate - don't return
            elif selected_sources:
                # Process browser selections to handle Discord channels correctly
                processed_sources, processed_filters = process_browser_selections(selected_sources)

                debug_print(f"🔍 Browser returned: {selected_sources}")
                debug_print(f"🔍 Processed to sources: {processed_sources}")
                debug_print(f"🔍 Processed to filters: {processed_filters}")

                # Store processed sources and filters
                current_sources = processed_sources
                current_filters = processed_filters if processed_filters else []

                context_state['sources'] = current_sources
                context_state['filters'] = current_filters if current_filters else []
                context_state['browse_selections'] = selected_sources  # Store raw selections for /e functionality

                # Store the original workspace command for display
                context_state['original_query_format'] = f"maia chat -b {actual_workspace}"
                print_text(f"📦 Selected {len(selected_sources)} sources from workspace '{actual_workspace}'", style="cyan")
            else:
                # User confirmed with nothing selected - blank slate
                print_text(f"No sources selected from workspace '{actual_workspace}'. Only prompt, no context.", style="bold yellow")
                context_state['sources'] = []
                context_state['filters'] = []
                context_state['browse_selections'] = []
                # Continue with blank slate - don't return
        elif not current_sources and len(combined_multi_source_data) == 0 and not user_provided_args:
            # For plain "maia chat" with no args, start with blank slate instead of loading defaults
            debug_print(f"No arguments provided, starting with blank slate (no default databases loaded).")
            context_state['blank_slate_message_shown'] = True  # Track that we've shown this message
        elif len(combined_multi_source_data) > 0 and not current_sources:
            debug_print(f"Have natural language content only (no browser selections) - using NL content only")
            # Only skip regular sources if we don't have browser selections
            # This preserves OR logic: browser selections + natural language results
        elif user_provided_args and not current_sources and len(combined_multi_source_data) == 0:
            debug_print(f"User provided arguments but no sources - MCP or other tools will provide context.")

        if current_filters and current_sources:
            debug_print(f"Applying filters: {current_filters}")

        # 3. Process filters and integrate them into source specifications
        processed_sources = []
        discord_filters = []  # Separate list for complete Discord filters
        source_specific_filters = {}  # Dict of source -> list of filters
        global_filters = []

        # Parse and categorize filters
        if current_filters:
            debug_print(f"Processing filters: {current_filters}")

            for filter_expr in current_filters:
                try:
                    parsed_filter = parse_filter_expression(filter_expr)

                    # Check if this is a source-specific filter (new format)
                    if isinstance(parsed_filter, dict) and 'source' in parsed_filter:
                        source = parsed_filter['source']
                        filter_spec = parsed_filter['filter']

                        # Check if this is a complete channel filter specification (includes days and filter)
                        # Format: acme.discord:7:discord_channel_name=general or pmslack:7:slack_channel_name=general
                        # Also handle "all" day values: acme.discord:all:discord_channel_name=...
                        source_parts = source.split(':')
                        source_has_days = (len(source_parts) > 1 and
                                         (source_parts[-1].isdigit() or source_parts[-1] == 'all'))
                        filter_is_channel = ('discord_channel_name' in filter_spec or
                                           'slack_channel_name' in filter_spec or
                                           ('(' in filter_spec and ('discord_channel_name' in filter_spec or 'slack_channel_name' in filter_spec)))
                        
                        if source_has_days and filter_is_channel:
                            # This is a complete channel filter (Discord/Slack) - handle separately
                            discord_filters.append(filter_expr)
                            debug_print(f"Added complete channel filter: {filter_expr}")
                            continue

                        if source not in source_specific_filters:
                            source_specific_filters[source] = []
                        source_specific_filters[source].append(filter_spec)
                        debug_print(f"Added source-specific filter: {source} -> {filter_spec}")
                    else:
                        # Backward compatibility - filter without source prefix
                        global_filters.append(parsed_filter)
                        debug_print(f"Added global filter: {parsed_filter}")

                except Exception as e:
                    print_text(f"Warning: Invalid filter '{filter_expr}': {e}", style="bold yellow")
                    continue

        # Validation for multi-source scenarios
        if current_sources and len(current_sources) > 1:
            if global_filters:
                print_text(
                    "Error: In multi-source scenarios, all filters must specify a source prefix.\n"
                    f"Example: Instead of '{global_filters[0]}', use 'source:\"{global_filters[0]}\"'\n"
                    "Available sources: " + ", ".join(current_sources),
                    style="bold red"
                )
                return False

            # Check that all filter sources are valid
            for filter_source in source_specific_filters.keys():
                if filter_source not in current_sources:
                    print_text(
                        f"Error: Filter source '{filter_source}' not found in specified sources.\n"
                        f"Available sources: {', '.join(current_sources)}",
                        style="bold red"
                    )
                    return False

        # Build processed sources with appropriate filters
        if current_sources:
            for source in current_sources:
                # Skip sources that have Discord filters - they're handled separately
                # Check if any discord_filter starts with this source (e.g., "acme.tg:14.")
                is_discord_source = any(df.startswith(source + '.') or df.startswith(source + ':') for df in discord_filters)
                if is_discord_source:
                    debug_print(f"Skipping source {source} - handled in discord_filters")
                    continue
                
                # Determine which filters apply to this source
                applicable_filters = []

                # Add source-specific filters
                if source in source_specific_filters:
                    # Filter out __COMPLEX_EXPR__ filters - they're handled separately in Discord filter processing
                    # and should not be reconstructed using dot notation
                    non_complex_filters = [f for f in source_specific_filters[source] if not f.startswith('__COMPLEX_EXPR__')]
                    applicable_filters.extend(non_complex_filters)

                # Add global filters (only in single-source scenarios or backward compatibility)
                if len(current_sources) == 1 or not source_specific_filters:
                    applicable_filters.extend(global_filters)

                # Build the source specification
                if applicable_filters:
                    # Create a source spec with integrated filters
                    # Check if source already has a day specification (e.g., "acme.discord:30")
                    if ':' in source:
                        # Source already has day spec, just append filters
                        source_with_filters = f"{source}.{'.'.join(applicable_filters)}"
                    else:
                        # Source has no day spec, use 'all' for unlimited days
                        source_with_filters = f"{source}:all.{'.'.join(applicable_filters)}"
                    processed_sources.append(source_with_filters)
                    debug_print(f"Created filtered source spec: {source_with_filters}")
                else:
                    # No applicable filters, add source as-is
                    # Note: Discord filters with __COMPLEX_EXPR__ are handled separately below
                    # and should not be added here to avoid duplication
                    # Extract qualified database name (before :) for deduplication
                    source_db_name = source.split(':')[0]
                    existing_db_names = [s.split(':')[0] for s in processed_sources]
                    if source_db_name not in existing_db_names:
                        processed_sources.append(source)
                        debug_print(f"Using unfiltered source: {source}")
                    else:
                        debug_print(f"Skipping duplicate source: {source} (already have {source_db_name})")

        # Log final filter application
        if DEBUG_MODE and (source_specific_filters or global_filters):
            print_text("Filter Summary:", style="bold cyan")
            for source in current_sources or []:
                filters_for_source = []
                if source in source_specific_filters:
                    filters_for_source.extend([f"source-specific: {f}" for f in source_specific_filters[source]])
                if len(current_sources) == 1 or not source_specific_filters:
                    filters_for_source.extend([f"global: {f}" for f in global_filters])

                if filters_for_source:
                    print_text(f"  {source}: {', '.join(filters_for_source)}", style="dim")
                else:
                    print_text(f"  {source}: no filters", style="dim")

        # 3b. Handle task_queue source (file-based, no DatabaseConfig)
        if "task_queue" in processed_sources:
            processed_sources = [s for s in processed_sources if s != "task_queue"]
            from promaia.agents.task_queue_file import read_task_queue
            try:
                content = read_task_queue()
                new_multi_source_data["task_queue"] = [{
                    "page_id": "task_queue",
                    "title": "Task Queue",
                    "content": content,
                    "metadata": {},
                    "filename": "queue.md",
                    "date": "",
                }]
            except Exception as e:
                debug_print(f"Error loading task queue: {e}")

        # 4. Parse the processed source specifications
        parsed_sources_init = []
        if processed_sources:
            if DEBUG_MODE:
                print_text(f"🔍 Processed sources to parse: {processed_sources}", style="dim cyan")
            try:
                parsed_sources_init = parse_source_specs(processed_sources)
                if DEBUG_MODE:
                    print_text(f"🔍 Parsed sources result: {len(parsed_sources_init)} sources", style="dim cyan")
            except Exception as e:
                print_text(f"Warning: Error parsing source specifications: {e}", style="bold yellow")
                return False
        else:
            if DEBUG_MODE:
                print_text("🔍 No processed sources to parse", style="dim cyan")

        # 5. Parse Discord filters separately (they need special handling)
        if discord_filters:
            try:
                # Manually construct Discord filter objects to avoid parser warnings
                for discord_filter in discord_filters:
                    parsed_filter = parse_filter_expression(discord_filter)
                    if isinstance(parsed_filter, dict) and 'source' in parsed_filter:
                        source = parsed_filter['source']
                        filter_spec = parsed_filter['filter']
                        
                        # Extract database and days from source (e.g., "acme.discord:7")
                        source_parts = source.split(':')
                        database = source_parts[0]
                        days = int(source_parts[1]) if len(source_parts) > 1 and source_parts[1].isdigit() else None
                        
                        # Get database config for validation
                        db_config = db_manager.get_database_by_qualified_name(database)
                        if not db_config:
                            debug_print(f"Warning: Database config for '{database}' not found. Skipping Discord filter.")
                            continue
                        
                        # Parse the filter specification
                        property_filters = {}
                        complex_filter = None
                        
                        # Handle complex filters (multiple channels with OR)
                        if filter_spec.startswith('__COMPLEX_EXPR__'):
                            # Extract complex expression
                            complex_expr = filter_spec[16:]  # Remove '__COMPLEX_EXPR__' prefix
                            # For channel filters (Discord/Slack), convert to property filters format
                            if '(' in complex_expr and ('discord_channel_name=' in complex_expr or 'slack_channel_name=' in complex_expr):
                                # Extract channel names from (discord_channel_name=a or ...) or (slack_channel_name=a or ...)
                                # For now, use the complex filter as-is - the file reader will handle it
                                from promaia.cli.database_commands import parse_complex_filter_expression
                                complex_filter = parse_complex_filter_expression(complex_expr)
                        else:
                            # Simple single channel filter
                            if '=' in filter_spec:
                                prop_name, prop_value = filter_spec.split('=', 1)
                                property_filters[prop_name] = prop_value
                        
                        # Construct parsed source object
                        discord_parsed_source = {
                            'name': db_config.get_qualified_name(),
                            'database': db_config.get_qualified_name(),
                            'qualified_name': db_config.get_qualified_name(),
                            'days': days,
                            'property_filters': property_filters,
                            'comparison_filters': {},
                            'complex_filter': complex_filter
                        }

                        parsed_sources_init.append(discord_parsed_source)
                        debug_print(f"🎮 Manually parsed Discord filter: {database}, days: {days}, filters: {property_filters}, complex: {complex_filter}")
                        
            except Exception as e:
                print_text(f"Warning: Error parsing Discord filters: {e}", style="bold yellow")
                # Continue anyway - don't fail completely on Discord filter errors
        else:
            debug_print("No Discord filters detected. discord_filters list is empty.")

        # Load content from sources
        sources_loaded_successfully = False
        if parsed_sources_init:
            if DEBUG_MODE:
                print_text("Loading context from sources...", style="cyan")
            for source_conf in parsed_sources_init:
                db_name = source_conf['database']
                # Try to get database by qualified name first, then fallback to regular lookup
                db_config = db_manager.get_database_by_qualified_name(db_name)
                if not db_config:
                    db_config = db_manager.get_database(db_name)
                if not db_config:
                    if DEBUG_MODE:
                        print_text(f"Warning: Config for database '{db_name}' not found. Skipping.", style="bold yellow")
                    continue

                try:
                    # Check if this source has a complex filter with date conditions
                    has_date_filter_in_complex = False
                    if source_conf.get('complex_filter'):
                        complex_filter = source_conf.get('complex_filter')
                        if complex_filter.get('type') == 'complex':
                            for or_clause in complex_filter.get('or_clauses', []):
                                for condition in or_clause:
                                    if condition.get('property') in ['created_time', 'last_edited_time']:
                                        has_date_filter_in_complex = True
                                        break
                                if has_date_filter_in_complex:
                                    break

                    # Don't use days constraint if complex filter already has date conditions
                    days_to_use = None if has_date_filter_in_complex else source_conf.get('days')

                    # DEBUG: Log Discord source loading details
                    if db_config.source_type == 'discord':
                        debug_print(f"🎮 Loading Discord source: {db_name}")
                        debug_print(f"  Days: {days_to_use}")
                        debug_print(f"  Property filters: {source_conf.get('property_filters', {})}")
                        debug_print(f"  Complex filter: {source_conf.get('complex_filter')}")

                    pages = load_database_pages_with_filters(
                        db_config,
                        days=days_to_use,
                        comparison_filters=source_conf.get('comparison_filters', {}),
                        complex_filter=source_conf.get('complex_filter'),
                        property_filters=source_conf.get('property_filters', {})
                    )
                    # Use qualified name to avoid collisions between workspaces
                    unique_key = db_config.get_qualified_name()

                    # DEBUG: Log Discord results
                    if db_config.source_type == 'discord':
                        debug_print(f"🎮 Discord load result: {len(pages)} pages from {unique_key}")

                    # Store pages (no need for deduplication since we now load database only once
                    # with OR filter for all channels)
                    if unique_key in new_multi_source_data:
                        # This shouldn't happen with the new grouping logic, but handle it gracefully
                        new_multi_source_data[unique_key].extend(pages)
                    else:
                        new_multi_source_data[unique_key] = pages
                    # Don't increment total here - calculate from final data to ensure consistency

                    # Only mark as successful if we actually got pages
                    if len(pages) > 0:
                        sources_loaded_successfully = True
                    if DEBUG_MODE:
                        print_text(f"  - Loaded {len(pages)} entries from: {unique_key}", style="green")
                except Exception as e:
                    if DEBUG_MODE:
                        print_text(f"Error loading data for database {db_config.name}: {e}", style="bold red")
                    # Continue trying other sources instead of failing completely
        else:
            # No regular sources to load, but might have natural language content
            if len(combined_multi_source_data) > 0:
                sources_loaded_successfully = True  # We have content from natural language

        # Calculate total from final data to ensure consistency with breakdown
        new_total_pages_loaded = sum(len(pages) for pages in new_multi_source_data.values() if pages is not None)
        
        # Always merge natural language content if it exists
        if len(combined_multi_source_data) > 0:
            debug_print(f"🔍 Merging NL data into new_multi_source_data")
            debug_print(f"  Before merge: new_multi_source_data has {len(new_multi_source_data)} databases, {new_total_pages_loaded} pages")
            debug_print(f"  combined_multi_source_data has {len(combined_multi_source_data)} databases")
            
            # Merge natural language content with regular sources
            for source_name, pages in combined_multi_source_data.items():
                if source_name not in new_multi_source_data:
                    debug_print(f"  Adding new NL source: {source_name} with {len(pages)} pages")
                    new_multi_source_data[source_name] = pages
                else:
                    # If source already exists, combine the pages (shouldn't happen but handle it)
                    debug_print(f"  Extending existing source: {source_name} with {len(pages)} additional pages")
                    new_multi_source_data[source_name].extend(pages)
            
            # Recalculate total after merging
            new_total_pages_loaded = sum(len(pages) for pages in new_multi_source_data.values() if pages is not None)
            debug_print(f"  After merge: new_multi_source_data has {len(new_multi_source_data)} databases, {new_total_pages_loaded} pages")
            
            if not sources_loaded_successfully:
                print_text("ℹ️  Using natural language content (regular sources had no data)", style="cyan")
        else:
            debug_print(f"⚠️  combined_multi_source_data is empty, no NL data to merge")
        
        # Check if we have any data at all
        if new_total_pages_loaded == 0:
            # If we have MCP servers, we can still chat even without content
            if context_state.get('mcp_servers'):
                print_text("❌ No content could be loaded from any source", style="bold red")
                print_text("💡 MCP tools are available for interaction", style="cyan")
            else:
                # Continue with blank slate - don't return False
                pass
        
        # Update context state
        context_state['initial_multi_source_data'] = new_multi_source_data
        context_state['total_pages_loaded'] = new_total_pages_loaded
        
        # Update sources list to reflect only the sources that were actually loaded
        # This ensures session logs show accurate source information
        # However, preserve the original sources format if we have user-provided args
        # to maintain day specifications in the query display
        if not context_state.get('original_query_format') and not user_provided_args and not context_state.get('sources'):
            # Only overwrite if sources were auto-generated AND we don't already have sources
            context_state['sources'] = list(new_multi_source_data.keys())
        # else: keep the existing sources with their day specifications for display
        
        # Update module-level variables
        initial_multi_source_data = new_multi_source_data
        total_pages_loaded = new_total_pages_loaded
        
        # Apply mute handling - use empty context if muted (but keep data loaded)
        context_data_for_prompt = {} if context_state.get('context_muted') else new_multi_source_data
        
        # Generate new system prompt
        mcp_tools_info = context_state.get('mcp_tools_info')
        system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
        context_state['system_prompt'] = system_prompt
        
        # Save context log when MCP servers are connected (for transparency)
        if context_state.get('mcp_servers') and mcp_tools_info:
            save_context_log(context_state, system_prompt, new_total_pages_loaded, current_api, "mcp_connection")
        
        # Debug: Log context reload details
        if DEBUG_MODE:
            debug_print(f"Context Reload: {len(new_multi_source_data)} data sources loaded")
            debug_print(f"Context Reload: {new_total_pages_loaded} total pages")
            debug_print(f"Context Reload: System prompt length: {len(system_prompt)}")
            debug_print(f"Context Reload: Data sources: {list(new_multi_source_data.keys())}")
            debug_print(f"Context Reload: Updated context_state sources: {context_state.get('sources')}")
        
        # Update query command
        update_query_command()
        query_command = context_state['query_command']

        # DEBUG: Log final state before returning
        debug_print(f"📊 reload_context() completed successfully:")
        debug_print(f"  Returning True")
        debug_print(f"  total_pages_loaded (nonlocal var): {total_pages_loaded}")
        debug_print(f"  initial_multi_source_data (nonlocal var) keys: {list(initial_multi_source_data.keys())}")
        debug_print(f"  initial_multi_source_data counts: {[(k, len(v)) for k, v in initial_multi_source_data.items()]}")

        return True

    async def sync_current_context_databases():
        """Sync the databases currently in the chat context."""
        nonlocal initial_multi_source_data
        
        # Determine what databases to sync based on current context
        databases_to_sync = []
        
        # Combine all source types in current context (browser, SQL, loaded data)
        seen = set()
        if context_state['sources']:
            for s in context_state['sources']:
                if s not in seen:
                    databases_to_sync.append(s)
                    seen.add(s)
        if context_state.get('sql_query_content'):
            for s in context_state['sql_query_content'].keys():
                if s not in seen:
                    databases_to_sync.append(s)
                    seen.add(s)
        if context_state.get('initial_multi_source_data'):
            for s in context_state['initial_multi_source_data'].keys():
                if s not in seen:
                    databases_to_sync.append(s)
                    seen.add(s)
        if not databases_to_sync and initial_multi_source_data:
            databases_to_sync = list(initial_multi_source_data.keys())
        
        if not databases_to_sync:
            print_text("No databases in current context to sync.", style="bold yellow")
            return
        
        from promaia.cli.database_commands import sync_database
        from promaia.config.databases import get_database_manager
        
        db_manager = get_database_manager()
        
        print_text(f"Syncing {len(databases_to_sync)} database(s) from current context...", style="bold cyan")
        print_text("Note: This only syncs databases in your current chat context.", style="dim")
        print_text("Use 'maia sync' outside chat to sync all enabled databases.", style="dim")
        
        # Create a mock args object for the sync function
        # Use same logic as standalone maia sync - pure incremental sync
        class MockArgs:
            def __init__(self):
                self.force = False
                self.days = None
                self.start_date = None
                self.end_date = None
                self.date_range = None
        
        mock_args = MockArgs()
        
        # Use the same source parsing logic as standalone sync, but include filters
        from promaia.cli.database_commands import parse_source_specs
        from promaia.config.databases import get_database_manager
        
        db_manager = get_database_manager()
        current_filters = context_state.get('filters', [])
        
        # For Discord sources with browse-mode channel filters, use the filtered specs
        # For regular sources, use the source as-is
        sources_to_sync = []
        
        for source in databases_to_sync:
            # Skip file-based sources that don't need syncing
            source_name = source.split(':')[0] if ':' in source else source
            if source_name == "task_queue":
                continue
            db_config = db_manager.get_database_by_qualified_name(source_name)
            
            if db_config and db_config.source_type == "discord":
                # Check if we have channel-specific filters for this Discord source
                # Match by database name (not including days) to handle day specification mismatches
                db_base_name = db_config.name  # Get the full database name (e.g., 'acme.discord')
                
                # Find filters that match this database (regardless of day specification)
                sync_discord_filters = []
                for f in current_filters:
                    filter_db_name = f.split(':')[0] if ':' in f else f
                    # Check if this filter belongs to the same database
                    filter_db_config = db_manager.get_database_by_qualified_name(filter_db_name)
                    if filter_db_config and filter_db_config.name == db_base_name:
                        sync_discord_filters.append(f)
                
                if sync_discord_filters:
                    # Use the filtered specifications (one per channel)
                    sources_to_sync.extend(sync_discord_filters)
                    print_text(f"📺 Discord source {source}: syncing {len(sync_discord_filters)} selected channels", style="cyan")
                else:
                    # No channel filters - skip Discord sources without browse selection
                    print_text(f"⚠️  Discord source {source}: No channels selected. Use browse mode (-b) to select channels first.", style="bold yellow")
                    continue
            else:
                # Regular source - use as-is
                sources_to_sync.append(source)
        
        if not sources_to_sync:
            print_text("No sources available to sync after filtering.", style="bold yellow")
            print_text("💡 For Discord sources, use browse mode (-b) to select specific channels first.", style="dim yellow")
            return
        
        # Parse all sources at once using the same logic as standalone sync
        parsed_sources = parse_source_specs(sources_to_sync)
        
        for source_spec in parsed_sources:
            try:
                source_name = source_spec.get('qualified_name', source_spec.get('name', 'Unknown'))
                
                # The sync_database function will print its own "🔄 Syncing..." and result messages
                result = await sync_database(source_spec, mock_args)
                
                # Only show additional error details if needed (sync_database shows the main error)
                if result.errors and len(result.errors) > 1:
                    for error in result.errors[1:3]:  # Show additional errors (first one already shown)
                        print_text(f"  - {error}", style="red")
                
            except Exception as e:
                print_text(f"  ❌ {source_name}: Sync failed - {e}", style="bold red")
                debug_print(f"Sync error for {source_name}: {e}")

    async def inline_sync_databases(sync_args_str: str = None):
        """
        Inline sync with optional source specifications.

        Args:
            sync_args_str: Optional string like "-s stories:7 -s projects:60"
                          If None or empty, syncs all databases in current context

        Returns:
            List of SyncResult objects, or None if sync failed
        """
        nonlocal initial_multi_source_data

        from promaia.cli.database_commands import sync_database, parse_source_specs
        from promaia.config.databases import get_database_manager
        import shlex

        db_manager = get_database_manager()

        # Parse sync arguments if provided
        sources_to_sync = []

        if sync_args_str and sync_args_str.strip():
            # Parse -s arguments: "-s stories:7 -s projects:60"
            try:
                args_list = shlex.split(sync_args_str)
                i = 0
                while i < len(args_list):
                    if args_list[i] == '-s' and i + 1 < len(args_list):
                        sources_to_sync.append(args_list[i + 1])
                        i += 2
                    else:
                        i += 1

                if not sources_to_sync:
                    print_text("❌ Invalid sync arguments. Use: -s <source:days>", style="bold red")
                    print_text("Example: -s stories:7 -s projects:60", style="dim")
                    return None

            except Exception as e:
                print_text(f"❌ Error parsing sync arguments: {e}", style="bold red")
                return None
        else:
            # Sync all databases in current context — combine all source types
            seen = set()
            if context_state['sources']:
                for s in context_state['sources']:
                    if s not in seen:
                        sources_to_sync.append(s)
                        seen.add(s)
            if context_state.get('sql_query_content'):
                for s in context_state['sql_query_content'].keys():
                    if s not in seen:
                        sources_to_sync.append(s)
                        seen.add(s)
            if context_state.get('initial_multi_source_data'):
                for s in context_state['initial_multi_source_data'].keys():
                    if s not in seen:
                        sources_to_sync.append(s)
                        seen.add(s)
            if not sources_to_sync and initial_multi_source_data:
                sources_to_sync = list(initial_multi_source_data.keys())

        if not sources_to_sync:
            print_text("⚠️  No databases to sync.", style="bold yellow")
            return None

        # Create mock args for sync
        class MockArgs:
            def __init__(self):
                self.force = False
                self.days = None
                self.start_date = None
                self.end_date = None
                self.date_range = None

        mock_args = MockArgs()

        # Parse source specifications
        parsed_sources = parse_source_specs(sources_to_sync)

        # Track results for summary
        sync_results = []

        # Sync each database
        for source_spec in parsed_sources:
            try:
                result = await sync_database(source_spec, mock_args)
                sync_results.append(result)
            except Exception as e:
                source_name = source_spec.get('qualified_name', source_spec.get('name', 'unknown'))
                print_text(f"❌ Error syncing {source_name}: {e}", style="bold red")
                debug_print(f"Inline sync error for {source_name}: {e}")

        return sync_results if sync_results else None

    def edit_context():
        """CLI-style context editing interface."""
        print_text("\n🔧 Edit Context", style="bold cyan")
        
        # Show current context summary
        print_text("Current command:", style="dim")
        # Always show with maia chat prefix for consistency and copy-ability
        command_to_display = context_state['query_command']
        if not command_to_display.startswith("maia chat"):
            command_to_display = f"maia chat {command_to_display}"
        print_text(f"  {command_to_display}", style="bold")
        
        # Show helpful info for Discord contexts
        if context_state.get('sources') and any('discord' in src.lower() or src.endswith('.ds') for src in context_state['sources']):
            workspace = context_state.get('resolved_workspace') or context_state.get('workspace')
            if workspace:
                print_text(f"  Discord workspace: {workspace}", style="dim cyan")
            if context_state.get('filters'):
                channel_count = len([f for f in context_state['filters'] if 'channel_name=' in f])
                if channel_count > 0:
                    print_text(f"  Discord channels selected: {channel_count}", style="dim cyan")
        
            # Show if this was originally a browse mode session
            if context_state.get('original_browse_mode'):
                print_text(f"  Original format: browse mode", style="dim cyan")
        
        print_text("")
        print_text("Options:", style="dim")
        print_text("  • Edit command manually (shown below)", style="dim")
        print_text("  • Ctrl+R for recent queries", style="dim")
        print_text("  • Ctrl+B for browse mode", style="dim")
        print_text("  • Press Enter alone to cancel", style="dim")
        print_text("")
        
        # Build the current command arguments (without 'maia chat')
        current_args = []
        current_args_str = ""  # Initialize to ensure it's never None
        
        # If we have an original query format, extract args from that
        if context_state.get('original_query_format'):
            # Extract everything after "maia chat "
            original_cmd = context_state['original_query_format']
            if original_cmd and original_cmd.startswith("maia chat "):
                current_args_str = original_cmd[10:]  # Remove "maia chat "
            elif original_cmd == "maia chat":
                current_args_str = ""  # No args when command is just "maia chat"
            else:
                current_args_str = original_cmd  # Use the whole thing as args
        else:
            # Check if we have multiple vector search queries (new format)
            if context_state.get('vector_search_queries'):
                # Reconstruct multiple -vs flags from the list
                for vs_query in context_state['vector_search_queries']:
                    # Handle both dict format (new) and string format (old/backward compatibility)
                    if isinstance(vs_query, dict):
                        current_args.extend(['-vs', vs_query['query']])
                        # Include per-query parameters if they differ from defaults
                        if vs_query.get('top_k', 20) != 20:
                            current_args.extend(['-tk', str(vs_query['top_k'])])
                        if vs_query.get('threshold', 0.75) != 0.75:
                            current_args.extend(['-th', str(vs_query['threshold'])])
                    else:
                        # Old format: just a string
                        current_args.extend(['-vs', vs_query])
            # Check if we're in natural language mode or vector search mode (old format)
            elif context_state.get('sql_query_prompt'):
                # Check if this was originally a vector search command
                original_cmd = context_state.get('original_query_format', '')
                if '-vs' in original_cmd and context_state.get('sql_query_content'):
                    # This is vector search mode - use -vs flag
                    current_args.extend(['-vs', context_state['sql_query_prompt']])
                else:
                    # This is regular natural language mode - use -sql flag
                    current_args.extend(['-sql', context_state['sql_query_prompt']])
            else:
                # Regular mode with sources and filters
                if context_state['sources']:
                    for source in context_state['sources']:
                        current_args.extend(['-s', source])
                if context_state['filters']:
                    for filter_expr in context_state['filters']:
                        current_args.extend(['-f', filter_expr])
                if context_state['workspace']:
                    current_args.extend(['-ws', context_state['workspace']])
            
            current_args_str = ' '.join(current_args) if current_args else ''
        
        try:
            # Create custom key bindings for Ctrl+R and Ctrl+B
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit import prompt
            
            bindings = KeyBindings()
            action_taken = {'type': None}
            
            @bindings.add('c-r')  # Ctrl+R
            def handle_recents(event):
                action_taken['type'] = 'recents'
                event.app.exit()
            
            @bindings.add('c-b')  # Ctrl+B  
            def handle_browse(event):
                action_taken['type'] = 'browse'
                event.app.exit()
            
            # Use prompt_toolkit to show the current command as editable default
            user_input = prompt(
                "maia chat ",
                default=current_args_str or "",
                mouse_support=True,
                key_bindings=bindings
            )
            
            # Ensure user_input is never None
            if user_input is None:
                user_input = ""
            else:
                user_input = user_input.strip()
                
            # Handle case where user types full command including "maia chat"
            if user_input.startswith("maia chat "):
                user_input = user_input[10:]  # Remove "maia chat " prefix
            elif user_input == "maia chat":
                user_input = ""  # Treat as empty input (reset to no args)
            
            # Check if a special action was triggered
            if action_taken['type'] == 'recents':
                return handle_recents_in_edit_context()
            elif action_taken['type'] == 'browse':
                return handle_browse_in_edit_context()
            
            # Handle different input scenarios
            if DEBUG_MODE:
                print_text(f"DEBUG: user_input='{user_input}', current_args_str='{current_args_str}'", style="dim yellow")
            
            if not user_input and not current_args_str:
                # No input and no current args - cancel
                print_text("Context edit cancelled.", style="bold yellow")
                return False
            elif not user_input and current_args_str:
                # Empty input but there were current args - user wants to keep current
                print_text("Keeping current context.", style="bold green")
                return 'no_change'  # Return special value to indicate no changes
            elif user_input == current_args_str:
                # User didn't change anything - keep current context
                print_text("No changes made. Keeping current context.", style="bold green")
                return 'no_change'  # Return special value to indicate no changes
            
            # Parse the input as CLI arguments
            import shlex
            import argparse
            
            try:
                # Check if this is a browse mode command that was manually edited
                if '-b' in user_input and user_input.strip() != current_args_str.strip():
                        # User manually edited a browse command - handle it with CLI logic
                        print_text("📝 Processing manually edited command...", style="bold cyan")
                        return handle_manual_browse_edit(user_input)
                
                # Use safe parsing that handles natural language queries with apostrophes
                args_list = safe_split_command(user_input)
                
                # Create a minimal parser for chat arguments
                parser = argparse.ArgumentParser(description="Chat context editor", add_help=False)
                parser.add_argument(
                    "--source", "-s",
                    action="append", 
                    dest="sources",
                    help="Load data from specific database with day filter"
                )
                parser.add_argument(
                    "--filter", "-f",
                    action="append",
                    dest="filters",
                    help="Add property filters"
                )
                parser.add_argument(
                    "--workspace", "-ws",
                    help="Specify which workspace to use"
                )
                parser.add_argument(
                    "--sql-query", "-sql",
                    action="append",
                    nargs="+",
                    help="Use SQL-based queries to search content. Can be used multiple times."
                )
                # Deprecated: Keep -nl as an alias for backward compatibility
                parser.add_argument(
                    "-nl",
                    action="append",
                    nargs="+",
                    dest="sql_query",
                    help=argparse.SUPPRESS
                )
                # NOTE: This -sql parsing MUST stay in sync with:
                # 1. Top-level CLI parsing in promaia/cli.py (lines ~2918-2936)
                # 2. safe_split_command() function above (line ~514)
                # These are two sides of one feature and must handle multiple -sql arguments identically.
                parser.add_argument(
                    "--vector-search", "-vs",
                    action="append",
                    nargs="+",
                    help="Use semantic vector search to find similar content. Can be used multiple times."
                )
                parser.add_argument(
                    "--top-k", "-tk",
                    type=int,
                    help="Maximum number of results to return from vector search (default: 20)"
                )
                parser.add_argument(
                    "--threshold", "-th",
                    type=float,
                    help="Minimum similarity threshold for vector search results, 0-1 scale (default: 0.75)"
                )
                parser.add_argument(
                    "--mcp", "-mcp",
                    action="append",
                    dest="mcp_servers",
                    help="Include MCP servers in chat context"
                )
                parser.add_argument(
                    "-dc", "--draft-context",
                    action="store_true",
                    dest="draft_context",
                    help="Enable draft context in draft chat"
                )
                # Parse the arguments
                parsed_args = parser.parse_args(args_list)

                # Parse structured -vs queries with per-query parameters (-tk/-th)
                vs_queries_structured = []
                if getattr(parsed_args, 'vector_search', None):
                    # Reconstruct the command as a list including 'maia chat' prefix for parsing
                    command_with_prefix = ['maia', 'chat'] + args_list
                    vs_queries_structured = parse_vs_queries_with_params(command_with_prefix)

                # Check if -dc (draft context) flag is being used in draft mode
                if getattr(parsed_args, 'draft_context', False) and mode:
                    from promaia.chat.modes import DraftMode
                    if isinstance(mode, DraftMode):
                        # Load context and prompt AI to write draft
                        print_text("\n🔍 Loading context...\n", style="cyan")

                        if not mode.context_builder:
                            print_text("❌ Context builder not available", style="red")
                            return False

                        try:
                            import asyncio

                            # Build thread dict from draft data
                            thread = {
                                'thread_id': mode.draft_data.get('thread_id'),
                                'subject': mode.draft_data.get('inbound_subject'),
                                'body': mode.draft_data.get('inbound_body'),
                                'conversation_body': mode.draft_data.get('thread_context', ''),
                                'from': mode.draft_data.get('inbound_from'),
                                'date': mode.draft_data.get('inbound_date'),
                                'message_count': mode.draft_data.get('message_count', 1)
                            }

                            # Build context using context builder
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            context = loop.run_until_complete(
                                mode.context_builder.build_context(thread, mode.workspace)
                            )
                            loop.close()

                            # Convert context to sql_query_content format for chat
                            message_context = {}

                            # Add email thread
                            message_context['email_thread'] = [{
                                'title': f"Email Thread: {mode.draft_data.get('inbound_subject', 'No Subject')}",
                                'content': mode.draft_data.get('inbound_body', ''),
                                'metadata': {
                                    'from': mode.draft_data.get('inbound_from', ''),
                                    'to': mode.draft_data.get('inbound_to', ''),
                                    'cc': mode.draft_data.get('inbound_cc', ''),
                                    'date': mode.draft_data.get('inbound_date', ''),
                                    'subject': mode.draft_data.get('inbound_subject', ''),
                                    'message_count': mode.draft_data.get('message_count', 1),
                                },
                                'database': 'email_thread',
                            }]

                            # Add vector search results
                            for doc in context.relevant_docs:
                                db_name = doc.get('database', 'unknown')

                                # Ensure consistent database naming with workspace prefix
                                if db_name == 'gmail':
                                    db_name = f"{mode.workspace}.gmail"
                                elif '.' not in db_name and db_name not in ['journal', 'stories', 'cpj', 'epics']:
                                    db_name = f"{mode.workspace}.{db_name}"

                                if db_name not in message_context:
                                    message_context[db_name] = []

                                # Convert doc format to page format
                                page = {
                                    'title': doc.get('title', 'Untitled'),
                                    'content': doc.get('content', ''),
                                    'metadata': doc.get('metadata', {}),
                                    'database': db_name,
                                }
                                message_context[db_name].append(page)

                            # Update context state with loaded context
                            context_state['sql_query_content'] = message_context

                            print_text(f"📚 Loaded {context.total_sources} sources from your knowledge base\n", style="green")

                            # Return True to indicate context was updated
                            return True

                        except Exception as e:
                            print_text(f"❌ Error generating draft: {e}", style="red")
                            logger.error(f"Draft generation error in edit context: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                            return False

                # Check if we have BOTH natural language and vector search queries
                sql_query_args = getattr(parsed_args, 'sql_query', None)
                vector_search_args = getattr(parsed_args, 'vector_search', None)

                # Extract top_k and threshold if provided
                if hasattr(parsed_args, 'top_k') and parsed_args.top_k is not None:
                    context_state['top_k'] = parsed_args.top_k
                if hasattr(parsed_args, 'threshold') and parsed_args.threshold is not None:
                    context_state['threshold'] = parsed_args.threshold

                # Process SQL queries if present
                sql_query_content = None
                combined_nl_prompt = None
                if sql_query_args is not None:
                    # Natural language mode - handle multiple -nl queries
                    # NOTE: This logic MUST match the top-level CLI implementation in promaia/cli.py
                    # Both edit mode and top-level query are two sides of one feature.
                    # With action="append" and nargs="+", we get a list of lists
                    nl_prompts = [' '.join(nl_args) for nl_args in sql_query_args if nl_args]

                    if not nl_prompts:
                        print_text("Error: Natural language prompt is empty.", style="bold red")
                        return False

                    # Create combined prompt for caching (without -nl prefixes for consistency with cli.py)
                    # This matches the format used in cli.py line 1458, 1641
                    combined_nl_prompt = " ".join(nl_prompts) if nl_prompts else ""

                    # Check if we already have cached results for this exact NL prompt
                    cached_nl_content = context_state.get('sql_query_content', {})
                    cached_nl_prompt = context_state.get('cached_sql_query_prompt', '')

                    # DEBUG: Log cache check in edit context
                    debug_print(f"🔍 Edit Context NL Cache Check:")
                    debug_print(f"  New prompt(s): {nl_prompts}")
                    debug_print(f"  Cached prompt: '{cached_nl_prompt}'")
                    debug_print(f"  Prompts match: {combined_nl_prompt == cached_nl_prompt}")
                    debug_print(f"  Has cached content: {bool(cached_nl_content)}")

                    if combined_nl_prompt == cached_nl_prompt and cached_nl_content:
                        print_text("🔄 Reusing cached natural language results (prompt unchanged)", style="dim")
                        sql_query_content = cached_nl_content
                        debug_print(f"  → Using cached results (edit context cache hit)")
                    else:
                        # Only clear cache if the prompt is actually different (not empty)
                        if cached_nl_prompt and combined_nl_prompt != cached_nl_prompt:
                            debug_print(f"  → Prompt changed, clearing cache")
                            context_state['sql_query_content'] = None
                            context_state['cached_sql_query_prompt'] = ''
                        elif not cached_nl_prompt:
                            debug_print(f"  → No cached prompt yet, will process and cache")
                        else:
                            debug_print(f"  → Cache miss, will re-process")

                        # Process multiple natural language queries
                        try:
                            from promaia.storage.unified_query import get_query_interface

                            if len(nl_prompts) > 1:
                                print_text(f"🤖 Processing {len(nl_prompts)} separate natural language queries", style="dim")
                                for i, prompt in enumerate(nl_prompts):
                                    print_text(f"   {i+1}. '{prompt}'", style="dim")
                            else:
                                print_text(f"🤖 Processing natural language query: '{nl_prompts[0]}'", style="dim")

                            # Determine workspace to use
                            workspace = context_state.get('resolved_workspace') or context_state.get('workspace')

                            # If no explicit workspace, try to infer from original sources
                            if not workspace and context_state.get('sources'):
                                # Try to extract workspace from source names (e.g., "acme.gmail" -> "acme")
                                for source in context_state['sources']:
                                    if '.' in source:
                                        potential_workspace = source.split('.')[0]
                                        workspace = potential_workspace
                                        break

                            # Fall back to default workspace
                            if not workspace:
                                from promaia.config.workspaces import get_workspace_manager
                                workspace_manager = get_workspace_manager()
                                workspace = workspace_manager.get_default_workspace()

                            if not workspace:
                                print_text("Error: No workspace available for natural language query.", style="bold red")
                                return False

                            # Process multiple natural language queries and combine results
                            query_interface = get_query_interface()

                            # For OR logic: Natural language searches ALL databases (not restricted to browser selections)
                            # Browser selections will be loaded separately and combined with NL results
                            database_names = None  # Search all databases for maximum content discovery

                            # Process each NL query separately and combine results
                            combined_nl_content = {}
                            total_results = 0

                            for i, nl_prompt in enumerate(nl_prompts):
                                print_text(f"🔍 Processing query {i+1}/{len(nl_prompts)}: '{nl_prompt}'", style="cyan")

                                # Always allow cross-workspace queries for natural language
                                # Workspace is just a classifier/tag, not a mandatory constraint
                                nl_content = query_interface.natural_language_query(nl_prompt, None, database_names)

                                if nl_content:
                                    # Merge results from this query into combined content
                                    for db_name, entries in nl_content.items():
                                        if db_name not in combined_nl_content:
                                            combined_nl_content[db_name] = []
                                        combined_nl_content[db_name].extend(entries)

                                    query_results = sum(len(entries) for entries in nl_content.values() if entries is not None)
                                    total_results += query_results
                                    print_text(f"   ✅ Query {i+1} found {query_results} results", style="green")
                                else:
                                    print_text(f"   ⚠️  Query {i+1} found no results", style="yellow")

                            if not combined_nl_content:
                                # Only fail if we don't also have VS queries to try
                                if not vector_search_args:
                                    return False
                                else:
                                    print_text("⚠️  No SQL results found, continuing with vector search...", style="yellow")
                            else:
                                print_text(f"🎯 Combined {len(nl_prompts)} queries: {total_results} total results", style="green")
                                sql_query_content = combined_nl_content

                                # Cache both the results and combined prompt for future use
                                context_state['sql_query_content'] = sql_query_content
                                context_state['cached_sql_query_prompt'] = combined_nl_prompt

                        except Exception as e:
                            print_text(f"Error processing natural language query: {e}", style="bold red")
                            # Only fail if we don't also have VS queries to try
                            if not vector_search_args:
                                return False
                            else:
                                print_text("⚠️  SQL query failed, continuing with vector search...", style="yellow")

                    # Update context state for natural language mode
                    # sql_query_content is already set above (either from cache or fresh query)
                    if sql_query_content:
                        context_state['sql_query_prompt'] = combined_nl_prompt

                # Check if vector search mode is being used
                vs_content = None
                combined_vs_prompt = None
                if vector_search_args is not None:
                    # Vector search mode - handle multiple -vs queries
                    # NOTE: This logic MUST match the top-level CLI implementation in promaia/cli.py
                    vs_prompts = [' '.join(vs_args) for vs_args in vector_search_args if vs_args]

                    if not vs_prompts:
                        print_text("Error: Vector search prompt is empty.", style="bold red")
                        return False

                    # Create combined prompt for display/tracking
                    combined_vs_prompt = " ".join(vs_prompts) if vs_prompts else ""

                    # Per-query caching: Check which queries changed vs stayed the same
                    # Get the per-query cache from context_state
                    per_query_cache = context_state.get('vector_search_per_query_cache', {})

                    # Track which queries need to be run and which can be reused
                    queries_to_run = []
                    cached_queries = []

                    # Build cache keys for each query (including per-query params)
                    query_cache_keys = []
                    for i, vs_query_obj in enumerate(vs_queries_structured):
                        cache_key = f"{vs_query_obj['query']}|{vs_query_obj['top_k']}|{vs_query_obj['threshold']}"
                        query_cache_keys.append(cache_key)

                        if cache_key in per_query_cache:
                            cached_queries.append((i, vs_query_obj['query']))
                        else:
                            queries_to_run.append((i, vs_query_obj['query']))

                    # Show cache status if we have multiple queries
                    if len(vs_queries_structured) > 1:
                        if cached_queries and queries_to_run:
                            print_text(f"🤖 Processing {len(vs_queries_structured)} vector search queries ({len(cached_queries)} cached, {len(queries_to_run)} new)", style="dim")
                        elif cached_queries:
                            print_text(f"🔄 Reusing cached results for all {len(cached_queries)} vector search queries", style="dim")
                        else:
                            print_text(f"🤖 Processing {len(vs_queries_structured)} separate vector search queries", style="dim")

                        # Show query list
                        for i, cache_key in enumerate(query_cache_keys):
                            cached_marker = " (cached)" if cache_key in per_query_cache else ""
                            print_text(f"   {i+1}. '{vs_queries_structured[i]['query']}'{cached_marker}", style="dim")
                    else:
                        if query_cache_keys[0] in per_query_cache:
                            print_text(f"🔄 Reusing cached vector search results (query unchanged)", style="dim")
                        else:
                            print_text(f"🤖 Processing vector search query: '{vs_queries_structured[0]['query']}'", style="dim")

                    # Show clarity message if both VS and SQL queries are active
                    if sql_query_args and vector_search_args:
                        print_text("📊 Processing both natural language and vector search queries - results will be combined", style="bold cyan")

                    # Show clarity message if both VS and browse modes are active
                    has_browse_in_command = '-b' in user_input or '--browse' in user_input
                    if has_browse_in_command:
                        print_text("💡 Using both vector search and browse mode - results will be combined", style="dim cyan")

                    # Process queries
                    try:
                        from promaia.nlq.nl_processor_wrapper import process_vector_search_to_content

                        combined_vs_content = {}
                        total_results = 0
                        new_queries_processed = 0

                        for i, vs_query_obj in enumerate(vs_queries_structured):
                            vs_prompt = vs_query_obj['query']
                            query_top_k = vs_query_obj['top_k']
                            query_threshold = vs_query_obj['threshold']
                            cache_key = query_cache_keys[i]

                            # Check if we have cached results for this specific query (with params)
                            if cache_key in per_query_cache:
                                # Use cached results
                                cached_result = per_query_cache[cache_key]

                                # Merge cached results
                                for db_name, entries in cached_result.items():
                                    if db_name not in combined_vs_content:
                                        combined_vs_content[db_name] = []
                                    combined_vs_content[db_name].extend(entries)

                                query_results = sum(len(entries) for entries in cached_result.values() if entries is not None)
                                total_results += query_results

                                if len(vs_queries_structured) > 1 and len(queries_to_run) > 0:
                                    print_text(f"   ♻️  Query {i+1} using cache: {query_results} results", style="dim green")
                            else:
                                # Need to run this query
                                if len(vs_queries_structured) > 1:
                                    print_text(f"🔍 Processing query {i+1}/{len(vs_queries_structured)}: '{vs_prompt}'", style="cyan")

                                # Process vector search with per-query parameters
                                vs_result = process_vector_search_to_content(
                                    vs_prompt,
                                    workspace=None,  # Allow cross-workspace searches
                                    verbose=True,  # Show detailed processing steps
                                    n_results=query_top_k,
                                    min_similarity=query_threshold
                                )

                                if vs_result:
                                    # Cache this query's results (with params in key)
                                    per_query_cache[cache_key] = vs_result

                                    # Merge results from this query into combined content
                                    for db_name, entries in vs_result.items():
                                        if db_name not in combined_vs_content:
                                            combined_vs_content[db_name] = []
                                        combined_vs_content[db_name].extend(entries)

                                    query_results = sum(len(entries) for entries in vs_result.values() if entries is not None)
                                    total_results += query_results
                                    new_queries_processed += 1
                                    if len(vs_queries_structured) > 1:
                                        print_text(f"   ✅ Query {i+1} found {query_results} results", style="green")
                                else:
                                    # Cache empty result to avoid re-running failed queries (with params in key)
                                    per_query_cache[cache_key] = {}
                                    if len(vs_queries_structured) > 1:
                                        print_text(f"   ⚠️  Query {i+1} found no results", style="yellow")

                        if not combined_vs_content:
                            # Only fail if we don't also have SQL results
                            if not sql_query_content:
                                print_text("❌ No content found for any vector search queries", style="red")
                                return False
                            else:
                                print_text("⚠️  No vector search results found, using SQL results only", style="yellow")
                        else:
                            if len(vs_queries_structured) > 1:
                                if new_queries_processed > 0 and len(cached_queries) > 0:
                                    print_text(f"🎯 Combined {len(vs_queries_structured)} queries: {total_results} total results ({new_queries_processed} new, {len(cached_queries)} cached)", style="green")
                                else:
                                    print_text(f"🎯 Combined {len(vs_queries_structured)} queries: {total_results} total results", style="green")
                            vs_content = combined_vs_content

                            # Update caches - use separate VS fields to avoid confusion with NL fields
                            context_state['vector_search_per_query_cache'] = per_query_cache
                            context_state['vector_search_content'] = vs_content
                            context_state['cached_vector_search_prompt'] = combined_vs_prompt

                    except Exception as e:
                        print_text(f"Error processing vector search query: {e}", style="bold red")
                        import traceback
                        traceback.print_exc()
                        # Only fail if we don't also have SQL results
                        if not sql_query_content:
                            return False
                        else:
                            print_text("⚠️  Vector search failed, using SQL results only", style="yellow")

                # Now handle the combined case or individual cases
                if sql_query_args or vector_search_args:
                    # Update other fields from parsed args
                    new_sources = getattr(parsed_args, 'sources', []) or []
                    new_filters = getattr(parsed_args, 'filters', []) or []
                    new_workspace = getattr(parsed_args, 'workspace', None)
                    new_mcp_servers = getattr(parsed_args, 'mcp_servers', []) or []

                    # Check if browse flag is present in the command
                    has_browse_flag = any('-b' in arg for arg in args_list)

                    # Preserve browse selections when user adds queries to browse command
                    if has_browse_flag and not new_sources:
                        debug_print("  → Preserving existing browse sources (query added to browse command)")
                    elif not new_sources and not has_browse_flag and context_state.get('browse_selections'):
                        # User typed only -sql/-vs without -b or -s — restore sources from browse_selections
                        context_state['sources'] = context_state['browse_selections'].copy()
                        context_state['is_mixed_browse_nl_command'] = True
                        debug_print("  → Restoring browse sources from previous browser selection")
                    else:
                        context_state['sources'] = new_sources

                    # Only clear filters if user explicitly provided new ones
                    if new_filters or new_sources:
                        context_state['filters'] = new_filters
                    context_state['mcp_servers'] = new_mcp_servers
                    if new_workspace:
                        context_state['workspace'] = new_workspace

                    # Update context state based on which queries we have
                    if sql_query_args and combined_nl_prompt:
                        context_state['sql_query_prompt'] = combined_nl_prompt
                    elif vector_search_args and combined_vs_prompt:
                        # Only set sql_query_prompt to VS prompt if there's no SQL prompt
                        context_state['sql_query_prompt'] = combined_vs_prompt

                    # Set mixed browse flag if we have browse selections and a query
                    if context_state.get('browse_selections') and (sql_query_args or vector_search_args):
                        context_state['is_mixed_browse_nl_command'] = True
                        debug_print("🔍 Set is_mixed_browse_nl_command=True for browse+query merge (edit_context)")

                    # Update the original query format
                    full_command = f"maia chat {user_input}"
                    context_state['original_query_format'] = full_command

                    # Reload with the updated content
                    if reload_context(skip_nl_cache_messages=True):
                        print_text("Context updated successfully!", style="bold green")
                        return True
                    else:
                        print_text("Failed to reload context with query content.", style="bold red")
                        return False

                else:
                    # Regular mode with sources and filters
                    new_sources = getattr(parsed_args, 'sources', []) or []
                    new_filters = getattr(parsed_args, 'filters', []) or []
                    new_workspace = getattr(parsed_args, 'workspace', None)
                    new_mcp_servers = getattr(parsed_args, 'mcp_servers', []) or []
                    
                    # Check if we're switching from NL mode to regular mode
                    had_nl_content = bool(context_state.get('sql_query_content'))
                    had_nl_prompt = bool(context_state.get('sql_query_prompt'))
                    nl_was_removed = had_nl_content or had_nl_prompt
                    
                    # Update context state
                    context_state['sources'] = new_sources
                    context_state['filters'] = new_filters
                    context_state['mcp_servers'] = new_mcp_servers
                    
                    if new_workspace:
                        context_state['workspace'] = new_workspace
                    
                    # Update the original query format for regular mode
                    full_command = f"maia chat {user_input}"
                    context_state['original_query_format'] = full_command
                    
                    # If we had NL content and now we don't, properly remove it
                    if nl_was_removed:
                        print_text("🔄 Natural language prompt removed - switching to regular mode", style="cyan")
                        print_text("🔄 Removing natural language results from context...", style="cyan")
                        
                        # IMPORTANT: Capture NL sources before clearing state to prevent any confusion  
                        nl_sources_to_remove = set(context_state.get('sql_query_content', {}).keys() if context_state.get('sql_query_content') else [])
                        context_state['sql_query_content'] = None
                        context_state['sql_query_prompt'] = None
                        context_state['cached_sql_query_prompt'] = ''
                        
                        try:
                            nonlocal initial_multi_source_data, total_pages_loaded
                            
                            # Get the current multi-source data
                            current_data = dict(initial_multi_source_data)
                            
                            # Build set of sources that should be kept based on new regular sources
                            sources_to_keep = set()
                            if new_sources:
                                for source_spec in new_sources:
                                    # Extract just the database name part (before :)
                                    db_name = source_spec.split(':')[0]
                                    sources_to_keep.add(db_name)
                            
                            # Remove sources that came from NL and are not in the new sources to keep
                            keys_to_remove = []
                            for key in current_data.keys():
                                # Check if this source was loaded via natural language
                                is_from_nl = key in nl_sources_to_remove
                                
                                # Check if this source should be kept based on new regular sources
                                should_keep = any(keep_src in key for keep_src in sources_to_keep) if sources_to_keep else False
                                
                                # Remove if it's from NL and shouldn't be kept
                                if is_from_nl and not should_keep:
                                    keys_to_remove.append(key)
                                    debug_print(f"Will remove NL source: {key} (not in new sources: {sources_to_keep})")
                            
                            # Remove the identified keys
                            for key in keys_to_remove:
                                if key in current_data:
                                    debug_print(f"Removing NL source from context: {key}")
                                    del current_data[key]
                            
                            # Update the global variables with the cleaned data
                            initial_multi_source_data = current_data
                            total_pages_loaded = sum(len(pages) for pages in current_data.values() if pages is not None)
                            
                            # Update context state as well
                            context_state['initial_multi_source_data'] = current_data
                            context_state['total_pages_loaded'] = total_pages_loaded
                            
                            # Apply mute handling - use empty context if muted
                            context_data_for_prompt = {} if context_state.get('context_muted') else current_data
                            
                            # Update the system prompt with the remaining data
                            mcp_tools_info = context_state.get('mcp_tools_info')
                            system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                            context_state['system_prompt'] = system_prompt
                            
                            debug_print(f"After NL removal: {len(current_data)} sources, {total_pages_loaded} pages")
                            print_text("Context updated successfully!", style="bold green")
                            return True
                            
                        except Exception as e:
                            print_text(f"❌ Error updating context after NL removal: {e}", style="red")
                            debug_print(f"NL removal error: {e}")
                            # Fall back to full reload if manual update fails
                            # Ensure NL state is still cleared before reload
                            context_state['sql_query_content'] = None
                            context_state['sql_query_prompt'] = None
                            context_state['cached_sql_query_prompt'] = ''
                            if reload_context():
                                print_text("Context updated successfully via reload!", style="bold green")
                                return True
                            else:
                                print_text("❌ Failed to reload context after removing natural language", style="red")
                                return False
                    else:
                        # No NL content to remove, just reload normally
                        if reload_context():
                            print_text("Context updated successfully!", style="bold green")
                            return True
                        else:
                            print_text("Failed to reload context with new settings.", style="bold red")
                            return False
                    
            except SystemExit:
                # argparse calls sys.exit on invalid arguments
                print_text("Invalid command syntax.", style="bold red")
                print_text("Examples:", style="dim")
                print_text("  -s journal:5 -s gmail:10 -f 'last week'", style="dim")
                print_text("  -nl emails from last week about project updates", style="dim")
                return False
            except Exception as e:
                print_text(f"Error parsing command: {e}", style="bold red")
                print_text("Examples:", style="dim")
                print_text("  -s journal:5 -s gmail:10 -f 'last week'", style="dim")
                print_text("  -nl emails from last week about project updates", style="dim")
                return False
                
        except (KeyboardInterrupt, EOFError):
            print_text("\nContext edit cancelled.", style="bold yellow")
            return False
    
    def handle_manual_browse_edit(user_input):
        """Handle manually edited browse commands."""
        # Access module-level function (needed for nested function scope)
        nonlocal_process_browser_selections = process_browser_selections
        
        try:
            # Import CLI functions we need
            import argparse
            import asyncio
            
            # Get original format from context state early (used throughout function)
            original_format = context_state.get('original_query_format', '')
            
            # Parse the browse command
            args_list = safe_split_command(user_input)
            
            # Create parser that handles browse commands
            parser = argparse.ArgumentParser(description="Manual browse command editor", add_help=False)
            parser.add_argument("--source", "-s", action="append", dest="sources")
            parser.add_argument("--filter", "-f", action="append", dest="filters")
            parser.add_argument("--workspace", "-ws", dest="workspace")
            parser.add_argument("--browse", "-b", action="append", nargs="*", dest="browse")
            # NOTE: This MUST match the other -sql/-vs parsers (top-level CLI and normal edit mode)
            parser.add_argument("--sql-query", "-sql", action="append", nargs="+", dest="sql_query")
            parser.add_argument("-nl", action="append", nargs="+", dest="sql_query", help=argparse.SUPPRESS)  # Deprecated alias
            parser.add_argument("--vector-search", "-vs", action="append", nargs="+", dest="vector_search")
            parser.add_argument("--top-k", "-tk", type=int, help="Maximum number of results from vector search")
            parser.add_argument("--threshold", "-th", type=float, help="Minimum similarity threshold for vector search")

            parsed_args = parser.parse_args(args_list)

            # Parse structured -vs queries with per-query parameters (-tk/-th)
            vs_queries_structured = []
            if getattr(parsed_args, 'vector_search', None):
                # Reconstruct the command as a list including 'maia chat' prefix for parsing
                command_with_prefix = ['maia', 'chat'] + args_list
                vs_queries_structured = parse_vs_queries_with_params(command_with_prefix)

            # Extract components
            regular_sources = parsed_args.sources or []
            # Flatten nested lists from multiple -b flags: [['acme'], ['acme.tg']] -> ['acme', 'acme.tg']
            raw_browse = parsed_args.browse or []
            browse_databases = []
            if raw_browse:
                for item in raw_browse:
                    if isinstance(item, list):
                        browse_databases.extend(item)
                    else:
                        browse_databases.append(item)
            original_filters = parsed_args.filters or []
            # Don't use old workspace from context - let it be resolved from browse_databases
            workspace = parsed_args.workspace
            # NOTE: With action="append" and nargs="+", sql_query is a list of lists
            # Convert to list of strings, matching the other implementations
            sql_query_raw = parsed_args.sql_query or []
            sql_query_parts = [' '.join(nl_args) for nl_args in sql_query_raw if nl_args] if sql_query_raw else []
            
            # Detect if the browse part of the command actually changed
            original_command = context_state.get('original_query_format', '')
            browse_changed = False
            
            if browse_databases:
                # Extract browse databases from original command
                import re
                original_browse_match = re.findall(r'-b\s+([^\s-]+(?:\s+[^\s-]+)*)', original_command)
                original_browse_databases = []
                for match in original_browse_match:
                    original_browse_databases.extend(match.split())
                
                # Compare current browse databases with original
                browse_changed = set(browse_databases) != set(original_browse_databases)
                
                # Check if the browse scope expanded (new databases added) or reduced (databases removed)
                # Only launch browser if new databases were added, not if databases were removed
                if browse_changed and context_state.get('browse_selections'):
                    # Build set of database/workspace names from NEW browse command
                    new_browse_db_set = set()
                    for browse_db in browse_databases:
                        base_name = browse_db.split(':')[0] if ':' in browse_db else browse_db
                        new_browse_db_set.add(base_name)
                    
                    old_browse_db_set = set()
                    for browse_db in original_browse_databases:
                        base_name = browse_db.split(':')[0] if ':' in browse_db else browse_db
                        old_browse_db_set.add(base_name)
                    
                    # Determine if scope expanded (new databases) or reduced (removed databases)
                    added_databases = new_browse_db_set - old_browse_db_set
                    removed_databases = old_browse_db_set - new_browse_db_set
                    
                    if added_databases and not removed_databases:
                        # Pure expansion: new databases added, none removed
                        debug_print(f"Detected scope expansion: new databases {added_databases} added")
                        browse_changed = True
                    elif removed_databases and not added_databases:
                        # Pure reduction: databases removed, none added
                        debug_print(f"Detected scope reduction: databases {removed_databases} removed, keeping browser closed")
                        browse_changed = False  # Don't launch browser for reductions
                    elif added_databases and removed_databases:
                        # Mixed: both added and removed - treat as changed, launch browser
                        debug_print(f"Detected scope change: added {added_databases}, removed {removed_databases}")
                        browse_changed = True
                    else:
                        # No actual change (shouldn't happen but handle it)
                        browse_changed = False
            elif context_state.get('browse_selections'):
                # If no browse databases now but we had them before, that's a change
                browse_changed = True
            
            # Detect if natural language was removed FIRST (before processing)
            nl_was_removed = False
            nl_sources_to_remove = set()  # Initialize for use throughout function
            if not sql_query_parts and (context_state.get('sql_query_content') or context_state.get('sql_query_prompt')):
                # Only show NL removal message if we're actually removing NL content (not VS content)
                # Check is_vector_search flag to distinguish between NL and VS modes
                if not context_state.get('is_vector_search'):
                    print_text("🔄 Natural language prompt removed - switching to regular browse mode", style="dim")
                nl_was_removed = True
                # Capture NL sources before any state changes
                nl_sources_to_remove = set(context_state.get('sql_query_content', {}).keys() if context_state.get('sql_query_content') else [])
                
            # Process natural language query if present (supports multiple -nl queries)
            nl_prompt = None
            sql_query_content = None
            if sql_query_parts:
                # Create combined prompt for caching (WITHOUT -nl prefix for comparison)
                combined_nl_prompt = " ".join(sql_query_parts)

                # Check cache first - compare against NL-specific cache key
                cached_nl_prompt = context_state.get('cached_sql_query_prompt', '')
                cached_nl_content = context_state.get('cached_sql_query_content', {})

                debug_print(f"🔍 Manual Browse Edit NL Cache Check:")
                debug_print(f"  New prompt(s): {sql_query_parts}")
                debug_print(f"  Cached NL prompt: '{cached_nl_prompt}'")
                debug_print(f"  Prompts match: {combined_nl_prompt == cached_nl_prompt}")
                debug_print(f"  Has cached content: {bool(cached_nl_content)}")

                if combined_nl_prompt == cached_nl_prompt and cached_nl_content:
                    print_text("🔄 Reusing cached natural language results (prompt unchanged)", style="cyan")
                    sql_query_content = cached_nl_content
                    debug_print(f"  → Using cached NL results from separate cache")
                else:
                    debug_print(f"  → Cache miss, will re-process query")
                    
                    # Display processing message
                    if len(sql_query_parts) > 1:
                        print_text(f"🤖 Processing {len(sql_query_parts)} separate natural language queries", style="cyan")
                        for i, prompt in enumerate(sql_query_parts):
                            print_text(f"   {i+1}. '{prompt}'", style="dim")
                    else:
                        print_text(f"🤖 Processing natural language query: '{sql_query_parts[0]}'", style="cyan")
                    
                    # Process the natural language queries (multiple prompts supported)
                    try:
                        from promaia.storage.unified_query import get_query_interface
                        
                        # Determine workspace for natural language processing
                        nl_workspace = workspace or context_state.get('resolved_workspace') or context_state.get('workspace')
                        if not nl_workspace:
                            from promaia.config.workspaces import get_workspace_manager
                            workspace_manager = get_workspace_manager()
                            nl_workspace = workspace_manager.get_default_workspace()
                        
                        if nl_workspace:
                            # Process each natural language query separately and combine results
                            query_interface = get_query_interface()
                            combined_nl_content = {}
                            
                            for i, nl_query in enumerate(sql_query_parts):
                                if len(sql_query_parts) > 1:
                                    print_text(f"🔍 Processing query {i+1}/{len(sql_query_parts)}: '{nl_query}'", style="dim")
                                
                                # For OR logic: Natural language searches ALL databases (not restricted to browser selections)
                                # Browser selections will be loaded separately and combined with NL results
                                database_names = None  # Search all databases for maximum content discovery
                                    
                                query_results = query_interface.natural_language_query(nl_query, nl_workspace, database_names)
                                
                                # Merge results from this query into combined results
                                if query_results:
                                    for db_name, pages in query_results.items():
                                        if db_name in combined_nl_content:
                                            # Combine pages, avoiding duplicates
                                            existing_ids = {p.get('id') for p in combined_nl_content[db_name] if isinstance(p, dict) and 'id' in p}
                                            for page in pages:
                                                if not isinstance(page, dict) or 'id' not in page or page['id'] not in existing_ids:
                                                    combined_nl_content[db_name].append(page)
                                                    if isinstance(page, dict) and 'id' in page:
                                                        existing_ids.add(page['id'])
                                        else:
                                            combined_nl_content[db_name] = pages
                            
                            sql_query_content = combined_nl_content

                            if sql_query_content:
                                total_results = sum(len(pages) for pages in sql_query_content.values() if pages is not None)
                                print_text(f"✅ NL results: {total_results} entries from {len(sql_query_content)} databases", style="green")

                                # Cache NL results separately (not mixed with VS)
                                context_state['cached_sql_query_prompt'] = combined_nl_prompt
                                context_state['cached_sql_query_content'] = sql_query_content
                                debug_print(f"  → Processed and cached new NL results (separate cache)")

                                # Store NL content separately from VS content
                                context_state['sql_query_content'] = sql_query_content
                                debug_print(f"  → Stored NL content separately in context_state['sql_query_content']")

                                # Show combined total if we also have VS content
                                vs_content = context_state.get('vector_search_content', {})
                                if vs_content:
                                    vs_total = sum(len(pages) for pages in vs_content.values() if pages is not None)
                                    combined_total = total_results + vs_total
                                    print_text(f"💡 Total with VS: {combined_total} entries ({total_results} NL + {vs_total} VS)", style="cyan")
                            else:
                                print_text("❌ No content found for natural language queries", style="yellow")
                        else:
                            print_text("❌ No workspace available for natural language processing", style="yellow")
                            
                    except Exception as e:
                        print_text(f"❌ Error processing natural language query: {e}", style="yellow")
            elif nl_was_removed:
                # Clear NL cache and content when removed (but keep VS cache if present)
                context_state['cached_sql_query_prompt'] = ''
                context_state['cached_sql_query_content'] = {}
                context_state['sql_query_content'] = None
                debug_print(f"🗑️  Cleared NL content and cache (NL query removed)")

            # Process vector search query if present (supports multiple -vs queries)
            # NOTE: This is similar to the -nl handling above but uses vector search instead
            vector_search_raw = parsed_args.vector_search or []
            vector_search_parts = [' '.join(vs_args) for vs_args in vector_search_raw if vs_args] if vector_search_raw else []

            # Store structured queries in context_state for per-query parameter handling
            if vs_queries_structured:
                context_state['vector_search_queries'] = vs_queries_structured
            else:
                # Backward compatibility: convert simple string queries to dict format with defaults
                context_state['vector_search_queries'] = [
                    {'query': vs_query, 'top_k': 20, 'threshold': 0.75}
                    for vs_query in vector_search_parts
                ]

            # Detect if VS was removed and capture sources BEFORE clearing
            vs_was_removed = False
            vs_sources_to_remove = set()  # Initialize for use throughout function
            had_vs_before = bool(context_state.get('cached_vector_search_prompt'))
            has_vs_now = bool(vector_search_parts)
            if had_vs_before and not has_vs_now:
                vs_was_removed = True
                # Capture VS sources before any state changes
                vs_sources_to_remove = set(context_state.get('vector_search_content', {}).keys() if context_state.get('vector_search_content') else [])

            if vector_search_parts:
                # If we have both NL and VS, we'll merge the results
                if sql_query_parts and sql_query_content:
                    print_text("📊 Processing both natural language and vector search queries - results will be combined", style="bold cyan")

                # Create combined prompt for caching (without -vs flag)
                combined_vs_prompt = " ".join(vector_search_parts)

                # Check VS cache independently (using separate cache key)
                cached_vs_prompt = context_state.get('cached_vector_search_prompt', '')
                cached_vs_content = context_state.get('cached_vector_search_content', {})
                vs_cache_hit = (combined_vs_prompt == cached_vs_prompt and cached_vs_content)

                debug_print(f"🔍 Manual Browse Edit VS Cache Check:")
                debug_print(f"  New prompt(s): {vector_search_parts}")
                debug_print(f"  Cached VS prompt: '{cached_vs_prompt}'")
                debug_print(f"  Prompts match: {combined_vs_prompt == cached_vs_prompt}")
                debug_print(f"  Has cached content: {bool(cached_vs_content)}")
                debug_print(f"  Cache hit: {vs_cache_hit}")

                if vs_cache_hit:
                    print_text("🔄 Reusing cached vector search results (prompt unchanged)", style="cyan")
                    combined_vs_content = cached_vs_content
                    debug_print(f"  → Using cached VS results from separate cache")

                if not vs_cache_hit:
                    debug_print(f"  → Cache miss, will re-process vector search query")

                    # Per-query caching for manual browse edit
                    per_query_cache = context_state.get('vector_search_per_query_cache', {})

                    # Track which queries need to be run vs cached
                    queries_to_run = []
                    cached_queries = []

                    # Build cache keys for all queries first
                    for i, vs_prompt in enumerate(vector_search_parts):
                        # Get parameters for this query
                        vs_queries = context_state.get('vector_search_queries', [])
                        query_top_k = 20
                        query_threshold = 0.75
                        if i < len(vs_queries) and isinstance(vs_queries[i], dict):
                            query_top_k = vs_queries[i].get('top_k', 20)
                            query_threshold = vs_queries[i].get('threshold', 0.75)

                        cache_key = f"{vs_prompt}|{query_top_k}|{query_threshold}"

                        if cache_key in per_query_cache:
                            cached_queries.append((i, vs_prompt))
                        else:
                            queries_to_run.append((i, vs_prompt))

                    # Show cache status
                    if len(vector_search_parts) > 1:
                        if cached_queries and queries_to_run:
                            print_text(f"🤖 Processing {len(vector_search_parts)} vector search queries ({len(cached_queries)} cached, {len(queries_to_run)} new)", style="cyan")
                        elif cached_queries:
                            print_text(f"🔄 Reusing cached results for all {len(cached_queries)} vector search queries", style="cyan")
                        else:
                            print_text(f"🤖 Processing {len(vector_search_parts)} separate vector search queries", style="cyan")

                        # Show query list
                        for i, prompt in enumerate(vector_search_parts):
                            # Check if this specific query+params is cached
                            vs_queries = context_state.get('vector_search_queries', [])
                            query_top_k = 20
                            query_threshold = 0.75
                            if i < len(vs_queries) and isinstance(vs_queries[i], dict):
                                query_top_k = vs_queries[i].get('top_k', 20)
                                query_threshold = vs_queries[i].get('threshold', 0.75)
                            cache_key = f"{prompt}|{query_top_k}|{query_threshold}"
                            cached_marker = " (cached)" if cache_key in per_query_cache else ""
                            print_text(f"   {i+1}. '{prompt}'{cached_marker}", style="dim")
                    else:
                        # Single query case
                        vs_queries = context_state.get('vector_search_queries', [])
                        query_top_k = 20
                        query_threshold = 0.75
                        if len(vs_queries) > 0 and isinstance(vs_queries[0], dict):
                            query_top_k = vs_queries[0].get('top_k', 20)
                            query_threshold = vs_queries[0].get('threshold', 0.75)
                        cache_key = f"{vector_search_parts[0]}|{query_top_k}|{query_threshold}"
                        if cache_key in per_query_cache:
                            print_text(f"🔄 Reusing cached vector search results (query unchanged)", style="cyan")
                        else:
                            print_text(f"🤖 Processing vector search query: '{vector_search_parts[0]}'", style="cyan")

                    # Show clarity message if both VS and browse modes are active
                    if browse_databases:
                        print_text("💡 Using both vector search and browse mode - results will be combined", style="dim cyan")

                    try:
                        from promaia.nlq.nl_processor_wrapper import process_vector_search_to_content

                        # Process each vector search query and combine results
                        combined_vs_content = {}
                        new_queries_processed = 0

                        for i, vs_prompt in enumerate(vector_search_parts):
                            # Get per-query parameters from structured queries
                            query_top_k = 20  # default
                            query_threshold = 0.75  # default
                            vs_queries = context_state.get('vector_search_queries', [])
                            if i < len(vs_queries) and isinstance(vs_queries[i], dict):
                                query_top_k = vs_queries[i].get('top_k', 20)
                                query_threshold = vs_queries[i].get('threshold', 0.75)

                            # Create cache key with query text AND parameters
                            cache_key = f"{vs_prompt}|{query_top_k}|{query_threshold}"

                            # Check if we have cached content for this exact query+params combination
                            if cache_key in per_query_cache:
                                # Use cached content (not just search results - full loaded content)
                                cached_result = per_query_cache[cache_key]

                                # Count total pages in cached result
                                cached_page_count = sum(len(pages) for pages in cached_result.values() if pages is not None)
                                print_text(f"   ♻️  Using cached content for query {i+1}: {cached_page_count} pages", style="dim green")

                                # Merge cached results
                                for db_name, pages in cached_result.items():
                                    if db_name in combined_vs_content:
                                        # Combine pages, avoiding duplicates
                                        existing_ids = {p.get('id') for p in combined_vs_content[db_name] if isinstance(p, dict) and 'id' in p}
                                        for page in pages:
                                            if not isinstance(page, dict) or 'id' not in page or page['id'] not in existing_ids:
                                                combined_vs_content[db_name].append(page)
                                                if isinstance(page, dict) and 'id' in page:
                                                    existing_ids.add(page['id'])
                                    else:
                                        combined_vs_content[db_name] = pages
                            else:
                                # Need to run this query
                                if len(vector_search_parts) > 1:
                                    print_text(f"🔍 Processing query {i+1}/{len(vector_search_parts)}: '{vs_prompt}'", style="dim")

                                vs_result = process_vector_search_to_content(
                                    vs_prompt,
                                    workspace=None,  # Allow cross-workspace
                                    verbose=True,
                                    n_results=query_top_k,  # Use per-query parameter
                                    min_similarity=query_threshold  # Use per-query parameter
                                )

                                # Cache this query's LOADED CONTENT with query+params as key
                                if vs_result:
                                    per_query_cache[cache_key] = vs_result
                                    new_queries_processed += 1

                                    # Merge results
                                    for db_name, pages in vs_result.items():
                                        if db_name in combined_vs_content:
                                            # Combine pages, avoiding duplicates
                                            existing_ids = {p.get('id') for p in combined_vs_content[db_name] if isinstance(p, dict) and 'id' in p}
                                            for page in pages:
                                                if not isinstance(page, dict) or 'id' not in page or page['id'] not in existing_ids:
                                                    combined_vs_content[db_name].append(page)
                                                    if isinstance(page, dict) and 'id' in page:
                                                        existing_ids.add(page['id'])
                                        else:
                                            combined_vs_content[db_name] = pages
                                else:
                                    # Cache empty result
                                    per_query_cache[cache_key] = {}

                        # Store VS results separately (don't merge with NL here - let reload_context do it)
                        if combined_vs_content:
                            vs_total_results = sum(len(pages) for pages in combined_vs_content.values() if pages is not None)

                            if len(vector_search_parts) > 1:
                                if new_queries_processed > 0 and len(cached_queries) > 0:
                                    print_text(f"✅ VS results: {vs_total_results} entries from {len(combined_vs_content)} databases ({new_queries_processed} new, {len(cached_queries)} cached)", style="green")
                                else:
                                    print_text(f"✅ VS results: {vs_total_results} entries from {len(combined_vs_content)} databases", style="green")
                            else:
                                print_text(f"✅ VS results: {vs_total_results} entries from {len(combined_vs_content)} databases", style="green")

                            # Cache VS results separately (not mixed with NL)
                            context_state['vector_search_per_query_cache'] = per_query_cache
                            context_state['cached_vector_search_prompt'] = combined_vs_prompt
                            context_state['cached_vector_search_content'] = combined_vs_content
                            debug_print(f"  → Processed and cached new VS results (separate cache)")

                            # Store VS content separately from NL content
                            context_state['vector_search_content'] = combined_vs_content
                            debug_print(f"  → Stored VS content separately in context_state['vector_search_content']")

                            # Show combined total if we also have NL content
                            nl_content = context_state.get('sql_query_content', {})
                            if nl_content:
                                nl_total = sum(len(pages) for pages in nl_content.values() if pages is not None)
                                combined_total = vs_total_results + nl_total
                                print_text(f"💡 Total with NL: {combined_total} entries ({nl_total} NL + {vs_total_results} VS)", style="cyan")
                        else:
                            print_text("❌ No content found for vector search queries", style="yellow")

                    except Exception as e:
                        print_text(f"❌ Error processing vector search query: {e}", style="yellow")
            elif vs_was_removed:
                # Clear VS cache and content when removed (but keep NL cache if present)
                context_state['cached_vector_search_prompt'] = ''
                context_state['cached_vector_search_content'] = {}
                context_state['vector_search_content'] = None
                context_state['vector_search_per_query_cache'] = {}
                context_state['vector_search_queries'] = []
                debug_print(f"🗑️  Cleared VS content and cache (VS query removed)")

            # Parse browse databases and expand workspace names (same logic as cli.py)
            database_filter = None
            default_days = None
            
            if browse_databases:
                from promaia.config.workspaces import get_workspace_manager
                from promaia.config.databases import get_database_manager
                workspace_manager = get_workspace_manager()
                db_manager = get_database_manager()
                
                database_filter = []
                workspace_names = []
                
                for browse_spec in browse_databases:
                    if ':' in browse_spec:
                        db_name, days_str = browse_spec.rsplit(':', 1)
                        try:
                            days = int(days_str)
                            if default_days is None:
                                default_days = days
                            
                            # Check if db_name is a workspace
                            if workspace_manager.validate_workspace(db_name):
                                # Don't expand workspace - let browser handle it
                                workspace_names.append(db_name)
                            else:
                                database_filter.append(browse_spec)  # Keep the full spec with days
                        except ValueError:
                            database_filter.append(browse_spec)
                    else:
                        # Check if this is a workspace name
                        if workspace_manager.validate_workspace(browse_spec):
                            # Don't expand workspace - let browser handle it
                            workspace_names.append(browse_spec)
                        else:
                            # It's a specific database name
                            database_filter.append(browse_spec)
                
                # If we only have workspace names, clear the database filter
                if workspace_names and not database_filter:
                    database_filter = None  # Let browser show all databases in the workspace
            
            # Resolve workspace from browse databases if needed
            resolved_workspace = workspace
            multiple_workspaces = []
            
            if browse_databases and not workspace:
                # First, collect all unique workspaces from the browse arguments
                for browse_db in browse_databases:
                    if workspace_manager.validate_workspace(browse_db):
                        if browse_db not in multiple_workspaces:
                            multiple_workspaces.append(browse_db)
                
                # Also check database_filter for workspace prefixes
                if database_filter:
                    for db_name in database_filter:
                        if '.' in db_name:
                            determined_workspace = db_name.split('.')[0]
                            if workspace_manager.validate_workspace(determined_workspace):
                                if determined_workspace not in multiple_workspaces:
                                    multiple_workspaces.append(determined_workspace)
                
                # Handle workspace resolution based on how many workspaces we found
                if len(multiple_workspaces) == 1:
                    # Single workspace - use it normally
                    resolved_workspace = multiple_workspaces[0]
                    print_text(f"INFO: Using workspace '{resolved_workspace}' from browse argument.", style="cyan")
                elif len(multiple_workspaces) > 1:
                    # Multiple workspaces - don't resolve to a single one, use database_filter instead
                    resolved_workspace = None
                    print_text(f"INFO: Using multiple workspaces: {', '.join(multiple_workspaces)}", style="cyan")
                else:
                    # No workspace found in browse args, try to determine from database_filter
                    if database_filter:
                        for browse_db in database_filter:
                            if '.' in browse_db:
                                determined_workspace = browse_db.split('.')[0]
                                if workspace_manager.validate_workspace(determined_workspace):
                                    resolved_workspace = determined_workspace
                                    print_text(f"INFO: Using workspace '{resolved_workspace}' from browse database '{browse_db}'.", style="cyan")
                                    break
            
            if not resolved_workspace:
                resolved_workspace = context_state.get('resolved_workspace')
            
            # Update context state with the resolved workspace(s)
            if resolved_workspace:
                context_state['workspace'] = resolved_workspace
                context_state['resolved_workspace'] = resolved_workspace
            elif multiple_workspaces:
                # For multiple workspaces, store them in a special way
                context_state['workspace'] = None  # No single workspace
                context_state['resolved_workspace'] = None
                context_state['multiple_workspaces'] = multiple_workspaces
            
            # Handle mixed commands (browse + regular sources) differently
            # BUT: If browse was just added (browse_changed and no previous browse_selections),
            # we should launch the browser even with regular sources present
            user_is_adding_browse_for_first_time = browse_changed and not context_state.get('browse_selections')
            
            if browse_databases and regular_sources and not user_is_adding_browse_for_first_time:
                # This is a mixed command - update context directly without launching browser
                print_text(f"🔄 Updating context with browse databases and regular sources...", style="cyan")
                
                # Start with regular sources
                all_sources = regular_sources.copy()
                # Hmm seems odd that regular sources gets stored as all sources. but maybe it makes sense.
                
                # For workspaces mentioned in -b, preserve existing browser selections instead of expanding
                existing_sources = context_state.get('sources', [])
                preserved_workspace_sources = []
                
                for browse_db in browse_databases:
                    if workspace_manager.validate_workspace(browse_db):
                        # This is a workspace - preserve existing browser selections for this workspace
                        for existing_source in existing_sources:
                            source_db = existing_source.split(':')[0] if ':' in existing_source else existing_source
                            if '.' in source_db:
                                source_workspace = source_db.split('.')[0]
                                if source_workspace == browse_db:
                                    preserved_workspace_sources.append(existing_source)
                
                # Add preserved workspace sources
                all_sources.extend(preserved_workspace_sources)
                
                # Only add database_filter sources that aren't already covered by preserved sources
                if database_filter:
                    for db_name in database_filter:
                        # Check if this database is already preserved
                        db_base = db_name.split(':')[0] if ':' in db_name else db_name
                        already_preserved = any(
                            existing.split(':')[0] == db_base 
                            for existing in preserved_workspace_sources
                        )
                        
                        if not already_preserved:
                            # Check if this is a workspace name that was expanded
                            workspace_found = False
                            for browse_db in browse_databases:
                                if workspace_manager.validate_workspace(browse_db) and db_name.startswith(f"{browse_db}."):
                                    # This database came from workspace expansion - add with default days
                                    if default_days:
                                        source_with_days = f"{db_name}:{default_days}"
                                    else:
                                        source_with_days = db_name
                                    if source_with_days not in all_sources:
                                        all_sources.append(source_with_days)
                                    workspace_found = True
                                    break
                            
                            # If not from workspace expansion, add the database as-is
                            if not workspace_found and db_name not in all_sources and f"{db_name}:" not in str(all_sources):
                                all_sources.append(db_name)
                
                # Update context state
                context_state['sources'] = all_sources
                context_state['filters'] = original_filters
                
                # Update the original query format to the manually edited command
                context_state['original_query_format'] = user_input
                update_query_command()
                
                # Reload context with the updated information
                if reload_context():
                    print_text("Context updated successfully with mixed command!", style="green")
                    return True
                else:
                    print_text("❌ Failed to reload context after mixed command update", style="red")
                    return False
            
            # Handle case where browse databases exist but didn't change (or changed via reduction)
            elif browse_databases and not browse_changed:
                # Check if this is a scope reduction (databases removed from browse list)
                # In this case, we need to filter out sources and reload context
                if set(browse_databases) != set(original_browse_databases if original_browse_databases else []):
                    # Scope changed but browse_changed=False means it was a reduction
                    removed_databases = set(original_browse_databases) - set(browse_databases)
                    debug_print(f"Applying scope reduction: {removed_databases} removed")
                    
                    # Build the new browse scope - what databases/sources should be kept
                    # We need to check against the ACTUAL browse_selections, not all workspace databases
                    # Because when you go from "-b acme acme.tg" to "-b acme", acme.tg should be removed
                    # even though it's part of the acme workspace
                    
                    from promaia.config.workspaces import get_workspace_manager
                    workspace_manager = get_workspace_manager()
                    
                    # Determine which specific databases were in the OLD command but not in the NEW command
                    old_db_set = set()
                    new_db_set = set()
                    
                    for browse_db in original_browse_databases:
                        old_db_set.add(browse_db.split(':')[0])
                    
                    for browse_db in browse_databases:
                        new_db_set.add(browse_db.split(':')[0])
                    
                    removed_dbs = old_db_set - new_db_set
                    debug_print(f"Databases to remove: {removed_dbs}")
                    
                    # Filter sources: keep only those NOT from removed databases
                    current_sources = context_state.get('sources', []) or []
                    filtered_sources = []
                    for source in current_sources:
                        source_db = source.split(':')[0] if ':' in source else source
                        
                        # Check if this source's database is in the removed set
                        should_remove = False
                        for removed_db in removed_dbs:
                            if source_db == removed_db or source_db.startswith(f"{removed_db}."):
                                should_remove = True
                                break
                        
                        if not should_remove:
                            filtered_sources.append(source)
                        else:
                            debug_print(f"Removing source from removed database: {source}")
                    
                    # Update context_state with filtered sources
                    debug_print(f"Filtered sources after removal: {filtered_sources}")
                    context_state['sources'] = filtered_sources
                    context_state['original_query_format'] = user_input
                    
                    # Also update browse_selections to match
                    context_state['browse_selections'] = filtered_sources.copy()
                    
                    # CRITICAL: Also filter out any filters that apply to the removed databases
                    current_filters = context_state.get('filters', []) or []
                    if current_filters:
                        filtered_filters = []
                        for filter_expr in current_filters:
                            # Check if this filter applies to a removed database
                            # Filter format can be: "acme.tg:7:discord_channel_name=general"
                            filter_db = filter_expr.split(':')[0] if ':' in filter_expr else filter_expr
                            
                            # Check if this filter's database is in the removed set
                            should_remove_filter = False
                            for removed_db in removed_dbs:
                                if filter_db == removed_db or filter_db.startswith(f"{removed_db}."):
                                    should_remove_filter = True
                                    break
                            
                            if not should_remove_filter:
                                filtered_filters.append(filter_expr)
                            else:
                                debug_print(f"Removing filter for removed database: {filter_expr}")
                        
                        context_state['filters'] = filtered_filters
                        debug_print(f"Filtered filters after removal: {filtered_filters}")
                    
                    update_query_command()
                    
                    if reload_context(skip_nl_cache_messages=True):
                        print_text("Context updated successfully after scope reduction!", style="green")
                        return True
                    else:
                        print_text("❌ Failed to reload context after scope reduction", style="red")
                        return False
                
                # Update only natural language/vector search context without re-launching browser
                # NOTE: NL and VS content are now stored separately for proper attribution
                if sql_query_content or 'combined_nl_content' in locals() or 'combined_vs_content' in locals():
                    # Determine what was processed
                    has_nl = 'combined_nl_prompt' in locals() and sql_query_parts and 'combined_nl_content' in locals()
                    has_vs = 'combined_vs_content' in locals() and vector_search_parts

                    if has_nl and has_vs:
                        print_text("🔄 Updating natural language + vector search context (browse unchanged)...", style="dim")
                        # Store NL and VS separately (don't merge them here)
                        context_state['sql_query_content'] = combined_nl_content
                        context_state['vector_search_content'] = combined_vs_content
                        context_state['sql_query_prompt'] = None  # Already processed, don't re-process
                        context_state['is_vector_search'] = 'mixed'  # Mark as mixed mode
                    elif has_vs:
                        print_text("🔄 Updating vector search context (browse unchanged)...", style="dim")
                        # VS content was already stored in context_state['vector_search_content'] above
                        # Just ensure NL prompt is cleared since we're not processing NL
                        context_state['sql_query_prompt'] = None
                        context_state['is_vector_search'] = True
                    else:
                        print_text("🔄 Updating natural language context (browse unchanged)...", style="dim")
                        # Store NL content separately
                        if 'combined_nl_content' in locals():
                            context_state['sql_query_content'] = combined_nl_content
                        elif sql_query_content:
                            # Backwards compatibility for old code paths
                            context_state['sql_query_content'] = sql_query_content
                        context_state['sql_query_prompt'] = None  # Already processed, don't re-process
                        context_state['is_vector_search'] = False
                    
                    # Set the mixed browse+NL flag for OR logic
                    # This ensures NL results are properly merged with browse results
                    has_browse_selections = bool(context_state.get('browse_selections'))
                    has_regular_sources = bool(context_state.get('sources'))
                    if has_browse_selections and has_regular_sources:
                        context_state['is_mixed_browse_nl_command'] = True
                        debug_print("🔍 Set is_mixed_browse_nl_command=True for browse+NL merge")
                    
                    # Update the original query format to reflect the new natural language query
                    context_state['original_query_format'] = f"maia chat {user_input}"
                    update_query_command()
                    
                    # Reload context to apply the new natural language results
                    if reload_context(skip_nl_cache_messages=True):
                        print_text("Context updated successfully!", style="bold green")
                        return True
                    else:
                        print_text("❌ Failed to reload context with new natural language results", style="red")
                        return False
                elif nl_was_removed or vs_was_removed:
                    # Handle removal of NL and/or VS queries while preserving browse selections
                    if nl_was_removed and vs_was_removed:
                        print_text("🔄 Removing natural language and vector search results from context...", style="cyan")
                    elif nl_was_removed:
                        print_text("🔄 Removing natural language results from context...", style="cyan")
                    else:  # vs_was_removed
                        print_text("🔄 Removing vector search results from context...", style="cyan")

                    # Update the original query format to reflect removal of queries
                    context_state['original_query_format'] = f"maia chat {user_input}"
                    update_query_command()

                    # Instead of full reload, just update the system prompt without NL/VS content
                    # This preserves browser sources while removing query results
                    try:
                        nonlocal initial_multi_source_data, total_pages_loaded

                        # Get the current multi-source data
                        current_data = dict(initial_multi_source_data)

                        # Build set of sources that should be kept based on browse selections
                        browse_selections = context_state.get('browse_selections', [])
                        sources_to_keep = set()
                        if browse_selections:
                            for browse_sel in browse_selections:
                                # Extract database name from selection, stripping day suffix
                                # e.g., 'acme.cpj:7' -> 'acme.cpj', 'acme.tg#channel:7' -> 'acme.tg#channel'
                                if '#' in browse_sel:
                                    # Discord channel: acme.tg#channel:7
                                    base_name = browse_sel.rsplit(':', 1)[0] if ':' in browse_sel else browse_sel
                                else:
                                    # Regular database: acme.cpj:7
                                    base_name = browse_sel.split(':')[0] if ':' in browse_sel else browse_sel
                                sources_to_keep.add(base_name.lower())

                        # Note: vs_sources_to_remove was captured earlier when VS removal was detected
                        # (see line 2667-2673) to ensure we have the sources before clearing the content

                        # Remove sources that came from NL/VS and are not in browser selections
                        keys_to_remove = []
                        for key in current_data.keys():
                            # Check if this source was loaded via natural language or vector search
                            is_from_nl = key in nl_sources_to_remove
                            is_from_vs = key in vs_sources_to_remove

                            # Check if this source should be kept based on browser selections
                            should_keep = any(keep_src in key.lower() for keep_src in sources_to_keep) if sources_to_keep else False

                            # Remove if it's from NL/VS and not selected in browser
                            if (is_from_nl or is_from_vs) and not should_keep:
                                keys_to_remove.append(key)
                                source_type = "NL" if is_from_nl else "VS"
                                debug_print(f"Will remove {source_type} source: {key} (not in browse selections: {sources_to_keep})")

                        # Remove the identified keys
                        for key in keys_to_remove:
                            if key in current_data:
                                debug_print(f"Removing query source from context: {key}")
                                del current_data[key]

                        # Update the global variables with the cleaned data
                        initial_multi_source_data = current_data
                        total_pages_loaded = sum(len(pages) for pages in current_data.values() if pages is not None)

                        # Update context state as well
                        context_state['initial_multi_source_data'] = current_data
                        context_state['total_pages_loaded'] = total_pages_loaded

                        # Apply mute handling - use empty context if muted
                        context_data_for_prompt = {} if context_state.get('context_muted') else current_data

                        # Update the system prompt with the remaining data
                        mcp_tools_info = context_state.get('mcp_tools_info')
                        system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                        context_state['system_prompt'] = system_prompt

                        debug_print(f"After query removal: {len(current_data)} sources, {total_pages_loaded} pages")
                        print_text("Context updated successfully!", style="bold green")
                        return True
                    except Exception as e:
                        print_text(f"❌ Error updating context after query removal: {e}", style="red")
                        debug_print(f"Query removal error: {e}")
                        # Fall back to full reload if manual update fails
                        # Ensure query state is still cleared before reload
                        if nl_was_removed:
                            context_state['sql_query_content'] = None
                            context_state['sql_query_prompt'] = None
                            context_state['cached_sql_query_prompt'] = ''
                        if vs_was_removed:
                            context_state['vector_search_content'] = None
                            context_state['cached_vector_search_prompt'] = ''
                            context_state['vector_search_per_query_cache'] = {}
                            context_state['vector_search_queries'] = []
                        if reload_context(skip_nl_cache_messages=True):
                            print_text("Context updated successfully via reload!", style="bold green")
                            return True
                        else:
                            print_text("❌ Failed to reload context after removing queries", style="red")
                            return False
                else:
                    print_text("ℹ️  No changes detected. Context unchanged.", style="yellow")
                    return False
            
            # Handle browse mode using unified browser - but only if browse part actually changed
            elif browse_databases and browse_changed:
                try:
                    from promaia.cli.workspace_browser import launch_unified_browser
                    
                    # Show what we're browsing
                    if database_filter:
                        if len(database_filter) == 1:
                            print_text(f"🔍 Launching unified browser for database: {database_filter[0]}...", style="cyan")
                        else:
                            print_text(f"🔍 Launching unified browser for databases: {', '.join(database_filter)}...", style="cyan")
                    elif multiple_workspaces:
                        print_text(f"🔍 Launching unified browser for workspaces: {', '.join(multiple_workspaces)}...", style="cyan")
                    elif resolved_workspace:
                        print_text(f"🔍 Launching unified browser for workspace '{resolved_workspace}'...", style="cyan")
                    else:
                        print_text("🔍 Launching unified browser...", style="cyan")
                    
                    # Get current sources for pre-population (handle mixed commands properly)
                    current_sources = []
                    
                    # Get what we have in context_state
                    stored_browser_selections = context_state.get('browse_selections', [])
                    current_regular_sources = context_state.get('sources', [])
                    
                    # Debug: Show what was retrieved
                    # debug_print(f"RETRIEVED stored_browser_selections: {stored_browser_selections}")
                    # debug_print(f"RETRIEVED current_regular_sources: {current_regular_sources}")
                    
                    # Ensure stored_browser_selections is never None to avoid iteration errors
                    if stored_browser_selections is None:
                        stored_browser_selections = []
                    
                    # For workspace browse commands (like -b acme), we want to default to ALL workspace sources selected
                    # BUT only if there are no existing browser selections (for persistence)
                    # Check if this is a workspace browse command by looking at the original format
                    is_workspace_browse = False
                    if original_format and '-b ' in original_format and workspace and not database_filter:
                        is_workspace_browse = True
                    
                    if is_workspace_browse:
                        # Check if we have previous browser selections
                        has_previous_selections = bool(stored_browser_selections)
                        
                        # Debug: Show the decision logic
                        # debug_print(f"is_workspace_browse: {is_workspace_browse}, has_previous_selections: {has_previous_selections}")
                        
                        if has_previous_selections:
                            # Filter stored selections to only include sources from the current workspace
                            # This prevents sources from other workspaces/databases from persisting
                            for sel in stored_browser_selections:
                                # Extract the database name from the selection
                                if '#' in sel:
                                    # Discord channel: acme.tg#channel-name:7
                                    db_name = sel.split('#')[0]
                                else:
                                    # Regular database: acme.journal:7
                                    db_name = sel.split(':')[0]
                                
                                # Only include if it belongs to the current workspace
                                if db_name.startswith(workspace + '.'):
                                    current_sources.append(sel)
                            
                            # Include regular sources that aren't part of this workspace
                            if current_regular_sources:
                                for source in current_regular_sources:
                                    source_db = source.split(':')[0] if ':' in source else source
                                    # Check if this source is from a different workspace
                                    if '.' in source_db:
                                        source_workspace = source_db.split('.')[0]
                                        if source_workspace != workspace:
                                            current_sources.append(source)
                                    else:
                                        # Non-workspace source (like journal:30), always include
                                        current_sources.append(source)
                        else:
                            # First time browsing workspace - default to all workspace databases
                            from promaia.config.databases import get_database_manager
                            db_manager = get_database_manager()
                            workspace_databases = db_manager.get_workspace_databases(workspace)
                            
                            # Add all workspace databases with their default days
                            for db in workspace_databases:
                                if db.browser_include:  # Only include databases visible in browser
                                    if default_days:
                                        source_with_days = f"{db.get_qualified_name()}:{default_days}"
                                    else:
                                        source_with_days = f"{db.get_qualified_name()}:7"  # Default to 7 days
                                    current_sources.append(source_with_days)
                            
                            # Include regular sources that aren't part of this workspace
                            if current_regular_sources:
                                for source in current_regular_sources:
                                    source_db = source.split(':')[0] if ':' in source else source
                                    # Check if this source is from a different workspace
                                    if '.' in source_db:
                                        source_workspace = source_db.split('.')[0]
                                        if source_workspace != workspace:
                                            current_sources.append(source)
                                    else:
                                        # Non-workspace source (like journal:30), always include
                                        current_sources.append(source)
                    else:
                        # For specific database browse or mixed commands, use existing logic
                        # First, get any stored Discord channel selections
                        if stored_browser_selections:
                            # Filter stored selections to only include sources that match the current browse scope
                            for sel in stored_browser_selections:
                                should_include = False
                                
                                # Extract the database name from the selection
                                if '#' in sel:
                                    # Discord channel: acme.tg#channel-name:7
                                    db_name = sel.split('#')[0]
                                else:
                                    # Regular database: acme.journal:7
                                    db_name = sel.split(':')[0]
                                
                                # Check if this selection matches the current browse scope
                                if database_filter:
                                    # If we have a database filter, check if the selection matches any filter item
                                    for filter_item in database_filter:
                                        filter_base = filter_item.split(':')[0]
                                        if db_name == filter_base or db_name.startswith(filter_base + '.'):
                                            should_include = True
                                            break
                                elif workspace:
                                    # If browsing a workspace, check if the selection belongs to that workspace
                                    if db_name.startswith(workspace + '.'):
                                        should_include = True
                                elif multiple_workspaces:
                                    # If browsing multiple workspaces, check if selection belongs to any of them
                                    for ws in multiple_workspaces:
                                        if db_name.startswith(ws + '.'):
                                            should_include = True
                                            break
                                
                                if should_include:
                                    current_sources.append(sel)
                        
                        # For mixed commands, we also need to include regular database sources
                        # Get current regular sources (but exclude those that are handled by Discord)
                        if current_regular_sources:
                            # Extract database names from Discord selections to avoid duplicates
                            discord_db_names = set()
                            for discord_sel in stored_browser_selections:
                                if '#' in discord_sel:
                                    db_name = discord_sel.split('#')[0]
                                    discord_db_names.add(db_name)
                            
                            # Add regular sources that aren't Discord databases
                            for source in current_regular_sources:
                                source_db = source.split(':')[0] if ':' in source else source
                                if source_db not in discord_db_names:
                                    current_sources.append(source)
                    
                    # Launch unified browser
                    # Handle multiple workspaces case
                    if multiple_workspaces and not resolved_workspace:
                        browser_result = launch_unified_browser(
                            workspace=None,
                            default_days=default_days,
                            database_filter=multiple_workspaces,
                            current_sources=current_sources
                        )
                    else:
                        browser_result = launch_unified_browser(
                            workspace=resolved_workspace,
                            default_days=default_days,
                            database_filter=database_filter,
                            current_sources=current_sources
                        )

                    if browser_result.cancelled:
                        print_text("Browser cancelled. Keeping current context unchanged.", style="yellow")
                        return False

                    selected_sources = browser_result.sources

                    if not selected_sources:
                        print_text("No sources selected. Browser context cleared.", style="yellow")
                        context_state['sources'] = []
                        context_state['filters'] = []
                        context_state['browse_selections'] = []
                        save_browser_selection([])
                        return True

                    print_text(f"Selected {len(selected_sources)} sources from unified browser", style="green")

                    # Save the browser selection for persistence
                    save_browser_selection(selected_sources)

                    # Use shared scope resolution and result application
                    from promaia.chat.context_editor import resolve_browse_scope, apply_browser_result as _apply_br
                    browse_dbs = database_filter or (multiple_workspaces if multiple_workspaces else None)
                    scope = resolve_browse_scope(
                        browse_databases=browse_dbs,
                        workspace=resolved_workspace,
                        context_state=context_state,
                    )
                    _apply_br(selected_sources, context_state, scope.browse_scope_db_names)
                    
                    # Store ALL browser selections for future /e preservation (not just Discord)
                    # This includes both regular database selections and Discord channel selections
                    if selected_sources:
                        context_state['browse_selections'] = selected_sources.copy()
                    
                    # For browse commands, use the manually edited command; for others, use reconstructed
                    if '-b ' in user_input:
                        # This was manually edited with browse arguments - use the new command
                        context_state['original_query_format'] = f"maia chat {user_input}"
                    else:
                        # Reconstruct command from current state
                        cmd_parts = ["maia", "chat"]
                        for source in processed_sources:
                            cmd_parts.extend(["-s", source])
                        for filter_expr in processed_filters:
                            cmd_parts.extend(["-f", filter_expr])
                        if workspace:
                            cmd_parts.extend(["-ws", workspace])
                        context_state['original_query_format'] = " ".join(cmd_parts)
                    
                    # Update the query command display to reflect the new state
                    update_query_command()

                    # Only clear query content if it's NOT present in the new command
                    # This preserves VS/NL content when browse is ADDED (not replaced)
                    if context_state.get('sql_query_content') and not sql_query_parts:
                        debug_print("🧹 Clearing old sql_query_content (NL removed from command)")
                        context_state['sql_query_content'] = {}
                        context_state['cached_sql_query_prompt'] = ''
                    if context_state.get('vector_search_content') and not vector_search_parts:
                        debug_print("🧹 Clearing old vector_search_content (VS removed from command)")
                        context_state['vector_search_content'] = {}
                        context_state['cached_vector_search_prompt'] = ''
                        context_state['vector_search_per_query_cache'] = {}
                        context_state['vector_search_queries'] = []

                    # Reload context with the updated information
                    if reload_context():
                        print_text("Context updated successfully from unified browser!", style="green")
                        return True
                    else:
                        print_text("❌ Failed to reload context after browser selection", style="red")
                        return False
                            
                except Exception as e:
                    print_text(f"Error handling manual browse edit: {e}", style="bold red")
                    return False
        except Exception as e:
            print_text(f"Error handling manual browse edit: {e}", style="bold red")
            return False
    
    def handle_browse_in_edit_context():
        """Handle unified browse mode selection within edit context."""
        # Access module-level function (needed for nested function scope)
        nonlocal_process_browser_selections = process_browser_selections
        
        try:
            # Initialize variables
            database_filter = None
            default_days = None
            workspace = None
            multiple_workspaces = None

            # Always respect the stored selections as the primary source of truth for pre-population
            stored_browser_selections = context_state.get('browse_selections', [])

            # Parse the original command to determine the scope of the browser
            original_format = context_state.get('original_query_format', '')
            if '-b' in original_format:
                import shlex
                from promaia.config.workspaces import get_workspace_manager
                workspace_manager = get_workspace_manager()

                try:
                    # Try to parse with shlex, but handle quote errors gracefully
                    try:
                        parsed_args = shlex.split(original_format)
                    except ValueError as quote_error:
                        if "No closing quotation" in str(quote_error):
                            # Handle unclosed quotes by adding a closing quote
                            fixed_format = original_format + '"' if original_format.count('"') % 2 == 1 else original_format
                            parsed_args = shlex.split(fixed_format)
                        else:
                            raise quote_error
                    
                    browse_databases = []
                    i = 0
                    while i < len(parsed_args):
                        if parsed_args[i] == '-b':
                            i += 1
                            while i < len(parsed_args) and not parsed_args[i].startswith('-'):
                                browse_databases.append(parsed_args[i])
                                i += 1
                        else:
                            i += 1
                    
                    if browse_databases:
                        database_filter = browse_databases.copy()
                        
                        # Correctly identify workspaces from the full browse filter
                        workspace_names_found = []
                        for browse_spec in database_filter:
                            browse_name = browse_spec.split(':')[0]
                            if workspace_manager.validate_workspace(browse_name):
                                if browse_name not in workspace_names_found:
                                    workspace_names_found.append(browse_name)
                            elif '.' in browse_name:
                                potential_ws = browse_name.split('.')[0]
                                if workspace_manager.validate_workspace(potential_ws):
                                    if potential_ws not in workspace_names_found:
                                        workspace_names_found.append(potential_ws)
                        
                        if len(workspace_names_found) > 1:
                            workspace = None
                            multiple_workspaces = workspace_names_found
                        elif len(workspace_names_found) == 1:
                            workspace = workspace_names_found[0]

                        # Extract default_days without altering the main filter logic
                        for browse_spec in browse_databases:
                            if ':' in browse_spec:
                                try:
                                    days = int(browse_spec.rsplit(':', 1)[1])
                                    if default_days is None:
                                        default_days = days
                                except ValueError:
                                    continue
                except Exception as e:
                    print_text(f"Warning: Could not parse browse arguments from original format: {e}", style="yellow")

            # Fallback to context if not determined from original command
            if workspace is None and multiple_workspaces is None:
                workspace = context_state.get('resolved_workspace') or context_state.get('workspace')

            # If no workspace context, try to get the default
            if workspace is None and multiple_workspaces is None:
                from promaia.config.workspaces import get_workspace_manager
                workspace_manager = get_workspace_manager()
                workspace = workspace_manager.get_default_workspace()
            
            # Determine current sources for pre-populating the browser
            current_sources = []
            if stored_browser_selections:
                logger.debug(f"📋 Stored browser selections: {stored_browser_selections}")
                logger.debug(f"   Workspace: {workspace}, Database filter: {database_filter}, Multiple workspaces: {multiple_workspaces}")

                # SIMPLIFIED: Just pass all stored selections to the browser
                # The browser will only show databases that exist in the current workspace anyway
                # This avoids complex filtering bugs while still maintaining proper scoping
                filtered_selections = stored_browser_selections.copy()
                logger.debug(f"📋 Passing all {len(filtered_selections)} stored selections to browser (browser will filter to current scope)")

                # OLD COMPLEX FILTERING REMOVED - caused bugs where valid selections were filtered out
                # The browser is smart enough to only show databases that exist in the workspace
                # So we don't need to pre-filter here

                current_sources.extend(filtered_selections)
            else:
                # If no selections are stored, and it's a workspace browse, populate with all sources
                is_workspace_browse = bool(workspace and not database_filter)
                if is_workspace_browse:
                    from promaia.config.databases import get_database_manager
                    db_manager = get_database_manager()
                    workspace_databases = db_manager.get_workspace_databases(workspace)
                    for db in workspace_databases:
                        if db.browser_include:
                            days = default_days if default_days is not None else 7
                            current_sources.append(f"{db.get_qualified_name()}:{days}")

            # Display what we're browsing
            if database_filter:
                print_text(f"🔍 Launching unified browser for: {', '.join(database_filter)}...", style="cyan")
            elif multiple_workspaces:
                print_text(f"🔍 Launching unified browser for workspaces: {', '.join(multiple_workspaces)}...", style="cyan")
            elif workspace:
                print_text(f"🔍 Launching unified browser for workspace '{workspace}'...", style="cyan")
            else:
                print_text("Error: No workspace available for browse mode.", style="bold red")
                return False

            # Launch unified browser
            from promaia.cli.workspace_browser import launch_unified_browser

            # Handle multiple workspaces case
            if multiple_workspaces and not workspace:
                browser_result = launch_unified_browser(
                    workspace=None,
                    default_days=default_days,
                    database_filter=multiple_workspaces,
                    current_sources=current_sources,
                )
            else:
                browser_result = launch_unified_browser(
                    workspace=workspace,
                    default_days=default_days,
                    database_filter=database_filter,
                    current_sources=current_sources,
                )

            if browser_result.cancelled:
                print_text("Browser cancelled. Keeping current context unchanged.", style="yellow")
                return False

            selected_sources = browser_result.sources

            if not selected_sources:
                print_text("No sources selected. Browser context cleared.", style="yellow")
                context_state['sources'] = []
                context_state['filters'] = []
                context_state['browse_selections'] = []
                save_browser_selection([])
                return True

            print_text(f"Selected {len(selected_sources)} sources from unified browser", style="green")

            # Save the browser selection for persistence
            save_browser_selection(selected_sources)

            # Use shared scope resolution and result application
            from promaia.chat.context_editor import resolve_browse_scope, apply_browser_result
            browse_dbs = database_filter or (multiple_workspaces if multiple_workspaces else None)
            scope = resolve_browse_scope(
                browse_databases=browse_dbs,
                workspace=workspace,
                context_state=context_state,
            )
            apply_browser_result(selected_sources, context_state, scope.browse_scope_db_names)

            # Preserve original command format
            if not context_state.get('original_query_format'):
                cmd_parts = ["maia", "chat", "-b"]
                cmd_parts.extend(database_filter if database_filter else [workspace] if workspace else multiple_workspaces)
                context_state['original_query_format'] = " ".join(cmd_parts)
            update_query_command()
            
            # Reload context with updated info - preserve NL cache since only browse changed
            if reload_context(skip_nl_cache_messages=True):
                print_text("Context updated successfully from unified browser!", style="green")
                return True
            else:
                print_text("❌ Failed to reload context after browser selection", style="red")
                return False
                
        except Exception as e:
            print_text(f"Error in browse mode: {e}", style="bold red")
            debug_print(f"Browse error: {traceback.format_exc()}")
            return False

    def handle_recents_in_edit_context():
        """Handle recents selection within edit context using browser-based flow.

        Opens browser in recents mode, then drops to query input on confirm.
        """
        try:
            from promaia.cli.workspace_browser import launch_unified_browser
            from promaia.storage.recents import RecentsManager
            from promaia.chat.context_editor import resolve_browse_scope, apply_browser_result

            rm = RecentsManager()
            recents = rm.get_recents()

            if not recents:
                print_text("No recent queries found.", style="yellow")
                return False

            # Determine workspace
            workspace = context_state.get('resolved_workspace') or context_state.get('workspace')
            if not workspace:
                from promaia.config.workspaces import get_workspace_manager
                workspace = get_workspace_manager().get_default_workspace()

            if not workspace:
                print_text("No workspace available.", style="red")
                return False

            most_recent = recents[0]
            current_sources = most_recent.sources
            query_text = ""
            if most_recent.sql_query_prompt:
                query_text = f"-sql {most_recent.sql_query_prompt}"

            # Browser -> Query input loop
            while True:
                browser_result = launch_unified_browser(
                    workspace=workspace,
                    current_sources=current_sources,
                    recents_mode=True,
                )

                if browser_result.cancelled:
                    print_text("Recents cancelled.", style="yellow")
                    return False

                selected_sources = browser_result.sources
                if hasattr(browser_result, '_recents_query_preview') and browser_result._recents_query_preview:
                    query_text = browser_result._recents_query_preview

                if not selected_sources:
                    print_text("No sources selected.", style="yellow")
                    return False

                # Query input
                from prompt_toolkit import prompt as pt_prompt
                print_text(f"\nSources: {len(selected_sources)} selected", style="dim green")
                try:
                    edited_query = pt_prompt(
                        "Query: ",
                        default=query_text,
                        mouse_support=True,
                    ).strip()
                except (KeyboardInterrupt, EOFError):
                    # Esc from query input -> back to browser
                    current_sources = selected_sources
                    continue

                # Apply browser result to context
                scope = resolve_browse_scope(workspace=workspace, context_state=context_state)
                apply_browser_result(selected_sources, context_state, scope.browse_scope_db_names)
                save_browser_selection(selected_sources)

                # Process query if present
                if edited_query:
                    from promaia.chat.context_editor import parse_chat_args
                    parsed = parse_chat_args(edited_query)

                    if parsed.sql_queries:
                        combined_prompt = " ".join(parsed.sql_queries)
                        try:
                            from promaia.storage.unified_query import get_query_interface
                            query_interface = get_query_interface()
                            sql_content = query_interface.natural_language_query(combined_prompt, workspace, None)
                            if sql_content:
                                context_state['sql_query_content'] = sql_content
                                context_state['sql_query_prompt'] = combined_prompt
                                context_state['cached_sql_query_prompt'] = combined_prompt
                                context_state['cached_sql_query_content'] = sql_content
                        except Exception as e:
                            print_text(f"Error processing query: {e}", style="red")

                    if parsed.vector_searches:
                        try:
                            from promaia.nlq.nl_processor_wrapper import process_vector_search_to_content
                            combined_vs = {}
                            for vs_prompt in parsed.vector_searches:
                                vs_result = process_vector_search_to_content(vs_prompt, workspace=None, verbose=True)
                                if vs_result:
                                    for db_name, pages in vs_result.items():
                                        if db_name in combined_vs:
                                            combined_vs[db_name].extend(pages)
                                        else:
                                            combined_vs[db_name] = pages
                            if combined_vs:
                                context_state['vector_search_content'] = combined_vs
                        except Exception as e:
                            print_text(f"Error processing vector search: {e}", style="red")
                else:
                    # Clear NL state if no query
                    context_state['sql_query_content'] = None
                    context_state['sql_query_prompt'] = None

                # Reload
                if reload_context(skip_nl_cache_messages=True):
                    print_text("Context updated from recents.", style="green")
                    return True
                else:
                    print_text("Failed to reload context.", style="red")
                    return False

        except Exception as e:
            print_text(f"Error accessing recents: {e}", style="bold red")
            debug_print(f"Recents error: {e}")
            return False

    # Declare variables that will be used in the nested function
    initial_multi_source_data = {}
    total_pages_loaded = 0
    system_prompt = None

    # Initial context load
    reload_result = reload_context()

    if not reload_result:
        return

    # Save initial context log
    save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "session_init")

    # MCP Tool Execution Functions
    async def execute_mcp_tools_in_response(response_text: str) -> str:
        """Execute any MCP tools found in the AI response and return updated response."""
        mcp_executor = context_state.get('mcp_executor')

        if not mcp_executor:
            return response_text

        # Check if there are tool calls in the response
        if not mcp_executor.has_tool_calls(response_text):
            return response_text

        try:
            # Parse tool calls from the response
            tool_calls = mcp_executor.parse_tool_calls(response_text)

            if not tool_calls:
                return response_text

            print_text(f"🔧 Executing {len(tool_calls)} tool call(s)...", style="bold cyan")

            # Execute the tools
            results = await mcp_executor.execute_tool_calls(tool_calls)

            # Format the results
            results_text = mcp_executor.format_tool_results(results, show_raw=DEBUG_MODE)

            # Add results to the response
            updated_response = response_text + "\n" + results_text

            return updated_response

        except Exception as e:
            error_text = f"\n❌ Error executing MCP tools: {e}"
            return response_text + error_text

    # Query Tool Execution Functions
    async def request_query_permission(tool_name: str, parameters: Dict[str, Any], execution_result: Optional[Dict[str, Any]] = None) -> tuple:
        """Request user permission to load query results into context.

        Args:
            tool_name: Name of the query tool
            parameters: Query parameters
            execution_result: Query execution results (shown during approval)

        Returns:
            Tuple of (status, data) where:
            - status: 'approved', 'declined', 'skipped', or 'modified'
            - data: execution_result (approved), None (declined/skipped), or modified_parameters (modified)
        """
        nonlocal initial_multi_source_data, total_pages_loaded, system_prompt

        query_text = parameters.get('query', parameters.get('source', 'unknown'))
        reasoning = parameters.get('reasoning', '')

        print()
        print_text(f"🔍 Query Tool: {tool_name}", style="bold cyan")
        print_text(f"   Query: \"{query_text}\"", style="white")
        print()

        # Note: Reasoning and parameters are already shown before execution in query_tools.py
        # So we don't duplicate them here - just show execution results

        # Show generated SQL if available (for SQL queries)
        if execution_result and execution_result.get('generated_sql'):
            print_text(f"   📝 Generated SQL:", style="cyan")
            sql = execution_result['generated_sql']
            # Always show full SQL (no truncation)
            for line in sql.split('\n'):
                print_text(f"      {line}", style="dim")
            print()

        # Show execution results
        if execution_result:
            if execution_result.get('success'):
                total = execution_result.get('total_pages', 0)
                dbs = execution_result.get('databases', [])
                workspace = execution_result.get('workspace', 'unknown')

                print_text("   📊 Execution Results:", style="bold green")
                print_text(f"      Found {total} pages", style="white")
                if dbs:
                    # Only add workspace prefix if database name isn't already qualified
                    db_display = ', '.join([
                        db if '.' in db else f'{workspace}.{db}'
                        for db in dbs
                    ])
                    print_text(f"      Databases: {db_display}", style="dim")

                # Show sample results
                if total > 0 and total <= 10:
                    # For small result sets, show all titles
                    loaded_content = execution_result.get('loaded_content', {})
                    print_text("\n      Preview:", style="dim")
                    for db_name, pages in loaded_content.items():
                        for page in pages:
                            title = _preview_title(page)
                            print_text(f"         • {title}", style="dim")
                elif total > 10:
                    # For large result sets, show first 5
                    loaded_content = execution_result.get('loaded_content', {})
                    print_text("\n      Preview (first 5):", style="dim")
                    count = 0
                    for db_name, pages in loaded_content.items():
                        for page in pages:
                            if count >= 5:
                                break
                            title = _preview_title(page)
                            print_text(f"         • {title}", style="dim")
                            count += 1
                        if count >= 5:
                            break
                    print_text(f"         ... and {total - 5} more", style="dim")
                print()
            else:
                # Query failed
                error = execution_result.get('error', 'Unknown error')
                print_text("   ❌ Query Execution Failed:", style="bold red")
                print_text(f"      {error}", style="red")
                print()

        # Request approval
        from rich.console import Console
        console = Console()

        if execution_result and not execution_result.get('success'):
            # Query failed - only offer to decline or modify
            console.print("Query failed. \\[n]o / \\[m]odify: ", style="bold yellow", end="")
        else:
            console.print("Load into context? \\[Enter] / \\[m]odify / \\[n]o / \\[s]kip: ",
                         style="bold yellow", end="")

        # Get single keypress
        import sys
        import tty
        import termios

        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            response = sys.stdin.read(1).lower()
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        print(response if response not in ['\n', '\r'] else '')
        print()

        # Handle response
        if response in ['\n', '\r', 'y']:
            # Approve - return the execution result
            return ('approved', execution_result if execution_result else parameters)

        elif response == 's':
            # Skip this query, continue to next
            print_text("⏭️  Skipped", style="yellow")
            return ('skipped', None)

        elif response == 'm':
            # Modify query
            print_text("Modify query:", style="bold cyan")
            try:
                new_query = input("New query (or Enter to cancel): ").strip()
                if new_query and new_query != query_text:
                    parameters['query'] = new_query
                    print_text("✏️  Query modified", style="green")
                    return ('modified', parameters)
                else:
                    print_text("No changes, skipping this query", style="dim")
                    return ('skipped', None)
            except (KeyboardInterrupt, EOFError):
                print()
                return ('skipped', None)

        else:  # 'n' or any other key
            print_text("🚫 Declined", style="red")
            return ('declined', None)

    async def execute_query_tools_in_response(response_text: str) -> tuple:
        """Execute any query tools found in the AI response.

        Returns:
            Tuple of (updated_response_text, needs_context_reload)
        """
        nonlocal initial_multi_source_data, total_pages_loaded, system_prompt

        from promaia.chat.query_tools import QueryToolExecutor

        # Create query tool executor
        query_executor = QueryToolExecutor(context_state)

        # Check if there are query tool calls in the response
        if not query_executor.has_query_tool_calls(response_text):
            return response_text, False

        try:
            # Parse query tool calls from the response
            tool_calls = query_executor.parse_query_tool_calls(response_text)

            if not tool_calls:
                return response_text, False

            print_text(f"🔎 Found {len(tool_calls)} query tool call(s)", style="bold cyan")

            # Execute the query tools with permission requests
            results = await query_executor.execute_query_tool_calls(tool_calls, request_query_permission)

            # Check if any queries succeeded AND loaded pages
            needs_reload = False
            pages_added = 0
            for result in results:
                if result.get('success'):
                    # Merge loaded content into initial_multi_source_data
                    loaded_content = result.get('loaded_content', {})
                    for db_name, pages in loaded_content.items():
                        if db_name not in initial_multi_source_data:
                            initial_multi_source_data[db_name] = []

                        # Deduplicate by page_id
                        existing_ids = {p.get('id') for p in initial_multi_source_data[db_name]
                                       if isinstance(p, dict) and 'id' in p}

                        for page in pages:
                            if not isinstance(page, dict) or 'id' not in page or page['id'] not in existing_ids:
                                initial_multi_source_data[db_name].append(page)
                                existing_ids.add(page.get('id'))
                                pages_added += 1

            # Only reload if we actually added pages
            if pages_added > 0:
                needs_reload = True

            # Track loaded sources in context_state
            for result in results:
                if result.get('success'):
                    loaded_content = result.get('loaded_content', {})
                    source_spec = result.get('source', '')
                    if source_spec and loaded_content:
                        current_srcs = context_state.get('sources') or []
                        for db_key in loaded_content.keys():
                            # Resolve to the same qualified name the browser uses
                            from promaia.config.databases import get_database_config
                            try:
                                # db_key is like "workspace.stories" — extract parts
                                parts = db_key.split('.', 1)
                                ws_part = parts[0] if len(parts) > 1 else ''
                                name_part = parts[1] if len(parts) > 1 else parts[0]
                                db_cfg = get_database_config(name_part, ws_part)
                                qualified = db_cfg.get_qualified_name() if db_cfg else db_key
                            except Exception:
                                qualified = db_key
                            days_part = source_spec.split(':')[1] if ':' in source_spec else ''
                            new_spec = f"{qualified}:{days_part}" if days_part else qualified
                            existing_bases = {s.split(':')[0] for s in current_srcs}
                            if qualified not in existing_bases:
                                current_srcs.append(new_spec)
                            else:
                                current_srcs = [new_spec if s.split(':')[0] == qualified else s for s in current_srcs]
                        context_state['sources'] = current_srcs

            # Format the results
            results_text = query_executor.format_query_results(results)

            # Remove the tool call tags from the response to prevent re-detection
            import re
            cleaned_response = re.sub(r'<tool_call>.*?</tool_call>', '', response_text, flags=re.DOTALL)

            # If cleaned response is empty (AI response was ONLY tool calls), add a prompt for context
            if not cleaned_response.strip():
                cleaned_response = "[Context loaded. Analyzing data to answer your question...]\n\n"

            # Add results to the cleaned response
            updated_response = cleaned_response + "\n" + results_text

            return updated_response, needs_reload

        except Exception as e:
            logger.error(f"Error executing query tools: {e}")
            error_text = f"\n❌ Error executing query tools: {e}"
            return response_text + error_text, False

    async def execute_all_tools_with_iteration(response_text: str, regenerate_callback=None, max_iterations: int = 8) -> str:
        """Execute MCP tools and query tools, with iterative query loop support.

        Args:
            response_text: The AI's response text
            regenerate_callback: Optional function to regenerate AI response after context changes
            max_iterations: Maximum number of query iterations allowed

        Returns:
            Updated response text with tool results
        """
        nonlocal initial_multi_source_data, total_pages_loaded, system_prompt

        # First, execute MCP tools
        response_text = await execute_mcp_tools_in_response(response_text)

        # Then, execute query tools with iteration support
        iteration_count = 0
        needs_more_queries = True

        while needs_more_queries and iteration_count < max_iterations:
            # Execute query tools
            response_text, needs_reload = await execute_query_tools_in_response(response_text)

            if needs_reload:
                iteration_count += 1
                context_state['query_iteration_count'] = iteration_count

                # Reload context with new data
                total_pages_loaded = sum(len(pages) for pages in initial_multi_source_data.values() if pages is not None)
                context_state['initial_multi_source_data'] = initial_multi_source_data
                context_state['total_pages_loaded'] = total_pages_loaded

                # Apply mute handling - use empty context if muted
                context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                # Regenerate system prompt
                mcp_tools_info = context_state.get('mcp_tools_info')
                system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                context_state['system_prompt'] = system_prompt

                # Display updated context in welcome message format
                print()
                print_text(f"📊 Context Updated (Iteration {iteration_count}):", style="bold green")
                print_text(f"Pages loaded: {total_pages_loaded}", style="white")

                source_breakdown = generate_source_breakdown(initial_multi_source_data)
                if source_breakdown:
                    for source_name, page_count in source_breakdown.items():
                        print_text(f"{source_name}: {page_count}", style="white")

                print_text(f"Model: {get_current_model_name()}", style="white")
                print()

                # Regenerate AI response with updated context if callback provided
                if regenerate_callback and iteration_count < max_iterations:
                    print_text("🔄 Generating AI response with updated context...", style="bold cyan")
                    print()

                    # For regeneration, disable query tools so AI uses loaded data instead of querying again
                    system_prompt_for_regen = build_system_prompt_with_mode(
                        initial_multi_source_data,
                        mcp_tools_info,
                        mode_system_prompt,
                        mode,
                        include_query_tools=False,  # Disable query tools for answer generation
                        workspace=context_state.get('workspace')
                    )

                    # Call the regenerate callback to get new AI response
                    new_response = regenerate_callback(system_prompt_for_regen)

                    if new_response:
                        response_text = new_response
                        # Continue loop to check if new response has more queries
                        continue
                    else:
                        # Regeneration failed, stop iteration
                        logger.error("⚠️ Regenerate callback returned None - stopping iteration")
                        print_text("⚠️ Failed to generate response with loaded context. The AI may have encountered an error.", style="bold yellow")
                        needs_more_queries = False
                elif iteration_count >= max_iterations:
                    print_text(f"⚠️  Max query iterations ({max_iterations}) reached", style="bold yellow")
                    needs_more_queries = False
            else:
                # No more queries needed
                needs_more_queries = False

        # Reset iteration count for next user message
        context_state['query_iteration_count'] = 0

        return response_text

    # Display Welcome Message
    print()

    # Use mode-specific welcome if available, otherwise use generic
    welcome_displayed = False
    if mode:
        mode_welcome = mode.get_welcome_message(context_state)
        if mode_welcome:
            print(mode_welcome)
            welcome_displayed = True

    # Only show generic welcome if mode didn't provide one
    if not welcome_displayed:
        print_welcome_message(query_command=query_command, total_pages=total_pages_loaded, model_name=get_current_model_name(), source_breakdown=generate_source_breakdown(initial_multi_source_data))

    # Handle Non-interactive Mode
    if non_interactive:
        return

    # Start Interactive Chat Loop
    messages = initial_messages.copy() if initial_messages else []

    # Temperature setting for creativity control (0.0 = focused, 2.0 = very creative)
    current_temperature = 0.7  # Default temperature

    # Process initial messages for artifacts (e.g., draft mode with existing draft)
    if initial_messages and context_state.get('artifact_manager'):
        artifact_manager = context_state['artifact_manager']
        logger.info(f"🔍 Checking {len(initial_messages)} initial messages for artifacts to reconstruct")
        
        for i, msg in enumerate(initial_messages):
            if msg['role'] == 'assistant':
                logger.debug(f"  Message {i}: assistant message, checking for <artifact> tags")
                if has_artifact_tags(msg['content']):
                    logger.info(f"  ✅ Found artifact in message {i}, reconstructing...")
                    # Extract and create artifact
                    artifact_content, commentary = artifact_manager.extract_artifact_content(msg['content'])
                    artifact_id = artifact_manager.create_artifact(artifact_content)
                    logger.info(f"📦 Created artifact #{artifact_id} from initial message {i}")
                else:
                    logger.debug(f"  ⚠️  No <artifact> tags in message {i}")
    
    # Display previous conversation
    # Skip generic headers if mode is active (mode will display its own)
    if initial_messages and not mode:
        print_text("--- Previous Conversation ---", style="bold yellow")
        for msg in initial_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            if role == 'user':
                print_text(f"You: {content}", style="bold cyan")
            elif role == 'assistant':
                print_markdown(f"**Maia:** {content}")
        print_text("--- Continuing Conversation ---", style="bold yellow")
        print()
    elif initial_messages and mode and context_state.get('artifact_manager'):
        # Check if there are any assistant messages before looking for artifacts
        has_assistant_messages = any(msg.get('role') == 'assistant' for msg in initial_messages)

        if has_assistant_messages:
            # For mode with artifacts, show artifact history instead of raw messages
            artifact_manager = context_state['artifact_manager']
            if artifact_manager.artifacts:
                logger.info(f"📝 Displaying {len(artifact_manager.artifacts)} reconstructed artifacts")
                print_text("--- Previous Draft(s) ---", style="bold yellow")
                for artifact_id in sorted(artifact_manager.artifacts.keys()):
                    print(artifact_manager.render_artifact(artifact_id))
                print()
            else:
                logger.warning(f"⚠️  No artifacts to display despite {len(initial_messages)} assistant messages")

    # Auto-respond to initial user message if requested
    if auto_respond_to_initial and messages and messages[-1].get('role') == 'user':
        logger.info("🤖 Auto-responding to initial user message")

        # The user message is already in messages list, now call the AI
        try:
            # Check for images in current message
            current_message_images = []
            if messages and "images" in messages[-1]:
                current_message_images = messages[-1].get("images", [])

            # Add email sending instructions to system prompt if enabled
            current_system_prompt = system_prompt
            if context_state.get('enable_email_send', False):
                email_instructions = """

## EMAIL COMPOSITION MODE

You are in email composition mode. Gmail threads are loaded in your context - search them naturally to find the right thread and recipient.

1. **Detect Intent**: User wants to send an email (phrases like "send this to", "email this", etc.)

2. **Find the Right Thread**: Search your loaded Gmail context for matching threads
   - Use keywords from the user's message (person names, subject matter, dates, etc.)
   - When you find a matching thread, extract: recipient email, subject line, thread_id, message_id
   - Use the EXACT subject line from the thread (don't add "RE:" or modify it)

3. **Compose Email as Artifact**: Create an artifact with this format:

   ```
   <artifact>
   Subject: [exact subject from Gmail thread]
   To: [recipient@example.com]

   [Email body]

   ---
   📎 Attachments:
   - /exact/path/file1.pdf
   - /exact/path/file2.png

   Thread: [thread_id from Gmail if replying]
   Message-ID: [message_id from Gmail if replying]
   </artifact>
   ```

   - List attachments on separate lines with "- " prefix
   - Include Thread and Message-ID if replying to an existing thread
   - Omit Thread and Message-ID if sending a new email

4. **Example**:
   User: "send this doc to bob about UK import"
   → Search Gmail for threads with "bob", "UK", "import"
   → Find matching thread, extract subject/thread_id/message_id
   → Create artifact with exact subject and IDs from Gmail
   → User types /send when ready

5. **Important**:
   - Search Gmail context first to find the right thread
   - Use EXACT subject line from Gmail (no "RE:" prefix)
   - Include thread_id and message_id from Gmail if replying
   - Use exact file paths from user's message for attachments
   - User will type `/send` when ready (you don't send it)
   - User can refine the email through conversation before sending

The user will type `/send` to trigger the actual sending process.
"""
                current_system_prompt = system_prompt + email_instructions

            # Call the appropriate API
            response_content = None
            if current_api == "anthropic" and anthropic_client:
                # Agentic mode: use autonomous multi-tool loop
                if context_state.get('agentic_mode'):
                    try:
                        import asyncio
                        from promaia.chat.agentic_adapter import run_agentic_turn
                        debug_print("Entering agentic loop...")

                        clean_messages = []
                        for msg in messages:
                            cm = {"role": msg["role"], "content": msg["content"]}
                            if msg.get("images"):
                                cm["images"] = msg["images"]
                            clean_messages.append(cm)

                        result = asyncio.run(run_agentic_turn(
                            system_prompt=current_system_prompt,
                            messages=clean_messages,
                            workspace=context_state.get('workspace') or context_state.get('resolved_workspace') or "",
                            mcp_tools=context_state.get('agentic_tools', []),
                            databases=context_state.get('agentic_databases', []),
                            print_text_fn=print_text,
                        ))

                        response_text = result.response_text
                        total_tokens = result.input_tokens + result.output_tokens

                        from promaia.utils.ai import calculate_ai_cost
                        cost_data = calculate_ai_cost(result.input_tokens, result.output_tokens, "claude-sonnet-4")

                        tools_used = list({tc.get("name", "?") for tc in result.tool_calls_made})
                        model_info = f"Claude Sonnet (agentic: {result.iterations_used} iters"
                        if tools_used:
                            model_info += f", tools: {', '.join(tools_used)}"
                        model_info += ")"

                        response_content = {
                            'text': response_text,
                            'tokens': {
                                'prompt_tokens': result.input_tokens,
                                'response_tokens': result.output_tokens,
                                'total_tokens': total_tokens,
                                'cost': cost_data["total_cost"],
                                'model': model_info,
                            }
                        }
                        # Track sources loaded by agentic query_source calls
                        _track_agentic_query_sources(result.tool_calls_made, context_state)

                    except Exception as e:
                        logger.error(f"Agentic turn failed: {e}", exc_info=True)
                        print_text(f"\n⚠️  Agentic mode error ({type(e).__name__}): {e}", style="bold red")
                        print_text("   Falling back to standard call\n", style="dim yellow")

                # Standard Anthropic call (or fallback from agentic failure)
                if response_content is None:
                    # Proactive context trimming
                    from promaia.agents.context_trimmer import trim_context_to_fit_sync
                    current_system_prompt, messages = trim_context_to_fit_sync(
                        current_system_prompt, messages
                    )

                    if current_message_images:
                        formatted_messages = _format_anthropic_with_images(messages, current_message_images)
                        response = call_anthropic(anthropic_client, current_system_prompt, formatted_messages, temperature=current_temperature)
                    else:
                        clean_messages = []
                        for msg in messages:
                            clean_msg = {"role": msg["role"], "content": msg["content"]}
                            clean_messages.append(clean_msg)
                        response = call_anthropic(anthropic_client, current_system_prompt, clean_messages, temperature=current_temperature)

                    if response and response.content:
                        response_text = response.content[0].text

                        # Execute MCP and query tools if present
                        import asyncio
                        response_text = asyncio.run(execute_all_tools_with_iteration(response_text))

                        # Extract token usage
                        if hasattr(response, 'usage'):
                            input_tokens = response.usage.input_tokens
                            output_tokens = response.usage.output_tokens
                            total_tokens = input_tokens + output_tokens

                            from promaia.utils.ai import calculate_ai_cost
                            cost_data = calculate_ai_cost(input_tokens, output_tokens, "claude-sonnet-4")
                            total_cost = cost_data["total_cost"]

                            response_content = {
                                'text': response_text,
                                'tokens': {
                                    'prompt_tokens': input_tokens,
                                    'response_tokens': output_tokens,
                                    'total_tokens': total_tokens,
                                    'cost': total_cost,
                                    'model': 'Claude 3 Sonnet'
                                }
                            }
                        else:
                            response_content = {
                                'text': response_text,
                                'tokens': None
                            }

            elif current_api == "openai" and openai_client:
                if current_message_images:
                    formatted_messages = _format_openai_with_images(current_system_prompt, messages, current_message_images)
                else:
                    formatted_messages = [{"role": "system", "content": current_system_prompt}] + messages

                # Use selected model ID if available
                selected_model = os.getenv("SELECTED_MODEL_ID")
                openai_model = selected_model if (selected_model and "gpt" in selected_model.lower()) else "gpt-4o"

                response = openai_client.chat.completions.create(
                    model=openai_model,
                    messages=formatted_messages,
                    max_tokens=4096,
                    temperature=current_temperature
                )
                if response.choices:
                    response_text = response.choices[0].message.content

                    # Execute MCP and query tools if present
                    import asyncio
                    response_text = asyncio.run(execute_all_tools_with_iteration(response_text))

                    # Extract token usage
                    if hasattr(response, 'usage') and response.usage:
                        prompt_tokens = response.usage.prompt_tokens
                        completion_tokens = response.usage.completion_tokens
                        total_tokens = response.usage.total_tokens

                        from promaia.utils.ai import calculate_ai_cost
                        cost_data = calculate_ai_cost(prompt_tokens, completion_tokens, "gpt-4o")
                        total_cost = cost_data["total_cost"]

                        response_content = {
                            'text': response_text,
                            'tokens': {
                                'prompt_tokens': prompt_tokens,
                                'response_tokens': completion_tokens,
                                'total_tokens': total_tokens,
                                'cost': total_cost,
                                'model': 'GPT-4'
                            }
                        }
                    else:
                        response_content = {
                            'text': response_text,
                            'tokens': None
                        }

            elif current_api == "gemini" and gemini_client:
                response_text_with_tools = None
                try:
                    if current_message_images:
                        current_gemini_model, gemini_messages = _format_gemini_with_images(current_system_prompt, messages, current_message_images)
                        response = current_gemini_model.generate_content(
                            contents=gemini_messages,
                            generation_config={
                                "temperature": current_temperature,
                            }
                        )
                    else:
                        # Format message for Gemini
                        formatted_prompt = f"System: {current_system_prompt}\n\nConversation:\n"
                        for msg in messages:
                            formatted_prompt += f"{msg['role'].title()}: {msg['content']}\n"
                        response = gemini_client.generate_content(formatted_prompt)

                    if response.text:
                        response_text = response.text

                        # Execute MCP and query tools if present
                        import asyncio
                        response_text_with_tools = asyncio.run(execute_all_tools_with_iteration(response_text))
                    else:
                        response_text_with_tools = f"I encountered an error: No response text generated. Please try again."
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Error calling Gemini API during auto-respond: {error_msg}")
                    response_text_with_tools = f"I encountered an error with Gemini: {error_msg}. Please try again."

                if response_text_with_tools:
                    # Extract token usage for Gemini
                    if 'response' in locals() and hasattr(response, 'usage_metadata') and response.usage_metadata:
                        prompt_tokens = response.usage_metadata.prompt_token_count
                        completion_tokens = response.usage_metadata.candidates_token_count
                        total_tokens = response.usage_metadata.total_token_count

                        from promaia.utils.ai import calculate_ai_cost
                        gemini_model = "gemini-2.5-pro-short" if total_tokens <= 128000 else "gemini-2.5-pro-long"
                        cost_data = calculate_ai_cost(prompt_tokens, completion_tokens, gemini_model)
                        total_cost = cost_data["total_cost"]

                        response_content = {
                            'text': response_text_with_tools,
                            'tokens': {
                                'prompt_tokens': prompt_tokens,
                                'response_tokens': completion_tokens,
                                'total_tokens': total_tokens,
                                'cost': total_cost,
                                'model': 'Gemini 2.5 Pro'
                            }
                        }
                    else:
                        response_content = {
                            'text': response_text_with_tools,
                            'tokens': None
                        }

            elif current_api == "llama":
                # Ensure llama client is initialized
                if not llama_client:
                    from promaia.utils.config import load_environment
                    load_environment()
                    initialize_llama_client()

                if llama_client:
                    # Define regenerate callback for query tool iterations
                    def regenerate_llama_response_auto(updated_system_prompt):
                        """Regenerate Llama response with updated context (auto-respond mode)."""
                        try:
                            if not messages:
                                logger.warning("messages is None or empty, cannot regenerate")
                                return None

                            if current_message_images:
                                formatted_messages = _format_llama_with_images(updated_system_prompt, messages, current_message_images)
                            else:
                                formatted_messages = [{"role": "system", "content": updated_system_prompt}] + messages

                            model_name = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")
                            regen_response = llama_client.chat.completions.create(
                                model=model_name,
                                messages=formatted_messages,
                                max_tokens=4096,
                                temperature=current_temperature
                            )

                            if regen_response.choices:
                                return regen_response.choices[0].message.content
                            return None
                        except Exception as e:
                            logger.error(f"Error regenerating Llama response: {e}")
                            return None

                    if current_message_images:
                        formatted_messages = _format_llama_with_images(current_system_prompt, messages, current_message_images)
                    else:
                        formatted_messages = [{"role": "system", "content": current_system_prompt}] + messages

                    model_name = os.getenv("LLAMA_DEFAULT_MODEL", "llama3:latest")

                    try:
                        response = llama_client.chat.completions.create(
                            model=model_name,
                            messages=formatted_messages,
                            max_tokens=4096,
                            temperature=current_temperature
                        )
                        if response.choices:
                            response_text = response.choices[0].message.content

                            # Execute MCP and query tools if present
                            import asyncio
                            response_text = asyncio.run(execute_all_tools_with_iteration(response_text, regenerate_callback=regenerate_llama_response_auto))

                            response_content = {
                                'text': response_text,
                                'tokens': None  # Llama doesn't provide token usage in same format
                            }
                    except Exception as e:
                        logger.error(f"Error calling Llama during auto-respond: {e}")
                        response_content = {
                            'text': f"I encountered an error with Llama: {str(e)}. Please try again.",
                            'tokens': None
                        }

            # Handle API response
            if response_content:
                # Lazy-initialize artifact manager if needed
                if context_state['artifact_manager'] is None:
                    from promaia.chat.artifacts import ArtifactManager
                    context_state['artifact_manager'] = ArtifactManager()

                artifact_manager = context_state['artifact_manager']

                if isinstance(response_content, dict):
                    response_text = response_content['text']
                    token_data = response_content.get('tokens')

                    timestamp = get_local_timestamp()
                    metadata_parts = [f"{timestamp} Maia"]
                    if token_data:
                        metadata_parts.append(f"{token_data['prompt_tokens']:,}, {token_data['response_tokens']:,}, {token_data['total_tokens']:,}")
                        metadata_parts.append(f"${token_data['cost']:.6f}")

                    # Print metadata
                    print()
                    if metadata_parts:
                        print_text(metadata_parts[0])
                    for part in metadata_parts[1:]:
                        print_text(part, style="dim")
                    print()

                    # Check if response should be an artifact
                    last_user_message = messages[-1]['content'] if messages else ""
                    force_artifacts = mode and mode.should_force_artifacts()

                    # Check if this is an artifact
                    # For auto-response, ONLY respect AI's <artifact> tags (no keyword matching)
                    # The AI's system prompt (maia_mail_prompt.md) already tells it when to use artifacts
                    is_artifact = False
                    if force_artifacts:
                        # Mode forces all responses to be artifacts
                        artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                        artifact_id = artifact_manager.create_artifact(artifact_content)
                        is_artifact = True

                        # Display commentary if present
                        if commentary:
                            print_markdown(commentary)
                            print()

                        # Display artifact
                        print(artifact_manager.render_artifact(artifact_id))
                    elif has_artifact_tags(response_text):
                        # AI explicitly used artifact tags - respect that decision
                        artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                        artifact_id = artifact_manager.create_artifact(artifact_content)
                        is_artifact = True

                        # Sync artifact metadata with draft DB if in draft mode
                        if draft_id and mode:
                            from promaia.chat.modes import DraftMode
                            from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                            if isinstance(mode, DraftMode):
                                _, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_id)
                                if metadata:
                                    update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                    # Reload draft_data to reflect updates
                                    mode.draft_data = mode.draft_manager.get_draft(draft_id)

                        # Display commentary if present
                        if commentary:
                            print_markdown(commentary)
                            print()

                        # Display artifact
                        print(artifact_manager.render_artifact(artifact_id))
                    else:
                        # No artifact - display as normal markdown
                        print_markdown(response_text)

                    # Save message with artifact tags if it was an artifact
                    if is_artifact and not has_artifact_tags(response_text):
                        artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                        saved_content = f"<artifact>{artifact_content}</artifact>"
                        if commentary:
                            saved_content = f"{commentary}\n\n{saved_content}"
                        messages.append({"role": "assistant", "content": saved_content})
                    else:
                        messages.append({"role": "assistant", "content": response_text})

                    # Auto-save messages in draft mode
                    if draft_id and mode:
                        try:
                            from promaia.mail.draft_manager import DraftManager
                            draft_manager = DraftManager()
                            draft_manager.save_chat_messages(draft_id, messages)
                            logger.info(f"💾 Auto-saved {len(messages)} messages after auto-response for draft {draft_id}")
                        except Exception as e:
                            logger.error(f"Failed to auto-save messages after auto-response: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error in auto-response: {e}", exc_info=True)
            print_text(f"❌ Error generating response: {e}", style="red")

    while True:
        try:
            global pending_input_text
            # Check if there's pending input text from Ctrl+O (edit mode)
            if pending_input_text:
                user_input = session.prompt("You: ", style=style, default=pending_input_text)
                pending_input_text = None  # Clear after using
            else:
                user_input = session.prompt("You: ", style=style)

            # Check for URLs or search keywords and suggest enabling web tools
            check_and_suggest_web_tools(user_input, context_state, style)

            # /clear - Clear all context
            if user_input.strip().lower() in ['/clear', '/c']:
                print_text("🗑️  Clearing all context...", style="yellow")
                initial_multi_source_data = {}
                combined_multi_source_data = {}
                context_state['sources'] = []
                context_state['browse_selections'] = []
                total_pages_loaded = 0
                print_text("✅ Context cleared. You're now in a blank slate.", style="green")
                continue

            # /mute - Mute context (temporarily hide but keep loaded)
            if user_input.strip().lower() == '/mute':
                if context_state.get('context_muted'):
                    print_text("ℹ️  Context is already muted", style="yellow")
                else:
                    context_state['context_muted'] = True
                    context_state['muted_sources'] = context_state.get('sources', []).copy()
                    context_state['muted_data'] = combined_multi_source_data.copy()
                    print_text("🔇 Context muted (hidden from AI, but preserved)", style="yellow")
                    print_text(f"   Muted {len(context_state['muted_sources'])} sources with {total_pages_loaded} pages", style="dim")
                continue

            # /unmute - Restore muted context
            if user_input.strip().lower() == '/unmute':
                if not context_state.get('context_muted'):
                    print_text("ℹ️  Context is not muted", style="yellow")
                else:
                    context_state['context_muted'] = False
                    context_state['sources'] = context_state.get('muted_sources', [])
                    combined_multi_source_data = context_state.get('muted_data', {})
                    total_pages_loaded = sum(len(pages) for pages in combined_multi_source_data.values() if pages is not None)
                    print_text("🔊 Context unmuted (restored)", style="green")
                    print_text(f"   Restored {len(context_state['sources'])} sources with {total_pages_loaded} pages", style="dim")
                continue

            if user_input.strip().lower() in ['/quit', '/exit', '/q']:
                # Save chat messages if in draft mode
                if draft_id and mode:
                    try:
                        from promaia.mail.draft_manager import DraftManager
                        draft_manager = DraftManager()
                        draft_manager.save_chat_messages(draft_id, messages)
                        logger.info(f"💾 Saved {len(messages)} chat messages for draft {draft_id}")
                    except Exception as e:
                        logger.error(f"Failed to save chat messages: {e}")
                # Auto-save conversation to history (if not in draft mode and has messages)
                elif messages and len([m for m in messages if m.get('role') != 'system']) > 0:
                    try:
                        from promaia.storage.chat_history import ChatHistoryManager
                        history_manager = ChatHistoryManager()
                        
                        # Prepare context for saving (same as /save command)
                        thread_context = {
                            'sources': context_state.get('sources'),
                            'filters': context_state.get('filters'),
                            'workspace': context_state.get('workspace'),
                            'resolved_workspace': context_state.get('resolved_workspace'),
                            'query_command': context_state.get('query_command'),
                            'sql_query_prompt': context_state.get('sql_query_prompt'),
                            'sql_query_content': context_state.get('sql_query_content'),
                            'original_query_format': context_state.get('original_query_format'),
                            'browse_selections': context_state.get('browse_selections'),
                            'vector_search_queries': context_state.get('vector_search_queries'),
                            'is_vector_search': context_state.get('is_vector_search')
                        }
                        
                        # Check if updating existing thread or creating new
                        current_thread_id = context_state.get('current_thread_id')
                        if current_thread_id:
                            history_manager.update_thread(
                                thread_id=current_thread_id,
                                messages=messages,
                                context=thread_context
                            )
                        else:
                            thread_id = history_manager.save_thread(
                                messages=messages,
                                context=thread_context
                            )
                            context_state['current_thread_id'] = thread_id
                        
                        print_text("💾 Conversation auto-saved to history", style="dim green")
                    except Exception as e:
                        debug_print(f"Auto-save error (non-fatal): {e}")

                print_text("Goodbye!", style="bold cyan")
                break

            # Handle mode-specific commands (e.g., /send, /archive for draft mode)
            if mode:
                mode_commands = mode.get_additional_commands()
                user_command = user_input.strip().lower()
                user_command_base = user_command.split()[0] if user_command else ''

                # Check if it's a mode command
                command_handled = False
                for cmd_name, cmd_handler in mode_commands.items():
                    # Match exact command or command with arguments (e.g., /send matches /send 2)
                    # Also support /a as shorthand for /archive
                    if user_command_base == cmd_name or user_command == cmd_name or (cmd_name == '/archive' and user_command_base == '/a'):
                        command_handled = True
                        try:
                            import asyncio
                            # Mode commands are async and take (artifact_manager, messages, context_state)
                            should_exit = asyncio.run(cmd_handler(
                                context_state.get('artifact_manager'),
                                messages,
                                context_state
                            ))

                            if should_exit:
                                # Save messages before exiting
                                if draft_id:
                                    try:
                                        from promaia.mail.draft_manager import DraftManager
                                        draft_manager = DraftManager()
                                        draft_manager.save_chat_messages(draft_id, messages)
                                    except Exception as e:
                                        logger.error(f"Failed to save chat messages: {e}")
                                return  # Exit chat

                            break  # Command handled, break from for loop
                        except Exception as e:
                            logger.error(f"Error executing mode command {cmd_name}: {e}")
                            print_text(f"❌ Error: {e}\n", style="red")
                            break

                if command_handled:
                    continue  # Skip to next user input

            if user_input.strip().lower() == '/debug':
                DEBUG_MODE = not DEBUG_MODE
                status = "enabled" if DEBUG_MODE else "disabled"
                print_text(f"Debug mode {status}.", style="bold yellow")
                continue
            elif user_input.strip().lower() == '/push':
                try:
                    import asyncio
                    result = asyncio.run(push_chat_to_notion(messages))
                    print_text(result, style="bold green")
                except Exception as e:
                    print_text(f"Error pushing to Notion: {e}", style="bold red")
                continue
            elif user_input.strip().lower() == '/s':
                # Sync current context databases
                try:
                    import asyncio
                    asyncio.run(sync_current_context_databases())
                    print_text("Context databases synced successfully. Reloading context...", style="bold green")
                    if reload_context():
                        print()
                        # Show the same detailed breakdown as when starting a new chat
                        print_welcome_message(
                            query_command=context_state['query_command'],
                            total_pages=total_pages_loaded,
                            model_name=get_current_model_name(),
                            source_breakdown=generate_source_breakdown(initial_multi_source_data)
                        )
                        # Save context log for sync-triggered update
                        save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "context_sync")
                    else:
                        print_text("Failed to reload context after sync.", style="bold red")
                except Exception as e:
                    print_text(f"Error syncing context databases: {e}", style="bold red")
                    debug_print(f"Sync error details: {e}")
                continue

            elif user_input.strip() == '/sync-inline':
                # Handle inline sync triggered by Ctrl+L
                try:
                    from prompt_toolkit import prompt
                    from prompt_toolkit.key_binding import KeyBindings as SyncKeyBindings

                    # Create bindings for sync prompt
                    sync_bindings = SyncKeyBindings()
                    sync_cancelled = {'value': False}

                    @sync_bindings.add('c-c')
                    def handle_cancel(event):
                        sync_cancelled['value'] = True
                        event.app.exit(result='')

                    # Show sync prompt
                    print_text("\n⚡ Quick Sync", style="bold cyan")
                    print_text("Press Enter to sync all in context, or type -s args (e.g., -s stories:7 -s projects:60)", style="dim")

                    try:
                        sync_input = prompt(
                            "Sync: ",
                            key_bindings=sync_bindings,
                            default=""
                        )

                        if sync_cancelled['value']:
                            print_text("Sync cancelled.", style="bold yellow")
                            continue

                        # Execute sync
                        import asyncio
                        print_text("Syncing...", style="cyan")

                        sync_results = asyncio.run(inline_sync_databases(sync_input.strip() if sync_input else None))

                        if sync_results:
                            # Build summary message
                            summary_parts = []
                            for result in sync_results:
                                if hasattr(result, 'database_name') and hasattr(result, 'pages_saved'):
                                    db_name = result.database_name or 'unknown'
                                    # Shorten database name for display
                                    display_name = db_name.split('.')[-1] if '.' in db_name else db_name
                                    new_count = result.pages_saved
                                    summary_parts.append(f"{display_name} ({new_count} new)")

                            if summary_parts:
                                summary = "✅ Synced: " + ", ".join(summary_parts)
                                print_text(summary, style="bold green")
                            else:
                                print_text("✅ Sync completed", style="bold green")

                            # Wait 2 seconds to show summary
                            import time
                            time.sleep(2)

                            # Reload context with new data
                            if reload_context():
                                print_text("Context reloaded with fresh data.", style="dim green")
                        else:
                            print_text("⚠️  Sync completed with no results.", style="dim yellow")

                    except KeyboardInterrupt:
                        print_text("\nSync cancelled.", style="bold yellow")
                    except Exception as e:
                        print_text(f"Sync error: {e}", style="bold red")
                        debug_print(f"Inline sync error details: {e}")

                except Exception as e:
                    print_text(f"Error in inline sync: {e}", style="bold red")
                    debug_print(f"Inline sync handler error: {e}")

                continue

            elif user_input.strip() == '/browser-inline':
                # Handle inline browser mode triggered by Ctrl+B
                try:
                    print_text("\n🔍 Browser Mode", style="bold cyan")
                    print_text("Launching unified browser...", style="dim")

                    # Get current context information
                    workspace = context_state.get('workspace')
                    multiple_workspaces = context_state.get('database_filter', [])
                    database_filter = context_state.get('database_filter', [])
                    default_days = context_state.get('default_days')
                    current_sources = context_state.get('sources', [])

                    # Fall back to default workspace when no context exists
                    if not workspace and not database_filter:
                        from promaia.config.workspaces import get_default_workspace
                        workspace = get_default_workspace()
                        if not workspace:
                            print_text("No workspace configured. Run 'maia workspace add' first.", style="red")
                            continue

                    # Try to load saved browser selection as current sources if none in context
                    if not current_sources:
                        saved_sources = get_browser_selection()
                        if saved_sources:
                            current_sources = saved_sources

                    # Launch unified browser
                    from promaia.cli.workspace_browser import launch_unified_browser

                    # Handle multiple workspaces case
                    if multiple_workspaces and not workspace:
                        browser_result = launch_unified_browser(
                            workspace=None,
                            default_days=default_days,
                            database_filter=multiple_workspaces,
                            current_sources=current_sources,
                            respect_defaults=not bool(current_sources),
                        )
                    else:
                        browser_result = launch_unified_browser(
                            workspace=workspace,
                            default_days=default_days,
                            database_filter=database_filter,
                            current_sources=current_sources,
                            respect_defaults=not bool(current_sources),
                        )

                    if browser_result.cancelled:
                        print_text("Browser cancelled. Keeping current context unchanged.", style="yellow")
                        continue

                    selected_sources = browser_result.sources

                    if not selected_sources:
                        # User confirmed with nothing selected - clear browser context
                        print_text("No sources selected. Browser context cleared.", style="yellow")
                        context_state['sources'] = []
                        context_state['filters'] = []
                        context_state['browse_selections'] = []
                        save_browser_selection([])
                        continue

                    print_text(f"Selected {len(selected_sources)} sources", style="green")

                    # Save the browser selection for persistence
                    save_browser_selection(selected_sources)

                    # Resolve browse scope and apply result using shared functions
                    from promaia.chat.context_editor import resolve_browse_scope, apply_browser_result
                    scope = resolve_browse_scope(
                        browse_databases=database_filter,
                        workspace=workspace,
                        context_state=context_state,
                    )
                    apply_browser_result(selected_sources, context_state, scope.browse_scope_db_names)

                    # Reload context with new selections
                    if reload_context():
                        print_text("Context reloaded with new browser selections.", style="dim green")

                        # Show updated welcome message
                        print_welcome_message(
                            query_command=context_state['query_command'],
                            total_pages=total_pages_loaded,
                            model_name=get_current_model_name(),
                            source_breakdown=generate_source_breakdown(initial_multi_source_data)
                        )
                    else:
                        print_text("Failed to reload context.", style="dim yellow")

                except KeyboardInterrupt:
                    print_text("\nBrowser cancelled.", style="bold yellow")
                except Exception as e:
                    print_text(f"Browser error: {e}", style="bold red")
                    debug_print(f"Inline browser error details: {e}")

                continue

            elif user_input.strip().lower() == '/e':
                # Edit context
                try:
                    edit_result = edit_context()
                    # Only redisplay welcome message if context was actually updated
                    # edit_result can be: True (updated), 'no_change' (no changes), or False (cancelled)
                    if edit_result is True:
                        print()

                        # Save the updated command to recents
                        try:
                            recents_manager = RecentsManager()
                            current_sources = context_state.get('sources', [])
                            current_filters = context_state.get('filters', [])
                            current_workspace = context_state.get('workspace')
                            current_nl_prompt = context_state.get('sql_query_prompt')
                            # Clean up extra spaces in natural language prompt
                            if current_nl_prompt:
                                current_nl_prompt = ' '.join(current_nl_prompt.split())
                            current_browse_command = context_state.get('original_query_format')

                            # Only save if we have meaningful content to save
                            if current_sources or current_filters or current_nl_prompt or current_browse_command:
                                recents_manager.add_query(
                                    sources=current_sources,
                                    filters=current_filters,
                                    workspace=current_workspace,
                                    sql_query_prompt=current_nl_prompt,
                                    original_browse_command=current_browse_command
                                )
                        except Exception as e:
                            # Don't let recents saving errors break the flow
                            debug_print(f"Failed to save updated command to recents: {e}")

                        # Show the same detailed breakdown as when starting a new chat
                        # DEBUG: Log what we're about to display
                        debug_print(f"📊 About to display welcome message after /e:")
                        debug_print(f"  total_pages_loaded: {total_pages_loaded}")
                        debug_print(f"  initial_multi_source_data keys: {list(initial_multi_source_data.keys())}")
                        debug_print(f"  initial_multi_source_data counts: {[(k, len(v)) for k, v in initial_multi_source_data.items()]}")
                        debug_print(f"  context_state['total_pages_loaded']: {context_state.get('total_pages_loaded')}")
                        debug_print(f"  context_state['initial_multi_source_data'] keys: {list(context_state.get('initial_multi_source_data', {}).keys())}")

                        print_welcome_message(
                            query_command=context_state['query_command'],
                            total_pages=total_pages_loaded,
                            model_name=get_current_model_name(),
                            source_breakdown=generate_source_breakdown(initial_multi_source_data)
                        )
                        # Save context log for edit-triggered update
                        save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "context_edit")
                        # Keep debug info if needed
                        if DEBUG_MODE:
                            print_text(f"Debug: System prompt length: {len(system_prompt)}", style="dim")
                            print_text(f"Debug: Sources in context_state: {context_state.get('sources')}", style="dim")
                            print_text(f"Debug: Multi-source data keys: {list(initial_multi_source_data.keys())}", style="dim")
                    elif edit_result == 'no_change':
                        # User pressed Enter without making changes - don't redisplay welcome message
                        # The context is already correct from the previous update
                        pass
                    else:
                        print_text("Context editing cancelled.", style="bold yellow")
                except Exception as e:
                    print_text(f"Error editing context: {e}", style="bold red")
                    debug_print(f"Context edit error: {e}")
                continue
            elif user_input.strip().lower() == '/queries':
                # List all AI-generated queries
                ai_queries = context_state.get('ai_queries', [])
                if not ai_queries:
                    print_text("No AI-generated queries in this session.", style="dim")
                else:
                    print_text("\n🔎 AI-Generated Queries:", style="bold cyan")
                    print()
                    for i, query_info in enumerate(ai_queries, 1):
                        query_type = query_info.get('type', 'unknown')
                        query_text = query_info.get('query', '')
                        reasoning = query_info.get('reasoning', '')
                        timestamp = query_info.get('timestamp', '')

                        print_text(f"  {i}. [{query_type}] \"{query_text}\"", style="white")

                        if reasoning:
                            # Truncate reasoning if too long
                            import textwrap
                            if len(reasoning) > 150:
                                reasoning_display = reasoning[:150] + "..."
                            else:
                                reasoning_display = reasoning
                            wrapped = textwrap.fill(reasoning_display, width=70, initial_indent='     💭 ', subsequent_indent='        ')
                            print_text(wrapped, style="dim")

                        if timestamp:
                            print_text(f"     Created: {timestamp}", style="dim")
                        print()

                    print_text("Use '/remove-query N' to remove a query", style="dim")
                    print()
                continue

            elif user_input.strip().lower().startswith('/remove-query '):
                # Remove a specific AI query
                try:
                    query_num = int(user_input.strip().split()[1])
                    ai_queries = context_state.get('ai_queries', [])

                    if 1 <= query_num <= len(ai_queries):
                        removed_query = ai_queries.pop(query_num - 1)
                        print_text(f"✓ Removed query: \"{removed_query.get('query', 'unknown')}\"", style="bold green")

                        # Reload context without this query
                        print_text("Reloading context...", style="dim")
                        reload_result = reload_context()

                        if reload_result:
                            print()
                            print_welcome_message(
                                query_command=context_state['query_command'],
                                total_pages=total_pages_loaded,
                                model_name=get_current_model_name(),
                                source_breakdown=generate_source_breakdown(initial_multi_source_data)
                            )
                            # Save context log for query removal
                            save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "query_removal")
                        else:
                            print_text("Failed to reload context after query removal.", style="bold red")
                    else:
                        print_text(f"Query #{query_num} not found. Use '/queries' to see all queries.", style="red")
                except (ValueError, IndexError):
                    print_text("Usage: /remove-query N (where N is the query number)", style="red")
                continue

            elif user_input.strip().lower() == '/help':
                print_help_message(query_command=query_command, total_pages=total_pages_loaded, model_name=get_current_model_name(), source_breakdown=generate_source_breakdown(initial_multi_source_data))
                continue
            
            elif user_input.strip().lower() in ['/artifacts', '/a']:
                # List all artifacts
                if context_state['artifact_manager'] is None or not context_state['artifact_manager'].artifacts:
                    print_text("No artifacts in this session.", style="dim")
                else:
                    print_text("\n📋 Artifacts in this session:", style="bold cyan")
                    for artifact_id, preview, version in context_state['artifact_manager'].list_artifacts():
                        print_text(f"  #{artifact_id} (v{version}): {preview}", style="dim")
                    print()
                continue
            
            elif user_input.strip().lower().startswith('/artifact '):
                # Show specific artifact
                try:
                    artifact_num = int(user_input.strip().split()[1])
                    if context_state['artifact_manager'] and artifact_num in context_state['artifact_manager'].artifacts:
                        print()
                        print(context_state['artifact_manager'].render_artifact(artifact_num))
                        print()
                    else:
                        print_text(f"Artifact #{artifact_num} not found.", style="red")
                except (ValueError, IndexError):
                    print_text("Usage: /artifact <number>", style="yellow")
                continue
            
            elif user_input.strip().lower().startswith('/edit '):
                # Edit specific artifact (prompt for changes)
                try:
                    artifact_num = int(user_input.strip().split()[1])
                    if context_state['artifact_manager'] and artifact_num in context_state['artifact_manager'].artifacts:
                        print()
                        print(context_state['artifact_manager'].render_artifact(artifact_num))
                        print()
                        print_text("What changes would you like to make?", style="cyan")
                        # The next user message will be treated as an update request
                        context_state['artifact_manager'].last_artifact_id = artifact_num
                    else:
                        print_text(f"Artifact #{artifact_num} not found.", style="red")
                except (ValueError, IndexError):
                    print_text("Usage: /edit <number>", style="yellow")
                continue

            elif user_input.strip().lower() == '/m' or user_input.strip().lower().startswith('/m '):
                # Manual edit mode - edit artifact directly with keyboard
                try:
                    parts = user_input.strip().split()

                    # Determine which artifact to edit
                    if len(parts) == 1:
                        # No number provided, use latest artifact
                        if context_state['artifact_manager'] and context_state['artifact_manager'].last_artifact_id:
                            artifact_num = context_state['artifact_manager'].last_artifact_id
                        else:
                            print_text("No artifacts available to edit.", style="yellow")
                            continue
                    else:
                        # Artifact number provided
                        artifact_num = int(parts[1])

                    # Check if artifact exists
                    if not context_state['artifact_manager'] or artifact_num not in context_state['artifact_manager'].artifacts:
                        print_text(f"Artifact #{artifact_num} not found.", style="red")
                        continue

                    # Get current artifact content
                    artifact = context_state['artifact_manager'].get_artifact(artifact_num)
                    artifact_data = artifact.get('data')
                    is_email_artifact = artifact_data and artifact_data.get('type') == 'email'

                    # For email artifacts, show rendered format (easier to edit)
                    # For other artifacts, show raw content
                    if is_email_artifact:
                        # Build editable format: Subject:/To:/Cc: headers + body
                        lines = []
                        if 'subject' in artifact_data and artifact_data['subject']:
                            lines.append(f"Subject: {artifact_data['subject']}")
                        if 'to' in artifact_data and artifact_data['to']:
                            lines.append(f"To: {artifact_data['to']}")
                        if 'cc' in artifact_data and artifact_data['cc']:
                            lines.append(f"Cc: {artifact_data['cc']}")
                        if lines:  # Add separator between headers and body
                            lines.append('')
                        if 'body' in artifact_data:
                            lines.append(artifact_data['body'])
                        current_content = '\n'.join(lines)
                    else:
                        current_content = artifact['content']

                    # Show header
                    print()
                    print_text(f"✏️  Manual Edit Mode - Artifact #{artifact_num}", style="bold cyan")
                    print_text("SHIFT+ENTER: New line  •  ENTER: Save  •  ESC/Ctrl+C: Cancel", style="dim")
                    print()

                    # Create a separate editing session with custom key bindings
                    edit_bindings = KeyBindings()

                    @edit_bindings.add('enter')
                    def _(event):
                        """Enter key saves and exits."""
                        event.app.exit(result=event.app.current_buffer.text)

                    @edit_bindings.add('c-j')
                    def _(event):
                        """Ctrl+J (Shift+Enter) adds a new line."""
                        event.current_buffer.insert_text('\n')

                    @edit_bindings.add('escape')
                    def _(event):
                        """ESC cancels edit."""
                        event.app.exit(result=None)

                    @edit_bindings.add('c-c')
                    def _(event):
                        """Ctrl+C cancels edit."""
                        event.app.exit(result=None)

                    # Create editing session with pre-populated content
                    edit_session = PromptSession(
                        multiline=True,
                        key_bindings=edit_bindings
                    )

                    try:
                        # Prompt with current content as default
                        edited_content = edit_session.prompt(
                            "Edit: ",
                            default=current_content,
                            style=style
                        )

                        # If user didn't cancel (ESC/Ctrl+C returns None)
                        if edited_content is not None:
                            # For email artifacts, parse headers and reconstruct JSON
                            if is_email_artifact:
                                import json as json_module
                                import re as re_module

                                # Parse edited content for headers
                                lines = edited_content.split('\n')
                                parsed_metadata = {}
                                body_start = 0

                                for i, line in enumerate(lines):
                                    stripped = line.strip()
                                    if not stripped:
                                        body_start = i + 1
                                        break
                                    elif stripped.lower().startswith('subject:'):
                                        parsed_metadata['subject'] = stripped[8:].strip()
                                    elif stripped.lower().startswith('to:'):
                                        parsed_metadata['to'] = stripped[3:].strip()
                                    elif stripped.lower().startswith('cc:'):
                                        parsed_metadata['cc'] = stripped[3:].strip()
                                    else:
                                        # Not a header line, body starts here
                                        body_start = i
                                        break

                                # Extract body (everything after headers)
                                body_lines = lines[body_start:]
                                # Strip leading empty lines
                                while body_lines and not body_lines[0].strip():
                                    body_lines.pop(0)
                                parsed_body = '\n'.join(body_lines)

                                # Reconstruct JSON
                                email_json = {
                                    "type": "email",
                                    "body": parsed_body
                                }
                                if 'subject' in parsed_metadata:
                                    email_json['subject'] = parsed_metadata['subject']
                                if 'to' in parsed_metadata:
                                    email_json['to'] = parsed_metadata['to']
                                if 'cc' in parsed_metadata:
                                    email_json['cc'] = parsed_metadata['cc']

                                # Serialize to JSON string
                                edited_content = json_module.dumps(email_json, indent=2)

                            # Update the artifact
                            context_state['artifact_manager'].update_artifact(artifact_num, edited_content)

                            # Sync metadata with draft DB if in draft mode
                            if draft_id and mode:
                                from promaia.chat.modes import DraftMode
                                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                                if isinstance(mode, DraftMode):
                                    _, metadata = extract_email_metadata_from_artifact(context_state['artifact_manager'], artifact_num)
                                    if metadata:
                                        update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                        # Reload draft_data to reflect updates
                                        mode.draft_data = mode.draft_manager.get_draft(draft_id)

                            # Add to message history as an update
                            messages.append({
                                "role": "user",
                                "content": f"[Manually edited artifact #{artifact_num}]"
                            })
                            messages.append({
                                "role": "assistant",
                                "content": f"<artifact>{edited_content}</artifact>"
                            })

                            print()
                            print_text(f"✅ Artifact #{artifact_num} updated successfully!", style="green")
                            print()

                        else:
                            # User cancelled
                            print()
                            print_text("❌ Edit cancelled.", style="yellow")
                            print()

                    except (KeyboardInterrupt, EOFError):
                        # Handle Ctrl+C or Ctrl+D
                        print()
                        print_text("❌ Edit cancelled.", style="yellow")
                        print()

                except (ValueError, IndexError):
                    print_text("Usage: /m [artifact-number]  (defaults to latest artifact)", style="yellow")
                continue

            elif user_input.strip().lower().startswith('/image'):
                # Handle multiple image attachments
                try:
                    parts = user_input.strip().split(' ', 1)
                    if len(parts) < 2:
                        print_text("Usage: /image <path1> [path2] [path3] ... [optional message]", style="bold yellow")
                        print_text("Example: /image /path/to/photo1.jpg /path/to/photo2.png What do you see in these images?", style="dim")
                        continue
                    
                    image_part = parts[1].strip()
                    
                    # Parse multiple image paths and message
                    image_paths, message_text = _parse_image_paths_and_message(image_part)
                    
                    if not image_paths:
                        print_text("No valid image paths found.", style="bold red")
                        continue
                    
                    # Process the images
                    from promaia.utils.image_processing import (
                        encode_image_from_path, is_vision_supported, get_model_image_limits
                    )
                    
                    # Check if current model supports vision
                    if not is_vision_supported(current_api):
                        print_text(f"Current model '{current_api}' does not support image inputs.", style="bold red")
                        print_text("Try switching to a vision-capable model with '/model'.", style="dim")
                        continue
                    
                    # Get model limits
                    model_limits = get_model_image_limits(current_api)
                    max_images = model_limits['max_images']
                    
                    # Limit images to model capacity
                    if len(image_paths) > max_images:
                        print_text(f"⚠️  {current_api.title()} supports max {max_images} images. Processing first {max_images} images.", style="bold yellow")
                        image_paths = image_paths[:max_images]
                    
                    # Encode all images
                    current_images = []
                    successful_paths = []

                    for image_path in image_paths:
                        try:
                            # Unescape shell characters in path (\=, \ , etc.)
                            actual_path = _shell_unescape(image_path)
                            # Translate host paths for Docker
                            actual_path = _resolve_host_path(actual_path)

                            # Use File API for Gemini if needed, otherwise base64
                            if current_api == 'gemini':
                                from promaia.utils.image_processing import process_image_for_gemini
                                encoded_image = process_image_for_gemini(actual_path)
                                # Show file size info for File API uploads
                                if encoded_image.get('method') == 'file_api':
                                    file_size_mb = os.path.getsize(actual_path) / (1024 * 1024)
                                    print_text(f"📸 Image uploaded via File API: {actual_path} ({file_size_mb:.1f} MB)", style="bold green")
                                else:
                                    print_text(f"📸 Image loaded: {actual_path}", style="bold green")
                            else:
                                encoded_image = encode_image_from_path(actual_path)
                                print_text(f"📸 Image loaded: {actual_path}", style="bold green")

                            current_images.append(encoded_image)
                            successful_paths.append(actual_path)
                        except Exception as img_error:
                            actual_path = _shell_unescape(image_path)
                            print_text(f"❌ Failed to load image: {actual_path} - {img_error}", style="bold red")
                    
                    if not current_images:
                        print_text("No images were successfully loaded.", style="bold red")
                        continue
                    
                    # Prompt for message if none provided
                    if not message_text:
                        message_text = session.prompt("Message (optional): ", style=style).strip()
                    
                    # Prepare message with images
                    user_input = message_text  # Set the text part
                    img_word = "image" if len(current_images) == 1 else "images"
                    print_text(f"📸 Processing {len(current_images)} {img_word} with {current_api.title()}...", style="bold green")
                    
                except Exception as e:
                    print_text(f"Error processing images: {e}", style="bold red")
                    continue
            elif user_input.strip().lower().startswith('/model'):
                # Switch AI model
                input_parts = user_input.strip().split(' ', 1)
                target_model = input_parts[1] if len(input_parts) > 1 else None
                
                if switch_model(target_model):
                    # Show updated model info
                    print_text(f"Now using: {get_current_model_name()}", style="bold cyan")
                continue
            elif user_input.strip().lower().startswith('/temp'):
                # Adjust temperature (creativity)
                input_parts = user_input.strip().split(' ', 1)
                if len(input_parts) > 1:
                    try:
                        new_temp = float(input_parts[1])
                        if 0.0 <= new_temp <= 2.0:
                            current_temperature = new_temp
                            creativity_label = "very focused" if new_temp < 0.3 else "focused" if new_temp < 0.6 else "balanced" if new_temp < 1.0 else "creative" if new_temp < 1.5 else "very creative"
                            print_text(f"🌡️  Temperature set to {current_temperature} ({creativity_label})", style="bold cyan")
                        else:
                            print_text("Temperature must be between 0.0 and 2.0", style="bold red")
                    except ValueError:
                        print_text("Invalid temperature value. Use: /temp 0.9", style="bold red")
                else:
                    print_text(f"Current temperature: {current_temperature}", style="cyan")
                    print_text("Usage: /temp <0.0-2.0>", style="dim")
                    print_text("  0.0-0.5: Very focused, deterministic", style="dim")
                    print_text("  0.6-0.9: Balanced (default: 0.7)", style="dim")
                    print_text("  1.0-2.0: Creative, diverse outputs", style="dim")
                continue
            elif user_input.strip().lower().startswith('/save'):
                # Save current conversation to history
                if not messages:
                    print_text("No conversation to save.", style="bold yellow")
                    continue
                
                try:
                    history_manager = ChatHistoryManager()
                    
                    # Extract custom name if provided: /save "My Custom Name"
                    input_parts = user_input.strip().split(' ', 1)
                    custom_name = None
                    if len(input_parts) > 1:
                        custom_name = input_parts[1].strip().strip('"\'')
                    
                    # Prepare context for saving
                    thread_context = {
                        'sources': context_state.get('sources'),
                        'filters': context_state.get('filters'),
                        'workspace': context_state.get('workspace'),
                        'resolved_workspace': context_state.get('resolved_workspace'),
                        'query_command': context_state.get('query_command'),
                        'sql_query_prompt': context_state.get('sql_query_prompt'),
                        'sql_query_content': context_state.get('sql_query_content'),  # Save the actual content for faster restore
                        'original_query_format': context_state.get('original_query_format'),  # Save original browse command
                        'browse_selections': context_state.get('browse_selections'),  # Save browse selections for re-editing
                        'vector_search_queries': context_state.get('vector_search_queries'),  # Save vector search queries
                        'vector_search_content': context_state.get('vector_search_content'),  # Save vector search results
                        'is_vector_search': context_state.get('is_vector_search')  # Save search mode flag
                    }
                    
                    # Check if we're continuing an existing thread
                    current_thread_id = context_state.get('current_thread_id')
                    if current_thread_id:
                        # Update existing thread
                        success = history_manager.update_thread(
                            thread_id=current_thread_id,
                            messages=messages,
                            context=thread_context,
                            thread_name=custom_name
                        )
                        
                        if success:
                            updated_thread = history_manager.get_thread(current_thread_id)
                            if updated_thread:
                                print_text(f"Conversation updated: {updated_thread.name}", style="bold green")
                            else:
                                print_text("Conversation updated successfully!", style="bold green")
                        else:
                            print_text("Error: Could not find thread to update. Creating new thread instead.", style="bold yellow")
                            # Fallback to creating new thread
                            thread_id = history_manager.save_thread(
                                messages=messages,
                                context=thread_context,
                                thread_name=custom_name
                            )
                            context_state['current_thread_id'] = thread_id
                            saved_thread = history_manager.get_thread(thread_id)
                            if saved_thread:
                                print_text(f"New conversation saved as: {saved_thread.name}", style="bold green")
                    else:
                        # Create new thread
                        thread_id = history_manager.save_thread(
                            messages=messages,
                            context=thread_context,
                            thread_name=custom_name
                        )
                        
                        # Update context to track this thread for future saves
                        context_state['current_thread_id'] = thread_id
                        
                        # Get the saved thread to show the generated name
                        saved_thread = history_manager.get_thread(thread_id)
                        if saved_thread:
                            print_text(f"Conversation saved as: {saved_thread.name}", style="bold green")
                        else:
                            print_text("Conversation saved successfully!", style="bold green")
                        
                except Exception as e:
                    print_text(f"Error saving conversation: {e}", style="bold red")
                    debug_print(f"Save error details: {e}")
                continue
            elif user_input.strip().lower() == '/mcp search':
                # Toggle internet search functionality
                current_search = context_state.get('enable_search', False)
                context_state['enable_search'] = not current_search

                if context_state['enable_search']:
                    # Enable search - add search MCP server if not already present
                    if 'search' not in (context_state.get('mcp_servers') or []):
                        if context_state.get('mcp_servers') is None:
                            context_state['mcp_servers'] = ['search']
                        else:
                            context_state['mcp_servers'].append('search')

                        # Reconnect MCP servers to include search
                        if context_state.get('mcp_client'):
                            try:
                                # Disconnect existing servers
                                import asyncio
                                asyncio.run(context_state['mcp_client'].disconnect_all())

                                # Reconnect with search server included
                                from promaia.config.mcp_servers import get_mcp_manager
                                from promaia.mcp.client import McpClient
                                from promaia.mcp.execution import McpToolExecutor

                                mcp_manager = get_mcp_manager()
                                mcp_client = McpClient()

                                connected_servers = []
                                for server_name in context_state['mcp_servers']:
                                    server_config = mcp_manager.get_server(server_name)
                                    if server_config:
                                        success = asyncio.run(mcp_client.connect_to_server(server_config))
                                        if success:
                                            connected_servers.append(server_name)

                                # Update context with new MCP client
                                context_state['mcp_client'] = mcp_client
                                context_state['mcp_executor'] = McpToolExecutor(mcp_client)

                                # Update system prompt with new tools
                                mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                                context_state['mcp_tools_info'] = mcp_tools_info

                                # Apply mute handling - use empty context if muted
                                context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                                # Regenerate system prompt with new tools
                                system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                                context_state['system_prompt'] = system_prompt

                                # Save context log when MCP servers are connected (for transparency)
                                save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "mcp_connection")

                                print_text("🔍 Internet search enabled and MCP servers reconnected!", style="bold green")
                                print_text("💡 You can now ask the AI to search the web by saying things like:", style="cyan")
                                print_text("   'search the web for information about X' or 'find Y online'", style="dim cyan")
                            except Exception as e:
                                print_text(f"Error reconnecting MCP servers: {e}", style="bold red")
                        else:
                            # Connect to MCP servers for the first time
                            try:
                                import asyncio
                                from promaia.config.mcp_servers import get_mcp_manager
                                from promaia.mcp.client import McpClient
                                from promaia.mcp.execution import McpToolExecutor

                                mcp_manager = get_mcp_manager()
                                mcp_client = McpClient()

                                # Connect to search server
                                connected_servers = []
                                for server_name in context_state.get('mcp_servers', []):
                                    server_config = mcp_manager.get_server(server_name)
                                    if server_config:
                                        success = asyncio.run(mcp_client.connect_to_server(server_config))
                                        if success:
                                            connected_servers.append(server_name)

                                if connected_servers:
                                    # Update context with new MCP client
                                    context_state['mcp_client'] = mcp_client
                                    context_state['mcp_executor'] = McpToolExecutor(mcp_client)

                                    # Update system prompt with new tools
                                    mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                                    context_state['mcp_tools_info'] = mcp_tools_info

                                    # Apply mute handling - use empty context if muted
                                    context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                                    # Regenerate system prompt with new tools
                                    system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                                    context_state['system_prompt'] = system_prompt

                                    # Save context log when MCP servers are connected (for transparency)
                                    save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "mcp_connection")

                                    print_text("🔍 Internet search enabled and MCP servers connected!", style="bold green")
                                    print_text("💡 You can now ask the AI to search the web by saying things like:", style="cyan")
                                    print_text("   'search the web for information about X' or 'find Y online'", style="dim cyan")
                                else:
                                    print_text("🔍 Internet search enabled but no servers connected", style="bold yellow")
                            except Exception as e:
                                print_text(f"Error connecting MCP servers: {e}", style="bold red")
                    else:
                        print_text("🔍 Internet search enabled", style="bold green")
                else:
                    # Disable search - remove search MCP server
                    if context_state.get('mcp_servers') and 'search' in context_state['mcp_servers']:
                        context_state['mcp_servers'].remove('search')

                        # Reconnect MCP servers without search
                        if context_state.get('mcp_client'):
                            try:
                                import asyncio
                                from promaia.config.mcp_servers import get_mcp_manager
                                from promaia.mcp.client import McpClient
                                from promaia.mcp.execution import McpToolExecutor

                                # Disconnect existing servers
                                asyncio.run(context_state['mcp_client'].disconnect_all())

                                # Reconnect without search server
                                mcp_manager = get_mcp_manager()
                                mcp_client = McpClient()

                                connected_servers = []
                                for server_name in context_state['mcp_servers']:
                                    server_config = mcp_manager.get_server(server_name)
                                    if server_config:
                                        success = asyncio.run(mcp_client.connect_to_server(server_config))
                                        if success:
                                            connected_servers.append(server_name)

                                # Update context with new MCP client
                                context_state['mcp_client'] = mcp_client
                                context_state['mcp_executor'] = McpToolExecutor(mcp_client)

                                # Update system prompt with new tools
                                mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                                context_state['mcp_tools_info'] = mcp_tools_info

                                # Apply mute handling - use empty context if muted
                                context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                                # Regenerate system prompt with new tools
                                system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                                context_state['system_prompt'] = system_prompt

                                print_text("🔍 Internet search disabled and MCP servers reconnected", style="bold yellow")
                            except Exception as e:
                                print_text(f"Error reconnecting MCP servers: {e}", style="bold red")
                        else:
                            # Update MCP tools info even without reconnection
                            from promaia.config.mcp_servers import get_mcp_manager
                            from promaia.mcp.client import McpClient
                            mcp_manager = get_mcp_manager()
                            mcp_client = McpClient()

                            # Get tools info for current servers
                            connected_servers = context_state.get('mcp_servers', [])
                            mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                            context_state['mcp_tools_info'] = mcp_tools_info

                            # Apply mute handling - use empty context if muted
                            context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                            # Regenerate system prompt with new tools
                            system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                            context_state['system_prompt'] = system_prompt

                            print_text("🔍 Internet search disabled - web search tools removed", style="bold yellow")
                    else:
                        print_text("🔍 Internet search disabled", style="bold yellow")

                continue
            elif user_input.strip().lower() == '/mcp fetch':
                # Toggle URL fetching functionality
                current_fetch = 'fetch' in (context_state.get('mcp_servers') or [])

                if not current_fetch:
                    # Enable fetch - add fetch MCP server
                    if 'fetch' not in (context_state.get('mcp_servers') or []):
                        if context_state.get('mcp_servers') is None:
                            context_state['mcp_servers'] = ['fetch']
                        else:
                            context_state['mcp_servers'].append('fetch')

                        # Reconnect MCP servers to include fetch
                        if context_state.get('mcp_client'):
                            try:
                                import asyncio
                                from promaia.config.mcp_servers import get_mcp_manager
                                from promaia.mcp.client import McpClient
                                from promaia.mcp.execution import McpToolExecutor

                                # Disconnect existing servers
                                asyncio.run(context_state['mcp_client'].disconnect_all())

                                # Reconnect with fetch server included
                                mcp_manager = get_mcp_manager()
                                mcp_client = McpClient()

                                connected_servers = []
                                for server_name in context_state['mcp_servers']:
                                    server_config = mcp_manager.get_server(server_name)
                                    if server_config:
                                        success = asyncio.run(mcp_client.connect_to_server(server_config))
                                        if success:
                                            connected_servers.append(server_name)

                                # Update context with new MCP client
                                context_state['mcp_client'] = mcp_client
                                context_state['mcp_executor'] = McpToolExecutor(mcp_client)

                                # Update system prompt with new tools
                                mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                                context_state['mcp_tools_info'] = mcp_tools_info

                                # Apply mute handling - use empty context if muted
                                context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                                # Regenerate system prompt with new tools
                                system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                                context_state['system_prompt'] = system_prompt

                                # Save context log
                                save_context_log(context_state, system_prompt, total_pages_loaded, current_api, "mcp_connection")

                                print_text("🌐 URL fetching enabled!", style="bold green")
                                print_text("💡 You can now ask the AI to visit URLs by providing them in your query", style="cyan")
                            except Exception as e:
                                print_text(f"Error enabling fetch: {e}", style="bold red")
                        else:
                            print_text("🌐 URL fetching marked for enable (will connect on first use)", style="bold green")
                else:
                    # Disable fetch - remove fetch MCP server
                    if context_state.get('mcp_servers') and 'fetch' in context_state['mcp_servers']:
                        context_state['mcp_servers'].remove('fetch')

                        # Reconnect MCP servers without fetch
                        if context_state.get('mcp_client'):
                            try:
                                import asyncio
                                from promaia.config.mcp_servers import get_mcp_manager
                                from promaia.mcp.client import McpClient
                                from promaia.mcp.execution import McpToolExecutor

                                # Disconnect existing servers
                                asyncio.run(context_state['mcp_client'].disconnect_all())

                                # Reconnect without fetch server
                                mcp_manager = get_mcp_manager()
                                mcp_client = McpClient()

                                connected_servers = []
                                for server_name in context_state['mcp_servers']:
                                    server_config = mcp_manager.get_server(server_name)
                                    if server_config:
                                        success = asyncio.run(mcp_client.connect_to_server(server_config))
                                        if success:
                                            connected_servers.append(server_name)

                                # Update context with new MCP client
                                context_state['mcp_client'] = mcp_client
                                context_state['mcp_executor'] = McpToolExecutor(mcp_client)

                                # Update system prompt with new tools
                                mcp_tools_info = mcp_client.format_tools_for_prompt(connected_servers, compact=True)
                                context_state['mcp_tools_info'] = mcp_tools_info

                                # Apply mute handling - use empty context if muted
                                context_data_for_prompt = {} if context_state.get('context_muted') else initial_multi_source_data

                                # Regenerate system prompt with new tools
                                system_prompt = build_system_prompt_with_mode(context_data_for_prompt, mcp_tools_info, mode_system_prompt, mode, include_query_tools=True, workspace=context_state.get('workspace'))
                                context_state['system_prompt'] = system_prompt

                                print_text("🌐 URL fetching disabled", style="bold yellow")
                            except Exception as e:
                                print_text(f"Error disabling fetch: {e}", style="bold red")
                        else:
                            print_text("🌐 URL fetching disabled", style="bold yellow")

                continue
            elif user_input.strip().lower() == '/agent':
                # Switch to agentic mode (autonomous multi-tool execution)
                if current_api != "anthropic":
                    print_text("Agentic mode requires Anthropic API. Switch with /model first.", style="bold red")
                    continue

                if context_state.get('agentic_mode'):
                    print_text("Already in agent mode.", style="dim")
                    continue

                from promaia.chat.agentic_adapter import detect_available_tools, _resolve_workspace
                from promaia.config.databases import get_database_manager

                ws = _resolve_workspace(context_state.get('workspace') or context_state.get('resolved_workspace') or "")
                detected_tools = detect_available_tools(ws)
                try:
                    db_mgr = get_database_manager()
                    detected_dbs = db_mgr.list_databases(workspace=ws) if ws else []
                except Exception:
                    detected_dbs = []

                context_state['agentic_mode'] = True
                context_state['agentic_tools'] = detected_tools
                context_state['agentic_databases'] = detected_dbs

                print_text("🤖 Agent mode", style="bold cyan")
                if detected_tools:
                    print_text(f"   Tools: {', '.join(detected_tools)}", style="dim cyan")
                if detected_dbs:
                    print_text(f"   Databases: {', '.join(detected_dbs)}", style="dim cyan")
                if not detected_tools and not detected_dbs:
                    print_text("   No tools or databases detected — query tools still available", style="dim")
                continue

            elif user_input.strip().lower() == '/chat':
                # Switch to standard chat mode (single LLM call, no autonomous tools)
                if not context_state.get('agentic_mode'):
                    print_text("Already in chat mode.", style="dim")
                    continue
                context_state['agentic_mode'] = False
                print_text("💬 Chat mode", style="bold yellow")
                continue
            elif user_input.strip().lower().startswith('/mail'):
                # Parse /mail command - can be "/mail" or "/mail workspace"
                mail_parts = user_input.strip().split(maxsplit=1)
                target_workspace = mail_parts[1] if len(mail_parts) > 1 else None

                # Toggle email sending functionality
                current_email_send = context_state.get('enable_email_send', False)
                context_state['enable_email_send'] = not current_email_send

                if context_state['enable_email_send']:
                    print_text("📧 Mail mode enabled", style="bold green")
                    print_text("🔍 Loading Gmail context...", style="cyan")

                    # Automatically load Gmail sources from specified or all workspaces
                    from promaia.config.databases import get_database_manager
                    from promaia.config.workspaces import get_workspace_manager

                    db_manager = get_database_manager()
                    workspace_manager = get_workspace_manager()

                    # Determine which workspaces to load from
                    if target_workspace:
                        # Load from specific workspace only
                        workspaces_to_load = [target_workspace]
                    else:
                        # Load from all workspaces
                        workspaces_to_load = workspace_manager.list_workspaces()

                    # Find all Gmail databases from selected workspaces
                    gmail_sources = []
                    mail_from_accounts = []  # Track available sending accounts
                    for ws in workspaces_to_load:
                        gmail_dbs = [
                            db for db in db_manager.get_workspace_databases(ws)
                            if db.source_type == "gmail"
                        ]
                        for gmail_db in gmail_dbs:
                            # Add with workspace prefix and 7 days of history
                            # Use nickname for source name (e.g., "gmail"), database_id is the email address
                            if ws == 'default':
                                gmail_sources.append(f"{gmail_db.nickname}:7")
                            else:
                                gmail_sources.append(f"{ws}.{gmail_db.nickname}:7")

                            # Track account info for "send from" selection
                            mail_from_accounts.append({
                                'workspace': ws,
                                'email': gmail_db.database_id,  # This is the actual email address
                                'display': f"{gmail_db.database_id} ({ws})" if ws != 'default' else gmail_db.database_id
                            })

                    # Store available accounts for /send to use
                    context_state['mail_from_accounts'] = mail_from_accounts
                    context_state['mail_target_workspace'] = target_workspace

                    if gmail_sources:
                        # Store original sources to restore later
                        if 'original_sources_before_mail' not in context_state:
                            context_state['original_sources_before_mail'] = context_state.get('sources')

                        # Add Gmail sources to current sources (don't replace, add to)
                        current_sources = context_state.get('sources') or []
                        new_sources = list(set(current_sources + gmail_sources))  # Deduplicate
                        context_state['sources'] = new_sources

                        # Reload context with Gmail data
                        if reload_context():
                            # Show what was actually loaded
                            workspace_msg = f" from '{target_workspace}'" if target_workspace else ""
                            print_text(f"✅ Loaded Gmail context{workspace_msg}", style="green")
                            print_text(f"Pages loaded: {total_pages_loaded}", style="dim")

                            # Show breakdown by source if available
                            source_breakdown = generate_source_breakdown(initial_multi_source_data)
                            if source_breakdown:
                                for source_name, count in source_breakdown.items():
                                    print_text(f"{source_name}: {count}", style="dim")

                            print_text(f"\nSending from {len(mail_from_accounts)} account(s):", style="cyan")
                            for account in mail_from_accounts:
                                print_text(f"   • {account['display']}", style="dim green")

                            print_text("\n💡 You can now drop files and say things like:", style="cyan")
                            print_text("   'send this to bob' or 'email this report to the team'", style="dim cyan")
                            print_text("   When ready, type /send to send the email", style="dim cyan")
                        else:
                            print_text("⚠️  Failed to load Gmail context, but mail mode is enabled", style="yellow")
                            print_text("   The AI may not be able to find threads or addresses", style="dim yellow")
                    else:
                        print_text("⚠️  No Gmail accounts configured", style="yellow")
                        print_text("   Mail mode enabled but AI won't have context", style="dim yellow")
                else:
                    print_text("📧 Mail mode disabled", style="bold yellow")

                    # Restore original sources if they exist
                    if 'original_sources_before_mail' in context_state:
                        context_state['sources'] = context_state['original_sources_before_mail']
                        del context_state['original_sources_before_mail']
                        if reload_context():
                            print_text("✅ Context restored", style="green")

                continue

            elif user_input.strip().lower() == '/send':
                # Handle email sending - check for valid email artifact first
                # Get the latest artifact (email draft)
                if not artifact_manager or not artifact_manager.artifacts:
                    print_text("❌ No email draft found", style="red")
                    continue

                # Use latest artifact
                artifact_id = max(artifact_manager.artifacts.keys())
                artifact = artifact_manager.get_artifact(artifact_id)

                # Extract email metadata from JSON artifact
                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, get_email_body_from_artifact

                email_body, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_id)

                if not email_body:
                    print_text("❌ Could not extract email body from artifact", style="red")
                    continue

                # Get email details from metadata (JSON) or fall back to old format
                recipient = metadata.get('to', '')
                subject = metadata.get('subject', '')
                cc_recipients = metadata.get('cc', '')
                thread_id = metadata.get('thread_id')
                message_id = metadata.get('message_id')

                # Log threading information for debugging
                if thread_id or message_id:
                    logger.info(f"📧 Email threading info - thread_id: {thread_id}, message_id: {message_id}")
                else:
                    logger.info("📧 New email (no thread_id/message_id)")

                # Safeguard: Check if thread_id matches the last sent email (potential reuse bug)
                last_sent = context_state.get('last_sent_thread')
                if last_sent and thread_id and thread_id == last_sent.get('thread_id'):
                    # Same thread as last email - this might be intentional (multiple replies to same thread)
                    # or it might be a bug (AI reusing stale thread_id)
                    # We'll warn if the subjects are different
                    if subject != last_sent.get('subject'):
                        print_text("⚠️  WARNING: This email appears to use the same thread as your previous email,", style="bold yellow")
                        print_text(f"   but has a different subject line:", style="yellow")
                        print_text(f"   Previous: {last_sent.get('subject')}", style="dim yellow")
                        print_text(f"   Current:  {subject}", style="dim yellow")
                        print_text("   This might attach your reply to the wrong email thread.", style="yellow")
                        print_text("   Type 'cancel' to abort or 'continue' to send anyway: ", style="yellow", end='')

                        confirmation = input().strip().lower()
                        if confirmation != 'continue':
                            print_text("❌ Send cancelled\n", style="red")
                            continue

                # For old-style artifacts, try to extract from context
                if not recipient and not subject:
                    email_metadata = context_state.get('email_metadata')
                    if email_metadata:
                        recipient = email_metadata.get('recipient', '')
                        subject = email_metadata.get('subject', '')

                attachments = []

                # Validate required fields
                if not recipient or not subject:
                    print_text("❌ Email artifact is missing required fields:", style="red")
                    if not subject:
                        print_text("   - Missing 'Subject' field", style="dim red")
                    if not recipient:
                        print_text("   - Missing 'To' field", style="dim red")
                    print_text("\n   Please ask the AI to include both Subject and To in the email.", style="yellow")
                    continue

                # If we have a valid email artifact but mail mode isn't enabled, auto-enable it
                # and auto-setup Gmail accounts for sending
                if not context_state.get('enable_email_send', False) or not context_state.get('mail_from_accounts'):
                    context_state['enable_email_send'] = True
                    logger.info("📧 Auto-enabled email send mode due to valid email artifact")
                    print_text("📧 Email send mode enabled", style="cyan")

                    # Auto-setup Gmail accounts if not already set
                    if not context_state.get('mail_from_accounts'):
                        try:
                            from promaia.config.databases import get_database_manager
                            from promaia.config.workspaces import get_workspace_manager

                            db_manager = get_database_manager()
                            workspace_manager = get_workspace_manager()

                            # Load from current workspace or all workspaces
                            current_workspace = context_state.get('workspace') or context_state.get('resolved_workspace')
                            workspaces_to_load = [current_workspace] if current_workspace else workspace_manager.list_workspaces()

                            # Find all Gmail databases
                            mail_from_accounts = []
                            for ws in workspaces_to_load:
                                gmail_dbs = [
                                    db for db in db_manager.get_workspace_databases(ws)
                                    if db.source_type == "gmail"
                                ]
                                for gmail_db in gmail_dbs:
                                    mail_from_accounts.append({
                                        'workspace': ws,
                                        'email': gmail_db.database_id,
                                        'display': f"{gmail_db.database_id} ({ws})" if ws != 'default' else gmail_db.database_id
                                    })

                            if mail_from_accounts:
                                context_state['mail_from_accounts'] = mail_from_accounts
                                logger.info(f"📧 Auto-setup {len(mail_from_accounts)} Gmail account(s) for sending")
                                print_text(f"📧 Found {len(mail_from_accounts)} Gmail account(s) for sending", style="cyan")
                            else:
                                print_text("⚠️  No Gmail accounts configured - you won't be able to send", style="yellow")
                        except Exception as e:
                            logger.error(f"Failed to auto-setup Gmail accounts: {e}", exc_info=True)

                # Validate email body
                if not email_body.strip() and not attachments:
                    print_text("❌ Email has no body and no attachments", style="red")
                    continue

                # Show email preview
                print()
                print_text("═" * 65, style="cyan")
                print_text("📧 EMAIL PREVIEW", style="bold cyan")
                print_text("═" * 65, style="cyan")
                print()
                print_text(f"Subject: {subject}", style="bold")
                print_text(f"To: {recipient}", style="dim")
                if cc_recipients:
                    print_text(f"Cc: {cc_recipients}", style="dim")
                print()
                print_text("─" * 65, style="dim")

                # Show body preview (truncate if very long)
                body_preview = email_body
                if len(email_body) > 500:
                    body_preview = email_body[:500] + "\n\n... [truncated]"

                print_text(body_preview, style="white")
                print()
                print_text("─" * 65, style="dim")

                if attachments:
                    print()
                    print_text(f"📎 Attachments: {len(attachments)}", style="dim")
                    for att in attachments[:3]:  # Show first 3
                        print_text(f"  - {att}", style="dim")
                    if len(attachments) > 3:
                        print_text(f"  ... and {len(attachments) - 3} more", style="dim")

                print()
                print_text("═" * 65, style="cyan")
                print()

                # Generate thread/message IDs if not provided (new email)
                from datetime import datetime
                if not thread_id:
                    thread_id = f"new_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

                if not message_id:
                    message_id = f"new_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

                # Get available sending accounts from mail mode
                mail_from_accounts = context_state.get('mail_from_accounts', [])

                if not mail_from_accounts:
                    print_text("❌ No Gmail accounts loaded", style="red")
                    print_text("   Use /mail or /mail <workspace> to load Gmail context first", style="dim red")
                    continue

                # Select which account to send from
                if len(mail_from_accounts) == 1:
                    # Only one account, use it automatically
                    selected_account = mail_from_accounts[0]
                    send_workspace = selected_account['workspace']
                    user_email = selected_account['email']
                    print_text(f"📧 Sending from: {selected_account['display']}", style="cyan")
                else:
                    # Multiple accounts - let user select
                    print_text("\n📧 Select account to send from:", style="cyan")
                    for i, account in enumerate(mail_from_accounts, 1):
                        print_text(f"  {i}. {account['display']}", style="dim cyan")

                    while True:
                        try:
                            choice = input("\nSelect account (1-{}) or 'cancel': ".format(len(mail_from_accounts))).strip()
                            if choice.lower() == 'cancel' or not choice:
                                print_text("\n↩️  Send cancelled\n", style="cyan")
                                break

                            choice_idx = int(choice) - 1
                            if 0 <= choice_idx < len(mail_from_accounts):
                                selected_account = mail_from_accounts[choice_idx]
                                send_workspace = selected_account['workspace']
                                user_email = selected_account['email']
                                print_text(f"✅ Sending from: {selected_account['display']}", style="green")
                                break
                            else:
                                print_text("Invalid choice, try again", style="red")
                        except ValueError:
                            print_text("Invalid input, try again", style="red")

                    # If user cancelled, exit
                    if choice.lower() == 'cancel' or not choice:
                        continue

                # Load thread context to get TO and CC recipients
                from promaia.storage.unified_query import get_query_interface
                import json

                to_addr = ''
                cc_addr = ''

                # If replying to an existing thread, load the original recipients
                if thread_id and not thread_id.startswith('new_'):
                    query_interface = get_query_interface()

                    # Query for messages in this thread
                    import sqlite3
                    from promaia.utils.env_writer import get_db_path
                    conn = sqlite3.connect(str(get_db_path()))
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT sender_email, recipient_emails, cc_recipients
                        FROM gmail_content
                        WHERE thread_id = ?
                        ORDER BY created_time DESC
                        LIMIT 1
                    """, (thread_id,))

                    result = cursor.fetchone()
                    if result:
                        sender_email, recipient_emails_json, cc_recipients_json = result

                        # Parse recipient emails
                        if recipient_emails_json:
                            try:
                                recipient_list = json.loads(recipient_emails_json)
                                to_addr = ', '.join(recipient_list)
                            except:
                                to_addr = recipient_emails_json

                        # Parse CC recipients
                        if cc_recipients_json:
                            try:
                                cc_list = json.loads(cc_recipients_json)
                                cc_addr = ', '.join(cc_list)
                            except:
                                cc_addr = cc_recipients_json

                    conn.close()

                # Show recipient selector
                from promaia.mail.recipient_selector import RecipientSelector
                import asyncio

                # Use Cc from JSON artifact if available, otherwise from thread
                if cc_recipients:
                    cc_addr = cc_recipients

                selector = RecipientSelector(
                    from_addr=recipient,  # Person we're sending to
                    to_addr=to_addr,
                    cc_addr=cc_addr,
                    thread_context='',
                    user_email=user_email
                )

                print_text("\n📧 Select recipients for this email...", style="cyan")
                confirmed, recipients = asyncio.run(selector.run())

                if not confirmed:
                    print_text("\n↩️  Send cancelled\n", style="cyan")
                    continue

                if not recipients:
                    print_text("\n❌ No recipients selected\n", style="red")
                    continue

                print_text(f"\n✅ Sending to: {', '.join(recipients)}", style="green")

                # Show email preview
                print_text("\n" + "─" * 60, style="dim")
                print_text("📧 Email Preview:", style="cyan bold")
                print_text("─" * 60, style="dim")
                preview = format_email_preview(email_body, attachments)
                print_text(preview, style="white")
                print_text("─" * 60 + "\n", style="dim")

                # Format the email body
                from promaia.mail.response_generator import ResponseGenerator
                generator = ResponseGenerator()
                email_body = generator._format_email_body(email_body)

                # Safety confirmation
                print()
                print_text(f"⚠️  Ready to send email", style="bold yellow")
                print_text(f"Subject: {subject}", style="yellow")

                # Generate safety string from first recipient email
                from promaia.mail.draft_manager import get_safety_string_from_recipient
                # Use first recipient from the confirmed list
                first_recipient = recipients[0] if recipients else recipient
                safety_string = get_safety_string_from_recipient(first_recipient)

                print_text(f"\nType the first 5 characters to confirm: '{safety_string}'", style="yellow")
                print_text(f"Or type 'cancel' (or press Enter) to abort", style="dim")

                confirmation = input("\nConfirm: ").strip()

                if not confirmation or confirmation.lower() == 'cancel':
                    print_text("\n↩️  Send cancelled\n", style="cyan")
                    continue

                if confirmation.lower() != safety_string:
                    print_text("\n❌ Confirmation failed\n", style="red")
                    continue

                print_text("\n📤 Sending email...", style="cyan")

                # Send email
                from promaia.mail.gmail_sender import GmailSender
                sender = GmailSender(send_workspace, user_email)

                # Check if this is a new email or a reply
                is_new_email = thread_id.startswith('new_')

                if is_new_email:
                    # New email - use send_email() without thread_id
                    to_field = ', '.join(recipients)
                    success = asyncio.run(sender.send_email(
                        to=to_field,
                        subject=subject,
                        body_text=email_body
                    ))
                else:
                    # Reply to existing thread - use send_reply()
                    success = asyncio.run(sender.send_reply(
                        thread_id=thread_id,
                        message_id=message_id,
                        subject=subject,
                        body_text=email_body,
                        recipients=recipients
                    ))

                if success:
                    print_text("✅ Email sent!", style="green")

                    # Save to learning system
                    from promaia.mail.learning_system import EmailResponseLearningSystem
                    from promaia.utils.timezone_utils import now_utc

                    learning = EmailResponseLearningSystem(workspace=send_workspace)

                    pattern = {
                        "inbound": {
                            "from": recipient,
                            "subject": subject,
                            "body_snippet": "",
                        },
                        "response": {
                            "subject": subject,
                            "body": email_body,
                            "tone": "professional",
                            "length": len(email_body.split())
                        },
                        "metadata": {
                            "workspace": workspace,
                            "ai_model": context_state.get('current_api', 'unknown'),
                            "timestamp": now_utc().isoformat(),
                            "sent_via_mail_mode": True
                        }
                    }
                    learning.save_successful_response(pattern)

                    # Clear email metadata and context after successful send to prevent thread ID pollution
                    context_state.pop('email_metadata', None)
                    # Store last sent thread info for validation of next email
                    context_state['last_sent_thread'] = {
                        'thread_id': thread_id,
                        'message_id': message_id,
                        'subject': subject,
                        'timestamp': now_utc().isoformat()
                    }
                    logger.info(f"📧 Email sent and context cleared. Thread: {thread_id}")
                    print()
                else:
                    print_text("❌ Failed to send\n", style="red")

                continue

            if not user_input.strip():
                continue

            # Reset current_images for each new message (fix bug where images from previous message persist)
            current_images = []

            # Reset loaded image paths for this message
            context_state['loaded_image_paths'] = []

            # Auto-detect image paths in regular messages
            # Only skip detection for actual commands, not absolute file paths
            # Commands are short words like /quit, /model, etc.
            # File paths have more path separators like /var/folders/...
            input_stripped = user_input.strip()

            # Check if it's a command: starts with / and next part is a known command word
            # File paths like /var/folders/... have more slashes, commands like /model don't
            is_command = False
            if input_stripped.startswith('/'):
                # Quick check: if there's another / in the first 20 chars, it's definitely a path
                first_chars = input_stripped[:20]
                if first_chars.count('/') > 1:
                    # Has multiple slashes like /var/folders/..., definitely a file path
                    is_command = False
                else:
                    # Extract the first word after / (respecting escaped spaces)
                    words = _split_respecting_escaped_spaces(input_stripped)
                    if words:
                        # Get the command part (everything after the first /)
                        command_with_slash = words[0]
                        command_part = command_with_slash[1:] if command_with_slash.startswith('/') else command_with_slash

                        # Known command prefixes
                        known_commands = {'quit', 'debug', 'push', 's', 'e', 'help', 'm', 'artifact',
                                        'edit', 'image', 'model', 'temp', 'save', 'mcp'}
                        # Check if it matches any known command
                        if command_part.lower() in known_commands:
                            is_command = True

            if is_command:
                cleaned_message = input_stripped
                detected_files = []
            else:
                cleaned_message, detected_files = _detect_file_paths_in_message(input_stripped)

            if detected_files:
                try:
                    from promaia.utils.image_processing import (
                        encode_image_from_path, is_vision_supported, get_model_image_limits,
                        process_document_for_gemini
                    )

                    # Separate images from documents
                    image_files = [(path, ftype) for path, ftype in detected_files if ftype == 'image']
                    document_files = [(path, ftype) for path, ftype in detected_files if ftype == 'document']

                    # Process images
                    if image_files:
                        # Check if current model supports vision
                        if not is_vision_supported(current_api):
                            print_text(f"📸 Detected {len(image_files)} image path(s) but {current_api} doesn't support images.", style="bold yellow")
                            print_text("Try switching to a vision-capable model with '/model' or use text-only.", style="dim")
                        else:
                            # Get model limits
                            model_limits = get_model_image_limits(current_api)
                            max_images = model_limits['max_images']

                            # Limit images to model capacity
                            if len(image_files) > max_images:
                                print_text(f"📸 Detected {len(image_files)} images, but {current_api.title()} supports max {max_images}. Processing first {max_images}.", style="bold yellow")
                                image_files = image_files[:max_images]

                            # Try to encode detected images
                            successful_images = []
                            successful_image_paths = []

                            for image_path, _ in image_files:
                                try:
                                    # Unescape shell characters in path (\=, \ , etc.)
                                    actual_path = _shell_unescape(image_path)
                                    # Translate host paths for Docker
                                    actual_path = _resolve_host_path(actual_path)

                                    if os.path.exists(actual_path):
                                        # Use File API for Gemini if needed, otherwise base64
                                        if current_api == 'gemini':
                                            from promaia.utils.image_processing import process_image_for_gemini
                                            encoded_image = process_image_for_gemini(actual_path)
                                        else:
                                            encoded_image = encode_image_from_path(actual_path)
                                        successful_images.append(encoded_image)
                                        successful_image_paths.append(actual_path)
                                    else:
                                        print_text(f"📸 Image path not found: {actual_path}", style="dim yellow")
                                        if "TemporaryItems" in actual_path or "NSIRD_" in actual_path:
                                            print_text("   macOS temp screenshot files are deleted quickly. Save to ~/Desktop first.", style="dim")
                                except Exception as img_error:
                                    print_text(f"❌ Failed to load detected image: {actual_path if 'actual_path' in locals() else image_path} - {img_error}", style="bold red")

                            if successful_images:
                                current_images = successful_images
                                context_state['loaded_image_paths'] = successful_image_paths
                                user_input = cleaned_message  # Use cleaned message without file paths
                                # Show single confirmation line
                                img_word = "image" if len(successful_images) == 1 else "images"
                                print_text(f"📸 {len(successful_images)} {img_word} loaded", style="bold green")

                    # Process documents (currently only for Gemini)
                    if document_files:
                        if current_api == 'gemini':
                            for doc_path, _ in document_files:
                                try:
                                    # Unescape shell characters in path
                                    actual_path = _shell_unescape(doc_path)

                                    if os.path.exists(actual_path):
                                        # Upload document to Gemini File API
                                        doc_data = process_document_for_gemini(actual_path)
                                        current_images.append(doc_data)
                                        print_text(f"📄 Document loaded: {doc_data['display_name']}", style="bold green")
                                    else:
                                        print_text(f"📄 Document path not found: {actual_path}", style="dim yellow")
                                except Exception as doc_error:
                                    print_text(f"❌ Failed to load document: {actual_path if 'actual_path' in locals() else doc_path} - {doc_error}", style="bold red")

                            # Update user input to remove file paths
                            if document_files and not image_files:
                                user_input = cleaned_message
                        else:
                            print_text(f"📄 Detected {len(document_files)} document(s) but {current_api} doesn't support documents yet.", style="bold yellow")
                            print_text("Document support is currently available with Gemini. Switch with '/model gemini'.", style="dim")

                except Exception as e:
                    print_text(f"Error processing detected files: {e}", style="bold red")
                    # Continue with original message
                    pass
            
            # Prepare message with potential images
            if current_images:
                debug_print(f"Processing message with {len(current_images)} images")
            
            messages.append({"role": "user", "content": user_input, "images": current_images})

            # Auto-save messages in draft mode after user input
            if draft_id and mode:
                try:
                    from promaia.mail.draft_manager import DraftManager
                    draft_manager = DraftManager()
                    draft_manager.save_chat_messages(draft_id, messages)
                    logger.info(f"💾 Auto-saved {len(messages)} messages after user input for draft {draft_id}")
                except Exception as e:
                    logger.error(f"Failed to auto-save messages after user input: {e}", exc_info=True)
            elif draft_id:
                logger.warning(f"⚠️  Draft mode detected but mode object is None - cannot auto-save")

            # Automatic email intent detection
            if not draft_id and not context_state.get('enable_email_send', False):
                try:
                    from promaia.mail.intent_detector import EmailIntentDetector

                    detector = EmailIntentDetector()
                    intent = detector.detect_intent(user_input, messages[:-1])  # Pass conversation without current message

                    # If high-confidence email intent detected, auto-enable email mode
                    if intent.has_intent and intent.confidence >= 0.85:
                        logger.info(f"📧 Email intent detected: {intent.intent_type} (confidence: {intent.confidence:.2f})")
                        logger.info(f"   Reasoning: {intent.reasoning}")

                        # Auto-load Gmail context if not already loaded
                        current_sources = context_state.get('sources') or []
                        has_gmail = any('gmail' in str(s).lower() for s in current_sources)

                        if not has_gmail:
                            print_text(f"\n📧 Email intent detected - loading Gmail context...", style="cyan")

                            # Load Gmail sources (replicate /mail command logic exactly)
                            try:
                                from promaia.config.databases import get_database_manager
                                from promaia.config.workspaces import get_workspace_manager

                                db_manager = get_database_manager()
                                workspace_manager = get_workspace_manager()

                                # Determine which workspaces to load from
                                workspaces_to_load = [workspace] if workspace else workspace_manager.list_workspaces()

                                # Find all Gmail databases from selected workspaces
                                gmail_sources = []
                                mail_from_accounts = []

                                for ws in workspaces_to_load:
                                    gmail_dbs = [
                                        db for db in db_manager.get_workspace_databases(ws)
                                        if db.source_type == "gmail"
                                    ]
                                    for gmail_db in gmail_dbs:
                                        # Add with workspace prefix and 7 days of history
                                        if ws == 'default':
                                            gmail_sources.append(f"{gmail_db.nickname}:7")
                                        else:
                                            gmail_sources.append(f"{ws}.{gmail_db.nickname}:7")

                                        # Track account info for /send
                                        mail_from_accounts.append({
                                            'workspace': ws,
                                            'email': gmail_db.database_id,
                                            'display': f"{gmail_db.database_id} ({ws})" if ws != 'default' else gmail_db.database_id
                                        })

                                if gmail_sources:
                                    # Store available accounts
                                    context_state['mail_from_accounts'] = mail_from_accounts

                                    # Add Gmail sources to current sources (don't replace)
                                    current_sources = context_state.get('sources') or []
                                    new_sources = list(set(current_sources + gmail_sources))  # Deduplicate
                                    context_state['sources'] = new_sources

                                    # Reload context with Gmail data
                                    if reload_context():
                                        print_text(f"✅ Gmail context loaded ({len(gmail_sources)} sources)", style="green")
                                    else:
                                        print_text("⚠️  Failed to reload context with Gmail data", style="yellow")
                                else:
                                    print_text("⚠️  No Gmail accounts configured", style="yellow")

                            except Exception as e:
                                logger.error(f"Failed to auto-load Gmail context: {e}", exc_info=True)
                                print_text(f"⚠️  Could not load Gmail context: {e}", style="yellow")
                        else:
                            # Gmail already loaded (e.g., from browse), just setup accounts
                            if not context_state.get('mail_from_accounts'):
                                try:
                                    from promaia.config.databases import get_database_manager
                                    from promaia.config.workspaces import get_workspace_manager

                                    db_manager = get_database_manager()
                                    workspace_manager = get_workspace_manager()

                                    # Determine which workspaces to check
                                    workspaces_to_check = [workspace] if workspace else workspace_manager.list_workspaces()

                                    # Find all Gmail databases
                                    mail_from_accounts = []
                                    for ws in workspaces_to_check:
                                        gmail_dbs = [
                                            db for db in db_manager.get_workspace_databases(ws)
                                            if db.source_type == "gmail"
                                        ]
                                        for gmail_db in gmail_dbs:
                                            mail_from_accounts.append({
                                                'workspace': ws,
                                                'email': gmail_db.database_id,
                                                'display': f"{gmail_db.database_id} ({ws})" if ws != 'default' else gmail_db.database_id
                                            })

                                    context_state['mail_from_accounts'] = mail_from_accounts
                                    logger.info(f"📧 Setup {len(mail_from_accounts)} mail accounts for sending")
                                except Exception as e:
                                    logger.error(f"Failed to setup mail accounts: {e}", exc_info=True)

                        # Enable email send mode
                        context_state['enable_email_send'] = True
                        logger.info("✅ Email mode auto-enabled")

                except Exception as e:
                    logger.error(f"Error in email intent detection: {e}", exc_info=True)
                    # Continue with regular chat if intent detection fails

            # Call the appropriate API
            response_content = None
            try:
                if DEBUG_MODE:
                    debug_print(f"AI Call Debug: System prompt length: {len(system_prompt)}")
                    debug_print(f"AI Call Debug: Total pages in context: {total_pages_loaded}")
                    debug_print(f"AI Call Debug: Context sources: {context_state.get('sources')}")
                
                # Check for images in current message
                current_message_images = []
                if messages and "images" in messages[-1]:
                    current_message_images = messages[-1].get("images", [])
                    if current_message_images:
                        img_word = "image" if len(current_message_images) == 1 else "images"
                        print_text(f"📸 Processing {len(current_message_images)} {img_word} with {current_api.title()}...", style="bold green")
                        
                    # Clean messages for existing handlers (but keep full messages for image processing)
                    clean_messages = []
                    for msg in messages:
                        clean_msg = {"role": msg["role"], "content": msg["content"]}
                        clean_messages.append(clean_msg)
                    messages_for_api = messages  # Keep full messages for image handlers
                else:
                    messages_for_api = messages

                # Add email sending instructions to system prompt if enabled
                current_system_prompt = system_prompt
                if context_state.get('enable_email_send', False):
                    # Load maia_mail_prompt.md for JSON artifact format instructions
                    from promaia.utils.env_writer import get_prompts_dir
                    mail_prompt_path = str(get_prompts_dir() / "maia_mail_prompt.md")

                    try:
                        with open(mail_prompt_path, 'r') as f:
                            mail_prompt_content = f.read()

                        # Add date/time context using replace to avoid issues with JSON {} in examples
                        from promaia.utils.timezone_utils import now_local
                        today = now_local().strftime('%Y-%m-%d')
                        current_time = now_local().strftime('%H:%M')

                        mail_prompt_content = mail_prompt_content.replace('{today_date}', today)
                        mail_prompt_content = mail_prompt_content.replace('{current_time}', current_time)

                        email_instructions = f"""

## EMAIL COMPOSITION MODE

{mail_prompt_content}

Gmail threads are loaded in your context. Search them to find recipients and threads.

The user will type `/send` when ready to send the email.
"""
                        current_system_prompt = system_prompt + email_instructions
                    except Exception as e:
                        logger.error(f"Failed to load maia_mail_prompt.md: {e}")
                        # Fallback: just add basic instructions
                        current_system_prompt = system_prompt + "\n\n## EMAIL MODE\nCompose emails as JSON artifacts. User will type /send to send."

                # Agentic mode: use autonomous multi-tool loop
                if context_state.get('agentic_mode') and current_api == "anthropic" and anthropic_client:
                    try:
                        import asyncio
                        from promaia.chat.agentic_adapter import run_agentic_turn
                        debug_print("Entering agentic loop...")

                        clean_msgs = []
                        for msg in messages:
                            cm = {"role": msg["role"], "content": msg["content"]}
                            if msg.get("images"):
                                cm["images"] = msg["images"]
                            clean_msgs.append(cm)

                        result = asyncio.run(run_agentic_turn(
                            system_prompt=current_system_prompt,
                            messages=clean_msgs,
                            workspace=context_state.get('workspace') or context_state.get('resolved_workspace') or "",
                            mcp_tools=context_state.get('agentic_tools', []),
                            databases=context_state.get('agentic_databases', []),
                            print_text_fn=print_text,
                        ))

                        response_text = result.response_text
                        total_tokens = result.input_tokens + result.output_tokens

                        from promaia.utils.ai import calculate_ai_cost
                        cost_data = calculate_ai_cost(result.input_tokens, result.output_tokens, "claude-sonnet-4")

                        tools_used = list({tc.get("name", "?") for tc in result.tool_calls_made})
                        model_info = f"Claude Sonnet (agentic: {result.iterations_used} iters"
                        if tools_used:
                            model_info += f", tools: {', '.join(tools_used)}"
                        model_info += ")"

                        response_content = {
                            'text': response_text,
                            'tokens': {
                                'prompt_tokens': result.input_tokens,
                                'response_tokens': result.output_tokens,
                                'total_tokens': total_tokens,
                                'cost': cost_data["total_cost"],
                                'model': model_info,
                            }
                        }
                        # Track sources loaded by agentic query_source calls
                        _track_agentic_query_sources(result.tool_calls_made, context_state)

                    except Exception as e:
                        logger.error(f"Agentic turn failed: {e}", exc_info=True)
                        print_text(f"\n⚠️  Agentic mode error ({type(e).__name__}): {e}", style="bold red")
                        print_text("   Falling back to standard call\n", style="dim yellow")

                # Direct API calls (streaming removed for reliability)
                if current_api == "anthropic" and anthropic_client and response_content is None:
                    # Define regenerate callback for query tool iterations
                    def regenerate_anthropic_response(updated_system_prompt):
                        """Regenerate Anthropic response with updated context."""
                        try:
                            # Use current messages (not messages_for_api) to get fresh conversation state
                            if not messages or len(messages) == 0:
                                logger.warning("messages is None or empty, cannot regenerate")
                                print_text("⚠️  Cannot regenerate response with empty message history", style="yellow")
                                return None

                            if current_message_images:
                                formatted_messages = _format_anthropic_with_images(messages, current_message_images)
                                regen_response = call_anthropic(anthropic_client, updated_system_prompt, formatted_messages, temperature=current_temperature)
                            else:
                                clean_messages = []
                                for msg in messages:
                                    if msg and isinstance(msg, dict) and msg.get("content"):
                                        clean_msg = {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                                        clean_messages.append(clean_msg)

                                # Check if we have any valid messages after filtering
                                if not clean_messages:
                                    logger.warning("No valid messages after cleaning, cannot regenerate")
                                    print_text("⚠️  Cannot regenerate response with invalid message history", style="yellow")
                                    return None

                                regen_response = call_anthropic(anthropic_client, updated_system_prompt, clean_messages, temperature=current_temperature)

                            if regen_response and regen_response.content:
                                return regen_response.content[0].text
                            return None
                        except Exception as e:
                            logger.error(f"Error regenerating Anthropic response: {e}")
                            print_text(f"⚠️  Error regenerating response: {e}", style="yellow")
                            return None

                    # Proactive context trimming
                    from promaia.agents.context_trimmer import trim_context_to_fit_sync
                    current_system_prompt, messages_for_api = trim_context_to_fit_sync(
                        current_system_prompt, messages_for_api
                    )

                    if current_message_images:
                        # Handle images with Anthropic
                        formatted_messages = _format_anthropic_with_images(messages_for_api, current_message_images)
                        response = call_anthropic(anthropic_client, current_system_prompt, formatted_messages, temperature=current_temperature)
                    else:
                        # Regular text-only message - clean messages to remove extra fields
                        clean_messages = []
                        for msg in messages_for_api:
                            clean_msg = {"role": msg["role"], "content": msg["content"]}
                            clean_messages.append(clean_msg)
                        response = call_anthropic(anthropic_client, current_system_prompt, clean_messages, temperature=current_temperature)
                    if response and response.content:
                        response_text = response.content[0].text

                        # Execute MCP and query tools if present
                        import asyncio
                        response_text = asyncio.run(execute_all_tools_with_iteration(response_text, regenerate_callback=regenerate_anthropic_response))

                        # Extract token usage for Anthropic
                        if hasattr(response, 'usage'):
                            input_tokens = response.usage.input_tokens
                            output_tokens = response.usage.output_tokens
                            total_tokens = input_tokens + output_tokens

                            # Calculate cost using centralized function
                            from promaia.utils.ai import calculate_ai_cost
                            debug_print(f"Cost calculation: input_tokens={input_tokens}, output_tokens={output_tokens}")
                            cost_data = calculate_ai_cost(input_tokens, output_tokens, "claude-sonnet-4")
                            total_cost = cost_data["total_cost"]
                            debug_print(f"Calculated cost: ${total_cost:.6f}")

                            debug_print(f"Token usage: {input_tokens:,} input + {output_tokens:,} output = {total_tokens:,} total")

                            response_content = {
                                'text': response_text,
                                'tokens': {
                                    'prompt_tokens': input_tokens,
                                    'response_tokens': output_tokens,
                                    'total_tokens': total_tokens,
                                    'cost': total_cost,
                                    'model': 'Claude 3 Sonnet'
                                }
                            }
                        else:
                            response_content = {
                                'text': response_text,
                                'tokens': None
                            }
                elif current_api == "openai" and openai_client:
                    # Define regenerate callback for query tool iterations
                    def regenerate_openai_response(updated_system_prompt):
                        """Regenerate OpenAI response with updated context."""
                        try:
                            # Use current messages (not messages_for_api) to get fresh conversation state
                            if not messages:
                                logger.warning("messages is None or empty, cannot regenerate")
                                return None

                            if current_message_images:
                                formatted_messages = _format_openai_with_images(updated_system_prompt, messages, current_message_images)
                            else:
                                formatted_messages = [{"role": "system", "content": updated_system_prompt}] + messages

                            # Use selected model ID if available
                            selected_model = os.getenv("SELECTED_MODEL_ID")
                            openai_model = selected_model if (selected_model and "gpt" in selected_model.lower()) else "gpt-4o"

                            regen_response = openai_client.chat.completions.create(
                                model=openai_model,
                                messages=formatted_messages,
                                max_tokens=4096,
                                temperature=current_temperature
                            )

                            if regen_response.choices:
                                return regen_response.choices[0].message.content
                            return None
                        except Exception as e:
                            logger.error(f"Error regenerating OpenAI response: {e}")
                            return None

                    if current_message_images:
                        # Handle images with OpenAI
                        formatted_messages = _format_openai_with_images(current_system_prompt, messages_for_api, current_message_images)
                    else:
                        # Regular text-only message
                        formatted_messages = [{"role": "system", "content": current_system_prompt}] + messages_for_api

                    # Use selected model ID if available
                    selected_model = os.getenv("SELECTED_MODEL_ID")
                    openai_model = selected_model if (selected_model and "gpt" in selected_model.lower()) else "gpt-4o"

                    response = openai_client.chat.completions.create(
                        model=openai_model,
                        messages=formatted_messages,
                        max_tokens=4096,
                        temperature=current_temperature
                    )
                    if response.choices:
                        response_text = response.choices[0].message.content

                        # Execute MCP and query tools if present
                        import asyncio
                        response_text = asyncio.run(execute_all_tools_with_iteration(response_text, regenerate_callback=regenerate_openai_response))

                        # Extract token usage for OpenAI
                        if hasattr(response, 'usage') and response.usage:
                            prompt_tokens = response.usage.prompt_tokens
                            completion_tokens = response.usage.completion_tokens
                            total_tokens = response.usage.total_tokens

                            # Calculate cost using centralized function
                            from promaia.utils.ai import calculate_ai_cost
                            cost_data = calculate_ai_cost(prompt_tokens, completion_tokens, "gpt-4o")
                            total_cost = cost_data["total_cost"]

                            debug_print(f"Token usage: {prompt_tokens:,} prompt + {completion_tokens:,} completion = {total_tokens:,} total")

                            response_content = {
                                'text': response_text,
                                'tokens': {
                                    'prompt_tokens': prompt_tokens,
                                    'response_tokens': completion_tokens,
                                    'total_tokens': total_tokens,
                                    'cost': total_cost,
                                    'model': 'GPT-4'
                                }
                            }
                        else:
                            response_content = {
                                'text': response_text,
                                'tokens': None
                            }
                elif current_api == "gemini" and gemini_client:
                    # Define regenerate callback for query tool iterations
                    def regenerate_gemini_response(updated_system_prompt):
                        """Regenerate Gemini response with updated context."""
                        try:
                            # Use current messages (not messages_for_api) to get fresh conversation state
                            if not messages:
                                logger.warning("messages is None or empty, cannot regenerate")
                                return None

                            if current_message_images:
                                current_gemini_model, gemini_messages = _format_gemini_with_images(updated_system_prompt, messages, current_message_images)
                                regen_response = current_gemini_model.generate_content(
                                    contents=gemini_messages,
                                    generation_config={"temperature": current_temperature}
                                )
                            else:
                                formatted_prompt = f"System: {updated_system_prompt}\n\nConversation:\n"
                                for msg in messages:
                                    if msg and isinstance(msg, dict):
                                        formatted_prompt += f"{msg.get('role', 'user').title()}: {msg.get('content', '')}\n"
                                regen_response = gemini_client.generate_content(formatted_prompt)

                            if regen_response.text:
                                return regen_response.text
                            return None
                        except Exception as e:
                            logger.error(f"Error regenerating Gemini response: {e}")
                            return None

                    response_text_with_tools = None
                    try:
                        if current_message_images:
                            # Handle images with Gemini
                            current_gemini_model, gemini_messages = _format_gemini_with_images(current_system_prompt, messages_for_api, current_message_images)
                            response = current_gemini_model.generate_content(
                                contents=gemini_messages,
                                generation_config={
                                    "temperature": current_temperature,
                                }
                            )
                        else:
                            # Regular text-only message
                            formatted_prompt = f"System: {current_system_prompt}\n\nConversation:\n"
                            for msg in messages_for_api:
                                formatted_prompt += f"{msg['role'].title()}: {msg['content']}\n"
                            response = gemini_client.generate_content(formatted_prompt)
                        if response.text:
                            response_text = response.text

                            # Execute MCP and query tools if present
                            import asyncio
                            response_text_with_tools = asyncio.run(execute_all_tools_with_iteration(response_text, regenerate_callback=regenerate_gemini_response))
                        else:
                            response_text_with_tools = f"I encountered an error: No response text generated. Please try again."
                    except Exception as e:
                        error_msg = str(e)
                        debug_print(f"Error calling Gemini API: {error_msg}")
                        debug_print(f"Gemini error traceback: {traceback.format_exc()}")
                        
                        # Check for specific Gemini error types
                        if "quota" in error_msg.lower() or "rate" in error_msg.lower():
                            response_text_with_tools = f"I encountered a rate limit issue with Gemini: {error_msg}. Please try switching to Anthropic with 'maia model' command."
                        elif "blocked" in error_msg.lower() or "safety" in error_msg.lower():
                            response_text_with_tools = f"I encountered a content filter issue: {error_msg}. Please try rephrasing your question."
                        elif "api" in error_msg.lower() and "key" in error_msg.lower():
                            response_text_with_tools = f"I encountered an API key issue: {error_msg}. Please check your GOOGLE_API_KEY environment variable."
                        elif "model" in error_msg.lower() and ("not found" in error_msg.lower() or "unavailable" in error_msg.lower()):
                            response_text_with_tools = f"I encountered a model availability issue: {error_msg}. The Gemini model may be temporarily unavailable. Please try switching to Anthropic with 'maia model' command."
                        elif "500" in error_msg or "internal" in error_msg.lower():
                            # Try fallback to Anthropic for 500 errors
                            if anthropic_client:
                                debug_print("Attempting fallback to Anthropic due to Gemini 500 error")
                                try:
                                    fallback_messages = [{"role": "user", "content": formatted_prompt}]
                                    fallback_response = call_anthropic(anthropic_client, "", fallback_messages)
                                    if fallback_response and fallback_response.content and len(fallback_response.content) > 0:
                                        response_text_with_tools = fallback_response.content[0].text
                                        debug_print("Successfully fell back to Anthropic")
                                    else:
                                        response_text_with_tools = f"I encountered a server error with Gemini: {error_msg}. Fallback to Anthropic also failed. Please try again or switch models with 'maia model' command."
                                except Exception as fallback_error:
                                    debug_print(f"Fallback to Anthropic failed: {fallback_error}")
                                    response_text_with_tools = f"I encountered a server error with Gemini: {error_msg}. Fallback to Anthropic also failed. Please try again or switch models with 'maia model' command."
                            else:
                                response_text_with_tools = f"I encountered a server error with Gemini: {error_msg}. Please try switching to Anthropic with 'maia model' command."
                        else:
                            response_text_with_tools = f"I encountered an error with Gemini: {error_msg}. Please try again, or switch to Anthropic with 'maia model' command."
                    
                    if response_text_with_tools:
                        response_content = {
                            'text': response_text_with_tools,
                            'tokens': None
                        }
                        
                        # Extract and display token usage for Gemini (moved outside except block)
                        if 'response' in locals() and hasattr(response, 'usage_metadata') and response.usage_metadata:
                            prompt_tokens = response.usage_metadata.prompt_token_count
                            completion_tokens = response.usage_metadata.candidates_token_count
                            total_tokens = response.usage_metadata.total_token_count

                            # Calculate cost using centralized function
                            from promaia.utils.ai import calculate_ai_cost
                            debug_print(f"Cost calculation: prompt_tokens={prompt_tokens}, completion_tokens={completion_tokens}")
                            
                            # Determine Gemini model for pricing (use short context pricing for now)
                            gemini_model = "gemini-2.5-pro-short" if total_tokens <= 128000 else "gemini-2.5-pro-long"
                            cost_data = calculate_ai_cost(prompt_tokens, completion_tokens, gemini_model)
                            total_cost = cost_data["total_cost"]
                            debug_print(f"Calculated cost: ${total_cost:.6f}")

                            debug_print(f"Token usage: {prompt_tokens:,} prompt + {completion_tokens:,} completion = {total_tokens:,} total")

                            response_content = {
                                'text': response_text_with_tools,
                                'tokens': {
                                    'prompt_tokens': prompt_tokens,
                                    'response_tokens': completion_tokens,
                                    'total_tokens': total_tokens,
                                    'cost': total_cost,
                                    'model': 'Gemini 2.5 Pro'
                                }
                            }
                elif current_api == "llama":
                    # Ensure llama client is initialized with current environment
                    if not llama_client:
                        # Force reload environment to ensure LLAMA_API_KEY is available
                        from promaia.utils.config import load_environment
                        load_environment()
                        initialize_llama_client()

                    if llama_client:
                        # Define regenerate callback for query tool iterations
                        def regenerate_llama_response(updated_system_prompt):
                            """Regenerate Llama response with updated context."""
                            try:
                                # Use current messages (not messages_for_api) to get fresh conversation state
                                if not messages:
                                    logger.warning("messages is None or empty, cannot regenerate")
                                    return None

                                if current_message_images:
                                    formatted_messages = _format_llama_with_images(updated_system_prompt, messages, current_message_images)
                                else:
                                    formatted_messages = [{"role": "system", "content": updated_system_prompt}] + messages

                                model_name = os.getenv("LLAMA_DEFAULT_MODEL", LLAMA_MODELS.get("llama3", "llama3:latest"))
                                regen_response = llama_client.chat.completions.create(
                                    model=model_name,
                                    messages=formatted_messages,
                                    max_tokens=4096,
                                    temperature=current_temperature
                                )

                                if regen_response.choices:
                                    return regen_response.choices[0].message.content
                                return None
                            except Exception as e:
                                logger.error(f"Error regenerating Llama response: {e}")
                                return None

                        if current_message_images:
                            # Handle images with Llama
                            formatted_messages = _format_llama_with_images(system_prompt, messages_for_api, current_message_images)
                        else:
                            # Regular text-only message
                            formatted_messages = [{"role": "system", "content": system_prompt}] + messages_for_api
                        model_name = os.getenv("LLAMA_DEFAULT_MODEL", LLAMA_MODELS.get("llama3", "llama3:latest"))

                        try:
                            response = llama_client.chat.completions.create(
                                model=model_name,
                                messages=formatted_messages,
                                max_tokens=4096,
                                temperature=current_temperature
                            )
                            if response.choices:
                                response_text = response.choices[0].message.content

                                # Execute MCP and query tools if present
                                import asyncio
                                response_text = asyncio.run(execute_all_tools_with_iteration(response_text, regenerate_callback=regenerate_llama_response))

                                # Extract token usage for local Llama if available
                                if hasattr(response, 'usage') and response.usage:
                                    prompt_tokens = response.usage.prompt_tokens
                                    completion_tokens = response.usage.completion_tokens
                                    total_tokens = response.usage.total_tokens

                                    debug_print(f"Token usage: {prompt_tokens:,} prompt + {completion_tokens:,} completion = {total_tokens:,} total")

                                    response_content = {
                                        'text': response_text,
                                        'tokens': {
                                            'prompt_tokens': prompt_tokens,
                                            'response_tokens': completion_tokens,
                                            'total_tokens': total_tokens,
                                            'cost': 0.0,  # Local models are free
                                            'model': f'Local Llama ({model_name})'
                                        }
                                    }
                                else:
                                    response_content = {
                                        'text': response_text,
                                        'tokens': None
                                    }
                        except Exception as e:
                            debug_print(f"Error calling local Llama: {e}")
                            response_content = f"Error calling local Llama: {e}"
                    else:
                        response_content = "Local Llama client not available. Check server connection."
                elif response_content is None:
                    print_text(f"Error: {current_api} API client not available.", style="bold red")
                    continue

                # Handle API responses
                if response_content:
                    # Lazy-initialize artifact manager
                    if context_state['artifact_manager'] is None:
                        from promaia.chat.artifacts import ArtifactManager
                        context_state['artifact_manager'] = ArtifactManager()
                    
                    artifact_manager = context_state['artifact_manager']
                    
                    if isinstance(response_content, dict):
                        # AI response with token data
                        response_text = response_content['text']
                        token_data = response_content.get('tokens')

                        timestamp = get_local_timestamp()
                        metadata_parts = [f"{timestamp} Maia"]
                        if token_data:
                            metadata_parts.append(f"{token_data['prompt_tokens']:,}, {token_data['response_tokens']:,}, {token_data['total_tokens']:,}")
                            metadata_parts.append(f"${token_data['cost']:.6f}")

                        # Print each metadata part on its own line
                        print()
                        if metadata_parts:
                            print_text(metadata_parts[0])  # Print timestamp line without dim
                        for part in metadata_parts[1:]:
                            print_text(part, style="dim")  # Print rest of metadata with dim
                        print()

                        # Check if response should be an artifact
                        last_user_message = messages[-1]['content'] if messages else ""

                        # Check if mode forces all responses to be artifacts
                        force_artifacts = mode and mode.should_force_artifacts()

                        # Check if this is an artifact update or new artifact
                        is_artifact = False
                        if artifact_manager.should_update_artifact(last_user_message) and artifact_manager.last_artifact_id:
                            # Update existing artifact
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                            artifact_manager.update_artifact(artifact_manager.last_artifact_id, artifact_content)
                            is_artifact = True

                            # Sync artifact metadata with draft DB if in draft mode
                            if draft_id and mode:
                                from promaia.chat.modes import DraftMode
                                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                                if isinstance(mode, DraftMode):
                                    _, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_manager.last_artifact_id)
                                    if metadata:
                                        update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                        # Reload draft_data to reflect updates
                                        mode.draft_data = mode.draft_manager.get_draft(draft_id)

                            # Display commentary if present
                            if commentary:
                                print_markdown(commentary)
                                print()

                            # Display updated artifact
                            print(artifact_manager.render_artifact(artifact_manager.last_artifact_id))
                        elif force_artifacts or artifact_manager.should_create_artifact(last_user_message, response_text):
                            # Create new artifact (forced by mode or detected by keywords)
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                            artifact_id = artifact_manager.create_artifact(artifact_content)
                            is_artifact = True

                            # Sync artifact metadata with draft DB if in draft mode
                            if draft_id and mode:
                                from promaia.chat.modes import DraftMode
                                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                                if isinstance(mode, DraftMode):
                                    _, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_id)
                                    if metadata:
                                        update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                        # Reload draft_data to reflect updates
                                        mode.draft_data = mode.draft_manager.get_draft(draft_id)

                            # Extract email metadata if in mail mode
                            if context_state.get('enable_email_send', False):
                                import re
                                # Parse artifact for email fields - only extract if properly formatted
                                # Look for Subject: at the start of a line (not in the middle of text)
                                subject_match = re.search(r'^Subject:\s*(.+)$', artifact_content, re.MULTILINE)
                                to_match = re.search(r'^To:\s*(.+)$', artifact_content, re.MULTILINE)
                                thread_match = re.search(r'^Thread:\s*(.+)$', artifact_content, re.MULTILINE)
                                message_id_match = re.search(r'^Message-ID:\s*(.+)$', artifact_content, re.MULTILINE)

                                # Parse attachments - support both old [file] format and new bullet list format
                                attachments = []
                                # Try new format first: "📎 Attachments:" followed by "- /path" lines
                                attachments_section = re.search(r'📎 Attachments:\s*\n((?:^-\s+.+$\n?)+)', artifact_content, re.MULTILINE)
                                if attachments_section:
                                    # Extract each "- /path" line
                                    attachment_lines = re.findall(r'^-\s+(.+)$', attachments_section.group(1), re.MULTILINE)
                                    attachments = [line.strip() for line in attachment_lines]
                                else:
                                    # Fall back to old format: "Attachments: [/path]"
                                    old_format = re.search(r'^Attachments:\s*\[(.+?)\]', artifact_content, re.MULTILINE)
                                    if old_format:
                                        attachments = [old_format.group(1).strip()]

                                # Only save metadata if we have both Subject and To
                                if subject_match and to_match:
                                    recipient_text = to_match.group(1).strip()
                                    subject_text = subject_match.group(1).strip()

                                    # Make sure we didn't accidentally parse something wrong
                                    # Subject should not start with "To:" or contain email-like pattern at the start
                                    if not subject_text.startswith('To:') and '@' not in subject_text[:20]:
                                        context_state['email_metadata'] = {
                                            'recipient': recipient_text,
                                            'subject': subject_text,
                                            'attachments': attachments,
                                            'thread_id': thread_match.group(1).strip() if thread_match else None,
                                            'message_id': message_id_match.group(1).strip() if message_id_match else None,
                                            'artifact_id': artifact_id
                                        }
                                        debug_print(f"Extracted email metadata: {context_state['email_metadata']}")
                                    else:
                                        debug_print(f"Skipping malformed email metadata - subject looks wrong: {subject_text}")

                            # Display commentary if present
                            if commentary:
                                print_markdown(commentary)
                                print()

                            # Display artifact
                            print(artifact_manager.render_artifact(artifact_id))

                            # Show send prompt for email artifacts
                            if context_state.get('enable_email_send', False):
                                artifact = artifact_manager.get_artifact(artifact_id)
                                if artifact and artifact.get('type') == 'email':
                                    print()
                                    print_text("💡 Ready to send? Type /send to review and send this email", style="cyan")
                                    print()
                        else:
                            # Normal response (no artifact)
                            print_markdown(response_text)

                        # Save message with artifact tags if it was an artifact
                        # This ensures artifacts can be reconstructed on reload
                        if is_artifact and not has_artifact_tags(response_text):
                            # Wrap in artifact tags for persistence
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_text)
                            saved_content = f"<artifact>{artifact_content}</artifact>"
                            if commentary:
                                saved_content = f"{commentary}\n\n{saved_content}"
                            messages.append({"role": "assistant", "content": saved_content})
                        else:
                            messages.append({"role": "assistant", "content": response_text})

                        # Auto-save messages in draft mode after each response
                        if draft_id and mode:
                            try:
                                from promaia.mail.draft_manager import DraftManager
                                draft_manager = DraftManager()
                                draft_manager.save_chat_messages(draft_id, messages)
                                logger.info(f"💾 Auto-saved {len(messages)} messages after AI response for draft {draft_id}")
                            except Exception as e:
                                logger.error(f"Failed to auto-save messages after AI response: {e}", exc_info=True)
                        elif draft_id:
                            logger.warning(f"⚠️  Draft mode detected but mode object is None - cannot auto-save")

                    else:
                        # String response (fallback for responses without token data)
                        timestamp = get_local_timestamp()
                        print()
                        print_text(f"{timestamp} Maia") # No style
                        print()
                        
                        # Check if response should be an artifact
                        last_user_message = messages[-1]['content'] if messages else ""

                        # Check if mode forces all responses to be artifacts
                        force_artifacts = mode and mode.should_force_artifacts()

                        # Check if this is an artifact update or new artifact
                        is_artifact = False
                        if artifact_manager.should_update_artifact(last_user_message) and artifact_manager.last_artifact_id:
                            # Update existing artifact
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_content)
                            artifact_manager.update_artifact(artifact_manager.last_artifact_id, artifact_content)
                            is_artifact = True

                            # Sync artifact metadata with draft DB if in draft mode
                            if draft_id and mode:
                                from promaia.chat.modes import DraftMode
                                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                                if isinstance(mode, DraftMode):
                                    _, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_manager.last_artifact_id)
                                    if metadata:
                                        update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                        mode.draft_data = mode.draft_manager.get_draft(draft_id)

                            # Display commentary if present
                            if commentary:
                                print_markdown(commentary)
                                print()

                            # Display updated artifact
                            print(artifact_manager.render_artifact(artifact_manager.last_artifact_id))
                        elif force_artifacts or artifact_manager.should_create_artifact(last_user_message, response_content):
                            # Create new artifact (forced by mode or detected by keywords)
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_content)
                            artifact_id = artifact_manager.create_artifact(artifact_content)
                            is_artifact = True

                            # Sync artifact metadata with draft DB if in draft mode
                            if draft_id and mode:
                                from promaia.chat.modes import DraftMode
                                from promaia.mail.artifact_helpers import extract_email_metadata_from_artifact, update_draft_with_artifact_metadata
                                if isinstance(mode, DraftMode):
                                    _, metadata = extract_email_metadata_from_artifact(artifact_manager, artifact_id)
                                    if metadata:
                                        update_draft_with_artifact_metadata(mode.draft_manager, draft_id, metadata)
                                        mode.draft_data = mode.draft_manager.get_draft(draft_id)

                            # Display commentary if present
                            if commentary:
                                print_markdown(commentary)
                                print()

                            # Display artifact
                            print(artifact_manager.render_artifact(artifact_id))
                        else:
                            # Normal response (no artifact)
                            print_markdown(response_content)

                        # Check for email draft creation if email send is enabled
                        if context_state.get('enable_email_send', False) and has_email_draft_tags(response_content):
                            print_text("\n📧 Creating email draft...", style="bold cyan")
                            draft_data = extract_email_draft_data(response_content)

                            if draft_data:
                                # Use loaded image paths as attachments if available
                                # This ensures images that were processed by the AI are also attached to the email
                                loaded_images = context_state.get('loaded_image_paths', [])
                                if loaded_images:
                                    # Combine AI-suggested attachments with loaded images
                                    ai_attachments = draft_data.get('attachments', [])
                                    all_attachments = list(set(loaded_images + ai_attachments))  # Deduplicate
                                    draft_data['attachments'] = all_attachments
                                    debug_print(f"📎 Adding {len(loaded_images)} loaded image(s) as attachments")
                                else:
                                    # Fallback: Validate attachment paths against actual files in conversation
                                    draft_attachments = draft_data.get('attachments', [])
                                    if draft_attachments:
                                        # Find all file paths mentioned in user messages
                                        mentioned_files = []
                                        for msg in messages:
                                            if msg.get('role') == 'user':
                                                content = msg.get('content', '')
                                                # Look for absolute paths
                                                import re
                                                file_pattern = r'(/[^\s]+\.[a-zA-Z]{2,4})'
                                                mentioned_files.extend(re.findall(file_pattern, content))

                                        # Check if draft attachments match mentioned files
                                        invalid_attachments = []
                                        for attachment in draft_attachments:
                                            if attachment and attachment not in mentioned_files:
                                                invalid_attachments.append(attachment)

                                        if invalid_attachments:
                                            print_text(f"⚠️  Warning: AI generated incorrect attachment paths:", style="bold yellow")
                                            for path in invalid_attachments:
                                                print_text(f"   - {path}", style="yellow")
                                            print_text(f"   Expected one of: {', '.join(mentioned_files) if mentioned_files else 'none'}", style="yellow")
                                            print_text("   Using empty attachments instead.", style="yellow")
                                            draft_data['attachments'] = []

                                try:
                                    from promaia.mail.email_send_helpers import EmailSendHelper

                                    # Get workspace, defaulting to 'default' if None
                                    workspace = context_state.get('workspace') or 'default'
                                    helper = EmailSendHelper(workspace=workspace)

                                    # Create the draft
                                    draft_id_email = helper.create_draft_from_info(
                                        recipient=draft_data.get('recipient', ''),
                                        subject=draft_data.get('subject', ''),
                                        message_body=draft_data.get('message_body', ''),
                                        thread_id=draft_data.get('thread_id'),
                                        message_id=draft_data.get('message_id'),
                                        attachments=draft_data.get('attachments', []),
                                        context_info={'created_from_chat': True}
                                    )

                                    print_text(f"✅ Draft created successfully! (ID: {draft_id_email})", style="bold green")
                                    print_text("📝 Opening draft in email interface...", style="cyan")

                                    # Save current conversation before switching modes
                                    if messages:
                                        try:
                                            from promaia.storage.chat_history import ChatHistoryManager
                                            history_manager = ChatHistoryManager()

                                            # Create a serializable copy of context_state (remove non-JSON objects)
                                            serializable_context = {
                                                k: v for k, v in context_state.items()
                                                if k not in ['artifact_manager', 'mcp_client', 'mcp_executor', 'mode']
                                            }

                                            history_manager.save_thread(
                                                messages=messages,
                                                context=serializable_context,
                                                thread_name=f"Email to {draft_data.get('recipient', 'recipient')}"
                                            )
                                        except Exception as e:
                                            logger.error(f"Failed to save chat before launching draft: {e}")

                                    # Launch draft chat interface
                                    from promaia.mail.email_send_helpers import launch_draft_chat_for_email
                                    launch_draft_chat_for_email(draft_id_email, workspace)

                                    # Exit current chat session after launching draft chat
                                    return

                                except Exception as e:
                                    print_text(f"❌ Failed to create email draft: {e}", style="bold red")
                                    logger.error(f"Email draft creation failed: {e}", exc_info=True)

                        # Save message with artifact tags if it was an artifact
                        # This ensures artifacts can be reconstructed on reload
                        if is_artifact and not has_artifact_tags(response_content):
                            # Wrap in artifact tags for persistence
                            artifact_content, commentary = artifact_manager.extract_artifact_content(response_content)
                            saved_content = f"<artifact>{artifact_content}</artifact>"
                            if commentary:
                                saved_content = f"{commentary}\n\n{saved_content}"
                            messages.append({"role": "assistant", "content": saved_content})
                        else:
                            messages.append({"role": "assistant", "content": response_content})

                        # Auto-save messages in draft mode after each response
                        if draft_id and mode:
                            try:
                                from promaia.mail.draft_manager import DraftManager
                                draft_manager = DraftManager()
                                draft_manager.save_chat_messages(draft_id, messages)
                                logger.info(f"💾 Auto-saved {len(messages)} messages after AI response for draft {draft_id}")
                            except Exception as e:
                                logger.error(f"Failed to auto-save messages after AI response: {e}", exc_info=True)
                        elif draft_id:
                            logger.warning(f"⚠️  Draft mode detected but mode object is None - cannot auto-save")

                else:
                    print_text("Error: No response generated.", style="bold red")

            except Exception as e:
                # Check for connection/network errors
                error_str = str(e).lower()
                if any(keyword in error_str for keyword in ['connection', 'network', 'timeout', 'unreachable', 'failed to connect']):
                    print_text(f"\n❌ Connection Error: Unable to reach {current_api.title()} API", style="bold red")
                    print_text(f"   Please check your internet connection and try again.", style="yellow")
                    debug_print(f"Connection error details: {e}")
                else:
                    print_text(f"Error calling {current_api} API: {e}", style="bold red")
                    debug_print(f"Full API error: {e}")

        except KeyboardInterrupt:
            # Save chat messages if in draft mode
            if draft_id and mode:
                try:
                    from promaia.mail.draft_manager import DraftManager
                    draft_manager = DraftManager()
                    draft_manager.save_chat_messages(draft_id, messages)
                    logger.info(f"💾 Saved {len(messages)} chat messages for draft {draft_id}")
                except Exception as e:
                    logger.error(f"Failed to save chat messages: {e}")
            # Auto-save conversation to history (if not in draft mode and has messages)
            elif messages and len([m for m in messages if m.get('role') != 'system']) > 0:
                try:
                    from promaia.storage.chat_history import ChatHistoryManager
                    history_manager = ChatHistoryManager()
                    
                    thread_context = {
                        'sources': context_state.get('sources'),
                        'filters': context_state.get('filters'),
                        'workspace': context_state.get('workspace'),
                        'resolved_workspace': context_state.get('resolved_workspace'),
                        'query_command': context_state.get('query_command'),
                        'sql_query_prompt': context_state.get('sql_query_prompt'),
                        'sql_query_content': context_state.get('sql_query_content'),
                        'original_query_format': context_state.get('original_query_format'),
                        'browse_selections': context_state.get('browse_selections'),
                        'vector_search_queries': context_state.get('vector_search_queries'),
                        'is_vector_search': context_state.get('is_vector_search')
                    }
                    
                    current_thread_id = context_state.get('current_thread_id')
                    if current_thread_id:
                        history_manager.update_thread(
                            thread_id=current_thread_id,
                            messages=messages,
                            context=thread_context
                        )
                    else:
                        thread_id = history_manager.save_thread(
                            messages=messages,
                            context=thread_context
                        )
                        context_state['current_thread_id'] = thread_id
                    
                    print_text("\n💾 Conversation auto-saved to history", style="dim green")
                except Exception as e:
                    debug_print(f"Auto-save error (non-fatal): {e}")

            print_text("\nGoodbye!", style="bold cyan")
            break
        except EOFError:
            # Save chat messages if in draft mode
            if draft_id and mode:
                try:
                    from promaia.mail.draft_manager import DraftManager
                    draft_manager = DraftManager()
                    draft_manager.save_chat_messages(draft_id, messages)
                    logger.info(f"💾 Saved {len(messages)} chat messages for draft {draft_id}")
                except Exception as e:
                    logger.error(f"Failed to save chat messages: {e}")
            # Auto-save conversation to history (if not in draft mode and has messages)
            elif messages and len([m for m in messages if m.get('role') != 'system']) > 0:
                try:
                    from promaia.storage.chat_history import ChatHistoryManager
                    history_manager = ChatHistoryManager()
                    
                    thread_context = {
                        'sources': context_state.get('sources'),
                        'filters': context_state.get('filters'),
                        'workspace': context_state.get('workspace'),
                        'resolved_workspace': context_state.get('resolved_workspace'),
                        'query_command': context_state.get('query_command'),
                        'sql_query_prompt': context_state.get('sql_query_prompt'),
                        'sql_query_content': context_state.get('sql_query_content'),
                        'original_query_format': context_state.get('original_query_format'),
                        'browse_selections': context_state.get('browse_selections'),
                        'vector_search_queries': context_state.get('vector_search_queries'),
                        'is_vector_search': context_state.get('is_vector_search')
                    }
                    
                    current_thread_id = context_state.get('current_thread_id')
                    if current_thread_id:
                        history_manager.update_thread(
                            thread_id=current_thread_id,
                            messages=messages,
                            context=thread_context
                        )
                    else:
                        thread_id = history_manager.save_thread(
                            messages=messages,
                            context=thread_context
                        )
                        context_state['current_thread_id'] = thread_id
                    
                    print_text("\n💾 Conversation auto-saved to history", style="dim green")
                except Exception as e:
                    debug_print(f"Auto-save error (non-fatal): {e}")

            print_text("\nGoodbye!", style="bold cyan")
            break

    # Return messages list so caller can save final state
    return messages

# CLI Image Helper Functions

def _format_anthropic_with_images(messages_for_api, current_message_images):
    """Format Anthropic messages with image support."""
    from promaia.utils.image_processing import format_image_for_anthropic
    
    # Build message history
    formatted_messages = []
    total_images = 0
    
    # Add all previous messages (including their images)
    for msg in messages_for_api[:-1]:  # Exclude current message
        if msg.get("images"):
            # Message has images - format as multimodal content
            msg_content = []
            
            # Add text if present
            if msg.get("content"):
                msg_content.append({"type": "text", "text": msg["content"]})
            
            # Add all images from this message
            for img in msg["images"]:
                msg_content.append(format_image_for_anthropic(img["data"], img["media_type"]))
                total_images += 1
            
            formatted_messages.append({
                "role": msg["role"],
                "content": msg_content
            })
        else:
            # Text-only message
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Add current user message with images
    current_content = []
    
    # Add text if present
    if messages_for_api:
        last_msg = messages_for_api[-1]
        if last_msg.get("content"):
            current_content.append({"type": "text", "text": last_msg["content"]})
    
    # Add current message images
    for img in current_message_images:
        current_content.append(format_image_for_anthropic(img["data"], img["media_type"]))
        total_images += 1
    
    formatted_messages.append({
        "role": "user",
        "content": current_content
    })
    
    debug_print(f"Calling Anthropic with {len(formatted_messages)} messages and {total_images} total images ({len(current_message_images)} current)")
    return formatted_messages

def _format_openai_with_images(system_prompt, messages_for_api, current_message_images):
    """Format OpenAI messages with image support."""
    from promaia.utils.image_processing import format_image_for_openai
    
    # Start with system message
    formatted_messages = [{"role": "system", "content": system_prompt}]
    total_images = 0
    
    # Add all previous messages (including their images)
    for msg in messages_for_api[:-1]:  # Exclude current message
        if msg.get("images"):
            # Message has images - format as multimodal content
            msg_content = []
            
            # Add text if present
            if msg.get("content"):
                msg_content.append({"type": "text", "text": msg["content"]})
            
            # Add all images from this message
            for img in msg["images"]:
                msg_content.append(format_image_for_openai(img["data"], img["media_type"]))
                total_images += 1
            
            formatted_messages.append({
                "role": msg["role"],
                "content": msg_content
            })
        else:
            # Text-only message
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Add current user message with images
    current_content = []
    
    # Add text if present
    if messages_for_api:
        last_msg = messages_for_api[-1]
        if last_msg.get("content"):
            current_content.append({"type": "text", "text": last_msg["content"]})
    
    # Add current message images
    for img in current_message_images:
        current_content.append(format_image_for_openai(img["data"], img["media_type"]))
        total_images += 1
    
    formatted_messages.append({
        "role": "user", 
        "content": current_content if current_content else "Please analyze the image"
    })
    
    debug_print(f"Calling OpenAI with {len(formatted_messages)} messages and {total_images} total images ({len(current_message_images)} current)")
    return formatted_messages

def _format_gemini_with_images(system_prompt, messages_for_api, current_message_images):
    """Format Gemini content with image support (base64 and File API)."""
    from promaia.utils.image_processing import format_image_for_gemini
    import google.generativeai as genai
    from promaia.nlq.models import get_current_google_model
    import os

    # Gemini uses a different approach - we need to create a model with system instruction
    # and then format the conversation with images

    # Use selected model ID if available, otherwise use default
    selected_model = os.getenv("SELECTED_MODEL_ID")
    if selected_model and "gemini" in selected_model.lower():
        model_name = selected_model
    else:
        model_name = get_current_google_model()

    current_gemini_model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_prompt
    )

    # Build conversation history
    gemini_messages = []
    total_images = 0
    file_api_count = 0

    # Add previous messages (including their images)
    for msg in messages_for_api[:-1]:  # Exclude current message
        role = 'user' if msg['role'] == 'user' else 'model'

        if msg.get("images"):
            # Message has images - include text and images
            msg_parts = []

            # Add text if present
            if msg.get("content"):
                msg_parts.append(msg["content"])

            # Add all images from this message
            for img in msg["images"]:
                # Handle both base64 and File API formats
                if img.get("method") == "file_api" and img.get("file_uri"):
                    msg_parts.append(format_image_for_gemini(file_uri=img["file_uri"]))
                    file_api_count += 1
                else:
                    msg_parts.append(format_image_for_gemini(img["data"], img["media_type"]))
                total_images += 1

            gemini_messages.append({'role': role, 'parts': msg_parts})
        else:
            # Text-only message
            gemini_messages.append({'role': role, 'parts': [msg['content']]})

    # Add current user message with images
    current_parts = []

    # Add text if present
    if messages_for_api:
        last_msg = messages_for_api[-1]
        if last_msg.get("content"):
            current_parts.append(last_msg["content"])

    # Add current message images
    for img in current_message_images:
        # Handle both base64 and File API formats
        if img.get("method") == "file_api" and img.get("file_uri"):
            current_parts.append(format_image_for_gemini(file_uri=img["file_uri"]))
            file_api_count += 1
        else:
            current_parts.append(format_image_for_gemini(img["data"], img["media_type"]))
        total_images += 1

    gemini_messages.append({'role': 'user', 'parts': current_parts})

    method_info = f" ({file_api_count} via File API)" if file_api_count > 0 else " (all base64)"
    debug_print(f"Calling Gemini with {len(gemini_messages)} messages and {total_images} total images{method_info} ({len(current_message_images)} current)")
    return current_gemini_model, gemini_messages

def _format_llama_with_images(system_prompt, messages_for_api, current_message_images):
    """Format Llama messages with image support (OpenAI-compatible format)."""
    from promaia.utils.image_processing import format_image_for_llama
    
    # Start with system message
    formatted_messages = [{"role": "system", "content": system_prompt}]
    
    # Find the first image across all messages (most local vision models support only 1 image)
    first_image = None
    first_image_msg = None
    total_images_available = 0
    
    # Check historical messages for images
    for msg in messages_for_api[:-1]:  # Exclude current message
        if msg.get("images") and not first_image:
            first_image = msg["images"][0]  # Take first image
            first_image_msg = msg
        if msg.get("images"):
            total_images_available += len(msg["images"])
    
    # Check current message for images
    if current_message_images:
        total_images_available += len(current_message_images)
        if not first_image:
            first_image = current_message_images[0]
    
    # Add all previous messages (text only, except the one with the first image)
    for msg in messages_for_api[:-1]:  # Exclude current message
        if msg.get("images") and msg == first_image_msg:
            # This message has the first image we're using - format as multimodal
            msg_content = []
            
            # Add text if present
            if msg.get("content"):
                msg_content.append({"type": "text", "text": msg["content"]})
            
            # Add first image
            msg_content.append(format_image_for_llama(first_image["data"], first_image["media_type"]))
            
            formatted_messages.append({
                "role": msg["role"],
                "content": msg_content
            })
        else:
            # Text-only message or message with images we're not using
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Add current user message
    current_content = []
    
    # Add text if present
    if messages_for_api:
        last_msg = messages_for_api[-1]
        if last_msg.get("content"):
            current_content.append({"type": "text", "text": last_msg["content"]})
    
    # Add image only if we haven't used one from history
    if current_message_images and not first_image_msg:
        current_content.append(format_image_for_llama(current_message_images[0]["data"], current_message_images[0]["media_type"]))
    
    formatted_messages.append({
        "role": "user",
        "content": current_content if current_content else "Please analyze the image"
    })
    
    images_used = 1 if first_image else 0
    debug_print(f"Calling Llama with {len(formatted_messages)} messages and {images_used} image (from {total_images_available} available)")
    return formatted_messages

def main():
    """Entry point for the chat interface."""
    import argparse

    parser = argparse.ArgumentParser(description="Interactive chat with Maia")
    parser.add_argument("-s", "--sources", nargs="+", help="Data sources to load")
    parser.add_argument("-f", "--filters", nargs="+", help="Filters to apply to sources")
    parser.add_argument("-w", "--workspace", help="Workspace to use")
    parser.add_argument("--non-interactive", action="store_true", help="Run in non-interactive mode")

    args = parser.parse_args()

    chat(
        sources=args.sources,
        filters=args.filters,
        workspace=args.workspace,
        non_interactive=args.non_interactive
    )

if __name__ == "__main__":
    main()
