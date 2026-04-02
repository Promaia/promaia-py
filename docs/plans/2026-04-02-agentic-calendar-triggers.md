# Fully Agentic Calendar Triggers

**Date:** 2026-04-02
**Status:** Planning

## Problem

Calendar-triggered agents currently route through an Orchestrator that pre-decomposes goals into typed tasks (`CONVERSATION`, `TOOL_CALL`, `SYNTHESIS`) with hardcoded channel routing via `messaging_channel_id`. This is rigid, duplicates logic the agentic turn already handles, and doesn't let the agent decide where or how to communicate.

Additionally, the agent config has platform-specific fields (`messaging_platform`, `messaging_channel_id`, `initiate_conversation`) that hardcode routing decisions that should be the agent's to make.

## Goal

Calendar triggers run a standard agentic turn with the full tool suite. The agent decides autonomously where to message, who to talk to, and what to do — same as any other instance of Promaia's agents.

## Design

### New calendar trigger flow

```
Calendar event fires
  -> AgentCalendarMonitor detects event
  -> Runs a standard agentic turn (NOT Orchestrator)
     - System prompt = agent's personality (prompt.md + database preview)
     - User message = calendar event title/description
     - Full tool suite available (if agent has permissions)
     - Messaging tools available (if agent has messaging_enabled=True)
  -> Agent autonomously decides what to do
     - Query databases, search web, read email
     - Send one-off messages to channels or DMs
     - Start interactive multi-turn conversations
     - Write to journal, update spreadsheets
     - Any combination, in any order
```

### Interactive conversations from agentic turns

The `start_conversation` tool evolves from a single-reply poller to a real conversation launcher:

**Today:** Creates passive `conversation_type='agentic'` state, polls every 3s for one reply, returns it.

**New:**
1. Agent calls `start_conversation(user="Mitchell", message="Hey, how's the order going?")`
2. A real `TagToChatLoop` starts — the agent's personality drives the conversation
3. The calendar trigger's context is in the conversation's message history (agent knows *why* it's reaching out)
4. Full back-and-forth happens (multiple messages, tool use, etc.)
5. When the conversation goes dormant (user stops replying), an `asyncio.Event` fires
6. The agentic turn resumes with the full conversation transcript
7. Agent continues its work (write journal, message someone else, etc.)

```
Agentic turn running (calendar goal as prompt)
  -> agent calls start_conversation(user="Mitchell", topic="...")
  -> real threaded DM conversation starts (TagToChatLoop)
  -> multiple back-and-forth messages happen
  -> conversation goes dormant (Mitchell stops replying)
  -> dormancy event fires -> agentic turn resumes with transcript
  -> agent continues: writes to Notion, messages #operations, etc.
  -> agent calls start_conversation(user="Dave", topic="...")
  -> same pattern repeats
  -> agent calls done() when finished
```

### Messaging platform simplification

Remove hardcoded platform/channel assignment from agent config. Instead:

- **Environment-level platform init:** If `SLACK_BOT_TOKEN` exists, Slack is available. If `DISCORD_BOT_TOKEN` exists, Discord is available. No per-agent platform assignment.
- **`messaging_enabled: bool`** stays as the permission gate (default `False`). The default Maia agent has it on. Custom agents only get it if explicitly configured.
- **Agent decides where to message** using context from its system prompt (workspace overview, conversation history, personality instructions).
- **Remove:** `messaging_platform`, `messaging_channel_id`, `initiate_conversation` from agent config.
- **Keep:** `messaging_enabled`, `conversation_timeout_minutes`.

### Dormancy signaling

Currently dormancy is state-based (DB field update). We need event-based signaling so the agentic turn can `await` it:

- `start_conversation` creates an `asyncio.Event`
- Passes it to the `TagToChatLoop` via `on_done` callback
- `asyncio.Event.set()` is sync-safe, so it works from the existing sync `on_done` callback in the `finally` block
- The agentic turn does `await asyncio.wait_for(done_event.wait(), timeout=...)`
- On timeout, the conversation is ended gracefully and the turn continues

## Changes by file

### 1. `promaia/gcal/agent_calendar_monitor.py`

Replace Orchestrator with direct agentic turn.

**Current:** `Orchestrator(agent).run_goal(goal, metadata)` (lines 86-98)

**New:** Call `run_goal()` from `run_goal.py` directly:
```python
from promaia.agents.run_goal import run_goal
result = await run_goal(
    agent_config=agent,
    goal=run_request,
    metadata={
        "calendar_event_id": event_id,
        "calendar_event_start": start_raw,
        "calendar_event_summary": summary,
        "calendar_event_link": link,
    },
    timeout_seconds=3600,
)
```

### 2. `promaia/agents/agent_config.py`

Remove hardcoded platform/channel fields.

**Remove:**
- `messaging_platform: Optional[str] = None`
- `messaging_channel_id: Optional[str] = None`
- `initiate_conversation: bool = False`

**Keep:**
- `messaging_enabled: bool = False` (permission gate)
- `conversation_timeout_minutes: int = 15`

### 3. `promaia/agents/run_goal.py`

Update `_init_messaging_platform()` to be environment-based instead of config-based.

**Current:** Reads `agent_config.messaging_platform` to decide which platform to create.

**New:** Check environment for available bot tokens. Create whichever platforms are available:
```python
def _init_messaging_platforms():
    """Create messaging platforms from environment bot tokens."""
    platforms = {}
    if os.environ.get("SLACK_BOT_TOKEN"):
        from promaia.agents.messaging.slack_platform import SlackPlatform
        platforms["slack"] = SlackPlatform(bot_token=os.environ["SLACK_BOT_TOKEN"])
    if os.environ.get("DISCORD_BOT_TOKEN"):
        from promaia.agents.messaging.discord_platform import DiscordPlatform
        platforms["discord"] = DiscordPlatform(bot_token=os.environ["DISCORD_BOT_TOKEN"])
    return platforms
```

Gate on `agent_config.messaging_enabled` — only pass `has_platform=True` if the agent has permission AND at least one platform is available.

### 4. `promaia/agents/agentic_turn.py` — Evolve `start_conversation`

**Current** (lines 3219-3333): Passive polling for single reply.

**New:**
- Send initial message at top-level (becomes thread parent — already done from DM threading work)
- Create a `ConversationState` with `conversation_type='tag_to_chat'` (real conversation, not passive)
- Seed the conversation context with the agentic turn's message history (so the agent knows why it's reaching out)
- Create a `TagToChatLoop` with the agent's ID and personality
- Register an `asyncio.Event` via `on_done`
- `await` the event with timeout
- On completion: load transcript from DB, return to agentic turn
- On timeout: end conversation gracefully, return timeout message

### 5. `promaia/agents/executor.py` — Remove post-execution messaging

**Delete:**
- The `_send_to_messaging_platform()` method (lines 722-797)
- The gate check that calls it (lines 229-243)
- Related config reads (`messaging_enabled`, `messaging_channel_id`, `initiate_conversation`)

The agent decides when/where to message during its turn. No post-execution override.

### 6. `promaia/agents/conversation_manager.py` — Remove agentic suppression

**Delete** the `conversation_type == 'agentic'` check (lines 472-475) that suppresses AI responses. No longer needed since `start_conversation` will create real `tag_to_chat` conversations.

### 7. `promaia/agents/agentic_turn.py` — Update `send_message` tool

The `send_message` tool (line 3149) currently falls back to `self.channel_context` when no target is specified. With environment-based platforms, it needs to know which platform to use.

Update: if multiple platforms exist, require the agent to specify. In practice, most deployments have one platform (Slack), so the tool auto-selects when there's only one.

### 8. Cleanup references to removed config fields

Grep for `messaging_platform`, `messaging_channel_id`, `initiate_conversation` across the codebase and update/remove all references:
- `executor.py` (primary consumer — being deleted)
- `orchestrator.py` (reads `messaging_platform` for platform registration)
- `run_goal.py` (reads `messaging_platform` for init)
- Any agent config loaders/serializers

## What we're NOT changing

- **The Orchestrator code itself** — leaving it in place, just not calling it from calendar triggers. It may have other uses or be refactored later.
- **The planner/task_queue** — not deleting, just bypassing.
- **TagToChatLoop core mechanics** — dormancy, threading, response generation all stay the same. We're just adding an event signal on top.
- **The `send_message` tool** — stays as-is functionally, just gets platform auto-detection.

## Verification

1. **Calendar trigger → agentic turn:** Event fires, agent runs with full tool suite, no orchestrator involved
2. **Agent sends DM:** Uses `send_message(user="Mitchell")` — agent chooses the recipient
3. **Agent posts to channel:** Uses `send_message(channel_id="C...")` — agent chooses the channel
4. **Agent starts conversation:** Real TagToChatLoop, full back-and-forth with agent personality
5. **Conversation dormancy → resume:** Conversation ends, agentic turn gets transcript, continues work
6. **Multiple sequential conversations:** Agent talks to person A, resumes, talks to person B
7. **Conversation timeout:** If user doesn't reply within timeout, agentic turn continues gracefully
8. **Permission gate:** Agent without `messaging_enabled=True` cannot access messaging tools
9. **No post-execution message dump:** Agent output is NOT auto-posted to a hardcoded channel
