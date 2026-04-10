# Maia Mail Full Chat Integration - Implementation Plan

## Overview

Enhance draft chat to have full maia chat capabilities while maintaining draft-specific features and copy-friendly UI principles.

## Architecture Decision

After analysis, the chat interface is **functional**, not class-based. It uses a `context_state` dictionary rather than class instances. This means we need a hybrid approach:

### Hybrid Integration Strategy

**Keep:** 
- Existing `DraftChatInterface` class for artifact/draft management
- All existing draft commands (/send, /d, /archive)
- Artifact rendering and display logic

**Add:**
- Message context loading from draft log files  
- Context editing via `/e` command (integrates with chat's edit logic)
- Custom welcome message showing context breakdown
- Support for -mc flag to toggle message context
- Integration with chat's argparse system for flags (-s, -b, -nl, -vs)

## Implementation Steps

### 1. Message Context Loading

**File:** `promaia/mail/draft_chat.py`

Add method to load initial context from draft log file:

```python
def _load_initial_context_from_log(self, draft_id: str) -> Dict[str, Any]:
    """
    Load initial draft context from log file.
    Parses EMAIL HISTORY and PROJECT CONTEXT sections.
    
    Returns dict with:
    - email_docs: List of email thread documents
    - project_docs: List of project context documents  
    - total_count: Total document count
    """
    # Find log file in context_logs/mail_draft_logs/
    # Parse === EMAIL HISTORY === and === PROJECT CONTEXT === sections
    # Extract database names and counts
    # Return structured data
```

### 2. Context State Management

Add to `__init__`:

```python
# Context management (for full chat integration)
self.message_context_enabled = True  # -mc flag state
self.initial_context_docs = []  # Loaded from log
self.additional_sources = []  # From -s flag
self.additional_filters = []  # From -f flag
self.browse_selections = []  # From -b flag
self.nl_prompt = None  # From -nl flag
self.vs_prompt = None  # From -vs flag
```

### 3. Welcome Message

Replace the command list display with:

```python
def _display_welcome_message(self):
    """Display maia mail draft chat welcome with context breakdown"""
    
    # Build command string
    cmd_parts = ["maia mail --draft", self.draft_id]
    if self.message_context_enabled:
        cmd_parts.append("-mc")
    # Add other flags...
    
    print_text("🐙 maia mail draft chat", style="bold magenta")
    print_text(f"Query: {' '.join(cmd_parts)}", style="dim")
    
    # Context breakdown
    print_text("Context loaded:", style="dim")
    if self.message_context_enabled and self.initial_context_docs:
        print_text("\tmessage-context", style="dim")
    
    # Show breakdown by database
    for db_name, count in self._get_context_breakdown().items():
        print_text(f"\t{db_name}: {count}", style="dim")
    
    print_text(f"Model: {model_name}", style="dim")
    print()
    
    # Command list
    print_text("Available commands:", style="dim")
    print_text("  /send [#] - Send draft (default: latest)", style="dim")
    
    # Draft count hint
    if len(self.artifacts) > 1:
        count = len(self.artifacts) - 1
        print_text(f"  /d - Toggle draft list view, 💡 {count} earlier draft(s) hidden", style="dim")
    else:
        print_text("  /d - Toggle draft list view", style="dim")
    
    print_text("  /e - Edit context (sources, filters, message context)", style="dim")
    print_text("  /s - Sync databases in current context", style="dim")
    print_text("  /mcp [name] - Include MCP server context (e.g., /mcp search)", style="dim")
    print_text("  /archive or /a - Archive this email", style="dim")
    print_text("  /q - Return to draft list", style="dim")
    print_text("  /model - Switch model", style="dim")
    print_text("  /help - Show detailed help", style="dim")
    print()
```

### 4. Edit Context Integration

Add `/e` command handler:

```python
async def _handle_edit_context(self):
    """Handle /e command - edit context like maia chat"""
    
    # Build current command string
    cmd_parts = ["--draft", self.draft_id]
    if self.message_context_enabled:
        cmd_parts.append("-mc")
    for source in self.additional_sources:
        cmd_parts.extend(["-s", source])
    # etc...
    
    current_command = " ".join(cmd_parts)
    
    # Show edit UI (same as maia chat)
    print_text("\n🔧 Edit Context", style="bold cyan")
    print_text("Current command:", style="dim")
    print_text(f"  maia mail {current_command}", style="bold")
    
    if self.message_context_enabled:
        print_text("\nMessage Context: ENABLED ✓", style="dim")
    
    print_text("\nOptions:", style="dim")
    print_text("  • Edit command manually (shown below)", style="dim")
    print_text("  • Ctrl+R for recent queries", style="dim")
    print_text("  • Ctrl+B for browse mode", style="dim")
    print_text("  • Press Enter alone to cancel", style="dim")
    print()
    
    # Use prompt_toolkit for editing (same as maia chat)
    from prompt_toolkit import prompt
    from prompt_toolkit.key_binding import KeyBindings
    
    bindings = KeyBindings()
    action_taken = {'type': None}
    
    @bindings.add('c-b')
    def handle_browse(event):
        action_taken['type'] = 'browse'
        event.app.exit()
    
    user_input = prompt(
        "maia mail ",
        default=current_command,
        key_bindings=bindings
    )
    
    if action_taken['type'] == 'browse':
        # Launch browser (reuse existing from chat)
        await self._launch_browse_mode()
    else:
        # Parse edited command
        await self._parse_and_apply_context_edit(user_input)
```

### 5. Browse Mode Integration

Reuse existing browser from chat interface:

```python
async def _launch_browse_mode(self):
    """Launch unified browser (reuse from maia chat)"""
    from promaia.chat.browser import launch_unified_browser
    
    # Call existing browser
    selections = await launch_unified_browser(self.workspace)
    
    if selections:
        self.additional_sources = selections
        await self._reload_context()
```

### 6. Context Reloading

When context is edited, reload pages:

```python
async def _reload_context(self):
    """Reload context after editing"""
    
    # Load pages for additional sources
    # Combine with message context if enabled
    # Update display
    
    self._display_welcome_message()
```

### 7. -mc Flag Support

Parse -mc flag in edit:

```python
def _parse_mc_flag(self, command: str) -> bool:
    """Check if -mc flag is present in command"""
    import shlex
    try:
        args = shlex.split(command)
        return "-mc" in args
    except:
        return False
```

## Safety Guarantees

1. **No modification to chat/interface.py** - only call into it, don't change it
2. **Existing draft_chat.py preserved** - only additions, no breaking changes
3. **Copy-friendly UI** - no boxes, clean text only
4. **Backward compatible** - existing mail flows work unchanged
5. **Isolated changes** - all new code in draft_chat.py

## Testing Plan

1. Test `/e` command opens editor correctly
2. Test Ctrl+B launches browser
3. Test -mc flag toggles message context
4. Test context reload after editing
5. Test welcome message shows correct breakdown
6. Test all flags (-s, -b, -nl, -vs, -mc) work
7. Test existing commands still work (/send, /d, /archive)
8. Test draft refinement still works
9. Test persona always loads

## File Changes Summary

**Modified:**
- `promaia/mail/draft_chat.py` - Add full chat integration

**Created:**
- `docs/MAIA_MAIL_FULL_CHAT.md` - User documentation

**Unchanged:**
- `promaia/chat/interface.py` - No modifications
- All other mail files - No changes

## Next Steps

1. Review this plan
2. Implement incrementally
3. Test each piece
4. Document for users

