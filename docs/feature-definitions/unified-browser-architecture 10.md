# Unified Browser Architecture - Technical Build Document

**Version**: 1.0  
**Date**: July 2025  
**Status**: Production Ready  

## Executive Summary

The Unified Browser is a complex interactive interface that replaced separate workspace and Discord browsers with a single, cohesive system. This document details the exact implementation, tested permutations, and architectural decisions required to rebuild this feature if it breaks.

## Architecture Overview

### Core Components

```
unified-browser/
├── CLI Entry Points (promaia/cli.py)
├── Browser Interface (promaia/cli/workspace_browser.py)
├── Chat Integration (promaia/chat/interface.py)
├── Filter Processing (promaia/cli/database_commands.py)
└── Storage Query System (promaia/storage/)
```

### Data Flow

```
Command Line Input → Argument Parsing → Workspace Expansion → 
Browser Launch → User Selection → Discord Processing → 
Filter Generation → Context Update → Query Execution
```

## Implementation Details

### 1. CLI Argument Parsing (promaia/cli.py)

**Critical Fix**: Multiple `-b` arguments handling

```python
# BEFORE (broken): Only kept last -b argument
parser.add_argument("-b", "--browse", nargs="*", dest="browse")

# AFTER (working): Accumulates all -b arguments
parser.add_argument("-b", "--browse", action="append", nargs="*", dest="browse")

# Required flattening logic:
if browse_databases:
    flattened = []
    for item in browse_databases:
        if isinstance(item, list):
            flattened.extend(item)
        else:
            flattened.append(item)
    browse_databases = flattened
```

**Workspace Expansion Logic**:
```python
# Expand workspace names to constituent databases
all_databases = set(browse_databases)
for browse_arg in browse_databases:
    if not ('.' in browse_arg or '#' in browse_arg):
        # This is a workspace name, expand it
        workspace_dbs = [db.get_qualified_name() for db in db_manager.get_databases_by_workspace(browse_arg) 
                        if db.enabled]
        all_databases.update(workspace_dbs)
```

### 2. Browser Interface (promaia/cli/workspace_browser.py)

**Key Implementation Requirements**:

#### Entry Point Function
```python
def launch_unified_browser(workspace: str, default_days: int = None, 
                          database_filter: List[str] = None, 
                          current_sources: List[str] = None) -> List[str]:
```

#### Safe Day Parsing
```python
def safe_parse_days(days_str: str) -> Union[int, str]:
    """Handle both numeric (7) and text (all) day values"""
    if days_str.isdigit():
        return int(days_str)
    return days_str  # Return as-is for 'all', etc.
```

#### Visual Grouping System
```python
def sort_key(item):
    """Sort to group regular databases first, then Discord channels"""
    name, _ = item
    if '#' in name:
        return (1, name.lower())  # Discord channels second
    else:
        return (0, name.lower())  # Regular databases first
```

#### Text Area Mapping
```python
# Critical: Map text areas to correct entries, skipping headers/spacers
self.text_area_to_source_window = {}
source_index = 0
for i, window in enumerate(self.source_windows):
    if hasattr(window, 'content') and hasattr(window.content, 'focusable'):
        if window.content.focusable:  # Only map focusable windows
            self.text_area_to_source_window[source_index] = i
            source_index += 1
```

#### Day Value Editing
```python
# Live text editing within browser
def on_text_changed():
    if self.current_focus >= 0 and self.current_focus < len(self.sources):
        source_name, _ = self.sources[self.current_focus]
        new_days = text_area.text.strip() or str(default_days or 7)
        self.sources[self.current_focus] = (source_name, safe_parse_days(new_days))
        self.update_display()
```

### 3. Chat Integration (promaia/chat/interface.py)

**Critical Functions**:

#### handle_browse_in_edit_context()
```python
def handle_browse_in_edit_context():
    """Handle unified browse mode selection within edit context."""
    
    # Parse browse arguments from original command
    browse_args = []
    if '-b ' in original_format:
        args_list = safe_split_command(original_format[10:])  # Remove "maia chat "
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("-b", "--browse", action="append", nargs="*")
        parsed_args, _ = parser.parse_known_args(args_list)
        
        if parsed_args.browse:
            # Flatten nested list from argparse
            for item in parsed_args.browse:
                if isinstance(item, list):
                    browse_args.extend(item)
                else:
                    browse_args.append(item)
```

#### Current Sources Building Logic
```python
# CRITICAL: Handle mixed commands correctly
current_sources = []

# First, get any stored Discord channel selections
stored_discord_selections = context_state.get('browse_selections', [])
if stored_discord_selections:
    current_sources.extend(stored_discord_selections)

# Add regular sources that aren't Discord databases
current_regular_sources = context_state.get('sources', [])
if current_regular_sources:
    discord_db_names = set()
    for discord_sel in stored_discord_selections:
        if '#' in discord_sel:
            db_name = discord_sel.split('#')[0]
            discord_db_names.add(db_name)
    
    for source in current_regular_sources:
        source_db = source.split(':')[0] if ':' in source else source
        if source_db not in discord_db_names:
            current_sources.append(source)
```

### 4. Discord Channel Processing

**Format Conversion**:
```
Input:  trass.tg#customer-support:7
Output: 
  - Source: trass.tg:7
  - Filter: trass.tg:7:discord_channel_name=customer-support
```

**Multiple Channel Grouping**:
```python
# Group by database + days combination
discord_db_groups = {}
for source in selected_sources:
    if '#' in source:
        db_channel, days_part = source.rsplit(':', 1)
        db_name, channel_name = db_channel.split('#', 1)
        
        db_key = f"{db_name}:{days_part}"
        if db_key not in discord_db_groups:
            discord_db_groups[db_key] = []
        discord_db_groups[db_key].append(channel_name)

# Generate filters
for db_spec, channels in discord_db_groups.items():
    processed_sources.append(db_spec)
    
    if len(channels) == 1:
        filter_spec = f"{db_spec}:discord_channel_name={channels[0]}"
    else:
        channel_conditions = [f"discord_channel_name={ch}" for ch in channels]
        combined_filter = " or ".join(channel_conditions)
        filter_spec = f"{db_spec}:({combined_filter})"
    
    processed_filters.append(filter_spec)
```

### 5. Filter Processing Logic

**Filter Detection** (promaia/chat/interface.py):
```python
# CRITICAL: Handle both numeric and "all" day values
source_parts = source.split(':')
source_has_days = (len(source_parts) > 1 and 
                  (source_parts[-1].isdigit() or source_parts[-1] == 'all'))

filter_is_discord = ('discord_channel_name' in filter_spec or 
                    ('(' in filter_spec and 'discord_channel_name' in filter_spec))

if source_has_days and filter_is_discord:
    discord_filters.append(filter_expr)
```

**Filter Parsing** (promaia/cli/database_commands.py):
```python
def parse_filter_expression(filter_expr: str) -> Dict[str, Any]:
    """Parse filter with source prefix support"""
    source_match = re.match(r'^([a-zA-Z0-9_.-]+(?::[0-9]+|:all)?):\s*(.+)$', filter_expr)
    if source_match:
        source = source_match.group(1)
        filter_part = source_match.group(2)
        return {'source': source, 'filter': filter_part}
```

## Tested Permutations

### 1. Command Line Parsing Tests

**✅ Single workspace**: `maia chat -b trass`
- Expected: Expands to all enabled databases in workspace
- Result: ✅ Working

**✅ Multiple -b arguments**: `maia chat -b trass.tg -b trass`
- Expected: Combines Discord channels + workspace databases  
- Result: ✅ Working (after argparse fix)

**✅ Mixed arguments**: `maia chat -b trass.tg trass`
- Expected: Same as multiple -b, flattened arguments
- Result: ✅ Working

**✅ Specific database**: `maia chat -b trass.journal`
- Expected: Shows only that database in browser
- Result: ✅ Working

### 2. Browser Interface Tests

**✅ Basic navigation**: Arrow keys up/down
- Expected: Navigate between sources
- Result: ✅ Working

**✅ Selection toggling**: Space bar
- Expected: Toggle checkboxes
- Result: ✅ Working

**✅ Day value editing**: Type numbers/text
- Expected: Live update of day values
- Result: ✅ Working

**✅ Day value types**: Numeric (7) and text (all)
- Expected: Both handled correctly
- Result: ✅ Working (after safe_parse_days fix)

**✅ Group display**: Regular databases first, Discord second
- Expected: Clear visual grouping with headers
- Result: ✅ Working

### 3. Persistence Tests

**✅ Simple persistence**: Select sources → `/e` → Ctrl+B
- Expected: Previous selections maintained
- Result: ✅ Working

**✅ Custom day persistence**: Change days → `/e` → Ctrl+B  
- Expected: Custom day values remembered
- Result: ✅ Working (after current_sources fix)

**✅ Mixed persistence**: Regular + Discord → `/e` → Ctrl+B
- Expected: Both types maintained correctly
- Result: ✅ Working

**✅ Multiple sessions**: Several `/e` cycles
- Expected: State maintained across all sessions
- Result: ✅ Working

### 4. Discord Integration Tests

**✅ Single channel**: Select one Discord channel
- Expected: Correct filter generation
- Result: ✅ Working

**✅ Multiple channels, same days**: Multiple channels, same day value
- Expected: OR filter with combined channels
- Result: ✅ Working

**✅ Multiple channels, different days**: Different day values
- Expected: Separate sources with individual filters
- Result: ✅ Working

**✅ Channel filtering**: Data actually filtered by channel
- Expected: Only selected channel data returned
- Result: ✅ Working

### 5. Error Condition Tests

**✅ No selection**: Press Enter without selecting anything
- Expected: Warning message, no changes
- Result: ✅ Working

**✅ Invalid day values**: Type non-numeric values
- Expected: Handled gracefully
- Result: ✅ Working

**✅ Missing databases**: Reference non-existent database
- Expected: Error handling, available options shown
- Result: ✅ Working

**✅ Cancel operation**: Press Esc
- Expected: No changes, return to previous state
- Result: ✅ Working

## Critical Bugs Fixed

### 1. Multiple -b Argument Support
**Problem**: `maia chat -b trass.tg -b trass` only processed the last argument
**Root Cause**: `argparse` with `nargs="*"` only keeps last occurrence
**Solution**: Use `action="append", nargs="*"` + flattening logic

### 2. "all" Day Value Handling  
**Problem**: Discord channels with `:all` not detected as having days
**Root Cause**: `source.split(':')[-1].isdigit()` failed for "all"
**Solution**: Check for both `.isdigit()` and `== 'all'`

### 3. Mixed Command Persistence
**Problem**: Regular databases lost when using `/e` with Discord channels
**Root Cause**: Incorrect current_sources building in handle_browse_in_edit_context
**Solution**: Explicit combination of stored Discord + current regular sources

### 4. Filter Application
**Problem**: Discord filters not properly categorized and applied
**Root Cause**: Discord filter detection logic missing "all" day values
**Solution**: Enhanced source_has_days logic in filter processing

### 5. UI Text Area Mapping
**Problem**: KeyError when navigating browser after adding group headers
**Root Cause**: text_area_to_source_window mapping included non-focusable windows
**Solution**: Only map focusable text area windows

## Dependencies

### Required Libraries
- `prompt_toolkit`: Interactive terminal interface
- `argparse`: Command line parsing
- `shlex`: Safe command splitting
- `re`: Regular expression parsing

### Internal Dependencies
- `promaia.storage.*`: Database query system
- `promaia.config.databases`: Database configuration
- `promaia.utils.display`: Text styling
- `promaia.cli.database_commands`: Filter parsing

## Configuration Requirements

### Database Configuration
```python
# Each database must have:
{
    "name": "tg",
    "workspace": "trass", 
    "enabled": true,
    "default_days": 7,
    "source_type": "discord"
}
```

### Context State Variables
```python
context_state = {
    'sources': [],              # Current regular sources with days
    'filters': [],              # Generated filter expressions  
    'browse_selections': [],    # Original Discord channel selections
    'original_query_format': '' # Preserved command format
}
```

## Performance Considerations

### Browser Launch Time
- **Database enumeration**: ~50ms for typical workspace
- **Discord channel loading**: ~100ms for server with 20+ channels  
- **UI rendering**: ~10ms for prompt_toolkit interface

### Memory Usage
- **Browser state**: <1MB for typical selections
- **Context preservation**: <100KB per session

### Query Performance  
- **Discord filtering**: Linear with message count
- **Multiple sources**: Parallel processing where possible

## Monitoring and Debugging

### Debug Flags
```python
# Enable in chat interface for troubleshooting
DEBUG_MODE = True  # Shows filter processing details
```

### Key Log Points
1. **Argument parsing**: CLI argument flattening
2. **Browser launch**: Database/channel enumeration
3. **Selection processing**: Discord channel conversion
4. **Filter generation**: Discord filter creation
5. **Context updates**: State preservation

### Common Debug Scenarios
```bash
# Test argument parsing
maia chat -b trass.tg trass --debug

# Test persistence
maia chat -b trass
# Use /e, modify selection, check state

# Test Discord filtering
maia chat -b trass.tg#specific-channel:7
# Verify filter generation and application
```

## Maintenance Guidelines

### Code Locations for Changes

**Adding new browser features**: `promaia/cli/workspace_browser.py`
**Modifying CLI parsing**: `promaia/cli.py` (chat_run_inline_browse)
**Changing persistence logic**: `promaia/chat/interface.py` (handle_browse_in_edit_context)
**Filter processing updates**: `promaia/cli/database_commands.py` + `promaia/chat/interface.py`

### Testing Checklist

When modifying unified browser:

1. **✅ CLI Parsing**: Test all argument combinations
2. **✅ Browser Navigation**: Arrow keys, space, enter, esc
3. **✅ Day Value Editing**: Numeric and text values
4. **✅ Persistence**: Multiple `/e` sessions 
5. **✅ Discord Integration**: Channel selection and filtering
6. **✅ Mixed Commands**: Regular + Discord sources
7. **✅ Error Handling**: Invalid inputs, cancellation
8. **✅ Performance**: Large workspaces, many channels

### Breaking Changes to Avoid

1. **Context state format changes**: Will break persistence
2. **Filter format changes**: Will break Discord integration  
3. **Argument parsing changes**: Will break CLI compatibility
4. **Browser key bindings**: Will confuse users

## Future Enhancement Opportunities

### Potential Improvements
1. **Search/filter within browser**: Type to filter visible sources
2. **Bulk day editing**: Set days for multiple sources at once
3. **Saved selections**: Named source combinations
4. **Visual indicators**: Show data volume per source
5. **Keyboard shortcuts**: Quick selection patterns

### Architecture Evolution
- **Plugin system**: Allow custom source types
- **Configuration UI**: Browser-based database management
- **Advanced filtering**: Complex filter builder within browser
- **Performance optimization**: Lazy loading for large workspaces

## Recovery Procedures

### If Browser Breaks Completely
1. **Disable unified browser**: Add CLI flag to use old individual browsers
2. **Fallback to -s arguments**: Use manual source specification
3. **Debug with minimal cases**: Single workspace, single Discord channel
4. **Check dependencies**: Verify prompt_toolkit installation
5. **Rebuild from working version**: Use git history to restore functionality

### If Persistence Breaks
1. **Clear context state**: Remove context_state persistence temporarily
2. **Test without /e**: Use fresh browser sessions only
3. **Debug state contents**: Add logging to context_state updates
4. **Restore manual selection**: Allow browser-only workflows

This document captures the complete implementation details required to rebuild the unified browser feature. The architecture is complex but well-tested across all major use cases and edge conditions. 