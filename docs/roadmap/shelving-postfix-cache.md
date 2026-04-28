# Shelving postfix + Anthropic prompt caching

## Why

Anthropic's prompt cache works on byte-exact prefix matching. Today the
agentic loop builds the system prompt as
`[base][suite_index][context_shelf_index][active_source_content][budget_note]`
and every one of those last three pieces changes per iteration:

- `context_shelf_index` regenerates as shelves get created/toggled
- `active_source_content` includes the bodies of all ON shelves
- `budget_note` includes the iteration counter and act-instruction
  checkbox state

Result: the cache is invalidated on every iteration, even when the
underlying base prompt + suite index + tool list + prior message
history are perfectly stable.

The fix is to separate volatile content from cacheable content:

- **Cacheable**: base prompt, suite index, tool list, prior message
  history.
- **Volatile (postfix)**: shelf index, ON shelf bodies, iteration
  counter, act-instruction checklist. Appended as a single text block
  on the last user message — outside any cache breakpoint.

This is also a prerequisite for the subagent split
([`subagent-architecture.md`](subagent-architecture.md)) — each child
session needs the same separation to be cacheable mid-burst.

## Constraints

- No `cache_control` exists in the codebase today; this PR introduces
  it.
- 4 cache breakpoints max per request. We use three: system, tools,
  penultimate assistant message. The fourth is held in reserve.
- Anthropic accepts `text` blocks appended after `tool_result` blocks
  in a single user message; we rely on this to inject the postfix.
- Apply only to the Think and Act mode paths in
  `agentic_turn.py:8892–8948`. The legacy fallback at line 8950
  (when `suite_registry is None`) stays untouched.

## Phase 1: Move shelves and budget_note into a postfix

- [x] Stop appending `ctx_index` to `base_prompt` in Think mode
- [x] Replace `_compose_prompt` with a system-only composer for
  Think/Act paths (no shelf content, no budget marker). Legacy
  retains the old shape.
- [x] Add `_build_postfix` helper that returns
  `ctx_index + active_source_content + postfix_extras + budget_marker`
- [x] After the trimmer returns, build a per-iteration message copy
  via `_build_api_messages_with_postfix` (text block appended to last
  message's content, converting str → list-of-blocks if needed).
  `internal_messages` stays clean so the next iteration recomposes
  from fresh shelf state.
- [x] Pass `rebuild_system_prompt=None` to the trimmer in Think/Act
  paths.
- [x] Move the act marker and instructions checklist out of
  `budget_note` and into `postfix_extras`.

## Phase 2: Cache breakpoints (system + tools)

- [x] Convert `api_kwargs["system"]` from a string to a list with
  one block carrying `cache_control: {"type":"ephemeral"}`
- [x] Add `cache_control` to the last tool definition in
  `iteration_tools` (cache breakpoint applies to all preceding tools)
- [ ] Verify the static prefix (base + suite_index) clears 1024
  tokens once a real conversation runs — confirm via telemetry

Message-level breakpoints (caching prior conversation across
iterations) are deferred to a follow-up PR. The byte-stability
semantics around `cache_control` markers on previous-iteration
messages need investigation (whether the SDK serialization includes
the marker in the cache-key bytes), and system+tools captures the
headline within-turn cache win without that risk.

## Phase 3: Telemetry

- [x] After each API call, log
  `response.usage.cache_read_input_tokens` and
  `response.usage.cache_creation_input_tokens` per iteration as
  `[agentic.cache] iter=N input=I cache_read=R cache_create=C hit_ratio=X%`
- [ ] Optional: aggregate-by-turn summary at end of `agentic_turn`

## Phase 4: Prompt wording

- [x] Reword `prompts/conversation_mode.md:30` — now reads "the full
  body lives in your Context Shelf, which is appended at the end of
  your context each turn."

## Verification

1. **Functional**: an end-to-end conversation with several Think
   iterations behaves identically to before — same shelves visible,
   same toggles, same Act burst report. Diff a saved conversation
   transcript before/after.
2. **Cache telemetry**: on iteration 2+ within a single agentic_turn,
   `cache_read_input_tokens` should be a substantial fraction
   (>50%) of input_tokens.
3. **Trimmer**: force overflow (huge shelves) and verify the trimmer
   still LRU-toggles sources off correctly. The shelves disappear
   from the postfix on the next compose, which is rebuilt fresh
   each iteration.
4. **conversation_mode.md self-consistency**: read the updated text
   and confirm the model wouldn't be confused by "above" references
   that no longer apply.

## Out of scope

- Legacy mode (no `suite_registry`) prompt path — unchanged.
- The parent/subagent split — separate roadmap, this is a
  prerequisite.
- Cross-turn caching optimization (separating notepad/memory into a
  pre-cache block) — possible future work, not needed for the
  in-turn caching headline win.
