# Plan: Retry with exponential backoff on Anthropic 529 / overloaded errors

## Context

Mitchell (pilot user) saw this surfaced into Slack:

> I'm sorry, I encountered an error (OverloadedError: Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}, ...}). Please try again.

HTTP 529 `overloaded_error` is Anthropic's server-side capacity throttle — transient, not a Promaia bug. Today the agentic loop wraps `client.messages.create` in a single try/except (`promaia/agents/agentic_turn.py:8171-8237`) that only has a special path for `prompt is too long`. Every other exception — including 529 — is re-raised, which surfaces the raw SDK error dict to the user and kills the turn.

**Intended outcome:** transient overloads become invisible to the user. Retry with exponential backoff inside the same turn. Only if we exhaust a reasonable retry budget do we surface a friendly message (not the raw dict).

## Design

### Retry policy

- **Retryable errors:** `overloaded_error` (529), `rate_limit_error` (429), `api_error` (500), `service_unavailable` (503). Anthropic SDK exposes these via exception type and/or status code — match by scanning `str(err)` + `type(err).__name__` to cover both SDK shapes.
- **Max attempts:** 5 total (1 initial + 4 retries).
- **Backoff schedule:** 2s, 4s, 8s, 16s (exponential, base=2, starting at 2s). Total worst-case wait: ~30s. No jitter for now — simple.
- **Scope:** the primary `client.messages.create` call at line 8171-8175. The inner retry on `prompt is too long` (line 8201-8208) stays as-is — that path is already handling a different failure mode.

### User-facing message on exhaustion

When all 5 attempts fail with a retryable error:
- Log the full error chain at WARNING level.
- Return an `AgenticTurnResult` with a friendly `response_text`:
  > "Claude is currently overloaded and I couldn't get a response after several retries. Please try again in a moment."
- Preserve any `text_parts` accumulated from prior iterations so partial work isn't lost, same pattern as the existing `prompt is too long` exhaustion branch (line 8210-8235).

### UX callback during retries

On each retry, fire `on_tool_activity` with `tool_name="__api_retry__"`, `summary=f"Anthropic overloaded, retrying in {delay}s (attempt {n}/{max})"` so the user sees the delay instead of a silent hang. Matches the existing `__context_trim__` pattern at line 8192-8199.

### Non-retryable errors

All non-retryable errors (auth, bad request, etc.) re-raise unchanged — matches current `else: raise` at line 8236-8237. The `prompt is too long` branch stays first in the chain and takes precedence.

### Code shape

Introduce a small helper `async def _call_with_retry(client, api_kwargs, on_tool_activity)` near `_trim_tool_results` (~line 7774). It:
1. Loops up to 5 attempts.
2. On each attempt calls `asyncio.to_thread(client.messages.create, **api_kwargs)`.
3. On catchable retryable error, sleeps `2 ** attempt` seconds via `asyncio.sleep`, fires UX callback, continues.
4. On non-retryable error, re-raises.
5. After exhaustion, raises a sentinel `_OverloadExhausted` exception that the caller recognizes.

The main loop catches `_OverloadExhausted` alongside the existing `prompt is too long` branch and returns the friendly `AgenticTurnResult`.

Helper placement keeps the main agentic loop readable; shared with the existing retry-on-trim path (both paths use the same helper).

## Files to modify

- **`promaia/agents/agentic_turn.py`**
  - Add `_RETRYABLE_ERROR_MARKERS` constant and `_OverloadExhausted` sentinel near the top of the file (near other module constants).
  - Add `_is_retryable_api_error(err)` and `_call_with_retry(...)` helpers near `_trim_tool_results` (~line 7774).
  - Replace the `try/except` around `client.messages.create` at lines 8171-8237: call `_call_with_retry` first, then on `_OverloadExhausted` return the friendly result (reusing the existing exhaustion return shape).
  - The inner retry at lines 8201-8208 also gets `_call_with_retry` so tightened-context retries are also protected from 529.

## Verification

1. **Unit-style smoke**: write a tiny test that monkeypatches `client.messages.create` to raise a synthetic `OverloadedError` 3 times then succeed. Assert the caller receives the success and total attempts=4.
2. **Exhaustion path**: monkeypatch to raise overload 5 times. Assert the caller gets an `AgenticTurnResult` with the friendly message and no raw error dict.
3. **Non-retryable passthrough**: monkeypatch to raise `BadRequestError`. Assert it propagates unchanged.
4. **Live**: `maia services restart all`, trigger an agentic turn normally, verify no regression in happy path (check `context_logs/agentic_turn_logs/*.md` still written).
5. **Backoff timing**: check logs for the `[agentic] Anthropic overloaded, retrying in Ns` lines and confirm delays match 2/4/8/16s schedule.

## Out of scope

- Jitter, adaptive backoff, circuit breaker — keep it simple.
- Retry on 529 at other `client.messages.create` sites (`_call_haiku_for_summary` was already deleted; other call sites if any can be migrated later).
- Changing the `prompt is too long` handling.
- Global/shared retry across turns — each turn is independent.
