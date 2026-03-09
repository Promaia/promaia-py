# Getting Started with Promaia

> Quick start guide for Promaia's multi-source content management and AI-powered workflows

## 🚀 5-Minute Setup

### 1. Initial Installation

```bash
# Clone and install
git clone <repository-url>
cd promaia
pip install -e .

# Copy environment template
cp docs/env.template .env
```

### 2. Add Your First Workspace

```bash
# Add a workspace with your Notion API key
maia workspace add myteam --api-key your_notion_token

# Set as default
maia workspace set-default myteam
```

### 3. Connect Your First Database

**Option A: Notion Database**
```bash
maia database add journal \
  --type notion \
  --id your_notion_database_id \
  --workspace myteam \
  --description "Team journal"
```

**Option B: Discord Server (Recommended)**
```bash
# Set up Discord integration
maia workspace discord-setup myteam --server-id your_discord_server_id
# (Enter your Discord bot token when prompted)

# Add Discord database
maia database add discord \
  --type discord \
  --id your_discord_server_id \
  --workspace myteam \
  --description "Team Discord"
```

### 4. Test Your Setup

```bash
# List your databases
maia database list

# Test connectivity
maia database test journal  # or discord

# Try your first sync
maia sync -s journal:1      # Sync 1 day of journal
# OR for Discord
maia sync -b myteam.discord # Interactive Discord browse
```

## 🎮 Try Discord Browse (Recommended First Experience)

The Discord integration provides the most interactive and immediate experience:

```bash
# Interactive Discord channel browser
maia sync -b myteam.discord
```

**What you'll see:**
```
🎮 Discord Channel Browser

🔍 Filter: (type to search)

📂 My Team Server (myteam.discord)
    ☐ #📢・announcements (30 days)
>>> ☑ #💬・general (7 days)
    ☐ #🗞️・release-notes (14 days)

📊 Selected: 1 channels

↑↓ Navigate  SPACE Toggle  D Cycle Days  ENTER Confirm  ESC Cancel
```

**Try these controls:**
- **Arrow keys**: Navigate between channels
- **Spacebar**: Select/deselect channels (☐ ↔ ☑)
- **D key**: Cycle days for highlighted channel (1→7→14→30→60→90)
- **Enter**: Confirm and sync selected channels

## 💬 Your First AI Chat

After syncing some content:

```bash
# Chat with your synced content
maia chat -s journal:7              # Chat with journal entries
maia chat -b myteam.discord         # Chat with Discord (browse mode)
maia chat -s journal:7 -b myteam.discord:14  # Combine sources

# Example conversation:
You: What were the key topics discussed this week?
AI: Based on your recent entries and Discord messages...
```

## 🔧 Essential Commands

### Daily Workflow
```bash
# Morning: Sync overnight activity
maia sync -b myteam.discord:1

# Work: Chat with context
maia chat -s journal:7 -b myteam.discord:7

# Evening: Broader sync for analysis
maia sync -s journal:7 -b myteam.discord:7
```

### Weekly Analysis
```bash
# Comprehensive weekly review
maia chat -s journal:7 -s projects:14 -b myteam.discord:7

# Ask questions like:
# "What were our biggest accomplishments this week?"
# "What challenges came up and how were they resolved?"
# "What should we prioritize next week?"
```

### Content Creation
```bash
# Sync content sources
maia sync -s blog_ideas:30 -b myteam.discord:14

# Generate content with context
maia write --prompt "Write a blog post about our recent project milestones"
```

## 🎯 Common Use Cases

### 1. Team Standup Preparation
```bash
# Get yesterday's team activity
maia chat -b myteam.discord:1

# Ask: "What were the key updates and blockers mentioned yesterday?"
```

### 2. Weekly Retrospective
```bash
# Comprehensive team analysis
maia chat -s meeting_notes:7 -b myteam.discord:7 myteam.dev_discord:7

# Ask: "Analyze this week's team dynamics and suggest improvements"
```

### 3. Product Release Planning
```bash
# Combine roadmap with community feedback
maia chat -s roadmap:30 -b myteam.discord:14 community.discord:30

# Ask: "Based on recent discussions, what features should we prioritize?"
```

### 4. Content Marketing
```bash
# Sync community discussions
maia sync -b community.discord:14 support.discord:7

# Generate content ideas
maia write --prompt "Create blog topics based on recent community questions"
```

## 🛠️ Troubleshooting

### Discord Setup Issues

**Problem: "No channels found"**
```bash
# Debug Discord connectivity
maia discord debug-channels --workspace myteam

# Check credentials
cat credentials/myteam/discord_credentials.json
```

**Problem: "Channel not found in server"**
```bash
# List available channels
maia discord list-channels myteam

# Verify bot permissions in Discord:
# - Read Messages
# - Read Message History
# - View Channels
```

### General Issues

**Problem: "No workspace specified"**
```bash
# Set default workspace
maia workspace set-default myteam

# Or use explicit workspace
maia sync -b myteam.discord --workspace myteam
```

**Problem: "Database not found"**
```bash
# List configured databases
maia database list

# Add missing database
maia database add <name> --type <type> --id <id> --workspace myteam
```

## 📚 Next Steps

### Explore Advanced Features
- **[Discord Integration Guide](DISCORD_INTEGRATION.md)**: Complete Discord setup and usage
- **[Enhanced Filtering](ENHANCED_FILTERING.md)**: Advanced filtering and query syntax
- **[Quick Reference](QUICK_REFERENCE.md)**: Command reference and examples

### Set Up Additional Sources
```bash
# Add Gmail integration
maia auth configure google

# Add more Notion databases
maia database add projects --type notion --id DATABASE_ID --workspace myteam

# Add secondary Discord servers
maia database add community_discord --type discord --id SERVER_ID --workspace myteam
```

### Automate Workflows
```bash
# Create daily sync script
echo "maia sync -b myteam.discord:1" > daily_sync.sh

# Schedule weekly analysis
echo "maia chat -s journal:7 -b myteam.discord:7" > weekly_review.sh
```

## 🎉 Welcome to Promaia!

You're now ready to leverage AI-powered workflows with your team's content. The Discord integration provides an intuitive starting point, while the multi-source capabilities enable sophisticated analysis across all your content sources.

**Key takeaways:**
- ✅ Start with Discord browse mode for the best first experience
- ✅ Combine multiple sources for richer context
- ✅ Use the interactive browser for visual channel selection
- ✅ Leverage per-channel day filtering for optimal relevance
- ✅ Ask specific questions to get actionable insights

Happy exploring! 🚀 