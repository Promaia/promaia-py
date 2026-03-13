# Unified Browser Feature

The Unified Browser is Maia's interactive source selection interface that allows you to browse and select from workspace databases and Discord channels in a single, intuitive interface.

## Overview

The unified browser replaces the separate workspace and Discord browsers with a single, powerful interface that supports:

- **Mixed source selection** (regular databases + Discord channels)
- **Custom day values** for each source individually
- **Live text editing** with arrow key navigation
- **Persistent selections** across `/e` (edit context) sessions
- **Grouped display** with clear visual organization

## Access Methods

### Command Line Browse (`-b`)

```bash
# Browse entire workspace
maia chat -b trass

# Browse specific databases/channels (single -b flag)
maia chat -b trass.tg trass.journal

# Mixed browsing (workspace + specific database)
maia chat -b trass trass.stories

# Multiple -b flags (equivalent to above)
maia chat -b trass -b trass.stories

# Multiple databases with separate -b flags
maia chat -b trass -b trass.tg

# Browse specific Discord database
maia chat -b trass.tg
```

**Note**: Both syntaxes are supported:
- Single `-b` with multiple arguments: `maia chat -b trass trass.tg`  
- Multiple `-b` flags: `maia chat -b trass -b trass.tg`

### Edit Context Browse (`/e` → `Ctrl+B`)

Within any chat session:
1. Type `/e` to enter edit context
2. Press `Ctrl+B` to launch the browser for the `-b` portion of your command
3. Make selections and press `Enter` to apply

## Browser Interface

### Display Format

```
🔍 trass | Sources: 5 databases, 7 channels | Selected: 8/12 | ↑↓ Navigate SPACE Toggle ENTER Confirm ESC Cancel

📄 Regular Sources:
☑       trass.cpj:7
☐       trass.epics:all
☑       trass.gmail:7
☑       trass.journal:20
☑       trass.stories:7

💬 Discord Sources:
☑       trass.tg#announcements:7
☐       trass.tg#customer-support:7
☑       trass.tg#koii-work:30
☑       trass.tg#maker-work:7
☐       trass.tg#merch-work:7
☑       trass.tg#plush-defects:7
☑       trass.tg#plush-work:7
```

### Controls

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate between sources |
| `Space` | Toggle source selection |
| `0-9` / `a-z` | Edit day value for current source |
| `Backspace` | Delete characters from day value |
| `Enter` | Confirm selections and apply |
| `Esc` | Cancel without changes |

### Day Value Editing

- **Default values**: Each source starts with its configured default days
- **Live editing**: Type numbers or text (like `all`) to change day values
- **Individual control**: Each source can have different day values
- **Persistence**: Custom day values are remembered across `/e` sessions

## Usage Examples

### Basic Workspace Browsing

```bash
maia chat -b trass
```
Opens browser showing all enabled databases in the `trass` workspace. Select desired sources and press Enter.

### Mixed Source Selection

```bash
# Single -b flag with multiple arguments
maia chat -b trass.tg trass

# Multiple -b flags (equivalent)
maia chat -b trass.tg -b trass
```
Opens browser showing:
- All Discord channels from `trass.tg` 
- All regular databases from `trass` workspace
- Allows selection from both categories

### Edit Context Workflow

```bash
# Start with initial selection
maia chat -b trass.journal trass.tg

# Later, edit your selection
You: /e
# Press Ctrl+B to open browser
# Modify selections (add/remove sources, change days)
# Press Enter to apply changes
```

### Custom Day Values

In the browser interface:
1. Navigate to a source (e.g., `trass.journal:7`)
2. Type a new value (e.g., `20`)
3. The display updates to `trass.journal:20`
4. Continue selecting other sources
5. Press Enter to apply all changes

## Advanced Features

### State Persistence

The unified browser remembers:
- **Selected sources** across `/e` sessions
- **Custom day values** for each source  
- **Mixed selections** (regular databases + Discord channels)
- **Original browse command format** (preserves `-b` syntax)

**Recent Improvements**: The `/e` edit context now properly preserves mixed workspace and Discord selections, ensuring both regular databases and Discord channels remain available for re-selection.

### Workspace Expansion

When you specify a workspace name (e.g., `trass`), it automatically expands to include all enabled databases in that workspace:

```bash
# Either syntax works:
maia chat -b trass.tg trass
maia chat -b trass.tg -b trass

# Both expand to: trass.tg, trass.journal, trass.stories, trass.gmail, trass.cpj, trass.epics
```

### Query Format Preservation

The browser preserves your original browse command format:

```bash
# Original command preserved
maia chat -b trass.tg trass

# NOT decomposed to individual -s flags
# NOT: maia chat -s trass.journal:7 -s trass.stories:7 -f trass.tg:7:discord_channel_name=...
```

## Discord Channel Features

### Channel-Specific Filtering

Discord channels are automatically converted to database sources with channel filters:

```bash
# Browser selection: trass.tg#customer-support:7, trass.tg#koii-work:30

# Becomes internally:
# - trass.tg:7 with filter discord_channel_name=customer-support  
# - trass.tg:30 with filter discord_channel_name=koii-work
```

### Multiple Channels, Same Database

You can select multiple channels from the same Discord database with different day values:

```
☑ trass.tg#customer-support:3
☑ trass.tg#koii-work:30  
☑ trass.tg#announcements:7
```

Each creates a separate query with its own day range and channel filter.

## Error Handling

### Common Issues

**No sources selected**: If you press Enter without selecting any sources, the browser will warn you and keep the current context unchanged.

**Invalid day values**: The browser handles non-numeric values like `all` gracefully.

**Missing databases**: If a specified database doesn't exist, the browser will show available options.

### Recovery

- Press `Esc` to cancel changes and keep current context
- Use `/e` to re-enter edit mode and try again
- Check `maia database list` to verify available sources

## Integration with Other Features

### Natural Language Queries (`-nl`)

The unified browser works alongside natural language queries:

```bash
maia chat -nl "recent updates" -b trass
```

### Filters (`-f`)

Browse selections work with additional filters:

```bash
maia chat -b trass -f "status=published"  
```

### MCP Servers (`-mcp`)

Browse selections can be combined with MCP server integration:

```bash
maia chat -b trass -mcp files
```

## Configuration

The unified browser uses your existing database configurations. Ensure your workspace databases are properly configured:

```bash
# View current configuration
maia database list

# Configure workspace defaults
maia workspace configure trass --default-days 7
```

## Tips and Best Practices

1. **Start broad, narrow down**: Use `-b workspace` first, then use `/e` to refine selections
2. **Use custom day values**: Adjust day values for different types of content (e.g., more days for journals, fewer for high-volume channels)  
3. **Mixed selections**: Combine regular databases with Discord channels for comprehensive context
4. **Persistent editing**: Use `/e` repeatedly to iteratively refine your source selection
5. **Visual feedback**: The browser shows exactly what will be included before you commit

The unified browser makes complex source selection intuitive and efficient, enabling powerful multi-source conversations with precise control over what content is included. 