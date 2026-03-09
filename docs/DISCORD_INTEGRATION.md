# Discord Integration Guide

> Complete guide for Discord integration in Promaia - setup, sync, chat, and troubleshooting

## 📋 Table of Contents

1. [Overview](#overview)
2. [Setup & Configuration](#setup--configuration)
3. [Interactive Browse Mode](#interactive-browse-mode)
4. [Sync Commands](#sync-commands)
5. [Chat Integration](#chat-integration)
6. [Advanced Usage](#advanced-usage)
7. [Troubleshooting](#troubleshooting)
8. [Best Practices](#best-practices)

## 🎯 Overview

Promaia's Discord integration provides:

- **Interactive Channel Browsing**: Visual TUI for selecting Discord channels
- **Per-Channel Day Filtering**: Individual day specifications for each channel
- **Emoji & Special Character Support**: Handles Discord channel names with emojis
- **Multi-Server Support**: Browse channels across multiple Discord servers
- **Intelligent Sync**: Parallel processing with optimized API calls
- **Seamless Chat Integration**: Use Discord content as context for AI chat

### Key Features
- ✅ **Visual Channel Browser** with keyboard navigation
- ✅ **Per-Channel Day Cycling** (1→7→14→30→60→90 days)
- ✅ **Real-time Filtering** by channel name
- ✅ **Channel Name Reverse Lookup** for emoji channels
- ✅ **Workspace Inference** from database specifications
- ✅ **Combined Sources** (mix Discord with Notion/Gmail)

## 🚀 Setup & Configuration

### 1. Discord Bot Setup

First, create a Discord bot and get required credentials:

1. **Go to [Discord Developer Portal](https://discord.com/developers/applications)**
2. **Create New Application** → Give it a name
3. **Go to "Bot" section** → Click "Add Bot"
4. **Copy the Bot Token** (keep this secret!)
5. **Go to "OAuth2" → "URL Generator"**:
   - Scopes: `bot`
   - Bot Permissions: `Read Messages`, `Read Message History`, `View Channels`
6. **Invite bot to your Discord server** using the generated URL

### 2. Workspace Configuration

```bash
# Configure Discord for a workspace
maia workspace discord-setup myworkspace --server-id YOUR_DISCORD_SERVER_ID

# You'll be prompted to enter your bot token
# This creates: credentials/myworkspace/discord_credentials.json
```

### 3. Database Configuration

Add Discord databases to your configuration:

```bash
# Add Discord database
maia database add discord \
  --type discord \
  --id YOUR_DISCORD_SERVER_ID \
  --workspace myworkspace \
  --description "Main Discord server"

# Add additional Discord servers (optional)
maia database add yeeps_discord \
  --type discord \
  --id ANOTHER_DISCORD_SERVER_ID \
  --workspace myworkspace \
  --description "Secondary Discord server"
```

### 4. Verify Setup

```bash
# Test Discord connectivity
maia discord debug-channels --workspace myworkspace

# List available channels
maia discord list-channels myworkspace

# Test sync with a specific channel
maia database sync -s myworkspace.discord:1.channel_name=general
```

## 🎮 Interactive Browse Mode

### Basic Usage

```bash
# Browse Discord channels for sync
maia sync -b myworkspace.discord

# Browse Discord channels for chat
maia chat -b myworkspace.discord

# Browse multiple Discord databases
maia sync -b myworkspace.discord myworkspace.yeeps_discord
```

### Browser Interface

```
🎮 Discord Channel Browser

🔍 Filter: (type to search)

📂 My Discord Server (myworkspace.discord)
    ☑ #📢・announcements (30 days)
    ☐ #💬・general (30 days)
>>> ☑ #🗞️・release-notes (7 days)
    ☐ #🎮・gaming (30 days)

📂 Secondary Server (myworkspace.yeeps_discord)
    ☐ #📣・updates (30 days)
    ☑ #🛠️・development (14 days)

📊 Selected: 3 channels

↑↓ Navigate  SPACE Toggle  D Cycle Days  ENTER Confirm  ESC Cancel
```

### Browser Controls

| Key | Action |
|-----|--------|
| **↑↓ Arrow Keys** | Navigate between channels |
| **Spacebar** | Toggle channel selection (☐ ↔ ☑) |
| **D** | Cycle days for highlighted channel (1→7→14→30→60→90) |
| **Type** | Filter channels by name in real-time |
| **Enter** | Confirm selection and proceed |
| **Escape** | Cancel and exit browser |

### Day Cycling

Each channel has independent day filtering:

```bash
# Press 'D' to cycle through day options:
#announcements (1 days)   → (7 days)   → (14 days)  → 
#announcements (30 days)  → (60 days)  → (90 days)  → (1 days)
```

## 🔄 Sync Commands

### Basic Sync

```bash
# Interactive browse sync
maia sync -b myworkspace.discord                    # Browse and select channels
maia sync -b myworkspace.discord:30                 # Browse with 30-day default
maia sync -b discord yeeps_discord                  # Browse multiple databases

# Direct sync with source specifications
maia sync -s myworkspace.discord:7.channel_name=announcements
maia sync -s myworkspace.discord:30 myworkspace.yeeps_discord:14
```

### Combined Sync

```bash
# Mix regular sources with Discord browse
maia sync -s journal:5 -b myworkspace.discord:30

# Mix multiple source types
maia sync -s journal:7 -s stories:14 -b myworkspace.discord myworkspace.yeeps_discord
```

### Results

```bash
✅ Selected 3 Discord channels for sync:
   • myworkspace.discord:30 → #announcements
   • myworkspace.discord:7 → #release-notes  
   • myworkspace.yeeps_discord:14 → #development

🚀 Starting sync with combined sources:
   Regular sources: ['journal:5']
   Discord sources: ['myworkspace.discord:30.channel_name=announcements', ...]

🔄 DATABASE SYNC SUMMARY
──────────────────────────────
✅ SUCCESSFUL SYNCS (4 databases)
  📊 journal • 💾 12 saved • 0 skipped • ⏱️ 2.1s
  📊 myworkspace.discord • 💾 45 saved • 0 skipped • ⏱️ 1.8s
  📊 myworkspace.discord • 💾 23 saved • 0 skipped • ⏱️ 1.6s
  📊 myworkspace.yeeps_discord • 💾 67 saved • 0 skipped • ⏱️ 2.3s
📈 TOTALS
   💾 147 saved • ⏭️ 0 skipped
```

## 💬 Chat Integration

### Basic Chat

```bash
# Interactive browse for chat
maia chat -b myworkspace.discord

# Chat with specific Discord content
maia chat -s myworkspace.discord:7.channel_name=announcements
```

### Combined Chat

```bash
# Mix Discord with other sources
maia chat -s journal:7 -b myworkspace.discord:30

# Multi-source context
maia chat -s journal:5 -s stories:10 -b myworkspace.discord myworkspace.yeeps_discord
```

### Chat Session Example

```bash
🎮 Discord Channel Browser
# [Select channels: #announcements, #release-notes]

✅ Selected 2 Discord channels:
   • myworkspace.discord:30 → #announcements
   • myworkspace.discord:7 → #release-notes

💬 Starting chat with combined sources:
   Regular sources: None
   Discord sources: ['myworkspace.discord:30', 'myworkspace.discord:7']

Applied property filters: 45 pages remain after filtering

🐙 maia chat
Query: maia chat -s myworkspace.discord:30 -s myworkspace.discord:7 -f "..."
Pages loaded: 45
discord: 45
Model: Gemini 2.5 Pro

You: What are the recent announcements and updates?

AI: Based on the recent Discord messages, here are the key announcements and updates:

[AI analyzes Discord content and provides summary]
```

## 🔧 Advanced Usage

### Complex Filtering

```bash
# Filter by channel and date
maia chat -s myworkspace.discord:30 -f 'myworkspace.discord:created_time>2025-01-01'

# Filter by author
maia chat -s myworkspace.discord:14 -f 'myworkspace.discord:author_name=admin'

# Filter by content
maia chat -s myworkspace.discord:7 -f 'myworkspace.discord:content~announcement'
```

### Workspace Inference

```bash
# These commands automatically infer workspace from database names:
maia sync -b myworkspace.discord                    # Infers workspace: 'myworkspace'
maia sync -b team.discord team.yeeps_discord        # Infers workspace: 'team'
maia chat -b company.internal_discord               # Infers workspace: 'company'
```

### Batch Operations

```bash
# Sync multiple Discord databases efficiently
maia sync -b \
  workspace1.discord:30 \
  workspace1.community_discord:14 \
  workspace1.dev_discord:7

# Generate content based on Discord discussions
maia write \
  --prompt "Summarize recent team discussions and create action items" \
  --context workspace.discord:7
```

## 🐛 Troubleshooting

### Common Issues

#### 1. "No channel specified in filters"

**Problem**: Discord sync returns 0 messages
**Cause**: Channel name mismatch between local and Discord server
**Solution**: Use debug command to verify channel names

```bash
# Debug Discord connectivity
maia discord debug-channels --workspace myworkspace

# Check available channels
maia discord list-channels myworkspace

# Verify channel name mapping
# Local: "announcements" should map to Discord: "📢・announcements"
```

#### 2. "Channel not found in server"

**Problem**: Browser shows channels but sync fails
**Cause**: Bot lacks permissions or channel is private
**Solution**: 

```bash
# Verify bot permissions in Discord server:
# - Read Messages
# - Read Message History  
# - View Channels

# Check bot can access the channel:
# - Channel isn't private/restricted
# - Bot has proper role permissions
```

#### 3. "Empty channels list in browser"

**Problem**: Discord browser shows no channels
**Cause**: Credentials or server ID mismatch
**Solution**:

```bash
# Verify credentials
cat credentials/myworkspace/discord_credentials.json

# Expected format:
{
  "bot_token": "YOUR_BOT_TOKEN",
  "default_server_id": "YOUR_SERVER_ID",
  "workspace": "myworkspace"
}

# Re-run setup if needed
maia workspace discord-setup myworkspace --server-id CORRECT_SERVER_ID
```

#### 4. "Workspace inference failed"

**Problem**: "No workspace specified" error
**Solution**: Use explicit workspace or properly qualified database names

```bash
# ❌ Ambiguous
maia sync -b discord

# ✅ Explicit workspace
maia sync -b myworkspace.discord
# OR
maia sync -b discord --workspace myworkspace
```

### Debug Commands

```bash
# Comprehensive Discord debugging
maia discord debug-channels --workspace myworkspace

# Test basic connectivity
maia database test discord

# Check database configuration
maia database info discord

# Verify file structure
ls -la data/md/discord/myworkspace/
```

### Log Analysis

```bash
# Run with debug logging
maia --debug sync -b myworkspace.discord

# Common log messages:
# ✅ "Mapped sanitized name 'announcements' to Discord channel '📢・announcements'"
# ✅ "Found 45 messages in channel announcements"
# ❌ "Channel 'announcements' not found in server"
# ❌ "Discord bot token not provided in config"
```

## 🏆 Best Practices

### 1. Channel Organization

**Recommended Structure**:
```
📂 Main Server (workspace.discord)
├── 📢・announcements      # Official updates
├── 💬・general           # General discussion  
├── 🗞️・release-notes     # Product updates
└── 🛠️・development       # Technical discussion

📂 Community Server (workspace.community_discord)  
├── 🎮・gaming            # Community gaming
├── 🎨・creative          # Creative content
└── 📝・feedback          # User feedback
```

### 2. Day Filtering Strategy

**Recommended Day Ranges**:
- **Announcements**: 30-90 days (historical context)
- **General Chat**: 7-14 days (recent conversations)
- **Release Notes**: 60-90 days (product history)
- **Development**: 14-30 days (technical context)

### 3. Sync Scheduling

**Recommended Sync Frequency**:
```bash
# Daily: Recent activity sync
maia sync -b workspace.discord:1

# Weekly: Broader context sync  
maia sync -b workspace.discord:7 workspace.community_discord:7

# Monthly: Full historical sync
maia sync -b workspace.discord:30 workspace.community_discord:30
```

### 4. Chat Context Management

**Effective Context Combinations**:
```bash
# Project updates context
maia chat -s project_notes:14 -b workspace.discord:7

# Product feedback analysis
maia chat -s feedback_db:30 -b workspace.community_discord:14

# Release planning context
maia chat -s roadmap:60 -b workspace.discord:30 workspace.dev_discord:14
```

### 5. Security Considerations

**Bot Token Security**:
- ✅ Store bot tokens in `credentials/` directory (git-ignored)
- ✅ Use workspace-specific bot tokens when possible
- ✅ Regularly rotate bot tokens
- ❌ Never commit bot tokens to version control
- ❌ Don't share bot tokens between environments

**Permission Management**:
- ✅ Grant minimal required permissions to Discord bot
- ✅ Use role-based access for sensitive channels
- ✅ Regularly audit bot permissions
- ✅ Monitor bot activity in Discord audit logs

### 6. Performance Optimization

**Efficient Sync Strategies**:
```bash
# ✅ Use specific day ranges
maia sync -b workspace.discord:7  # vs unlimited days

# ✅ Target specific channels
maia sync -s workspace.discord:14.channel_name=announcements

# ✅ Combine related sources
maia sync -s journal:7 -b workspace.discord:7  # vs separate commands

# ✅ Use parallel sync for multiple channels
maia sync -b workspace.discord workspace.community_discord  # parallel processing
```

## 📊 Example Workflows

### Daily Team Standup

```bash
# Morning: Get yesterday's updates
maia chat -b team.discord:1 -b team.dev_discord:1

# Ask: "What were the key updates and blockers discussed yesterday?"
```

### Weekly Release Planning  

```bash
# Weekly: Analyze recent feedback and development
maia chat -s roadmap:30 -b team.discord:7 -b team.community_discord:14

# Ask: "Based on recent discussions, what should be prioritized for next release?"
```

### Monthly Team Retrospective

```bash
# Monthly: Comprehensive team communication analysis
maia chat -s team_notes:30 -b team.discord:30 -b team.dev_discord:30

# Ask: "Analyze team communication patterns and suggest improvements"
```

### Content Creation Pipeline

```bash
# Sync recent discussions
maia sync -b company.discord:7 -b company.community_discord:14

# Generate content based on community discussions
maia write \
  --prompt "Create a blog post about recent feature requests and community feedback" \
  --context company.community_discord:14

# Create newsletter content
maia newsletter sync -s company.discord:7
```

---

## 🎉 Conclusion

Discord integration in Promaia provides a powerful way to incorporate team communications and community discussions into your AI-powered workflows. The interactive browse mode makes it easy to select relevant channels, while the flexible sync options ensure you have the right context for your specific use case.

**Key Takeaways**:
- ✅ Use the interactive browser for visual channel selection
- ✅ Leverage per-channel day filtering for optimal context
- ✅ Combine Discord with other sources for comprehensive analysis
- ✅ Follow security best practices for bot token management
- ✅ Use debug commands when troubleshooting connectivity issues

For additional help, see the [main README](../README.md) or [Enhanced Filtering Guide](ENHANCED_FILTERING.md). 