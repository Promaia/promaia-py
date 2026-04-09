# Plan: Fix context-loss bug via shelved act-mode tool results + bucket-aware trimmer

## Context

**The bug.** During a long conversation, Promaia silently dropped earlier user turns from history. Mitchell's pilot run with the "generate-vendor-po-with-drawing" workflow lost his original parts list mid-conversation. When he later said "the full list I gave at the beginning," Promaia responded that it couldn't see the original message and speculated it might have been "in a prior session." It wasn't — the message was real but had been overwritten by the context trimmer.

**Root cause** (`promaia/agents/context_trimmer.py:245-359`, `_summarize_old_turns`):
- Hardcoded `pairs_to_keep = 4` discards everything before the last 4 user turns.
- Discarded turns are passed to Haiku for summarization. On Haiku failure (silent), it falls back to a placeholder string (`[Earlier conversation truncated... Contained N messages.]`) — fully erasing the original content.
- No protection for user messages containing structured data (lists, BOMs, specs).
- The real bloat source is **act-mode tool result payloads** (search_emails, get_email_thread, sheets_*, mcp__po-manager__*), not user turns. The trimmer was deleting the wrong thing.

**Why it's bad.** This produces invisible amnesia — the model honestly believes the early turns never happened, and the user can't tell the difference between "context loss" and "hallucination." It killed Mitchell's PO workflow on what was otherwise a successful run.

**Intended outcome.** Tool result payloads (the actual bloat) get moved into the existing `_sources` shelf system on act-mode exit, freeing message-history tokens without losing data — the model can re-mount them via `turn_on_source`. The old user-turn-summarizing trimmer is removed entirely. A new bucket-aware trimmer measures sources vs history vs other and trims the largest bucket first, with sources LRU'd off before any history is touched.

---

## Design

### Part 1 — Shelve act-mode tool results on `__DONE__`

**Behavior**
- During an act burst, tool_result blocks remain inline in `internal_messages` as today. The act-mode agent needs to read fresh results to verify success/failure.
- When act mode exits via `__DONE__` (`agentic_turn.py:8221-8228`), walk all act-mode tool_result blocks produced during the burst and:
  1. Register the full `result_text` in `tool_executor._sources` with a generated `source_id`, `on=True` (so think mode sees it immediately via `build_active_source_content`), `source="<tool_name>"`, and a short title derived from `tool_name + tool_input` (e.g. `search_emails("Macron")`).
  2. Replace the `tool_result.content` in `internal_messages` with a structured stub:
     ```
     [tool result shelved] source_id=<id> tool=<tool_name> size=<N> chars
     Call turn_on_source if you need to re-read this.
     ```
  3. The matching `tool_use` block is left intact — workflow capture (`all_tool_calls[]` at lines 8313-8317) is unaffected.
- **Scope** (confirmed): every act-mode tool result, regardless of prefix. Excludes only:
  - query_sql/query_vector/query_source (already self-shelve)
  - Tiny control returns under a small threshold (e.g. < 500 chars) — `mark_step_done`, `done`, mode sentinels, error strings. These stay inline.
- **No size threshold beyond the tiny-result skip** — every meaningful act-mode result is shelved. (User: "every for now".)
- **Tool_use/tool_result pairing** is preserved: every shelved tool_result still has `tool_use_id` and non-empty `content`, satisfying Anthropic API rules.
- **Source naming**: `act_<tool_name>_<short_hash>` to avoid collisions across multiple calls of the same tool.
- **Atomicity**: stubbing happens in a single pass at `__DONE__` handling, so partial failure is impossible.

**Tracking the act burst.** Need a list `act_tool_use_ids: List[str]` populated each iteration in act mode (alongside `tool_results` append at line 8308) and cleared on `__DONE__` after stubbing. On `__DONE__`, walk `internal_messages` from the back and replace the matching tool_result blocks by `tool_use_id`.

**Why ON not OFF.** The user's instinct: shelving OFF would force think mode to immediately re-mount everything it just had. ON means the source content appears in the system prompt's active-source block (`build_active_source_content`, line 6677) the moment think mode regains control, with zero extra tool calls. The bloat reduction comes from the bucket trimmer LRU'ing them off later when needed, not from shelving OFF up-front.

**Re-mount grace period.** When a source is `turn_on_source`'d by the model, mark it with `mounted_at_iteration=<n>`. The bucket trimmer's LRU pass refuses to shelve anything mounted within the last 2 iterations.

### Part 2 — Replace `_summarize_old_turns` with a bucket-aware trimmer

**Delete** `_summarize_old_turns`, `_call_haiku_for_summary`, `_fix_alternating_structure` from `context_trimmer.py`. Layer 2 in `trim_context_to_fit` (lines 447-455) is replaced with the bucket logic below. Layer 1 (system prompt context-entry trimming, `_trim_context_entries`) stays as-is — it operates on database pages, separate concern.

**New Layer 2: bucket-aware trimming.** When `total > available` after Layer 1:

1. **Measure three buckets** (cached per-call):
   - `sources_tokens` = sum of `estimate_token_count(src["content"])` for `src in tool_executor._sources.values() if src["on"]`
   - `history_tokens` = `_estimate_messages_tokens(messages)`
   - `other_tokens` = `system_tokens + tools_tokens - sources_tokens` (sources are part of the system prompt via `build_active_source_content`, so subtract to avoid double-counting)

2. **Pick largest bucket and trim that one.** Margin = 10k tokens above the overflow.

3. **Sources bucket trim** (preferred — lossless):
   - Order sources by LRU (`mounted_at_iteration` ascending; never-mounted = oldest).
   - Skip any source mounted in the last 2 iterations.
   - Toggle `on=False` until enough freed (overflow + 10k margin).
   - Log: `trim: sources bucket, turned off [src_a, src_b], freed 18k tokens`.

4. **History bucket trim** (only if sources bucket is exhausted or smaller):
   - Walk messages from oldest to newest, dropping complete turns until ≥ (overflow + 10k) freed.
   - **Pair safety**: never split a `tool_use` from its matching `tool_result`. Drop in user→assistant→tool_results blocks. Use the existing pair-walking logic from `_summarize_old_turns` lines 270-284 as a starting point.
   - **No pinning** of turn 1 — user confirmed: if history is the limiting factor, the conversation is long enough that turn 1 doesn't matter.
   - Log: `trim: history bucket, dropped turns 0-3, freed 22k tokens`.

5. **Other bucket** (system prompt minus sources): not trimmable in Layer 2 — Layer 1 already handled context entries. Just report it: `bucket measurement: other=42k (untrimmable)`.

6. **Fallback chain**: sources → history → bail (return as-is, the existing 50k/25k retry-on-overflow at `agentic_turn.py:8014-8045` is still the last-resort safety net).

**Observability.** Every trim action logs `[context_trimmer]` with bucket name, action taken, and tokens freed. The Mitchell bug was diagnosed in code archaeology, not from logs — that should never happen again.

### Part 3 — Caching

`tool_executor._sources_token_cache` dict: `{source_id: token_count}`. Invalidate the entry when:
- A source is added/removed.
- A source's `content` changes (currently never happens after creation, so creation-time computation is enough).
Sum on demand from cached values during Layer 2 measurement.

---

## Files to modify

- **`promaia/agents/agentic_turn.py`**
  - Around line 8141-8350 (act-mode tool execution loop): track `act_tool_use_ids` per burst.
  - Around line 8221-8228 (`__DONE__` handler): call new helper `tool_executor.shelve_act_results(act_tool_use_ids, internal_messages, current_iteration)`.
  - Add helper `ToolExecutor.shelve_act_results()` near the existing source management methods (around line 6655 — next to `build_context_index`/`build_active_source_content`).
  - `ToolExecutor.__init__` (line 2960): add `self._sources_token_cache = {}` and update `_sources` schema docstring to include `mounted_at_iteration`.
  - `_context_action` `on` branch (line 6574-6585): set `mounted_at_iteration` when toggled on.
  - Sources created by query_* (`agentic_turn.py:3203, 3245, 3282, 4447`): also set `mounted_at_iteration=0` for consistency.

- **`promaia/agents/context_trimmer.py`**
  - Delete `_summarize_old_turns`, `_call_haiku_for_summary`, `_fix_alternating_structure`.
  - Rewrite Layer 2 in `trim_context_to_fit` (lines 447-455) as `_bucket_trim(messages, tool_executor, available, current_iteration)`.
  - The new function needs `tool_executor` access — add it as a param to `trim_context_to_fit`. Update the call site at `agentic_turn.py:7980`.
  - `trim_context_to_fit_sync` (line 470): no Layer 2 today, leave alone — but verify nothing else calls Layer 2 helpers being deleted.

## Critical existing code to reuse

- `tool_executor._sources` registry — `agentic_turn.py:2961` — already exactly the storage we need.
- `build_context_index` / `build_active_source_content` — lines 6655, 6677 — display path is already wired and respects `_sources_muted` (so act mode still won't see shelved results in its prompt, only think mode will).
- `_context_action` — line 6571 — already implements `turn_on_source`/`turn_off_source` semantics; we're just adding `mounted_at_iteration` bookkeeping.
- Pair-walking logic from `_summarize_old_turns:270-284` — copy into the new history-bucket trimmer before deletion.
- `_estimate_messages_tokens` — line 92 — already correctly handles `tool_use` and `tool_result` blocks.
- `all_tool_calls[]` append at `agentic_turn.py:8313-8317` — workflow capture path; **must remain untouched**. Verify after edit.

## Out of scope (explicit deferrals)

- **Source persistence across conversation reload.** Sources today live in the in-memory `ToolExecutor` and are lost on reload, same as `query_*`. Don't touch.
- **Haiku summarization of dropped history turns.** Considered, deferred. Once shelving is in place, the history bucket should rarely be the limiting factor; if real cases emerge later, add as a follow-up with hard timeout + no-silent-fallback safety rails.
- **Auto-summary metadata for shelved results.** Just structural metadata (tool name, size, char count) in the stub — no Haiku call at shelve time.
- **`trim_context_to_fit_sync`** — has no Layer 2 today, leave as-is.

## Verification

1. **Unit-level**: write a small test case that creates a synthetic conversation with 30 act-mode tool results totaling ~150k tokens, runs through the full think→act→done cycle, and asserts:
   - All 30 tool_result blocks have content replaced with stubs after `__DONE__`.
   - All 30 entries appear in `_sources` with `on=True`.
   - Token count of `internal_messages` drops by ~145k.
   - `all_tool_calls` still contains all 30 entries with original `tool_name`/`input`.
2. **Bucket trimmer test**: feed the trimmer a state where sources=120k, history=20k, other=30k, available=140k → assert sources bucket gets LRU'd, history untouched. Then flip the bucket sizes and assert history gets trimmed instead.
3. **End-to-end**: re-run Mitchell's exact workflow ("generate-vendor-po-with-drawing" with the Macron parts list). After the act burst that fetched all the sheets/emails/MCP data, send a follow-up message asking the model to recall the original parts list. The model should either (a) still see it in history because turn 1 was never trimmed, or (b) `turn_on_source` an MCP-result shelf to re-read it. Either is acceptable — what must NOT happen is the model claiming it doesn't see the original list.
4. **Restart services**: `maia services restart all` after edits, then run the agentic loop via the chat UI. Watch `~/.promaia/.../context_logs/agentic_turn_logs/*.md` for the effective system prompt; verify shelved sources appear in the active-source block when expected.
5. **Logs**: confirm new `[context_trimmer]` log lines fire on long conversations and clearly state which bucket was trimmed.

## Risks / things to double-check during implementation

- **Anthropic API tool_use/tool_result pairing.** After stubbing, every `tool_use` must still match a `tool_result` with the same id and non-empty content. Verify with a synthetic API call before shipping.
- **`_serialize_content_blocks` (line 7799)** must handle stubbed tool_result blocks correctly — should be fine since stubs are plain dicts, but verify.
- **Conversation persistence** (`conversation_manager.py:550-567`) already strips tool_result content on save, so stubs vs full content makes no difference at rest. Verify no other consumer of `history_messages` reads tool_result content (e.g. evals, debugging UI).
- **`_trim_tool_results`** at line 7774 (the 50k/25k retry safety net) still operates on full tool_result content. After our change, post-shelving most tool_results are tiny stubs and this path becomes a true last-resort. Leave it in place.
- **Multiple act bursts in one turn**: each `__DONE__` shelves only that burst's results. `act_tool_use_ids` resets on `__ACT__` entry.
