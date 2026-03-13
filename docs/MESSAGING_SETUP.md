# Messaging Platform Setup Guide

## Overview

Promaia supports conversational AI on both **Slack** and **Discord** using a unified, platform-agnostic architecture. Calendar events can trigger agents to initiate natural, multi-turn conversations with comprehensive edge case handling.

## Key Features

- **Platform Agnostic:** Same conversation logic works on Slack and Discord
- **Calendar Triggered:** Schedule conversations via Google Calendar
- **Natural Conversations:** Multi-turn dialogue with timeout handling
- **Security:** User validation, rate limiting, malicious input detection
- **Self-Configuration:** Agents can manage their own messaging settings
- **Audit Logging:** All conversations logged locally

---

## Slack Setup

### 1. Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click "Create New App" → "From scratch"
3. Name: "Promaia" (or your choice)
4. Choose your workspace

### 2. Configure Bot Permissions

**OAuth & Permissions → Bot Token Scopes:**

Required scopes:
- `chat:write` - Send messages
- `channels:history` - Read channel history
- `channels:read` - View channels
- `users:read` - Read user information
- `commands` - Slash commands (optional)

### 3. Enable Socket Mode

1. **Settings → Socket Mode**: Enable Socket Mode
2. Generate App-Level Token
   - Name: "Socket Mode Token"
   - Scopes: `connections:write`
3. Copy the token (starts with `xapp-...`)

### 4. Install App to Workspace

1. **OAuth & Permissions** → Install to Workspace
2. Authorize the app
3. Copy the **Bot User OAuth Token** (starts with `xoxb-...`)

### 5. Add to Channels

Invite the bot to channels where it should operate:
```
/invite @Promaia
```

### 6. Environment Configuration

Add to `.env`:

```bash
# Slack Bot Tokens
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here
```

### 7. Test Connection

```bash
python -c "from promaia.connectors.slack_connector import SlackConnector; import asyncio; c = SlackConnector({'workspace': 'client_workspace', 'bot_token': 'xoxb-...'}); asyncio.run(c.test_connection())"
```

### 8. Start Slack Bot

```bash
python -m promaia.messaging.slack_bot
```

You should see: "Bot is ready to receive messages!"

---

## Discord Setup

### 1. Create Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click "New Application"
3. Name: "Promaia"

### 2. Configure Bot

1. **Bot** → Add Bot
2. **Privileged Gateway Intents:**
   - ✅ Message Content Intent
   - ✅ Server Members Intent
   - ✅ Presence Intent
3. Copy the **Token**

### 3. Invite Bot to Server

1. **OAuth2 → URL Generator**
2. Scopes:
   - `bot`
   - `applications.commands`
3. Bot Permissions:
   - Send Messages
   - Read Message History
   - Read Messages/View Channels
4. Copy the generated URL and open in browser
5. Select your server and authorize

### 4. Environment Configuration

Add to `.env`:

```bash
# Discord Bot Token
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_SERVER_ID=your-server-id
```

To get server ID: Enable Developer Mode in Discord settings, right-click server → Copy ID

### 5. Test Connection

```bash
python -c "from promaia.connectors.discord_connector import DiscordConnector; import asyncio; c = DiscordConnector({'workspace': 'client_workspace', 'bot_token': 'your-token', 'database_id': 'server-id'}); asyncio.run(c.test_connection())"
```

### 6. Start Discord Bot

```bash
python -m promaia.discord_bot.bot
```

---

## Agent Configuration

### Option 1: Manual Configuration

Edit `promaia.config.json`:

```json
{
  "agents": [
    {
      "name": "Sprint Assistant",
      "agent_id": "sprint-assistant",
      "workspace": "client_workspace",
      "databases": ["tasks:30", "notes:7"],
      "sdk_enabled": true,
      "max_iterations": 8,
      
      "messaging_platform": "slack",
      "messaging_channel_id": "C06ABCD1234",
      "messaging_enabled": true,
      "initiate_conversation": true,
      "conversation_timeout_minutes": 15,
      "conversation_max_turns": 20,
      
      "calendar_id": "sprint-assistant@group.calendar.google.com",
      "output_notion_page_id": "abc123def456"
    }
  ]
}
```

**Field Descriptions:**

- `messaging_platform`: `"slack"` or `"discord"`
- `messaging_channel_id`: Platform-specific channel ID
  - Slack: Get from channel details (e.g., `C06ABCD1234`)
  - Discord: Right-click channel → Copy ID (e.g., `123456789012345678`)
- `messaging_enabled`: `true` to enable messaging
- `initiate_conversation`: 
  - `true` - Start interactive multi-turn conversations
  - `false` - Post output as one-way message
- `conversation_timeout_minutes`: Minutes before timeout (default 15)
- `conversation_max_turns`: Max turns before ending (null = unlimited)

### Option 2: Conversational Configuration (Recommended!)

**Let the agent configure itself:**

```
You: Hey Sprint Assistant, can you start posting updates to our Slack #engineering channel?

Agent: I'd be happy to! Let me check which channels are available...

[Agent uses: list_available_messaging_channels(platform="slack")]

Agent: I found these channels:
- #engineering (C06ABC123)
- #general (C06DEF456)
- #sprint-updates (C06GHI789)

Should I post to #engineering?

You: Yes, and make it interactive so we can discuss

Agent: Perfect! Configuring now...

[Agent uses: update_agent_messaging_config(
    messaging_platform="slack",
    messaging_channel_id="C06ABC123",
    messaging_enabled=True,
    initiate_conversation=True
)]

Agent: ✓ Done! I'll now post sprint updates to #engineering on Slack as interactive conversations.
```

---

## Running the Complete System

### Deployment Architecture

```bash
# Terminal 1: Calendar Monitor (triggers agents)
python -m promaia.gcal.agent_calendar_monitor

# Terminal 2: Slack Bot (listens for messages)
python -m promaia.messaging.slack_bot

# Terminal 3: Discord Bot (optional, if using Discord)
python -m promaia.discord_bot.bot
```

All three processes should run simultaneously.

### Process Manager (Recommended)

Use `supervisord` or `systemd` for production:

**supervisord.conf:**
```ini
[program:promaia_calendar]
command=python -m promaia.gcal.agent_calendar_monitor
directory=/path/to/promaia
autostart=true
autorestart=true

[program:promaia_slack_bot]
command=python -m promaia.messaging.slack_bot
directory=/path/to/promaia
autostart=true
autorestart=true

[program:promaia_discord_bot]
command=python -m promaia.discord_bot.bot
directory=/path/to/promaia
autostart=true
autorestart=true
```

---

## Usage Examples

### Example 1: Slack Conversation

**Calendar Event:**
- Summary: "Sprint Review"
- Description: "Review sprint progress and blockers"
- Time: Today 2 PM

**What Happens:**

```
2:00 PM - Agent posts to #engineering:
"Hi! Ready to discuss your sprint progress?"

User (2:05 PM):
"Yes, what's the status?"

Agent (2:05 PM):
[Queries tasks database]
"15 tasks completed, 3 blocked, 7 in progress. 
Key blockers: API integration pending review..."

User (2:10 PM):
"What's blocking the API integration?"

Agent (2:10 PM):
[Queries vector search for related content]
"Based on recent discussions, the API integration..."
```

If user doesn't respond for 15 minutes → timeout message sent

### Example 2: Platform Migration

```
User: We're switching from Slack to Discord. Can you migrate?

Agent: Sure! Let me check Discord channels...

[Uses: list_available_messaging_channels(platform="discord")]

Agent: I found:
- #sprint-planning (123456789)
- #engineering (987654321)

Which channel should I use?

User: #sprint-planning

Agent: Migrating...

[Uses: update_agent_messaging_config(
    messaging_platform="discord",
    messaging_channel_id="123456789"
)]

Agent: ✓ Migrated to Discord #sprint-planning! 
Next scheduled message will appear there.
```

---

## Security Features

### 1. User Validation
- Only the user who started the conversation can continue it
- Prevents conversation hijacking

### 2. Rate Limiting
- Max 10 messages per minute per user
- Prevents spam and DOS attacks

### 3. Malicious Input Detection

Blocks messages containing:
- Prompt injection attempts ("ignore previous instructions")
- SQL injection patterns (`'; DROP TABLE`)
- XSS attempts (`<script>`)
- Path traversal (`../`)
- Excessive special characters (>30%)

**Agent Response:** "I can't process that message."

### 4. Audit Logging

All conversations logged to:
- SQLite database: `~/.promaia/conversations.db`
- Includes: full message history, timestamps, security events

Query conversation history:
```bash
sqlite3 ~/.promaia/conversations.db "SELECT * FROM conversations WHERE agent_id='sprint-assistant'"
```

---

## Slash Commands

### Slack Commands

- `/promaia-reset` - End current conversation and start fresh
- `/promaia-status` - Check active conversation status

### Discord Commands  

- `!maia reset` - End current conversation
- `!maia status` - Check conversation status

---

## Troubleshooting

### "Bot not responding on Slack"

1. Check bot is running: `ps aux | grep slack_bot`
2. Verify tokens in `.env`
3. Check bot is in channel: `/invite @Promaia`
4. Review logs for errors

### "No active conversation found"

- Agent must initiate conversation (via calendar or manual trigger)
- Check `messaging_enabled: true` and `initiate_conversation: true` in config
- Verify conversation hasn't timed out (default 15 min)

### "Permission denied"

- Verify bot has required scopes (Slack) or intents (Discord)
- Check bot is member of target channel
- Review bot permissions in channel settings

### "Rate limit exceeded"

- User sending messages too quickly (>10/min)
- Wait 60 seconds and try again
- Or adjust rate limit in `conversation_security.py`

---

## Best Practices

### 1. Timeout Configuration

**Short tasks (5-10 min):**
```json
"conversation_timeout_minutes": 10
```

**Long discussions (30+ min):**
```json
"conversation_timeout_minutes": 30
```

### 2. Turn Limits

**Quick updates:**
```json
"conversation_max_turns": 5
```

**Deep discussions:**
```json
"conversation_max_turns": null
```

### 3. Calendar Event Descriptions

**Good:**
```
Event Description: "Review sprint progress, identify blockers, suggest next steps"
```

Agent uses this as initial conversation prompt.

**Bad:**
```
Event Description: "Meeting"
```

Too vague - agent won't know what to discuss.

### 4. Channel Selection

**Private channels:** Better for sensitive discussions
**Public channels:** Better for team-wide updates

### 5. Testing

Test with calendar event set 2 minutes in future:
```python
# test_calendar_conversation.py
from promaia.gcal import get_calendar_manager

gcal = get_calendar_manager()
gcal.create_event(
    calendar_id="agent-calendar-id",
    summary="Test Conversation",
    description="Test messaging integration",
    start_time=datetime.now() + timedelta(minutes=2),
    duration_minutes=30
)
```

---

## Advanced: Multi-Platform Deployment

Run conversations on BOTH Slack and Discord simultaneously:

**Agent 1 Config (Slack):**
```json
{
  "agent_id": "assistant-slack",
  "messaging_platform": "slack",
  "messaging_channel_id": "C06ABC123"
}
```

**Agent 2 Config (Discord):**
```json
{
  "agent_id": "assistant-discord",  
  "messaging_platform": "discord",
  "messaging_channel_id": "987654321"
}
```

Both agents share same data sources but communicate on different platforms.

---

## API Reference

### Agent Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `messaging_platform` | string | "slack" or "discord" |
| `messaging_channel_id` | string | Platform-specific channel ID |
| `messaging_enabled` | boolean | Enable messaging integration |
| `initiate_conversation` | boolean | Interactive vs one-way |
| `conversation_timeout_minutes` | int | Timeout (default 15) |
| `conversation_max_turns` | int\|null | Turn limit |

### Agent Self-Configuration Tools

**get_agent_messaging_config()**
- Returns current messaging configuration
- No parameters required

**update_agent_messaging_config()**
- Updates messaging configuration
- Parameters: `messaging_platform`, `messaging_channel_id`, `messaging_enabled`, `initiate_conversation`, `conversation_timeout_minutes`
- Only provide fields to change

**list_available_messaging_channels(platform)**
- Lists accessible channels
- Parameters: `platform` ("slack" or "discord")

---

## Support

For issues or questions:
1. Check bot logs for errors
2. Verify tokens and permissions
3. Test connection with connector test scripts
4. Review conversation database for state issues

**Conversation Database:**
```bash
sqlite3 ~/.promaia/conversations.db
```

**View active conversations:**
```sql
SELECT * FROM conversations WHERE status='active';
```

**View conversation history:**
```sql
SELECT conversation_id, agent_id, platform, status, turn_count, created_at 
FROM conversations 
ORDER BY created_at DESC 
LIMIT 10;
```
