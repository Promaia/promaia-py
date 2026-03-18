"""
Prompt Editor Chat - AI-assisted iteration on maia mail prompts.

Provides a chat-like interface where the user converses with an AI
that understands the prompt's structure and can modify it via tools.
"""
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from rich import box

logger = logging.getLogger(__name__)

_console = Console()

# ── Monkey-patch prompt_toolkit for Shift+Enter / Ctrl+Enter support ──
# Terminals normally send the same byte for Enter regardless of modifiers.
# Modern terminals (kitty, WezTerm, Windows Terminal 1.25+) support the
# "kitty keyboard protocol" which sends distinct escape sequences.
# prompt_toolkit already has these sequences but maps them all to plain Enter.
# We remap them to a custom key name so we can bind newline-insert to it.
_NEWLINE_KEY = "shift-enter"  # custom key name for our binding
_kitty_protocol_activated = False

def _patch_prompt_toolkit_keys():
    """Patch prompt_toolkit to recognize Shift+Enter and Ctrl+Enter."""
    # Patch _parse_key to accept our custom key name in key bindings
    try:
        from prompt_toolkit.key_binding import key_bindings as _kb_module
        _orig_parse_key = _kb_module._parse_key

        def _patched_parse_key(key):
            if key == _NEWLINE_KEY:
                return key
            return _orig_parse_key(key)

        _kb_module._parse_key = _patched_parse_key
    except Exception:
        logger.debug("Failed to patch _parse_key for custom key", exc_info=True)

    # Patch ANSI escape sequences to map Shift/Ctrl+Enter to our custom key
    try:
        from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
        # xterm modifyOtherKeys sequences (already in dict, mapped to ControlM)
        ANSI_SEQUENCES["\x1b[27;2;13~"] = _NEWLINE_KEY  # Shift+Enter
        ANSI_SEQUENCES["\x1b[27;5;13~"] = _NEWLINE_KEY  # Ctrl+Enter
        ANSI_SEQUENCES["\x1b[27;6;13~"] = _NEWLINE_KEY  # Ctrl+Shift+Enter
        # Kitty keyboard protocol sequences (not in dict by default)
        ANSI_SEQUENCES["\x1b[13;2u"] = _NEWLINE_KEY     # Shift+Enter
        ANSI_SEQUENCES["\x1b[13;5u"] = _NEWLINE_KEY     # Ctrl+Enter
        ANSI_SEQUENCES["\x1b[13;6u"] = _NEWLINE_KEY     # Ctrl+Shift+Enter
    except Exception:
        logger.debug("Failed to patch ANSI sequences for Shift+Enter", exc_info=True)

    # Patch Win32 input path: add Shift+Enter to the shift-key mapping
    try:
        from prompt_toolkit.input.win32 import ConsoleInputReader
        from prompt_toolkit.keys import Keys
        _orig_process = ConsoleInputReader._process_key_event

        def _patched_process(self, ev):
            result = _orig_process(self, ev)
            # If Shift was held and the key is Enter (ControlM), remap it
            if ev.ControlKeyState & self.SHIFT_PRESSED:
                from prompt_toolkit.input.win32 import KeyPress
                return [
                    KeyPress(_NEWLINE_KEY, k.data) if k.key == Keys.ControlM else k
                    for k in result
                ]
            return result

        ConsoleInputReader._process_key_event = _patched_process
    except Exception:
        logger.debug("Failed to patch Win32 input for Shift+Enter", exc_info=True)


def _activate_kitty_protocol():
    """Send kitty keyboard protocol opt-in sequence to the terminal."""
    global _kitty_protocol_activated
    try:
        import sys
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(b"\x1b[>1u")
            sys.stdout.buffer.flush()
            _kitty_protocol_activated = True
    except Exception:
        logger.debug("Failed to activate kitty keyboard protocol", exc_info=True)


def _deactivate_kitty_protocol():
    """Send kitty keyboard protocol opt-out sequence to the terminal."""
    global _kitty_protocol_activated
    if not _kitty_protocol_activated:
        return
    try:
        import sys
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(b"\x1b[<u")
            sys.stdout.buffer.flush()
            _kitty_protocol_activated = False
    except Exception:
        pass


# Apply patches at import time
_patch_prompt_toolkit_keys()

# ── Tool definitions (XML-style, matching codebase conventions) ──────

TOOL_NAMES = {"rewrite_prompt", "replace_text", "save_and_exit", "exit_without_saving"}

TOOLS_DESCRIPTION = """
You have the following tools available. Call them using XML tags in your response.
You may include commentary text before or after the tool call.

## rewrite_prompt
Replace the entire prompt with new content.
<tool_call>
  <tool_name>rewrite_prompt</tool_name>
  <parameters>
    <content>...the full new prompt text...</content>
  </parameters>
</tool_call>

## replace_text
Find and replace a specific string in the prompt (literal match, not regex).
<tool_call>
  <tool_name>replace_text</tool_name>
  <parameters>
    <find>exact text to find</find>
    <replace>replacement text</replace>
  </parameters>
</tool_call>

## save_and_exit
Save the current prompt and exit the editor. Call this when the user is satisfied.
<tool_call>
  <tool_name>save_and_exit</tool_name>
  <parameters></parameters>
</tool_call>

## exit_without_saving
Discard all changes and exit. Call this when the user wants to cancel.
<tool_call>
  <tool_name>exit_without_saving</tool_name>
  <parameters></parameters>
</tool_call>
"""

# ── Context descriptions for each prompt type ────────────────────────

CLASSIFICATION_CONTEXT = """
## What this prompt controls

This is a **classification prompt** for maia mail. It controls how incoming emails
are triaged — deciding which emails need a response, which are spam, and which
can be skipped.

## Template variables

These are filled in automatically when the prompt is used. They MUST appear in
the prompt using `{variable_name}` syntax:

- `{user_email}` — The user's email address
- `{workspace}` — The workspace name
- `{from_addr}` — Sender's email address
- `{to_addr}` — Recipient(s)
- `{subject}` — Email subject line
- `{date}` — Email date
- `{body}` — Email body (truncated to 1000 chars)
- `{thread_context}` — Previous messages in the thread

## Required output format

The AI that uses this prompt MUST return a JSON object with exactly these fields.
The prompt must instruct the AI to return ONLY this JSON — no extra text.

## How each field affects behavior

These fields combine to determine what happens to the email. Understanding this
helps you guide the user toward rules that produce the outcomes they want.

- `is_spam` (boolean) — **Highest priority filter.** If `true`, the email is
  immediately skipped — no draft is generated, no further checks run. Use this
  for newsletters, promotional emails, automated notifications, etc.

- `addressed_to_user` (boolean or `"ambiguous"`) — If `false`, the email is
  skipped (e.g., CC'd on a thread that doesn't need their input). If
  `"ambiguous"`, the email gets an "unsure" status — a draft IS generated but
  it's flagged for the user to review whether they actually need to respond.
  This is the only way to produce the "unsure" status.

- `pertains_to_me` (boolean) — If `false`, the email is skipped with no draft.
  Use this for emails that reached the user but aren't relevant to them (e.g.,
  mailing lists they don't participate in, mis-routed messages).

- `requires_response` (boolean) — If `false`, the email is skipped. This is for
  emails that are relevant and addressed to the user, but don't need a reply
  (e.g., FYI messages, read receipts, shipping confirmations, calendar updates).

- `reasoning` (string) — Stored for display in the review UI so the user can
  understand why an email was classified a certain way. No behavioral effect —
  purely informational.

### Status summary

| All pass + addressed=true | → **pending** — full draft generated, ready to review/send |
| All pass + addressed="ambiguous" | → **unsure** — draft generated but flagged for review |
| Any field triggers skip | → **skipped** — no draft body, visible but deprioritized |
"""

GENERATION_CONTEXT = """
## What this prompt controls

This is a **generation prompt** (persona prompt) for maia mail. It defines the AI's
persona, tone, and style when drafting email replies on behalf of the user.

## Template variables

These are filled in automatically when the prompt is used. They MUST appear in
the prompt using `{variable_name}` syntax:

- `{today_date}` — Current date (e.g., "March 17, 2026")
- `{current_time}` — Current time (e.g., "02:30 PM UTC")

## Additional context provided at runtime

The AI that uses this prompt will also receive (appended automatically by the system,
NOT part of this prompt file):
- Relevant documents from the user's knowledge base (vector search results)
- Related email threads (similar past conversations)
- The full email thread conversation (if multi-message)
- The specific email being replied to (from, to, cc, date, subject, body)

## Expected output

The AI should produce an email body — no subject line (added automatically).
The prompt should define the user's communication style, any standard sign-offs,
and rules for different email types.

## How this prompt is used

The generation prompt is the AI's persona when drafting replies. The user reviews
every draft before it's sent — nothing goes out automatically. This means the
prompt should focus on getting the tone and style close enough that the user only
needs minor edits, not rewrites. Common things users customize:

- Communication style (formal vs casual, verbose vs concise)
- Sign-off preferences (e.g., "Best," vs "Thanks," vs none)
- Rules for specific email types (e.g., "keep scheduling replies to one sentence")
- Context about their role or responsibilities that affects how they'd reply
- Things to avoid (e.g., "never use exclamation marks", "don't apologize unnecessarily")
"""


def _build_system_prompt(prompt_type: str) -> str:
    """Build the system prompt for the editor AI."""
    type_context = CLASSIFICATION_CONTEXT if prompt_type == "classification" else GENERATION_CONTEXT

    return f"""You are a prompt engineering assistant helping the user iterate on a maia mail prompt.

Your job is to understand what the user wants and modify the prompt accordingly using the
tools provided. After each modification, briefly explain what you changed and ask if they
want further adjustments.

{type_context}

{TOOLS_DESCRIPTION}

## Guidelines

- When you first see the prompt, briefly acknowledge what it does and ask what the user
  wants to change. If it's clearly a starter template, mention that and suggest what
  they might want to customize.
- Always preserve the template variables — they are required for the system to work.
- For classification prompts: always ensure the output format instruction (JSON with the
  required fields) is present.
- For generation prompts: keep it focused on persona/style. Don't include context
  sections — those are added automatically at runtime.
- Use `rewrite_prompt` for large changes and `replace_text` for small, targeted edits.
- When the user seems satisfied or says something like "done", "looks good", "save it",
  call `save_and_exit`.
- When the user says "cancel", "nevermind", "discard", call `exit_without_saving`.
- Keep your responses concise. Show the key changes, not the entire prompt every time.
- HTML comments (<!-- -->) are stripped before the prompt is used. You can include them
  as documentation, but they are not required.
"""


def _parse_tool_calls(response: str) -> List[Dict[str, Any]]:
    """Parse tool calls from AI response text."""
    tool_calls = []
    pattern = r'<tool_call>(.*?)</tool_call>'
    matches = re.findall(pattern, response, re.DOTALL)

    for match in matches:
        name_match = re.search(r'<tool_name>(.*?)</tool_name>', match, re.DOTALL)
        if not name_match:
            continue
        tool_name = name_match.group(1).strip()
        if tool_name not in TOOL_NAMES:
            continue

        params = {}
        params_match = re.search(r'<parameters>(.*?)</parameters>', match, re.DOTALL)
        if params_match:
            params_content = params_match.group(1)
            # Extract each parameter tag — use non-greedy and allow multiline content
            for param_match in re.finditer(r'<(\w+)>(.*?)</\1>', params_content, re.DOTALL):
                params[param_match.group(1)] = param_match.group(2)

        tool_calls.append({"tool_name": tool_name, "parameters": params})

    return tool_calls


def _strip_tool_calls(response: str) -> str:
    """Remove tool call XML from response text, leaving commentary."""
    cleaned = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
    # Collapse multiple blank lines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _display_prompt(content: str, label: str = "Current Prompt"):
    """Display the prompt content in a grey panel."""
    panel = Panel(
        Markdown(content),
        title=f"[bold]{label}[/bold]",
        title_align="left",
        border_style="grey50",
        box=box.ROUNDED,
        padding=(1, 2),
    )
    _console.print()
    _console.print(panel)
    _console.print()


def _make_assistant_panel(content):
    """Create an assistant panel from text or renderable content."""
    return Panel(
        content,
        title="[bold purple]Promaia[/bold purple]",
        title_align="left",
        border_style="dark_violet",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def _display_assistant(text: str):
    """Display assistant response in a purple panel with markdown rendering."""
    _console.print()
    _console.print(_make_assistant_panel(Markdown(text)))


def _display_user_header():
    """Display an open-bottom input header for the user."""
    w = _console.width
    title_str = " You "
    inner = w - 2  # space inside corners
    title_segment = f"─{title_str}"
    remaining = inner - len(title_segment)
    top_line = f"[dark_orange]╭[bold]{title_segment}[/bold]{'─' * remaining}╮[/dark_orange]"
    hint = "  Enter to send, Ctrl+Enter or Ctrl+J for newline. Ask to save or discard when done."
    hint_padded = hint.ljust(inner)
    hint_line = f"[dark_orange]▼[/dark_orange][dim]{hint_padded}[/dim][dark_orange]▼[/dark_orange]"
    _console.print()
    _console.print(top_line)
    _console.print(hint_line)


def _display_user_footer():
    """Display the closing bottom of the user input box."""
    w = _console.width
    inner = w - 2
    empty_line = f"[dark_orange]│[/dark_orange]{' ' * inner}[dark_orange]│[/dark_orange]"
    bottom_line = f"[dark_orange]╰{'─' * inner}╯[/dark_orange]"
    _console.print(empty_line)
    _console.print(bottom_line)


def _get_ai_client():
    """Get Anthropic AI client."""
    from anthropic import Anthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY required for prompt editor")
    return Anthropic(
        api_key=api_key,
        base_url=os.environ.get("ANTHROPIC_BASE_URL"),
        max_retries=5,
    )


async def edit_prompt_with_ai(filepath: Path, content: str, prompt_type: str) -> Optional[str]:
    """Run the AI-assisted prompt editor chat loop.

    Args:
        filepath: Path to the prompt file
        content: Current prompt content
        prompt_type: "classification" or "generation"

    Returns:
        Updated prompt content if saved, None if cancelled
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.key_binding import KeyBindings

    # Key bindings: Enter sends, Shift+Enter / Ctrl+Enter / Ctrl+J for newline
    kb = KeyBindings()

    @kb.add('enter')
    def _submit(event):
        event.current_buffer.validate_and_handle()

    try:
        @kb.add(_NEWLINE_KEY)  # Shift+Enter / Ctrl+Enter (patched sequences)
        def _newline_modified(event):
            event.current_buffer.insert_text('\n')
    except ValueError:
        pass  # _parse_key patch didn't take — Ctrl+J fallback still works

    @kb.add('c-j')  # Ctrl+J — universal fallback for newline
    def _newline_fallback(event):
        event.current_buffer.insert_text('\n')

    session = PromptSession(key_bindings=kb, multiline=True)
    client = _get_ai_client()
    system_prompt = _build_system_prompt(prompt_type)
    current_content = content

    # Build initial message showing the current prompt
    initial_message = f"Here is the current {prompt_type} prompt:\n\n```\n{content}\n```\n\nWhat would you like to change?"

    messages = [{"role": "user", "content": initial_message}]

    _console.print()
    _console.rule(f"[bold cyan]Prompt Editor — {filepath.name}[/bold cyan]")
    _console.print("[dim]  Ctrl+C to exit at any time.[/dim]")

    _display_prompt(current_content)

    _activate_kitty_protocol()
    try:
        return await _editor_loop(session, client, system_prompt, messages, current_content)
    finally:
        _deactivate_kitty_protocol()


async def _editor_loop(session, client, system_prompt, messages, current_content):
    """Inner chat loop for the prompt editor."""
    while True:
        # ── AI turn (with typing indicator) ─────────────────────
        _console.print()
        typing_panel = _make_assistant_panel(
            Text.assemble(("  Typing...", "dim italic purple"))
        )
        try:
            with Live(typing_panel, console=_console, refresh_per_second=4) as live:
                response = await asyncio.to_thread(
                    lambda: client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=system_prompt,
                        messages=messages,
                    )
                )
                ai_text = response.content[0].text
                tool_calls = _parse_tool_calls(ai_text)
                commentary = _strip_tool_calls(ai_text)
                # Clear typing indicator before rendering results
                live.update(Text(""))
        except Exception as e:
            _console.print(f"\n  [red]Error calling AI: {e}[/red]\n")
            break

        should_exit = False
        saved = False

        # Execute tool calls first — prompt changes display before commentary
        for tc in tool_calls:
            name = tc["tool_name"]
            params = tc["parameters"]

            if name == "rewrite_prompt":
                new_content = params.get("content", "")
                if new_content:
                    current_content = new_content
                    _display_prompt(current_content, "Updated Prompt")
                else:
                    _console.print("  [yellow](rewrite_prompt called with empty content — skipped)[/yellow]")

            elif name == "replace_text":
                find = params.get("find", "")
                replace = params.get("replace", "")
                if find and find in current_content:
                    current_content = current_content.replace(find, replace)
                    _display_prompt(current_content, "Updated Prompt")
                elif find:
                    _console.print(f"  [yellow](replace_text: \"{find[:50]}...\" not found in prompt)[/yellow]")

            elif name == "save_and_exit":
                saved = True
                should_exit = True

            elif name == "exit_without_saving":
                should_exit = True

        # Show AI commentary after prompt changes
        if commentary:
            _display_assistant(commentary)

        if should_exit:
            if saved:
                _console.print("  [green]Saving prompt...[/green]")
                return current_content
            else:
                _console.print("  [dim]Changes discarded.[/dim]")
                return None

        # Add AI response to history
        messages.append({"role": "assistant", "content": ai_text})

        # ── User turn ────────────────────────────────────────────
        try:
            _display_user_header()
            print()
            user_input = await session.prompt_async("  ")
            _display_user_footer()
            user_input = user_input.strip()
        except (EOFError, KeyboardInterrupt):
            _console.print("\n  [dim]Cancelled.[/dim]\n")
            return None

        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
