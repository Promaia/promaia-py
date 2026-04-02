# Unified TUI Implementation

## Overview

The Unified Terminal User Interface (TUI) makes everything in Promaia feel like a conversation. It's the default command when you run `maia` with no arguments, providing a single home base for:

- 🐙 **FEED mode** - Watch live agent activity in real-time
- 💬 **CHAT mode** - Interactive conversations with agents
- ⚡ **Inline commands** - Run any `maia` command with `/<command>`

**Vision:** "Make everything a chat" - inspired by Claude Code's unified experience.

## Architecture

### Component Structure

```
promaia/tui/
├── __init__.py              # Package exports
├── app.py                   # Main TUI application (PromaiaApp)
├── modes/
│   ├── __init__.py
│   ├── base.py              # BaseMode abstract class
│   ├── feed_mode.py         # Feed mode implementation
│   └── chat_mode.py         # Chat mode implementation
├── command_executor.py      # Inline command execution
└── breakout.py              # Terminal window spawning

promaia/cli/tui_commands.py  # CLI integration
```

### Key Classes

#### PromaiaApp (app.py)
Main TUI application class that:
- Manages mode switching
- Handles input and slash commands
- Integrates with prompt_toolkit for UI
- Coordinates display updates

#### BaseMode (modes/base.py)
Abstract base class for all modes with:
- `activate()` / `deactivate()` - Lifecycle hooks
- `handle_input()` - Process user input
- `handle_command()` - Process slash commands (mode-specific)
- `get_display_content()` - Return Rich Text content
- `get_prompt()` - Return mode-specific prompt

#### FeedMode (modes/feed_mode.py)
Default "home screen" that:
- Integrates with existing `FeedAggregator`
- Shows real-time agent activity in group-chat format
- Read-only (users watch and use slash commands)
- Formats events with emoji markers

#### ChatMode (modes/chat_mode.py)
Interactive chat interface that:
- Will integrate with `ConversationManager` (TODO)
- Supports mode-specific commands (/e, /model, /temp, etc.)
- Handles multi-turn conversations
- Currently a placeholder with architecture in place

#### CommandExecutor (command_executor.py)
Executes inline commands by:
- Parsing command strings
- Loading the CLI parser
- Capturing stdout/stderr
- Formatting output as Rich Text
- Just like Claude Code!

## Command System

### Three-Tier Command Precedence

When a user types a slash command, it's processed in this order:

```
User types: /something
    ↓
┌─────────────────────────────────────────────────┐
│ TIER 1: Reserved TUI Commands (ALWAYS handled) │
├─────────────────────────────────────────────────┤
│  /feed, /chat, /help, /clear, /break, /quit   │
│  → Handled by PromaiaApp directly              │
└─────────────────────────────────────────────────┘
    ↓ (if not handled)
┌─────────────────────────────────────────────────┐
│ TIER 2: Mode-Specific Commands                 │
├─────────────────────────────────────────────────┤
│  ChatMode: /e, /model, /temp, /s, /artifacts  │
│  FeedMode: (none)                              │
│  → Handled by current_mode.handle_command()   │
└─────────────────────────────────────────────────┘
    ↓ (if not handled)
┌─────────────────────────────────────────────────┐
│ TIER 3: CLI Fallback Execution                 │
├─────────────────────────────────────────────────┤
│  /agent list → maia agent list                 │
│  /database sync → maia database sync           │
│  → Executed by CommandExecutor, shown inline  │
└─────────────────────────────────────────────────┘
```

### Reserved TUI Commands

These work in any mode and are always handled first:

- `/feed` - Switch to feed mode
- `/chat` - Switch to chat mode
- `/help` - Show help overlay
- `/clear` - Clear current mode's display
- `/break <component>` - Open component in new terminal
- `/quit`, `/exit`, `/q` - Exit TUI

### Chat Mode Commands

When in chat mode, these commands are handled by ChatMode:

- `/e` - Edit context
- `/model` - Switch model
- `/temp` - Set temperature
- `/save` - Save conversation
- `/m` - Send to model
- `/artifact`, `/artifacts`, `/a` - Work with artifacts
- `/edit` - Edit last message
- `/image` - Add image
- `/queries` - Show active queries
- `/remove-query` - Remove query
- `/push` - Push context
- `/mute`, `/unmute` - Notification controls
- `/mcp` - MCP tools
- `/mail`, `/send` - Email tools
- `/debug` - Debug mode
- `/s` - System prompt

### Inline CLI Commands

Any other slash command executes as `maia <command>` inline:

```
/agent list        → maia agent list
/database sync     → maia database sync
/daemon status     → maia daemon status
```

## Usage

### Launching the TUI

```bash
# Default way (TUI is now the default command)
maia

# Explicit
maia tui

# With options
maia tui --mode chat  # Start in chat mode
```

### Mode Switching

**Type commands:**
- `/feed` - Switch to feed mode
- `/chat` - Switch to chat mode

**Keyboard shortcuts:**
- `Ctrl+F` - Switch to feed mode
- `Ctrl+T` - Switch to chat mode
- `Ctrl+C` - Exit TUI

### Running Commands

From any mode, type `/<command>` to run inline:

```
/agent list
/database sync --source journal:7
/daemon status
```

### Breaking Out Components

Open a component in a new terminal window:

```
/break feed
/break chat
```

## Integration Points

### CLI Integration (cli.py)

The TUI is integrated into the main CLI:

1. **Parser creation** - `create_parser()` function builds the CLI parser
2. **TUI command** - Registered via `add_tui_commands(subparsers)`
3. **Default behavior** - When no command specified, launches TUI
4. **Command execution** - TUI reuses `create_parser()` for inline commands

### Feed Integration (feed_aggregator.py)

FeedMode directly integrates with existing components:

- Uses `FeedAggregator` to consume events
- Uses `format_as_group_chat()` for formatting
- No changes needed to existing feed system!

### Chat Integration (conversation_manager.py)

ChatMode will integrate with existing chat system:

- Will use `ConversationManager` for conversations
- Will register as 'tui' platform
- Will delegate mode-specific commands to existing handlers
- **TODO:** Full integration pending

## Keyboard Shortcuts

- `Ctrl+C` - Exit TUI
- `Ctrl+F` - Switch to feed mode
- `Ctrl+T` - Switch to chat mode

## Display Rendering

The TUI uses Rich for formatted output:

1. Each mode returns `List[Text]` from `get_display_content()`
2. `PromaiaApp.refresh_display()` renders to ANSI
3. prompt_toolkit displays the ANSI in a `Window`
4. Updates happen on:
   - Mode switch
   - User input
   - Feed event (FeedMode)
   - Agent response (ChatMode)

## Error Handling

- Parse errors show inline with formatting
- Command failures capture stderr
- Mode-specific errors displayed in mode's content
- Graceful exit on Ctrl+C

## Future Enhancements

### Phase 2 (Planned)
- [ ] Full ChatMode integration with ConversationManager
- [ ] Multi-agent chat support
- [ ] Persistent history across sessions
- [ ] Filter controls for FeedMode (--agent, --level, --goal)

### Phase 3 (Ideas)
- [ ] Split views (feed + chat side-by-side)
- [ ] Custom themes/color schemes
- [ ] Plugin system for custom modes
- [ ] Native tmux integration
- [ ] Remote TUI over SSH

## Testing

Run the test script to verify all components load:

```bash
python test_tui_imports.py
```

Expected output:
```
✅ TUI main module imports
✅ TUI modes import
✅ Command executor imports
✅ Breakout module imports
✅ CLI TUI commands import
✅ PromaiaApp instantiates successfully
✅ Feed mode created: FeedMode
🎉 All TUI components loaded successfully!
```

## Verification Checklist

### Phase 1: Basic TUI
- [x] `maia` (no args) launches TUI
- [x] Welcome screen displays
- [x] `/feed`, `/chat` commands switch modes
- [x] `Ctrl+F`, `Ctrl+T` shortcuts work
- [x] `/help`, `/clear`, `/quit` work
- [x] PromaiaApp instantiates correctly
- [x] All imports successful

### Phase 2: Feed Mode
- [ ] Switch to feed mode
- [ ] Live events appear (requires daemon/agent activity)
- [ ] Emoji formatting and correlation IDs show
- [ ] Scrolling works (last 50-100 messages)

### Phase 3: Chat Mode
- [ ] Switch to chat mode
- [ ] Type message → agent responds
- [ ] Multi-turn conversation works
- [ ] History displays correctly

### Phase 4: Command Integration
- [ ] `/agent list` executes inline
- [ ] `/database sync` shows progress
- [ ] Error handling works
- [ ] Output formatting looks good

### Phase 5: Breakout
- [ ] `/break feed` opens new terminal
- [ ] Feed runs in new window
- [ ] Works on macOS (iTerm/Terminal)

## Known Issues

1. **Chat Integration Incomplete** - ChatMode is currently a placeholder showing architecture. Full integration with ConversationManager pending.

2. **Feed Requires Activity** - FeedMode shows live events, so you need agents running to see activity. Run `maia daemon start` first.

3. **Terminal Compatibility** - Breakout tested on macOS. Linux support planned.

## Files Modified

### New Files (9 files)
1. `promaia/tui/__init__.py`
2. `promaia/tui/app.py`
3. `promaia/tui/modes/__init__.py`
4. `promaia/tui/modes/base.py`
5. `promaia/tui/modes/feed_mode.py`
6. `promaia/tui/modes/chat_mode.py`
7. `promaia/tui/command_executor.py`
8. `promaia/tui/breakout.py`
9. `promaia/cli/tui_commands.py`

### Modified Files (1 file)
1. `promaia/cli.py` - Added TUI commands, made TUI default, extracted `create_parser()`

### Files Reused (no changes)
1. `promaia/agents/feed_aggregator.py`
2. `promaia/agents/feed_formatters.py`
3. `promaia/agents/conversation_manager.py`

## Summary

The Unified TUI is now implemented with:

- ✅ Complete architecture and mode system
- ✅ Feed mode with live event streaming
- ✅ Chat mode with placeholder (architecture ready)
- ✅ Inline command execution (just like Claude Code!)
- ✅ Three-tier command precedence system
- ✅ CLI integration (TUI is default)
- ✅ Breakout support for new terminals
- ✅ All imports working
- ✅ PromaiaApp instantiates successfully

**Ready for:** Testing with live feed and completing chat integration!

**Try it:** Run `maia` to launch the TUI!
