# Promaia Roadmap

High-level index of active workstreams. Each entry links to a detailed
file with phases and checkable tasks.

See [`README.md`](README.md) for how this folder is organised and how to
work with it.

## Active

### Shelving postfix + Anthropic prompt caching

Move the volatile per-iteration content (context shelf index, ON
shelf bodies, budget note) out of the system prompt and into a
postfix on the last user message, then add `cache_control`
breakpoints on system, tools, and the most recent stable message.
Today the cache is invalidated on every iteration; this restores it.

See [`shelving-postfix-cache.md`](shelving-postfix-cache.md).

### Subagent architecture — split Think and Act into parent/child sessions

Replace the in-place Think↔Act mode flip with a parent (Think) loop
that spawns fresh child (Act) agentic_turn invocations, optionally in
parallel. Stable system prompts per agent type (cacheable) and
genuine concurrency for multi-suite Act work. Builds on the
shelving-postfix work.

See [`subagent-architecture.md`](subagent-architecture.md).

### Agent permissions — unified external-tools picker

Collapse the agent-edit menu's separate "MCP Tools" and "Channel
Permissions" entries into one "External Tools" picker with two
visible sections: built-in external tools (notion, gmail, calendar,
sheets, slack, discord) and user-added MCP servers. Each ticked
tool drills into its own sub-flow. Underlying gates are unchanged.

See [`agent-permissions-unified-picker.md`](agent-permissions-unified-picker.md).

### Agent permissions — read/write split for internal sources (SQL & friends)

Gate writes separately from reads. Today `SourcePermission.WRITE` is
defined but unenforced; any source the agent can read it can also
modify. Adds an explicit per-source read-only / read+write toggle.

See [`agent-permissions-sql-rw-split.md`](agent-permissions-sql-rw-split.md).

## Deferred (post-pilot)

### Agent permissions — Notion page-level scope

Per-page allowlist within a Notion database with per-page R/W
matrix. Out of pilot scope — DB-level R/W via the unified picker is
enough for now.

See [`agent-permissions-notion-page-scope.md`](agent-permissions-notion-page-scope.md).

## Recently shipped

- Granular agent permissions (per-tool MCP, channel groups, column
  redaction, allowed_tables, is_default uniqueness, CLI pickers,
  chat-side schema). Shipped to koii-prod 2026-04-26; main → Glacier
  merge pending separate sign-off. See
  [`Archive/2026-04-26-agent-permissions.md`](Archive/2026-04-26-agent-permissions.md).

## Future

Items deferred from active work, plus new ideas waiting for prioritisation.

See [`future.md`](future.md).

## Archive

Completed workstreams live in [`archive/`](archive/), prefixed by their
approximate completion date (ISO `YYYY-MM-DD-<slug>.md`).
