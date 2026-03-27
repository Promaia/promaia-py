# DM Conversation Persistence — PRD

## Problem

When Promaia has a DM conversation with someone on Slack or Discord, that conversation disappears after the session ends. Promaia can't recall what was discussed, continue a prior thread of thought, or learn from past interactions. Terminal chat conversations already save to `koii.convos` — Slack/Discord DMs should too.

## Insight

The messaging tools (`send_message`, `start_conversation`) already work. The missing piece isn't the tools — it's **persistence**. DM conversations need to save to a queryable database so Promaia can remember them.

## What Exists Today

| Feature | Terminal (`maia chat`) | Slack/Discord DMs |
|---------|----------------------|-------------------|
| Conversations save to history | Yes (`chat_history.json` → `koii.convos`) | No |
| Queryable via `query_sql` | Yes | No |
| Queryable via `query_vector` | Yes | No |
| Queryable via `query_source` | Yes | No |
| Conversation boundaries | Per session | 30-min timeout or `/new` |
| Continues prior conversation | Via history file | Lost on timeout |

### Existing infrastructure

- `ChatHistoryManager._save_to_database()` — saves terminal conversations to unified storage (SQLite + markdown + vector embeddings)
- `ConversationConnector` — indexes conversation content into the unified registry
- `koii.convos` database config — already exists, points to conversation storage
- `ConversationState` in `conversation_manager.py` — already tracks Slack/Discord DM state in SQLite
- 30-minute DM timeout in `slack_bot.py` — creates natural conversation boundaries

## Requirements

### Must Have

1. **Save DM conversations to unified storage** when they end (timeout, `/new`, or explicit close)
   - Same format as terminal conversations in `koii.convos`
   - Include: participants, platform, channel, timestamps, full message history
   - Trigger: on conversation boundary (30-min gap or explicit reset)

2. **Queryable via all search tools** — `query_sql`, `query_vector`, `query_source("convos")` should return Slack/Discord DM conversations alongside terminal ones

3. **Continuable** — when a DM conversation resumes within the timeout window, Promaia has the full prior history. When it starts fresh (after timeout), Promaia can still recall past conversations via search.

### Nice to Have

4. **Cross-platform recall** — "What did Rose and I talk about last week?" works from terminal, Slack, or Discord

5. **Conversation summaries** — auto-generate a one-line summary when a conversation ends (for the index)

6. **Participant metadata** — store who the conversation was with (user name, platform user ID) so Promaia can search by person

## Architecture

### Save Path

```
DM conversation ends (timeout/reset)
    ↓
conversation_manager detects boundary
    ↓
Format messages as markdown (same as terminal chat)
    ↓
ChatHistoryManager._save_to_database() OR ConversationConnector
    ↓
Unified storage: SQLite (conversation_content table) + markdown file + vector embedding
    ↓
Available via query_sql, query_vector, query_source
```

### Key Decision: Where to trigger the save

**Option A: In conversation_manager** — when `_handle_timeout()` or `/new` fires, save the conversation.
- Pro: Clean, centralized
- Con: conversation_manager doesn't currently know about ChatHistoryManager

**Option B: In slack_bot.py / discord equivalent** — when the bot detects a conversation boundary, call save.
- Pro: Platform-specific logic stays in platform layer
- Con: Duplicated across Slack and Discord

**Recommendation: Option A** — conversation_manager already manages state lifecycle. Add a `_save_conversation_to_history()` method that calls the existing save pipeline.

### Storage Format

Same as terminal conversations:
```markdown
# Conversation: Slack DM with Rose
Date: 2026-03-27
Platform: slack
Participants: Rose, Maia

## Messages

**Rose**: hey can you check my calendar for tomorrow?
**Maia**: Looking at your calendar... you have a standup at 3pm and a coffee with Amina at noon.
**Rose**: thanks! can you move the standup to 4?
**Maia**: Done — moved to 4pm.
```

### Database Entry

Stored in `conversation_content` table (same as terminal):
- `page_id`: conversation ID
- `workspace`: agent's workspace
- `database_name`: "convos" (same as terminal)
- `title`: "DM with Rose — calendar check" (auto-generated summary)
- `content`: full markdown
- `created_time`: conversation start
- `last_edited_time`: last message

## Incognito Mode

### Design

Incognito is **per-conversation, not persistent.** No toggle to forget about.

- User types `/incognito` → that conversation becomes incognito
- Next conversation (after 30-min timeout or `/new`) → back to normal automatically
- No way to make it "default on" — always a conscious choice

### UX

**Turning on:**
```
User: /incognito
Maia: 🕶️ Incognito mode — this conversation won't be saved. Resets next conversation.
```

**During incognito conversation:**
- Subtle 🕶️ indicator in thinking/status updates so user remembers
- Promaia functions normally — just doesn't save when the conversation ends

**When conversation ends (timeout or /new):**
```
Maia: 🕶️ Incognito conversation ended — nothing was saved.
```

**Next conversation starts (normal):**
```
Maia: 💬 Conversation saving is on. (Type /incognito to go private.)
```

### Scope

- **DMs only** — channels are synced via the Slack connector, not through conversation saving
- Incognito skips the save-to-database step at conversation boundary
- Implementation: `state.context["incognito"] = True` → save trigger checks and skips

### Why per-conversation not persistent

Forces intentionality. User must actively choose incognito each time. No risk of "forgot it was on" and losing weeks of conversation history.

## Answered Questions

1. **Channel conversations**: Channels sync via Slack connector daemon, not conversation saving. Only DMs save through this path. Channel @mention threads are already captured by sync.

2. **Privacy**: Incognito mode (above). Per-conversation, DMs only.

3. **Deduplication**: Not an issue — DMs don't sync via the Slack connector (only channels do). DM save path and channel sync path are separate.

4. **Summary generation**: TBD — Haiku auto-summary or first message as title. Can start simple (first message) and add Haiku later.

## Implementation Order

1. Add `_save_conversation_to_history()` to conversation_manager
2. Trigger on DM timeout and `/new` command
3. Add `/incognito` command to slack_bot.py
4. Check `state.context["incognito"]` before saving
5. Clear communication on mode transitions
6. Verify conversations appear in `query_source("convos")`
7. Test cross-platform recall
