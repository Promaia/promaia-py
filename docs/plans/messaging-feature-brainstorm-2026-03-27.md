# Messaging Feature — Brainstorm

## Current State

Two platform tools, only available when agent runs in Slack/Discord:
- `send_message` — fire-and-forget (DM or channel)
- `start_conversation` — request-reply (sends + waits for response)

## What Feels Off

- Only 2 tools — feels incomplete as a "suite"
- No read tools in the suite — reading Slack/Discord goes through `query_sql` on synced data, which is a separate flow
- Naming is generic ("messaging") — could be confused with email
- Conceptual ambiguity: is it a tool suite? A platform capability? A workflow?

## Open Questions

1. **Should messaging be a suite at all?** It's platform-dependent — only exists when `has_platform=True`. Every other suite (notion, gmail, calendar) works everywhere. Maybe messaging tools should be auto-injected when platform is present, not user-selected.

2. **Should there be read tools?** e.g.:
   - `search_channel` — search messages in a specific Slack channel
   - `get_channel_history` — load recent messages from a channel
   - `list_channels` — show available channels
   - These would let agents proactively gather context from Slack without waiting for sync

3. **How does this relate to the channel context we just added?** We now inject recent channel history for @mentions. But agents running on a schedule (not @mentioned) don't get channel context. Should they be able to request it?

4. **Cross-platform messaging:** Should `send_message` work from terminal too? e.g., "send Rose a Slack message" from `maia chat`. Currently it only works when the agent IS in Slack.

5. **start_conversation as a workflow primitive:** The request-reply pattern is powerful — agent sends a message, waits for human response, continues. This is basically an interview but over Slack instead of terminal. Should it be generalized?

## Possible Directions

### A: Keep it minimal (current)
Just 2 tools, platform-only. Good enough for now.

### B: Messaging suite with read tools
Add channel search/history tools. Make it a proper suite alongside gmail/notion/calendar.

### C: Platform tools (not a suite)
Auto-inject when platform is present. Not selectable via `act(suites=[...])`. Always available in Act mode when running in Slack/Discord.

### D: Cross-platform messaging
Make `send_message` available from terminal too (calls Slack API directly). Enables "send a Slack DM from maia chat" workflow.

## Decision: TBD
