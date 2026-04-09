# Plan: Immutable agent IDs, self-edit guard, messaging toggle

## Context

Two user-facing failures converged into this work:

1. **Calendar-triggered agent runs cannot message Slack.** `maia` agent had `messaging_enabled: false`, so `run_goal._init_messaging_platform` short-circuited, `has_platform=False`, `MESSAGING_TOOL_DEFINITIONS` was never attached, the model hallucinated a `send_slack_message` tool name, and every attempt returned "unknown tool" errors.

2. **The field isn't exposed anywhere the user can reach it.** `messaging_enabled` exists on `AgentConfig` but:
   - the `update_agent` chat tool's schema doesn't include it,
   - the `agent_edit` chat workflow prompt doesn't mention it,
   - so neither the user nor the agent itself can toggle it after creation.

While investigating the fix, a deeper issue surfaced: **agent identity is brittle.** The `agent_id` field exists but is empty on the live `maia` agent (`""`), the interactive CLI edit menu lets users overwrite `agent_id` in place, and one creation path (`chat/agentic_adapter.py`) skips assignment entirely. Any self-edit guard keyed on `agent_id` would be silently bypassable.

Finally, per user direction: **agents must not edit themselves** (including the master agent — only the user can edit maia), and **agents must not create other agents** for now (human-only action until a tier/rank system exists).

## Intended outcomes

- `maia.messaging_enabled = true` (local + VM), unblocking calendar-triggered Slack messages.
- `maia.agent_id = "maia"` (local + VM), backfilling the empty id.
- Every agent creation path assigns an `agent_id`.
- Every `agent_id` mutation path is closed.
- `update_agent` tool can toggle `messaging_enabled`.
- `agent_edit` chat workflow lists Messaging as an editable field.
- Agents are refused when trying to edit themselves or create other agents.
- `create_agent` blocked at execution for all agents.

## Design

### Part 1 — Data backfill (local + VM)

`maia-data/promaia.config.json` and `/root/promaia/maia-data/promaia.config.json`:
- `maia.agent_id: "" → "maia"`
- `maia.messaging_enabled: false → true`

No service restart needed: calendar monitor reloads agent configs each cycle, web runs uvicorn `--reload`.

### Part 2 — Close the unassigned creation path

`promaia/chat/agentic_adapter.py:87-95` creates a default maia agent via `AgentConfig(...)` without an `agent_id`. Add `agent_id="maia"` to the constructor call, matching `scheduled_agent_commands.py:843`'s convention for the default agent.

All other creation paths already assign ids:
- `cli/scheduled_agent_commands.py:482` — `generate_agent_id(name, existing_agents)` for CLI create.
- `cli/scheduled_agent_commands.py:843` — hardcoded `"maia"` in setup flow.
- `agentic_turn.py:7375` — `generate_agent_id(name, load_agents())` in `create_agent` tool (this path will be blocked separately in Part 6 but the id-assignment logic is correct and stays).

### Part 3 — Enforce `agent_id` immutability

Delete the interactive "edit Agent ID" block at `cli/scheduled_agent_commands.py:1933-1942`. It currently prints `Current Agent ID: @<id>` then prompts `New Agent ID (without @, ENTER to keep):` and writes `agent.agent_id = new_id`.

After the change:
- The agent's current `agent_id` still appears in the config display (wherever it's printed as part of show-current-config output).
- No prompt accepts input for it.
- Menu numbering is renumbered if the deletion leaves gaps, so remaining items stay contiguous.

Add a documentation comment at the top of `AgentConfig` in `agent_config.py`:
> `agent_id` is immutable after creation. Assigned once by `generate_agent_id()` or hardcoded for default agents. Never mutate.

### Part 4 — Self-edit guard keyed on `agent_id`

Add a helper in `agentic_turn.py` near `_update_agent`:

```python
def _refuse_self_edit(self, target_name: str) -> Optional[str]:
    """Return a refusal string if target is the currently-running agent, else None."""
    from promaia.agents.agent_config import get_agent
    target = get_agent(target_name)
    if not target:
        return None  # let the caller's not-found path handle it
    self_id = getattr(self.agent, "agent_id", "") or ""
    target_id = getattr(target, "agent_id", "") or ""
    if self_id and target_id and self_id == target_id:
        return ("Refused: agents cannot edit themselves. "
                "Only the user can modify this agent directly.")
    # Fallback: match by name if either id is missing (safety net during backfill)
    if getattr(self.agent, "name", None) == getattr(target, "name", None):
        return ("Refused: agents cannot edit themselves. "
                "Only the user can modify this agent directly.")
    return None
```

Call at the top of (after the name-validation check):
- `_update_agent`
- `_enable_agent`
- `_disable_agent`
- `_remove_agent`
- `_rename_agent`

TODO comment inline: *drop the name-fallback branch once all environments are backfilled with non-empty agent_id.*

### Part 5 — `messaging_enabled` in `update_agent` tool

- Schema (`agentic_turn.py:2389-2407`): add `"messaging_enabled": {"type": "boolean", "description": "Whether this agent can use messaging tools (send_message, start_conversation, etc.)"}` to properties.
- Handler `_update_agent` (lines 7266-7302): add the branch alongside the others:
  ```python
  if "messaging_enabled" in tool_input:
      agent.messaging_enabled = tool_input["messaging_enabled"]
      changes.append("messaging_enabled")
  ```

### Part 6 — Block `create_agent` execution

At the top of `_create_agent` handler (`agentic_turn.py` ~ line 7375), before any work: return
> "Refused: agents cannot create other agents. This is a human-only action for now."

Leave the tool definition registered so the model still sees the capability documented. TODO inline: *replace with tier-based permission once agent ranks exist — master agent (rank 1) will create sub-agents (rank ≥ 2).*

### Part 7 — Chat edit workflow exposes Messaging

`promaia/chat/workflows/agent_edit.py` — add under step 3 editable fields:
> **Messaging**: Enable/disable messaging tools (send_message, start_conversation)

TODO comment: *add tier-aware create/edit permissions to user-facing agent flows once agent ranks exist.*

## Files to modify

- `maia-data/promaia.config.json` (local) — Part 1
- `/root/promaia/maia-data/promaia.config.json` (VM via `ssh kb`) — Part 1
- `promaia/chat/agentic_adapter.py` — Part 2
- `promaia/cli/scheduled_agent_commands.py` — Part 3
- `promaia/agents/agent_config.py` — Part 3 (docstring)
- `promaia/agents/agentic_turn.py` — Parts 4, 5, 6
- `promaia/chat/workflows/agent_edit.py` — Part 7

## Out of scope (explicit deferrals)

- **Agent tier / rank system.** The master-agent / sub-agent hierarchy is noted in TODOs but not built here.
- **Surfacing create/edit-agent permissions in the user chat flows as gated options.** TODO recorded.
- **`custom_tools.py`'s existing `update_agent_messaging_config` helper** — orthogonal path, not touched.
- **Backfill of `agent_id` on environments other than local + VM.** The name-fallback in the guard covers those transiently.
- **Migration/validation on `save_agent` that refuses empty `agent_id`** — too aggressive for existing agents; left to future hardening.

## Verification

1. **Config backfill**: after Part 1, grep both config files — `agent_id: "maia"` and `messaging_enabled: true` on the maia entry.
2. **Messaging unblocked**: trigger a calendar event on the maia calendar, confirm Slack receives the message (no more `send_slack_message` guessing).
3. **Self-edit blocked**: in chat, ask maia to edit itself (`update maia's description to "test"`). Expect refusal message. Then ask maia to edit any *other* agent — should succeed (if any exists; otherwise the not-found path is fine).
4. **create_agent blocked**: in chat, ask maia to create a new agent. Expect refusal message.
5. **CLI immutability**: run `maia agent edit maia`, step through menu, confirm no prompt asks for a new agent ID. Current id still displays.
6. **Messaging toggle via tool**: in chat, ask maia to toggle another agent's messaging. Expect `update_agent` to accept `messaging_enabled` and report the change.
7. **agent_edit chat workflow**: start the agent edit workflow, verify "Messaging" appears in the editable fields list.

## Risks

- **Concurrent config edits.** `maia-data/promaia.config.json` has ambient writes from sync processes (observed `last_sync_time` churn). Each edit re-reads the exact target line immediately before writing and scopes the change to the `messaging_enabled` / `agent_id` fields on the maia entry only.
- **CLI menu renumbering** could desync with hardcoded string comparisons elsewhere in `scheduled_agent_commands.py` (e.g. `if choice in ["2", "8"]`). Before deleting, grep for every place that references the affected choice numbers.
- **Self-edit guard name-fallback** is a safety net that could produce false positives if two agents share a name but have different ids. Acceptable during backfill because names must be unique in practice; TODO to drop it later.
- **`get_agent` import inside the guard** — make sure it handles the case where the target name doesn't exist and returns `None` cleanly, so we don't short-circuit the caller's own not-found error path.
