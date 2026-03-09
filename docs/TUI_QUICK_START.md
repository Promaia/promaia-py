# TUI Quick Start Guide

## What is the TUI?

The Unified Terminal User Interface makes Promaia feel like one continuous conversation. It's your home base where you:

- 🐙 Watch live agent activity (Feed mode)
- 💬 Chat with agents (Chat mode)
- ⚡ Run any command inline (just type `/command`)

**Philosophy:** "Make everything a chat" - no more juggling terminals!

## Launch the TUI

```bash
# Simple! (TUI is now the default)
maia

# Or explicit
maia tui
```

## Two Modes

### 🐙 Feed Mode (Default)
Your "home screen" - watch agents work in real-time:
- See all agent activity in a group-chat format
- Emoji markers show what's happening (🗓️ calendar, 🤖 executor, etc.)
- Correlation IDs link related events ([goal:xyz] [task:abc])
- Just watch and use slash commands

### 💬 Chat Mode
Talk with your agents:
- Full conversation interface
- Multi-turn discussions
- Special commands like `/e` (edit), `/model` (switch model)
- Everything you'd expect from `maia agent chat`

## Switch Between Modes

**Type:**
- `/feed` - Switch to feed
- `/chat` - Switch to chat

**Keyboard shortcuts:**
- `Ctrl+F` - Feed mode
- `Ctrl+T` - Chat mode

## Run Commands Inline

From ANY mode, just type `/<command>`:

```
/agent list
/database sync
/daemon status
/agent create
```

The output appears inline, just like Claude Code!

## Essential Commands

### Work Everywhere
```
/feed         - Switch to feed mode
/chat         - Switch to chat mode
/help         - Show help
/clear        - Clear display
/quit         - Exit (or Ctrl+C)
```

### Chat Mode Only
```
/e            - Edit context
/model        - Switch AI model
/temp 0.7     - Set temperature
/save         - Save conversation
```

### Break Out Components
```
/break feed   - Open feed in new terminal
/break chat   - Open chat in new terminal
```

## Quick Examples

### Watch Agent Activity
```bash
maia                    # Launch TUI (starts in feed mode)
# Watch the feed stream
```

### Chat with Agent
```bash
maia                    # Launch TUI
/chat                   # Switch to chat mode
# Type your message
Help me plan my week
```

### Run Commands
```bash
maia                    # Launch TUI
/agent list             # See your agents
/database sync --source journal:7  # Sync data
/daemon status          # Check background services
```

### Multi-Window Setup
```bash
maia                    # Main TUI
/break feed             # Open feed in new window
# Now you have: TUI in one window, dedicated feed in another
```

## Tips

1. **Feed is your home base** - Start here, watch everything, use commands when needed

2. **Slash commands are your friend** - Any `/command` works from anywhere

3. **Tab between windows** - Use `/break` to split components across terminals

4. **Keyboard shortcuts** - Ctrl+F/T for quick mode switching

5. **Help is always available** - Type `/help` anytime

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+F` | Switch to feed mode |
| `Ctrl+T` | Switch to chat mode |
| `Ctrl+C` | Exit TUI |

## Troubleshooting

**"No feed events showing"**
- Feed shows live activity - start some agents first!
- Try: `maia daemon start` in another terminal

**"Chat not responding"**
- Chat integration is a work in progress
- Check the placeholder message for updates

**"Command not found"**
- Use `/help` to see available commands
- Try the full command: `/agent list` not just `/list`

## What's Next?

Once the TUI is running:

1. **Watch the feed** - See what your agents are doing
2. **Try chat** - Type `/chat` and talk to an agent
3. **Run commands** - `/agent list`, `/database sync`, etc.
4. **Explore** - Type `/help` to discover more

## Need Help?

- Type `/help` in the TUI
- Read `docs/TUI_IMPLEMENTATION.md` for details
- Check `IMPLEMENTATION_SUMMARY.md` for technical overview

---

**Welcome to the unified Promaia experience!** 🐙

Run `maia` and make everything a chat.
