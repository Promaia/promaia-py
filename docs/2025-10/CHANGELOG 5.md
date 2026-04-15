# Changelog

All notable changes to Promaia will be documented in this file.

## [Unreleased] - 2025-07-23

### 🎮 Discord Integration - Major Feature Release

#### Added
- **Interactive Discord Channel Browser**: Visual TUI for selecting Discord channels with keyboard navigation
- **Sync with Browse (`-b`) functionality**: `maia sync -b` works exactly like `maia chat -b` 
- **Per-Channel Day Cycling**: Press `D` to cycle days (1→7→14→30→60→90) for individual channels
- **Multi-Server Discord Support**: Browse channels across multiple Discord servers simultaneously
- **Emoji & Special Character Support**: Automatic reverse lookup for Discord channels with emojis (e.g., `📢・announcements`)
- **Workspace Inference**: Automatically detects workspace from database specifications
- **Combined Sources**: Mix regular sources (`-s`) with Discord browse (`-b`) in single command
- **Parallel Discord Sync**: Efficient concurrent processing across multiple Discord channels
- **Discord Debug Commands**: Comprehensive troubleshooting tools (`maia discord debug-channels`)

#### New Commands
```bash
# Interactive browse for both sync and chat
maia sync -b workspace.discord workspace.yeeps_discord
maia chat -b workspace.discord:30

# Combined sources
maia sync -s journal:7 -b workspace.discord:14
maia chat -s journal:5 -s stories:10 -b workspace.discord:30

# Discord management
maia discord debug-channels --workspace myworkspace
maia discord list-channels myworkspace
maia workspace discord-setup myworkspace --server-id ID
```

#### Enhanced
- **Enhanced Filtering Guide**: Updated with Discord-specific filtering examples
- **Quick Reference Guide**: Added comprehensive Discord command examples
- **Main README**: Complete rewrite with Discord integration examples
- **Error Handling**: Improved Discord connectivity troubleshooting
- **Performance**: Optimized sync performance for Discord channels with large message history

#### Fixed
- **Channel Name Mapping**: Fixed Discord channel name reverse lookup for channels with emojis
- **Guild Channel Fetching**: Fixed empty channel list issue by properly fetching guild channels
- **Filter Parsing**: Fixed `property_filters` vs `filters` mismatch in sync command
- **Top-level Sync Command**: Added missing `-b/--browse` argument to `maia sync` (not just `maia database sync`)

#### Documentation
- **[NEW] Discord Integration Guide**: Complete setup, usage, and troubleshooting guide
- **[NEW] Getting Started Guide**: 5-minute setup focusing on Discord integration
- **[NEW] Documentation Reorganization**: Separated user docs (`/docs/`) from build docs (`/build-docs/`)
- **Updated Enhanced Filtering**: Added Discord-specific filtering examples and properties
- **Updated Quick Reference**: Added Discord command examples and workflows
- **Updated README**: Comprehensive rewrite showcasing Discord integration with organized doc structure

### 🔧 Technical Details

#### Architecture Changes
- Added `handle_database_sync_with_browse()` function for sync browse functionality
- Enhanced Discord connector with `_find_channel_by_sanitized_name()` for emoji support
- Added workspace inference logic for browse databases
- Implemented per-channel day specifications in browser TUI

#### Performance Improvements
- **Parallel Channel Sync**: Multiple Discord channels sync concurrently
- **Optimized API Calls**: Reduced Discord API calls through efficient channel caching
- **Smart Channel Filtering**: Channel name reverse lookup prevents API errors

#### Browser Interface
- **Visual Channel Selection**: Clear indication of selected channels (☑) vs unselected (☐)
- **Real-time Day Display**: Each channel shows current day filter (e.g., "#announcements (30 days)")
- **Multi-Server Organization**: Channels grouped by Discord server with clear hierarchy
- **Intuitive Controls**: Standard arrow keys, spacebar, and dedicated day cycling

### 🎯 Usage Examples

#### Daily Team Workflow
```bash
# Morning standup: Review yesterday's Discord activity
maia chat -b team.discord:1

# Sync recent team discussions for context
maia sync -s meeting_notes:7 -b team.discord:7

# Generate weekly team summary
maia write --prompt "Weekly team summary" --context team.discord:7
```

#### Content Creation Pipeline
```bash
# Sync content sources with community feedback
maia sync -s cms:14 -b community.discord:30

# Create content based on community discussions
maia chat -s cms:7 -b community.discord:14
```

#### Product Release Analysis
```bash
# Comprehensive release context
maia chat -s release_notes:30 -b team.discord:14 community.discord:30

# Analyze user feedback across channels
maia sync -b community.discord:7 support.discord:14
```

---

## Previous Versions

### [1.0.0] - 2025-01-01
- Initial release with Notion integration
- Basic chat functionality
- CMS and newsletter features
- Gmail integration
- Multi-workspace support

---

*For detailed technical information, see the [Discord Integration Guide](docs/DISCORD_INTEGRATION.md)* 