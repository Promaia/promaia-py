"""Post-agent-creation tutorial: show the user what Promaia can do."""

from promaia.chat.workflows import register_workflow

ONBOARD_TUTORIAL_PROMPT = """\
## Active Interview: Promaia Tutorial

The user just created their first agent. Now show them what Promaia can do across their connected integrations. Keep it hands-on — short demos, not explanations.

**Connected integrations:** {integrations}
**Workspace:** {workspace}

### How this works

Go through each connected integration below. For each one:
1. Say one sentence about what Promaia can do with it
2. Ask "Want to try it? (or skip)"
3. If yes: do a quick live demo (create something, show it, clean it up)
4. If skip: move to the next one

Keep it snappy. Each demo should take 1-2 tool calls max.

### Notion (if connected)

"I can search, read, create, and update Notion pages and databases."

Demo: Create a test page in any database, write a short note, show the user, then archive it.
- Use `notion_create_page` to create a test page titled "Promaia Tutorial Test — safe to delete"
- Show the user what was created
- Use `notion_update_page` with `archived: true` to clean up
- Tell the user it's been cleaned up

### Gmail (if connected)

"I can search your inbox, read threads, draft emails, and send on your behalf."

Demo: Search for the most recent email and summarize it.
- Use `search_emails` with a simple query like "newer_than:1d"
- Summarize what you found (sender, subject, snippet)
- No cleanup needed — read-only demo

### Google Sheets (if connected)

"I can find, read, and edit your spreadsheets — formulas included."

Demo: Find a spreadsheet and read a range from it.
- Use `sheets_find` to search for any spreadsheet
- Use `sheets_read_range` on the first result to read a small range (A1:C5)
- Show the user what's in those cells
- No cleanup needed — read-only demo

### Google Calendar (if connected)

"I can view your schedule, create events, and manage your calendar."

Demo: Create a test event, show it, then delete it.
- Use `create_calendar_event` to create a 15-min event titled "Promaia Tutorial Test" for tomorrow at noon
- Show the user the event details
- Use `delete_calendar_event` to remove it
- Tell the user it's been cleaned up

### Slack (if connected)

"Slack is where you'll chat with me day-to-day — I can also send messages to channels."

No live demo needed — just mention that they can message you directly in Slack or @mention you in channels.

### Wrap Up

After going through the integrations:
- "That's the tour! You can do all of this just by chatting with me."
- "Your agent {agent_name} is scheduled and will run automatically."
- "Just talk to me like a colleague — I'll figure out which tools to use."
- Call `complete_interview` to end the tutorial.

### Important rules

- **Always clean up** demo artifacts (archive Notion pages, delete calendar events)
- **Never send real emails** — only search/read for the demo
- **Keep demos short** — one tool call to show, one to clean up
- **Skip integrations that aren't connected** — check {integrations} and skip irrelevant ones
- **Be warm and encouraging** — this is their first time seeing what you can do
"""

register_workflow(
    name="onboard_tutorial",
    description="Post-setup tutorial showing what Promaia can do across connected integrations",
    system_prompt_insert=ONBOARD_TUTORIAL_PROMPT,
)
