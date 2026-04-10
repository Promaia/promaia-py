# Glacier Pilot Automation Research

## Task 1: Track Responses for Custom Parts

**Best mechanism: Calendar-triggered agent**

The infrastructure already supports this end-to-end:

1. **Trigger**: Create a recurring Google Calendar event (e.g., daily at 9am) on the agent's calendar. The `maia-calendar` daemon polls every 1 minute and triggers the agent when the event starts. The event description becomes the agent's goal.

2. **Flow**:
   - Calendar event fires → `CalendarMonitorDaemon` detects it → calls `Orchestrator.run_goal()`
   - Agent uses `query_source("google_sheets", ...)` or `query_sql(...)` to read the custom parts sheet and find rows in "ordered/waiting" state
   - Agent uses `search_emails(query="from:vendor subject:custom part")` to check for vendor responses
   - Agent uses `get_email_thread(thread_id)` to read full threads and verify address correctness
   - Agent reports findings via messaging (Slack/Discord) or `send_email()` / `create_email_draft()`

3. **What's needed**:
   - A dedicated agent config in `agents.json` with `calendar_id` set to its Google Calendar
   - A recurring calendar event with a description like: *"Check Google Sheets for custom parts in ordered/waiting state. For each, search Gmail for vendor responses. Verify shipping address matches. Report any issues."*
   - Google Sheets must be synced (already supported via `google_sheets_connector.py`)
   - Gmail must be synced and accessible (blocked by OAuth refresh — needs Mitchell's Internal project setup)

4. **All required tools already exist**: `query_sql`, `query_source`, `search_emails`, `get_email_thread`, `send_email`, `reply_to_email`

---

## Task 2: Paying Invoices (Finding + Drafting Responses)

**Best mechanism: `maia mail` (already running as `maia-mail` service)**

The mail processing pipeline is purpose-built for this:

1. **Flow**:
   - `maia-mail` daemon runs every 30 minutes, fetches new emails
   - `EmailClassifier` categorizes each email — can detect invoice-related emails via workspace-specific prompt
   - `ResponseGenerator` drafts a response using persona prompt + learned patterns + vector context
   - Draft saved with status `pending` for review, or can be auto-sent

2. **What's needed**:
   - A workspace-specific classification prompt at `{data_dir}/data/md/prompts/maia_mail_classification_prompt_glacier.md` that teaches the classifier to recognize invoice emails (e.g., "if email mentions invoice, payment due, bill — mark as requires_response=true")
   - A persona prompt at `{data_dir}/data/md/prompts/maia_mail_prompt.md` that includes the invoice response format (e.g., "When replying to invoices, include PO number, payment terms, and forward to accounting@...")
   - The learning system will improve over time — each sent invoice response gets stored and used as a template for future ones

3. **Alternative**: A calendar-triggered agent could also handle this by using `search_emails(query="subject:invoice newer_than:1d")` and `reply_to_email()`. This gives more control over timing and logic but requires more explicit configuration.

**Recommendation**: Use `maia mail` for the inbox-monitoring/draft-generation part (it's already running), and optionally add a calendar-triggered agent for proactive checks (e.g., "find unpaid invoices older than 3 days and escalate").

---

## Blockers

Both tasks require working Gmail access, which is currently blocked by the expired Google OAuth token. Mitchell needs to either:
- Re-auth with the existing testing-mode credentials (temporary, expires again in 7 days), or
- Set up an Internal Google Cloud project per the doc at `docs/2026-03-17_GOOGLE_OAUTH_PILOT_SETUP.md` (permanent fix)
