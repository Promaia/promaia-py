# Promaia - Notion Integration & Automation Framework

> A comprehensive Python framework for multi-source content management, AI-powered workflows, and intelligent synchronization

Promaia provides a powerful CLI interface and Python API for syncing content from multiple sources (Notion, Discord, Gmail), managing workspaces, and leveraging AI for content analysis and generation.

## ✨ Key Features

### 🔗 **Multi-Source Connectivity**
- **Notion Integration**: Full database sync, property management, and content processing
- **Discord Integration**: Interactive channel browsing, message sync with emoji support
- **Gmail Integration**: Email thread synchronization and management
- **Multi-Workspace Support**: Organize and manage multiple team/project workspaces

### 🚀 **Intelligent Sync & Browse**
- **Interactive Channel Browser**: Visual TUI for selecting Discord channels with per-channel day filtering
- **Smart Sync**: Combines regular sources (`-s`) with interactive browse (`-b`) functionality
- **Workspace Inference**: Automatically detects workspace from source specifications
- **Parallel Processing**: Concurrent sync across multiple databases for optimal performance

### 🎯 **Advanced Filtering & AI**
- **Context-Aware Chat**: AI chat with content from multiple synchronized sources
- **Complex Filtering**: Date ranges, property filters, multi-source queries
- **Natural Language Processing**: AI-powered content analysis and generation
- **Hybrid Storage**: Optimized architecture for different content types

### 📊 **Content Management**
- **Format Conversion**: Seamless conversion between Markdown, JSON, and structured formats
- **Newsletter Automation**: Content distribution and email workflows
- **CMS Integration**: Automated content workflows between platforms
- **Real-time Sync**: Timestamp tracking with conflict resolution

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd promaia

# Create and activate virtual environment (recommended)
# Install uv if you don't have it: brew install uv
uv venv
source venv/bin/activate

# Install dependencies
uv pip install .
pip3 install -e .

# Set up configuration
cp docs/env.template .env
# Edit .env and add your API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
```

### Initial Setup

**Note**: Promaia works best with Notion integration, but you can use basic chat features (`maia chat`) without Notion by just setting up your AI API keys in the `.env` file.

```bash
# Add a workspace (for Notion integration)
maia workspace add myworkspace --api-key your_notion_token

# Add a Notion database
# To get your database ID: Open the database in Notion, copy the URL
# Extract the ID between the slash and question mark: notion.so/workspace/DATABASE_ID?v=...
maia database add journal --id your_database_id --workspace myworkspace

# Set up Discord (optional)
maia workspace discord-setup myworkspace --server-id your_discord_server_id

# Test your setup
maia database list
maia database test journal
```

**Understanding Workspaces & Databases:**
- **Workspace**: Represents a Notion account/team (useful if you have multiple Notion accounts)
- **Database**: Individual Notion databases within a workspace
- Promaia adapts to any database schema - it works with any properties you've defined

### Sync Commands

```bash
# Basic sync with source specifications
maia sync -s journal:7 -s stories:30

# Interactive Discord channel browser
maia sync -b myworkspace.discord

# Combined sources with browse
maia sync -s journal:5 -b myworkspace.discord:30

# Multiple Discord databases
maia sync -b workspace.discord workspace.yeeps_discord
```

### Chat Commands

```bash
# Basic AI chat with synced content
maia chat -s journal:7

# Interactive Discord browse for chat
maia chat -b myworkspace.discord

# Combined multi-source chat
maia chat -s journal:5 -s stories:10 -b myworkspace.discord:30
```

## 📚 Core Commands

### 🔄 **Sync & Browse**
```bash
# Source-based sync
maia sync -s journal:7 -s stories:30           # Sync specific sources with days
maia database sync -s workspace.db:14          # Full database command syntax

# Interactive browse sync
maia sync -b workspace.discord                 # Browse & select Discord channels
maia sync -b workspace.discord:30              # Browse with 30-day default filter
maia sync -b discord yeeps_discord             # Browse multiple Discord databases

# Combined sync
maia sync -s journal:5 -b workspace.discord:7  # Mix regular sources with browse
```

### 💬 **AI Chat**
```bash
# Source-based chat
maia chat -s journal:7 -s stories:30           # Chat with specific content
maia chat -s workspace.database:14             # Full source specification

# Interactive browse chat  
maia chat -b workspace.discord                 # Browse & chat with Discord channels
maia chat -b workspace.discord:30              # Browse with day filtering

# Combined chat
maia chat -s journal:5 -b workspace.discord:7  # Mix sources with interactive browse
```

### 🗄️ **Database Management**
```bash
maia database list                              # List all configured databases
maia database add journal --id ID --workspace W # Add new database
maia database sync -s journal:7                # Sync specific database
maia database test journal                      # Test database connection
maia database status                           # Show sync status
```

### 🏢 **Workspace Management**
```bash
maia workspace list                            # List configured workspaces
maia workspace add team --api-key TOKEN       # Add new workspace
maia workspace set-default team               # Set default workspace
maia workspace discord-setup team             # Configure Discord integration
```

### 🎮 **Discord Integration**
```bash
maia discord browse team                      # Interactive Discord channel browser
maia discord list-channels team               # List available Discord channels
maia discord debug-channels --workspace team  # Debug Discord connectivity
```

### 📊 **Content & Analytics**
```bash
maia cms pull                                  # Pull CMS content from Notion
maia cms push                                  # Push local changes to Notion
maia newsletter sync                           # Sync newsletter content
maia convert --format json                    # Convert between storage formats
maia write --prompt "Generate content..."     # AI-powered content generation
```

## 🎮 Interactive Discord Browser

### Features
- **Visual Channel Selection**: Navigate with arrow keys, toggle with spacebar
- **Per-Channel Day Cycling**: Press `D` to cycle days (1→7→14→30→60→90) for individual channels
- **Multi-Server Support**: Browse channels across multiple Discord servers
- **Real-time Filtering**: Type to filter channels by name
- **Workspace Inference**: Automatically detects workspace from database specifications

### Usage Examples
```bash
# Basic Discord browse
maia sync -b workspace.discord
maia chat -b workspace.discord

# Multi-database Discord browse  
maia sync -b workspace.discord workspace.yeeps_discord
maia chat -b workspace.discord workspace.yeeps_discord

# Browse with day specifications
maia sync -b workspace.discord:30 workspace.yeeps_discord:7
maia chat -b workspace.discord:60
```

### Browser Controls
- **↑↓ Arrow Keys**: Navigate between channels
- **Spacebar**: Toggle channel selection
- **D Key**: Cycle days for highlighted channel (1→7→14→30→60→90)
- **Type**: Filter channels by name
- **Enter**: Confirm selection and proceed
- **Escape**: Cancel and exit

## 🏗️ Architecture

### Storage Structure
```
promaia/
├── promaia/               # Main package
│   ├── cli/              # Command-line interface
│   │   ├── database_commands.py      # Database sync commands
│   │   ├── discord_commands.py       # Discord integration
│   │   └── enhanced_commands.py      # Advanced chat/sync
│   ├── config/           # Configuration management
│   │   ├── databases.py             # Database configurations
│   │   └── workspaces.py            # Workspace management
│   ├── connectors/       # Data source connectors
│   │   ├── notion_connector.py      # Notion API integration
│   │   ├── discord_connector.py     # Discord bot integration
│   │   └── gmail_connector.py       # Gmail API integration
│   ├── storage/          # Unified storage system
│   └── ai/               # AI integration
├── data/                 # Content storage
│   ├── md/              # Markdown files
│   │   ├── notion/      # Notion content by workspace
│   │   ├── discord/     # Discord messages by server/channel
│   │   └── gmail/       # Gmail threads
│   └── json/            # JSON metadata
├── credentials/          # API credentials by workspace
│   └── workspace/
│       └── discord_credentials.json
└── docs/                # Documentation
```

### Data Flow
1. **Source Configuration**: Define databases and workspaces
2. **Interactive Browse**: Select channels/sources via TUI
3. **Intelligent Sync**: Parallel sync with optimized API calls
4. **Unified Storage**: Organize content by source type and workspace
5. **AI Processing**: Context-aware chat and content generation

## 🔧 Advanced Configuration

### Environment Variables & API Keys

Promaia uses environment variables for API keys and configuration. The `promaia.config.json` file references these variables using template literals:

```json
{
  "workspaces": {
    "myworkspace": {
      "api_key": "${NOTION_MYWORKSPACE_API_KEY}"
    }
  }
}
```

These placeholders are dynamically replaced at runtime with values from your `.env` file:

```bash
# .env file
ANTHROPIC_API_KEY=your_anthropic_key
OPENAI_API_KEY=your_openai_key
NOTION_MYWORKSPACE_API_KEY=your_notion_integration_token
```

### Discord Setup
```bash
# Set up Discord bot for workspace
maia workspace discord-setup myworkspace --server-id 123456789

# Test Discord connectivity
maia discord debug-channels --workspace myworkspace

# List available channels
maia discord list-channels myworkspace
```

### Source Specifications
```bash
# Format: database:days.property=value
maia sync -s journal:7                         # Last 7 days
maia sync -s workspace.database:30             # Qualified database name
maia sync -s discord:14.channel_name=general   # With property filter
```

### Multi-Source Filtering
```bash
# Complex filtering across sources
maia chat -s journal:7 -s stories:30 -f 'journal:created_time>2025-01-01' -f 'stories:"Status"=Published'

# Discord-specific filtering
maia chat -b workspace.discord -f 'workspace.discord:"channel_name=announcements"'
```

## 🧪 Development & Testing

### Development Setup
```bash
# Install development dependencies
pip3 install -r requirements.txt

# Run tests
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=promaia

# Format code
black promaia/ tests/
isort promaia/ tests/

# Lint code
flake8 promaia/ tests/
```

### Testing
```bash
# Test database connectivity
maia database test journal

# Test Discord integration
maia discord debug-channels --workspace myworkspace

# Test sync functionality
maia database status
maia sync -s journal:1

# Test chat functionality  
maia chat -s journal:1
```

## 📖 Documentation

### 👥 User Documentation
- **[Getting Started Guide](docs/GETTING_STARTED.md)**: 5-minute setup with Discord integration ⭐ NEW
- **[Discord Integration Guide](docs/DISCORD_INTEGRATION.md)**: Complete Discord setup, sync, and troubleshooting ⭐ NEW
- **[Enhanced Filtering Guide](docs/ENHANCED_FILTERING.md)**: Advanced filtering and query syntax for all sources
- **[Quick Reference](docs/QUICK_REFERENCE.md)**: Command reference with Discord examples
- **[Configuration Examples](docs/examples/)**: Sample configurations and workflows

### 🔧 Developer Documentation
- **[Build Documentation](build-docs/)**: Internal architecture, PRDs, and development guides
- **[Architecture Deep Dive](build-docs/INTELLIGENT_ARCHITECTURE.md)**: System design and data flow
- **[Migration Guides](build-docs/JSON_TO_METADATA_MIGRATION.md)**: Technical migration procedures

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with comprehensive tests
4. Ensure all tests pass (`python3 -m pytest tests/ -v`)
5. Follow code formatting standards (`black promaia/ tests/`)
6. Submit a pull request with detailed description

### Code Standards
- **Python 3.8+** compatibility
- **Type hints** for all public APIs
- **Comprehensive test coverage** (>90%)
- **Clear documentation** for all features
- **Error handling** with informative messages

## 📄 License

MIT License - see LICENSE file for details.

---

## 🎯 Example Workflows

### Daily Journaling Workflow
```bash
# Morning: Review recent entries
maia chat -s journal:7

# Throughout day: Sync new entries
maia sync -s journal:1

# Evening: Review and analyze patterns
maia chat -s journal:30 -f 'journal:created_time>2025-01-01'
```

### Team Communication Analysis
```bash
# Browse Discord channels for team updates
maia chat -b team.discord team.yeeps_discord

# Sync recent discussions
maia sync -b team.discord:7

# Generate summary of recent activity
maia write --prompt "Summarize recent team discussions" --context team.discord:7
```

### Content Management Pipeline
```bash
# Sync content sources
maia sync -s cms:7 -s stories:30

# Process and analyze content
maia chat -s cms -s stories -f 'cms:"Status"=Published'

# Generate newsletter
maia newsletter sync
```

*Built for seamless multi-source integration and AI-powered content workflows.*
