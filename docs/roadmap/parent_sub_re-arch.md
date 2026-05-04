# Parent / Sub re-architecture — unified prefix, delegating parent

## Why

Today the parent agent holds **everything**: query tools, raw tool
results auto-shelved into a postfix on the last user message, plus
delegating to act sub-agents. The shelf postfix is volatile; every
shelf toggle invalidates cache from that point forward. A single
auto-loaded daily-journal workflow can pull >1000 pages into the
parent's context and re-cache them every turn.

This re-arch makes the parent a pure delegator. It can spawn search
sub-agents (read) and act sub-agents (write); it never directly calls
query tools, never holds raw tool results, and never accumulates
shelves. Combined with a unified system prompt + tool union shared
across all three agents, this lets every API call across the whole
family share the same cached prefix up through `[system, tools,
parent_history]`.

## Outcomes

1. **Parent's context is prose-only.** System + tools + history of
   user messages and parent's text replies + sub-agent reports as
   tool_results. No raw query data ever lands in parent's history.
2. **Shared cache prefix.** All three agents (parent, search child,
   act child) send the same system and tools on every API call.
   Children inherit a copy of the parent's history and append. First
   iteration of any child burst hits the parent's prefix cache.
3. **One-hour cache TTL.** Long sub-agent bursts no longer cause the
   parent's cache to expire while it's waiting.
4. **Single feature flag.** `PROMAIA_PARENT_SUB_MODE=1` subsumes the
   old `PROMAIA_SUBAGENT_MODE`. Default off until verified on koii-prod;
   then becomes the only mode and legacy think/act flip is removed.

## Constraints

- Anthropic prompt cache: 4 breakpoints max, 1024-token minimum
  prefix, 5-min default TTL or 1-hour with beta header
  `anthropic-beta: extended-cache-ttl-2025-04-11` and
  `cache_control: { type: "ephemeral", ttl: "1h" }`.
- Cache writes at 1h TTL cost 2× input price (vs 1.25× for 5-min).
  Cache reads at 0.1× regardless of TTL.
- Anthropic API contract: assistant message with N `tool_use` blocks
  must be followed by exactly N matching `tool_result` blocks.
- Existing conversations have squashed act bursts in their stored
  history; new code must read them without crashing.

## Final spec — agreed before starting

### Roles

- **Parent (think/respond/delegate):**
  Tools: `search`, `act`, `memory`, `notepad`.
  No `query_*`, no `context`, no `write_agent_journal`, no shelves.
  Finishes a turn by responding to the user.

- **Search child (read-only):**
  Tools: `query_sql`, `query_vector`, `query_source`,
  `compress_last_result`, `mark_step_done`, `done`.
  Reads notepad as immutable context. Cannot write notepad. Cannot
  spawn grandchildren. Cannot write memory.

- **Act child (writes):**
  Tools: suite tools loaded by parent (`notion`, `gmail`, …),
  `compress_last_result`, `mark_step_done`, `done`.
  Same constraints as search child.

### Unified system prompt

Single file `prompts/parent_sub.md` with three role sections:
PARENT, SEARCH, ACT. The model recognises its role from the kickoff
user message: `[role: search]` or `[role: act:notion,gmail]`. Parent
has no role marker — its history simply contains user/assistant
turns the way a normal conversation does.

### Per-role tool lists (revised)

Each role has its OWN tool list, sized to what that role can actually
call. The parent does not carry suite-specific tool schemas; instead
its prompt has a brief one-line-per-suite index so it can pick the
right `suites=[…]` argument when calling `act()`.

- **Parent:** `search`, `act`, `notepad`, `memory` — 4 tools.
- **Search child:** `query_sql`, `query_vector`, `query_source`,
  `compress_last_result`, `mark_step_done`, `done` — 6 tools.
- **Act child:** the spawned suites' tools + `compress_last_result`,
  `mark_step_done`, `done` — typically 15-50 tools depending on
  suites loaded.

Cache implication: parent's per-turn tools cache read drops from
~1.5k effective tokens to ~150 (10× reduction on the dominant
request). Sub-agent first iteration loses the "share parent's
tools cache" benefit — pays one tools cache write per spawn — but
the parent's per-turn savings dominate over the conversation.

Children's tool definitions are scoped to their burst: passed at
spawn, gone when the child returns. They never appear in the
parent's request again.

### Role validation

`ToolExecutor` carries a `current_role` field. When a tool is
called, executor checks the tool's allowlist against `current_role`.
If not allowed, executor returns a tool_result of the form:

```
ERROR: <tool> is not available in <role> role.
Allowed in this role: [<list>].
If you need to escalate, call done(report="…").
```

The error is fed back to the model so it self-corrects. No exception
is raised; the burst continues.

### History inheritance

`spawn_child(role, instructions, ...)` does:
1. `child_messages = copy(parent.internal_messages)`
2. `child_messages.append({"role": "user", "content": "[role: <role>]\nInstructions:\n1. …\n2. …\n\nWorking notepad:\n<parent's notepad text>"})`
3. Run `agentic_turn` against `child_messages` with role-restricted
   executor.
4. On `done(report=…)`, child returns. Parent's `internal_messages`
   gets exactly ONE new tool_result (the result of the parent's
   `search()` or `act()` tool_use), containing the report text.

Child's intermediate messages are discarded entirely. Parent's
history is structurally:
`[..., user_msg, assistant_msg_with_search_tool_use, user_msg_with_search_tool_result, assistant_msg, ...]`

### Notepad

Parent-owned, parent-mutable. Children get a snapshot in their
kickoff message and treat it as immutable context. The child has
no `notepad` write tool. To return information to the parent, the
child uses its `done(report=…)` text.

### Compression

`compress_last_result(summary: str)` is callable by either child
type. It modifies the most recent uncompressed tool_result in the
child's *own* message history, replacing its content with
`f"[compressed by agent] {summary}"`.

**One-shot rule:** the child gets exactly one chance to compress,
on the iteration immediately following the tool result. Once the
child has emitted any other tool_use (other than `compress_last_result`
itself) or any text-only assistant turn after that tool result, the
result is locked at full size for the rest of the burst.

Implementation: track a `pending_compressible_idx` in the child
executor. Set it when a tool_result is appended to the child
history. Clear it after the child's next assistant message is
processed unless that message contained `compress_last_result`.

### Cache breakpoints

Three breakpoints used, one reserved:
- bp1: end of system content
- bp2: end of last tool definition
- bp3: rolling, on the last completed assistant message in history
- bp4: reserved

All three with `{"type": "ephemeral", "ttl": "1h"}` and the request
sends `anthropic-beta: extended-cache-ttl-2025-04-11`.

## Phase 1: Unified prompt + tool union

Foundation. No behavior change yet — just the shared scaffolding
the rest of the phases build on.

- [x] Write `prompts/parent_sub.md` with PARENT / SEARCH / ACT
  sections (single unified prompt, role detected from `[role: …]`
  marker on kickoff message).
- [x] Add `current_role` field to `ToolExecutor`. Default `"parent"`.
- [x] Add `_PARENT_ROLE_TOOLS`, `_SEARCH_ROLE_TOOLS`,
  `_act_role_tools_for()` and `role_tool_allowlist()` in
  `promaia/agents/agentic_turn.py`. Act role is computed dynamically
  from the unified tool list (everything that isn't parent- or
  search-exclusive).
- [x] Add `_role_check_or_error()` on `ToolExecutor` — pre-dispatch
  gate that returns an informative error tool_result when the
  current role can't call the tool. Permissive when
  `_role_unified_tools` is None (legacy mode).
- [x] Wire role gate into top of `ToolExecutor.execute()`.
- [x] Add `build_parent_tool_definitions(agent)`,
  `build_search_child_tool_definitions()`,
  `build_act_child_tool_definitions(agent, suites, has_platform, mcp_suites)`.
  Per-role builders return scoped tool lists. Parent gets 4 tools;
  search child gets 6; act child gets only the named suites' tools
  + scaffolding. (Originally a single unified union; revised after
  feedback that the parent doesn't need 78 tool schemas — just a
  brief suite index in its prompt.)
- [x] Add `__SEARCH__:` and `__COMPRESS__:` sentinels to
  `execute()` so Phase 2's loop-level handlers have something to
  match against.
- [x] Add `PROMAIA_PARENT_SUB_MODE` env var read in
  `build_agentic_system_prompt` and `run_agentic_turn`. When on,
  swap to `parent_sub.md` + `build_unified_tool_definitions` and
  stamp `executor._role_unified_tools = tools`. When off, legacy
  paths unchanged.

## Phase 2: Search child + spawn plumbing

- [x] Add `__SEARCH__` sentinel handling in `agentic_turn.py` mirror
  of `__ACT__` — calls `spawn_child(role="search", …)`, rolls up the
  child's tokens + cache stats into the parent's totals, embeds the
  child's report as the tool_result for the parent's `search()` call.
- [x] Add `_spawn_search_child` and `_spawn_child_dispatch` closures
  in `agentic_adapter.py`. Dispatch picks act vs. search by role.
  Search child has no suites; gets the unified tool list with
  `current_role="search"` so the executor's role gate restricts to
  the SEARCH allowlist.
- [x] Add `SEARCH_TOOL_DEFINITION` to the unified tool union (Phase 1).
- [x] Add `COMPRESS_LAST_RESULT_TOOL_DEFINITION`. Track
  `_pending_compressible: Optional[Tuple[int,int]]` on the
  `ToolExecutor`. Set at the end of each iteration to point at the
  last non-compress tool_result; cleared when compress consumes it.
  `__COMPRESS__:` handler rewrites the targeted tool_result block
  in `internal_messages` in-place; an error tool_result is returned
  if no eligible result is pending (one-shot rule).
- [x] `__DONE__` for search child — already returned the report via
  the existing child path (`is_child_mode` early-returns
  `AgenticTurnResult` with `response_text=report`).
- [x] Tool logging in `tag_to_chat.py`: `search` spawn renders as
  `` `search` (N steps: head…) `` with the same placeholder/swap
  pattern as `act`. Child steps still get the `↳` indent prefix.
- [x] `_summarize_tool_input` in `run_goal.py` updated for `search`,
  `act`, and `compress_last_result` so breadcrumb lines show useful
  detail.

## Phase 3: History inheritance

- [x] Spawn callsites in `agentic_turn.py` pass
  `parent_messages=internal_messages[:-1]` (snapshot at spawn time,
  excluding the in-flight assistant message that holds the spawn
  tool_use).
- [x] `spawn_child` callback signature gains `parent_messages` kwarg.
  `_spawn_child_dispatch` forwards to either `_spawn_act_child` or
  `_spawn_search_child`, both of which now accept the snapshot.
- [x] `_build_kickoff_messages` helper appends the role-marked
  kickoff user message after the parent snapshot. In parent_sub_mode
  the child's `messages=` is the inherited prefix; in legacy
  subagent_mode the act child still uses fresh-history (preserves
  semantics).
- [x] Working notes are appended to the child's *system prompt* as
  immutable kickoff context (the same place the parent surfaces its
  own notes). Children have no `notepad` tool in parent_sub_mode so
  there is no merge-back path to disturb.
- [x] On `done()`: existing `is_child_mode` early-return embeds the
  child's report as the tool_result for the parent's `search()` /
  `act()` call. Child's intermediate history is discarded with the
  child's executor — parent's history sees only the spawn tool_use
  and its tool_result.
- [x] Notepad-merge-back gated off when `PROMAIA_PARENT_SUB_MODE=1`
  (legacy subagent mode unchanged).

## Phase 4: Caching uniform — 1-hour TTL + rolling bp3

- [x] Add `anthropic-beta: extended-cache-ttl-2025-04-11` header
  via `extra_headers` on Anthropic API calls when
  `PROMAIA_PARENT_SUB_MODE=1`.
- [x] Update `cache_control` blocks on bp1 (system) and bp2 (last
  tool) to include `"ttl": "1h"` in parent_sub_mode.
- [x] Add bp3: walk `api_messages` backwards, tag the last content
  block of the most-recent assistant message with
  `{"type": "ephemeral", "ttl": "1h"}`. Skip if history has no
  prior assistant turn. Defensive copy of `api_messages` before
  mutation so `internal_messages` stays clean.
- [ ] Verify with `[agentic.cache]` log lines that hit ratio is
  >85% on parent's turn-over-turn calls in a real conversation.
  *(Deferred to Phase 6 — needs koii-prod traffic.)*
- [ ] Verify a child's first iteration shows cache_read covering
  the parent's prefix (largest single win). *(Deferred to Phase 6.)*

## Phase 5: Strip legacy from parent (when flag on)

- [x] `query_sql`, `query_vector`, `query_source` are absent from
  the parent's 4-tool list — the API would reject any call. They
  live only in the search child's tool list at spawn time. The
  role gate is defense-in-depth.
- [x] Drop the shelf postfix machinery from the parent path —
  trivially satisfied: parent never calls query_* directly and has
  no `context` tool, so shelves never accumulate. The postfix
  builder runs but has nothing to add. Legacy paths unchanged.
- [x] Drop the `context` tool entirely — not in any per-role list.
- [x] Drop `write_agent_journal` — not in any per-role list.
  Executor handler stays for legacy code paths.
- [x] Parent's prompt uses `{suite_index}` (one line per suite)
  instead of `{tool_sections}` (per-tool docs). Brief enough to
  cache cheaply; the parent only needs to know suite names + what
  each suite covers at a high level.
- [x] Make `days_back` a required schema field on `query_source`
  (matches existing `query_sql` / `query_vector` behaviour). The
  executor refuses calls without it with an instructive error.
  Legacy callers passing `days` still work via fallback.

## Phase 6: Flag, deploy, observe

- [ ] Cherry-pick to koii-prod via the worktree pattern.
- [ ] Set `PROMAIA_PARENT_SUB_MODE=1` in koii-prod env.
- [ ] Restart `maia-slack` container.
- [ ] Run real conversations: a search-heavy one, an act-heavy one,
  a mixed one. Capture per-turn cost + cache hit ratios.
- [ ] Compare against pre-change baseline. Confirm token reduction
  on the daily-journal-workflow case.
- [ ] If stable: schedule legacy-cleanup follow-up (remove the old
  think/act flip code, retire `PROMAIA_SUBAGENT_MODE`, archive this
  doc).

## Risk register

- **Tool union bloat.** 30-50 tools may exceed comfort. Mitigation:
  measure tool-spec token count; if >15k, split unused suites out
  per-conversation rather than always loading everything.
- **Role confusion.** Model may try wrong-role tools. Mitigation:
  the executor's hard error in tool_result trains the model in one
  iteration. If it persists, tighten the role-section of the system
  prompt.
- **Latency.** Each search delegation adds ~3-10s of round trip.
  Mitigation: prompt the parent to batch all search needs into one
  `search()` call.
- **Notepad-as-immutable-input.** Children can no longer stamp facts.
  All return goes through `done(report=…)`. Mitigation: prompt
  children to be aggressive about including IDs/titles/dates in their
  report.
- **Compression cache invalidation.** Each `compress_last_result`
  invalidates the child's bp3 cache from that result forward.
  Mitigation: the one-shot rule means at most one compression event
  per query in a burst, and the win across remaining iterations
  outweighs the single invalidation.

## Out of scope

- Per-shelf caching (no shelves).
- Inline-LLM summarization for tool results (post-hoc agent-driven only).
- 4th cache breakpoint experimentation (kept in reserve).
- promaia-ts integration (this is promaia-py only).
