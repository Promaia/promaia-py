# Changelog

All notable changes to Promaia will be documented in this file.

## [Unreleased] - 2025-10-26

### 🔍 Property Embeddings - Semantic Search on Notion Properties

#### Added
- **Property-Aware Search**: Natural language queries can now search Notion database properties (e.g., "stories with epic holiday launch")
- **Separate Property Collection**: New ChromaDB collection `promaia_properties` for property-specific embeddings
- **Property Schema Tracking**: New `notion_property_schema` table tracks available properties per database
- **Automatic Sync Integration**: Property schemas and embeddings created automatically during database sync
- **Backfill Script**: `sync_property_embeddings.py` for backfilling embeddings on existing content
- **Property Type Classification**: Embeddable properties (title, text, rich_text, relation) vs filterable properties (select, status, multi_select, people)

#### Enhanced
- **Intent Parser**: Extracts property constraints from natural language queries
- **Query Router**: Intelligently routes queries based on property types (semantic vs filter)
- **Relation Resolution**: Relation properties resolved to page titles for meaningful semantic search
- **Result Intersection**: Combined constraints (semantic + filter + date) properly intersected
- **Vector DB Manager**: New `add_property_embedding()` and `search_property()` methods

#### Configuration
```json
{
  "global": {
    "vector_search": {
      "property_embeddings": {
        "enabled": true,
        "embeddable_types": ["title", "text", "rich_text", "relation"],
        "filter_types": ["select", "status", "multi_select", "people"],
        "default_property_similarity_threshold": 0.75
      }
    }
  },
  "databases": {
    "your_database": {
      "include_properties": true  // Required for property embeddings
    }
  }
}
```

#### Query Examples
```bash
# Semantic property search
maia chat "stories with epic 2025 holiday launch"

# Filter property search
maia chat "stories with status in progress"

# Combined constraints
maia chat "in-progress stories with epic holiday launch from last sprint"
```

#### Implementation Details
- **Files Modified**: `vector_db.py`, `hybrid_storage.py`, `nl_orchestrator.py`, `query_strategies.py`, `notion_connector.py`
- **Vector ID Format**: `{page_id}_prop_{property_name}`
- **Property Embedding Metadata**: Includes workspace, database, property name/type for filtering
- **Schema Population**: Automatic during sync after database schema caching

#### Benefits
- Natural language queries on structured properties (no more exact property value matching)
- Semantic search on relations enables powerful cross-database queries
- Clean separation of concerns (semantic embeddings vs exact filters)
- Automatic integration with existing sync workflow
- Backfill support for existing content
- Performance optimized with separate collection and result intersection

#### Documentation
- **[NEW] docs/property_embeddings.md**: Complete feature documentation with architecture, usage, examples
- **[NEW] docs/PROPERTY_EMBEDDINGS_CHEATSHEET.md**: Quick reference guide
- **[UPDATED] docs/QUICK_REFERENCE.md**: Added property embeddings section
- **[UPDATED] docs/README.md**: Added property embeddings to navigation

## [Previous] - 2025-10-20

### 📧 Maia Mail - History Feature

#### Added
- **History View**: Separate view for completed emails (sent/archived) accessible via 'h' key or `--history` flag
- **Completion Tracking**: New `completed_time` field tracks when messages were marked done
- **Queue Separation**: Active queue no longer shows completed messages (cleaner workflow)
- **CLI Flag**: `--history` flag to start directly in history view
- **Toggle Navigation**: Press 'h' to switch between queue and history views
- **Persistent Archive**: All sent/archived messages preserved and searchable

#### Enhanced
- **DraftManager**: Added `get_history_for_workspace()` method for history retrieval
- **Status Tracking**: `update_draft_status()` now sets `completed_time` for sent/archived messages
- **Queue Filtering**: `get_drafts_for_workspace()` excludes completed messages by default
- **Auto Migration**: Existing databases automatically updated with `completed_time` column

#### Changed
- Completed messages (sent/archived) removed from active queue automatically
- History ordered by completion time (most recent first) instead of creation time
- Archive action ('a' key) disabled in history view (view-only mode)

#### Benefits
- Clean separation between active work and completed items
- Easy verification of sent emails via history view
- Completion timestamps for audit/review purposes
- Same familiar interface for both queue and history
- No data loss - all messages retained in database

#### Documentation
- **[NEW] docs/MAIA_MAIL_HISTORY.md**: Complete history feature guide with examples

### 📧 Maia Mail - Copy-Friendly Thread Formatting

#### Added
- **Message Position Indicators**: Each message shows "Message: x/y" for orientation
- **Color-Coded Messages**: Alternating cyan/blue colors for visual distinction
- **Copy-Friendly Format**: Simple dashes instead of box characters (clean copy/paste)
- **ThreadFormatter Module**: New `thread_formatter.py` with parsing and formatting utilities

#### Enhanced
- **Better Orientation**: Users always know their position when scrolling
- **Visual Hierarchy**: Alternating colors make messages easier to distinguish
- **Clean Copying**: Content pastes cleanly without formatting artifacts
- **Consistent Display**: Same formatting in review UI and chat interface

#### Changed
- Multi-message threads now show individual message boundaries with position indicators
- Thread display follows COPY_FRIENDLY_RICH.md principles

#### Benefits
- Easy navigation through long email threads
- Clear visual separation between messages
- Professional formatting that copies perfectly
- Improved user experience when scrolling

#### Documentation
- **[NEW] MAIA_MAIL_THREAD_SCROLLING.md**: Thread formatting implementation guide

### 📧 Maia Mail - Full Thread Scrolling

#### Added
- **Full Thread Viewing**: Draft chat now displays complete email conversation history
- **Smart Thread Labels**: Multi-message threads show "EMAIL THREAD (X messages)" for clarity
- **Scroll Tips**: Helpful reminder that users can scroll up to see full conversation
- **Forced Full Thread Mode**: Email processor always fetches complete threads for maia mail

#### Enhanced
- **Better Context**: Users can see entire conversation when reviewing/refining drafts
- **Natural UX**: Draft chat scrolls to bottom (latest message first), review queue starts at top
- **Improved Queue Layout**: Review queue now shows controls before list (header → controls → queue)
- **Alternate Screen Buffer**: Review queue uses alternate screen (like vim/less) for clean viewport control
- **Smooth Navigation**: Arrow keys work without scroll glitches or viewport jumping
- **Consistent Display**: Both draft chat and review UI use same thread display format
- **Cleaner UI**: Removed redundant "Thread:" metadata line

#### Changed
- Email processor now uses `gmail_content_mode: "full_thread"` for maia mail (previously used `latest_only`)
- Draft chat displays full conversation body instead of just latest message
- Review queue now uses alternate screen buffer with ANSI escape sequences for proper viewport control

#### Fixed
- **Review Queue Scrolling**: Fixed issue where queue would start at bottom instead of top
- **Arrow Key Glitches**: Eliminated viewport jumping when navigating with arrow keys
- **Scrollback Interference**: Alternate screen buffer prevents scrollback from affecting display

#### Benefits
- Users can understand full context of ongoing conversations
- Short latest messages no longer lack context
- Informed decision-making when refining responses
- Standard terminal scrolling works as expected
- Controls are visible before long lists (better UX for queue navigation)

#### Documentation
- **[NEW] MAIA_MAIL_THREAD_SCROLLING.md**: Complete implementation details and user guide

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