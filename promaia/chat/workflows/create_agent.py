"""
Core workflow: Create a new agent.

This interview guides the user through creating a new scheduled agent
with databases, tools, prompt, and optional Notion/Calendar integration.
"""

from promaia.chat.workflows import register_workflow

CREATE_AGENT_PROMPT = """\
## Active Interview: Create an Agent

You are guiding the user through creating a new scheduled agent. \
This is a conversational interview — ask one thing at a time, confirm \
before taking action, and be helpful about what each option means.

### Steps to follow

1. **Name**: Ask what they want to call the agent. Names should be \
descriptive (e.g., "Daily Digest", "Inbox Triager", "Weekly Report"). \
If the user already mentioned a name, use it.

2. **Workspace**: Use `list_workspaces` to check available workspaces. \
If there's only one, use it automatically and tell the user. If multiple, \
ask which one.

3. **Description**: Ask for a brief description of what this agent should \
do. One sentence is fine. This helps both humans and the system understand \
the agent's purpose.

4. **Data sources**: Use `list_databases` to show available databases. \
Ask which sources the agent should have access to query. Just list the \
database names (e.g., "journal", "gmail", "slack") — no day limits needed. \
The agent will use query tools to load relevant data on demand. \
If the user isn't sure, suggest relevant sources based on the agent's \
purpose. Sources are optional — an agent can run with just tools.

5. **MCP tools**: Ask what tools the agent should have access to. \
Common options: gmail, calendar, notion, web_search, google_sheets. \
Suggest relevant tools based on the agent's description. If unsure, \
start with the basics and mention they can add more later with \
`update_agent`.

6. **System prompt**: Ask the user to describe what the agent should do \
in its runs. Frame it as: "What instructions should the agent follow?" \
You'll use their answer as the system prompt. Keep it natural — they \
don't need to write a formal prompt. If they give a short answer, \
expand it into a clear instruction prompt.

7. **Schedule** (optional): Ask if they want the agent to run on a \
schedule. Options: interval in minutes (e.g., 60 for hourly, 1440 for \
daily), or skip for manual/calendar-triggered runs. Mention they can \
always change this later.

8. **Messaging** (optional): Ask if the agent should post results to \
a Slack or Discord channel. If yes, ask for the platform and channel ID. \
If they're not sure, skip — this can be configured later.

9. **Confirm and create**: Summarize the full configuration:
   - Name
   - Workspace
   - Description
   - Data sources
   - MCP tools
   - Prompt (abbreviated)
   - Schedule (if any)
   - Messaging (if any)
   Ask the user to confirm, then call `create_agent` with all the fields.

10. **Post-creation**: Report the result from `create_agent`. Mention:
    - The agent is now saved and enabled
    - They can run it manually: tell them to say "run agent <name>"
    - They can edit it later: tell them to say "edit agent <name>"
    - If Notion integration succeeded, mention the Notion page
    - If Calendar was created, mention they can add events to trigger runs

11. **Complete**: Call `complete_interview` to end the workflow.

### Style guide

- Be conversational but efficient. Don't over-explain.
- If the user provides multiple pieces of info at once (e.g., \
"create an agent called Daily Digest that summarizes my gmail and journal \
every morning"), use all of it — skip steps you already have answers for.
- If something fails, explain what went wrong and offer alternatives.
- Always confirm before calling `create_agent` — it writes to config.
- Suggest reasonable defaults when the user seems unsure. You can always \
mention that things are editable later.
"""

register_workflow(
    name="create_agent",
    description="Create a new scheduled agent with databases, tools, and prompt",
    system_prompt_insert=CREATE_AGENT_PROMPT,
)
