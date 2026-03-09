# Maia CLI Quick Reference

Maia is your personal AI assistant manager with powerful database synchronization, multi-source chat, and content generation capabilities.

**Note**: Maia now uses a hybrid storage architecture with optimized separate tables for each content type, providing faster queries and better natural language processing.

## Core Commands

### Database Management (Primary Interface)
```bash
# List all configured databases
maia database list

# Add a new database
maia database add --name stories --source-type notion --database-id abc123

# Test database connections
maia database test

# Sync databases with optional filters
maia database sync                                    # Sync all enabled databases
maia database sync --sources journal awakenings      # Sync specific databases
maia database sync --sources journal[date>-30d]      # Sync with filters

# Get database information
maia database info journal
maia database info journal --schema                  # Include schema details

# Remove a database
maia database remove old-database
```

### 🔍 Unified Browser (NEW)

The unified browser provides an interactive interface for selecting sources from workspaces and Discord channels.

#### Basic Browse Commands
```bash
# Browse entire workspace
maia chat -b acme

# Browse specific databases/channels
maia chat -b acme.tg acme.journal

# Mixed browsing (workspace + Discord)
maia chat -b acme.tg acme

# Browse with default days
maia chat -b acme:30
```

#### Edit Context Integration
```bash
# Start any chat session
maia chat -s journal:7

# Use /e to edit, then Ctrl+B to browse
You: /e
# Press Ctrl+B to open unified browser
# Modify selections and press Enter
```

#### Unified Browser Interface
```
🔍 acme | Sources: 5 databases, 7 channels | Selected: 8/12 | ↑↓ Navigate SPACE Toggle ENTER Confirm ESC Cancel

📄 Regular Sources:
☑       acme.cpj:7
☐       acme.epics:all
☑       acme.gmail:20        # ← Custom day value
☑       acme.journal:7
☑       acme.stories:7

💬 Discord Sources:
☑       acme.tg#announcements:7
☐       acme.tg#customer-support:7
☑       acme.tg#dev-work:30    # ← Custom day value
☑       acme.tg#maker-work:all  # ← Text day value
```

#### Browser Controls
| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate sources |
| `Space` | Toggle selection |
| `0-9` / `a-z` | Edit day values |
| `Backspace` | Delete characters |
| `Enter` | Apply selections |
| `Esc` | Cancel changes |

**See [UNIFIED_BROWSER.md](UNIFIED_BROWSER.md) for complete documentation.**

### 🎮 Discord Integration

#### Setup & Configuration
```bash
# Configure Discord for a workspace
maia workspace discord-setup myworkspace --server-id YOUR_DISCORD_SERVER_ID

# Add Discord database
maia database add discord --type discord --id YOUR_SERVER_ID --workspace myworkspace

# Test Discord connectivity
maia discord debug-channels --workspace myworkspace
maia discord list-channels myworkspace

# Test sync with specific channel
maia database sync -s myworkspace.discord:1.channel_name=general
```

#### Interactive Browse Mode
```bash
# Use unified browser for Discord channels (see Unified Browser section above)
maia chat -b workspace.discord                      # Browse Discord channels
maia chat -b workspace.discord workspace.yeeps_discord # Multiple Discord databases

# Direct channel specification still supported
maia sync -s workspace.discord:7.channel_name=announcements
maia sync -s workspace.discord:30.author_name=admin
```

**Note**: The old Discord-specific browser has been replaced by the unified browser. 
Use the unified browser (see above) for interactive Discord channel selection.

#### Direct Discord Sync
```bash
# Sync specific Discord channels
maia sync -s workspace.discord:7.channel_name=announcements
maia sync -s workspace.discord:30.author_name=admin

# Combined Discord and Notion sync
maia sync -s journal:5 -s workspace.discord:14.channel_name=general
```

### Multi-Source Chat & Sync
```bash
# Start chat with default settings
maia chat

# Chat with specific data sources
maia chat -s journal:7 -s awakenings:all
maia chat -s journal:30 -s cms:14

# Interactive unified browse mode (replaces old Discord browser)
maia chat -b workspace                               # Browse workspace sources
maia chat -b workspace.discord workspace            # Browse Discord + workspace
maia sync -b workspace:30                           # Browse for sync with day filter

# Combined sources with browse
maia chat -s journal:7 -b workspace.discord:14     # Mix manual with browse selection

# Direct Discord specifications
maia chat -s workspace.discord:7.channel_name=announcements
maia sync -s workspace.discord:14.channel_name=general

# Advanced filtering (see docs/ENHANCED_FILTERING.md for complete guide)
maia chat -s journal -f 'journal:created_time>2025-01-01'
maia chat -s workspace.discord:7 -f 'workspace.discord:author_name=admin'

# Available in chat:
# /pull        - Sync all databases and reload context
# /days        - Change context days
# /switch      - Change AI model (anthropic/openai/gemini)
# /clear       - Clear chat history
# /push        - Push conversation to Notion
# /quit        - Exit
```

### Property-Aware Search & Embeddings

Property embeddings enable semantic search on Notion database properties for queries like "stories with epic holiday launch".

#### Query Examples
```bash
# Semantic property search (title, text, rich_text, relation)
maia chat "stories with epic 2025 holiday launch"
maia chat "journal entries about project milestone 1.0"

# Filter property search (select, status, multi_select, people)
maia chat "stories with status in progress"
maia chat "tasks assigned to Consumer Product team"

# Combined constraints
maia chat "in-progress stories with epic holiday launch from last sprint"
```

#### Backfill Property Embeddings
```bash
# Sync property embeddings for existing content
python sync_property_embeddings.py

# Dry run to preview
python sync_property_embeddings.py --dry-run

# Sync specific workspace or database
python sync_property_embeddings.py --workspace acme
python sync_property_embeddings.py --database stories

# Force re-embed (overwrite existing)
python sync_property_embeddings.py --force

# Verbose output for debugging
python sync_property_embeddings.py --verbose
```

**See [property_embeddings.md](property_embeddings.md) for complete documentation.**

### Content Generation
```bash
# Generate blog content
maia write
maia write --days 14 --prompt "Write about productivity"
maia write --no-push                                 # Save to drafts/ instead of Notion

# Set default AI model
maia model
```

## Legacy Commands (Preserved for Workflows)

### CMS Operations
```bash
# Pull CMS entries from Notion
maia cms pull
maia cms pull --days 30
maia cms pull --force

# Push draft to CMS
maia cms push
maia cms push --title "My Post" --draft path/to/file.md

# Sync with Webflow
maia cms sync --collection abc123
maia cms sync --force-update
```

### Newsletter
```bash
# Send eligible CMS pages to newsletter via Resend
# Automatically includes Notion cover photos (uses Webflow-hosted images when available)
maia newsletter send

# Alternative command alias
maia news send
```

### Subscriber Migration
```bash
# Migrate subscribers from MailerLite to Resend
maia migrate                                    # Migrate only active subscribers
maia migrate --include-unsubscribed             # Include unsubscribed subscribers
maia migrate --audience-name "My Newsletter"    # Specify custom audience name
maia migrate --batch-size 100                   # Adjust batch size for import
```

## Configuration

### Database Configuration
Configuration is stored in `promaia.config.json`:

```json
{
  "global": {
    "default_sync_days": 7,
    "default_output_directory": "data",
    "vector_search": {
      "enabled": true,
      "property_embeddings": {
        "enabled": true,
        "embeddable_types": ["title", "text", "rich_text", "relation"],
        "filter_types": ["select", "status", "multi_select", "people"],
        "default_property_similarity_threshold": 0.75
      }
    }
  },
  "databases": {
    "journal": {
      "source_type": "notion",
      "database_id": "your_database_id",
      "nickname": "journal",
      "description": "Personal journal entries",
      "sync_enabled": true,
      "include_properties": true,
      "default_days": 7,
      "output_directory": "data/journal"
    }
  }
}
```

### Environment Variables
```bash
# AI Model API Keys
export ANTHROPIC_API_KEY="your_key"
export OPENAI_API_KEY="your_key" 
export GOOGLE_API_KEY="your_key"

# Legacy database IDs (auto-migrated to config)
export NOTION_JOURNAL_DATABASE_ID="your_id"
export NOTION_CMS_DATABASE_ID="your_id"

# Newsletter settings (for email sending)
export RESEND_API_KEY="your_resend_api_key"
export RESEND_FROM_EMAIL="newsletter@yourdomain.com"
export RESEND_FROM_NAME="Your Name"
export RESEND_TEST_EMAIL="your_test_email@domain.com"

# Optional settings
export WEBFLOW_COLLECTION_ID="your_id"
export MAIA_DEBUG="1"                               # Enable debug mode
```

## Multi-Source Query Syntax

### Basic Source Specifications
```bash
# Format: database_name:days
-s journal:7                    # Last 7 days from journal
-s awakenings:all              # All entries from awakenings
-s cms:30                      # Last 30 days from CMS

# Discord sources
-s workspace.discord:7         # Last 7 days from Discord
-s workspace.discord:30.channel_name=announcements # Specific channel
-s workspace.yeeps_discord:14.author_name=admin    # Author filter

# Browse mode (interactive TUI)
-b workspace.discord           # Browse Discord channels
-b workspace.discord:30        # Browse with 30-day default
-b discord yeeps_discord       # Browse multiple databases
```

### Advanced Filtering (Database Sync)
```bash
# Date filters
maia database sync --sources journal[date>-30d]     # Last 30 days
maia database sync --sources cms[date>2024-01-01]   # After specific date

# Property filters
maia database sync --sources cms[status=published]   # Filter by property
maia database sync --sources stories[team=plush]     # Multiple filters supported
```

## Common Workflows

### Daily Journal & Team Sync
```bash
maia database sync journal                           # Sync journal database
maia chat -s journal:7                             # Chat with recent entries

# Discord team updates
maia sync -b team.discord:1                        # Sync yesterday's Discord
maia chat -b team.discord:1                        # Chat with recent team updates
```

### Team Communication Analysis
```bash
# Browse and sync team Discord channels
maia sync -b team.discord team.dev_discord
maia chat -s project_notes:14 -b team.discord:7    # Combine project notes with Discord

# Analyze specific channels
maia chat -s team.discord:7.channel_name=announcements
```

### Content Creation with Team Context
```bash
maia sync -s cms:7 -b team.discord:14              # Sync content and team discussions
maia write --prompt "Blog post based on recent team discussions"
maia cms push                                       # Push to CMS
```

### Multi-Source Analysis
```bash
# Traditional multi-source
maia chat -s journal:30 -s awakenings:all -s projects:14

# With Discord integration
maia chat -s journal:7 -s projects:14 -b team.discord:7 team.community_discord:14
```

### Weekly Team Retrospective
```bash
# Comprehensive team analysis
maia chat -s team_notes:7 -b team.discord:7 team.dev_discord:7
# Ask: "What were the key discussions and decisions this week?"
```

### Blog Publishing Pipeline
```bash
maia cms pull                                       # Pull latest CMS entries
maia cms sync                                       # Sync to Webflow
maia newsletter send                                # Send to email campaign with cover photos
```

## Debug and Help

```bash
# Enable debug mode
maia --debug <command>

# Get help for any command
maia --help
maia database --help
maia chat --help

# See detailed multi-source documentation
# docs/guides/MULTI_SOURCE_CHAT_GUIDE.md
```

## Performance Tips

- Use specific day filters rather than `:all` for faster loading
- Configure `sync_enabled: false` for databases you don't need regularly
- Use `maia database test` to verify connections before syncing
- Check `data/` directories for locally cached content

## Migration from Legacy Commands

The journal commands have been removed. Use the database system instead:

```bash
# Old (removed)
maia journal pull --days 7

# New (recommended)  
maia database sync journal --days 7

# Or configure and use
maia database sync                                   # Syncs all enabled databases
``` 