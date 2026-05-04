# Future

Items deliberately deferred from active workstreams, plus new ideas
that haven't been prioritised yet. Move into an active roadmap file
when picked up.

## Permissions

- **MCP tool fingerprint enforcement (Q5d).** Detect when an MCP
  tool's input schema changes between reviews so the agent's allow
  list stays trustworthy. Foundation already exists:
  `schema_fingerprint` is computed in `mcp_tool_cache`. Filed for Rose
  for whenever external (non-Promaia) MCP servers come into the
  picture; internal MCP servers are stable enough not to need this.

- **Public / private / thread channel distinctions.** The current
  channel-groups schema only knows `dm` vs `channel`. A richer
  taxonomy (`public_channel`, `private_channel`, `thread_in_channel`)
  needs platform metadata we don't currently fetch. Pick up when a
  user actually needs the distinction.

- **Multi-table SQL source.** `SourceAccess.allowed_tables` is wired
  end-to-end but no caller exists today (Notion sources are
  effectively single-table). When a multi-table SQL source lands, the
  gate fires automatically — no plumbing needed.

- **Agents-edit-agents permission (2.0).** No agent self-edits.
  Agents can be edited by users and by other agents that have an
  explicit "may edit other agents" permission. Covered by the same
  pattern as the existing per-tool gates; defer until the chat-side
  interview surface is in.

## Tooling / workflow

- **Roadmap archiving cadence.** Move completed feature files into
  `archive/` periodically. No script for it yet — just `git mv` when
  the last box is checked.

- **Self-review file template.** `docs/self-review.md` is referenced
  in `docs/roadmap/README.md` per-step loop but doesn't exist as a
  template. Add when we use it for the first time.

- **Architecture docs.** `docs/architecture/` is referenced in the
  per-step loop but doesn't exist. Create once a roadmap step
  actually changes architecture.
