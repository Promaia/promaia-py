# Subagent architecture — split Think and Act into parent/child sessions

## Why

Today the agentic loop in `promaia/agents/agentic_turn.py` runs ONE
long-lived API session that flips between Think and Act mode in place.
Each flip changes the system prompt (suite index toggles, mode-specific
instructions, budget_note format) and the tool schema list, which means
Anthropic's prompt cache is invalidated on every transition. It also
means everything is serialized — a Think turn that wants to update
Notion AND check Calendar runs them one after the other inside a single
Act burst.

Splitting into parent (Think) and child (Act) sessions:

1. **Stable system prompts per agent type.** Parent's system prompt is
   stable for the entire conversation. Child's is stable for the
   duration of its burst. Both become legitimately cacheable.
2. **Parallelism.** Anthropic API allows multiple `tool_use` blocks in
   a single assistant message. If `act` becomes "spawn one child", the
   model can issue several `act(...)` calls in one turn and we run them
   concurrently with `asyncio.gather`. The parent waits for all
   children before its next turn.
3. **Cleaner mental model.** No more squash logic, no `_sources_muted`
   global, no `_act_start_iteration` book-keeping. The child has its
   own ToolExecutor; selected shelves merge back into the parent on
   completion via the existing `keep_shelves` contract.

The shelving-postfix work
([`shelving-postfix-cache.md`](shelving-postfix-cache.md)) is a
prerequisite/building block: each child session still accumulates
shelves mid-burst, so the same cache-invalidation problem exists in
miniature inside a child. Postfix-shelving + cache breakpoints make
the child cacheable too.

## Constraints

- Anthropic prompt cache: 4 cache breakpoints max per request, 1024
  token minimum prefix (Sonnet/Haiku), 5-min default TTL (1h optional).
- Anthropic API contract: assistant message with N `tool_use` blocks
  must be followed by a single user message with N matching
  `tool_result` blocks.
- Existing conversations have squashed Act bursts in their stored
  history; the new code must read them without crashing.




- Do sub agents need shelves? No I don't think they even need them. Only the parent agent.
- As long as their context trimming logic is working well they should never get context overload. 


## Phase 1: Foundation — fresh ToolExecutor per child

Goal: child gets its own ToolExecutor instance seeded from parent state,
so concurrent children can't trample each other.

- [x] Add `ToolExecutor.clone_for_child()` factory that returns a
  fresh executor carrying the parent's notepad. Per user spec children
  don't use shelves, so sources start empty.
- [x] Add `is_child_mode: bool` parameter to
  `build_agentic_system_prompt` in
  `promaia/chat/agentic_adapter.py`; loads
  `conversation_mode_child.md` when True
- [x] Create `prompts/conversation_mode_child.md` — Act-only
  guidance (loaded suites, notepad, mark_step_done, done). No shelving
  language, no `keep_shelves` field on `done`.
- [x] Rewrite the parent prompt for subagent-on. Created
  `prompts/conversation_mode_parent.md` — drops Think/Act-cycle
  language, drops the `done` tool and `keep_shelves` references,
  reframes `act()` as "delegate a batch and get a report back."
  Notes-as-bridge framing made explicit so the model knows to lift
  facts into notes before delegating. The legacy
  `conversation_mode.md` is still loaded when the env flag is off.
- [ ] DEFERRED: tests. No active test infra in this repo today;
  add when introducing one.

## Phase 2: Single-child spawn (no parallelism yet)

Goal: when the model calls `act(...)`, instead of flipping mode in
place, we spawn ONE child agentic_turn and await it. Same observable
behavior as today; gates the architectural change behind a feature flag.

- [x] Feature flag `PROMAIA_SUBAGENT_MODE` (env var, default off).
  Read in `agentic_adapter.run_agentic_turn`.
- [x] When flag is on, the parent's `__ACT__` handler in
  `agentic_turn.py` calls `spawn_child(suites, instructions, parent_tool_use_id)`
  and uses the returned report as the `act()` tool_result.
- [x] Child `agentic_turn` runs with `is_child_mode=True`, returns a
  standard `AgenticTurnResult` whose `response_text` is the report.
  Per user spec, no shelves on children — `keep_shelves` is intentionally
  not surfaced. (The legacy `keep_shelves` field on `done` is ignored
  by children since children own no shelves.)
- [x] Parent's act handler embeds the child's report as the tool_result
  for the parent's `act()` tool_use. Notepad updates from the child
  carry forward (the only state that crosses).
- [x] Squash code path is bypassed when `spawn_child` is set: the
  legacy in-place flip lives in the `else` branch of the act handler.
- [x] Token usage: child input/output/cache tokens roll up into the
  parent's totals.
- [x] Activity callback: child tool steps stream through the parent's
  `on_tool_activity` so they show up in the same breadcrumb. (Per-child
  labelling deferred — needs a UI consumer; today we just log
  `[subagent.spawn]` and `[subagent.child] done`.)
- [ ] DEFERRED: tests. No active test infra in this repo today.

Then we'll stop here for user testing

Then we'll stop here for user testing

## Phase 3: Parallel children

Goal: multiple `act(...)` tool_uses in one assistant message run
concurrently.

- [ ] Walk all `tool_use` blocks in the assistant response;
  collect every `act(...)` into a list of pending child coroutines
- [ ] After the (already-fast) non-act tool calls complete, run
  `asyncio.gather(*pending_children, return_exceptions=True)`
- [ ] Each child gets its OWN ToolExecutor clone (Phase 1) — no shared
  mutable state between siblings
- [ ] Configurable `MAX_PARALLEL_CHILDREN` (default 5) via env var
- [ ] Error handling: if a child raises, surface as a `tool_result`
  with `is_error=True` and a brief message; siblings continue
- [ ] Tests: 2 parallel children, 3 parallel children, one-fails-other-
  succeeds, max-parallel-cap enforcement

## Phase 4: Backwards compatibility

Goal: load and resume conversations whose stored history was produced
by the in-place mode-flip code.

- [ ] On conversation load, detect synthetic squash messages (the
  marker text from `agentic_turn.py:9419-9439`) and pass them
  through unchanged — they're plain text now, no special handling
- [ ] Don't error on stored shelves whose `mounted_at_iteration` is
  from an old session; treat them as ordinary parent-mounted sources
- [ ] Doc update: note in conversation_mode.md that legacy bursts
  appear as a single combined assistant message

## Phase 5: Cache breakpoints on parent and child

Goal: now that each session has a stable system prompt, place the
cache breakpoints to actually realize the win.

Builds on `shelving-postfix-cache.md` Phase 1.

- [ ] Parent: cache breakpoint at end of system prompt; tools
  breakpoint; penultimate-assistant-message breakpoint
- [ ] Child: same three-breakpoint layout; child's stable system
  prompt + child's stable tool list cache across the child's
  iterations
- [ ] Cache telemetry already added in postfix work — confirm hit
  rate climbs

## Phase 6: Polish + retire flag

- [ ] Default `PROMAIA_SUBAGENT_MODE` to on
- [ ] Delete in-place mode-flip code paths
- [ ] Remove `_sources_muted`, `_act_start_iteration`,
  `act_start_msg_idx`, `_is_source_visible` (the visibility filter
  becomes unnecessary because children don't share `_sources` with
  the parent)
- [ ] Update `docs/architecture/` if there's a doc on the agentic
  loop

## Open questions (carried over from scoping)

1. **Notepad merge strategy** when multiple children both write notes:
   replace with last-completer's version, or concatenate with
   `[Child N]` prefixes? Recommendation: concatenate with prefix —
   safer, lossless.
2. **Child history persistence**: store child's full
   `tool_use`/`tool_result` blocks in the conversation record for
   debugging, or only the report? Recommendation: separate
   `child_histories` field on the conversation, parent's main
   message history stays clean.
3. **`act` schema**: keep the existing `(suites, instructions)` shape
   (model issues multiple `act` calls for parallelism), or add a
   new `parallel_act([{suites, instructions}, ...])`? Recommendation:
   keep existing shape — multiple tool_uses in one message is the
   idiomatic Anthropic pattern.

## Verification

- Manual: a Think→Act burst produces the same user-facing report as
  before the refactor; cache hit telemetry shows non-zero
  `cache_read_input_tokens` on iteration 2+.
- Manual parallel test: ask the agent to simultaneously update Notion
  and send a calendar invite; observe two `act` tool_uses in one
  assistant turn; confirm both children run concurrently (parent
  total wall-clock time roughly equals max(child times), not sum).
- Tests: `test_tool_executor_clone`, `test_subagent_single`,
  `test_subagent_parallel`, `test_subagent_error`,
  `test_subagent_cache_breakpoints`.

## Estimated effort

3–4 weeks if done strictly sequentially. Phases 1–2 can ship behind a
feature flag with full backwards compatibility (~1 week). Phase 3
(parallelism) is where the headline win lands but also introduces the
real concurrency surface (~1 week). Phases 4–6 are cleanup
(~1–2 weeks).
