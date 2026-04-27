"""
Core workflow: Edit an existing agent.

Guides the user through viewing and modifying an agent's configuration.
"""

from promaia.chat.workflows import register_workflow

AGENT_EDIT_PROMPT = """\
## Active Interview: Edit Agent

You are helping the user view and edit an existing agent's configuration.

### Steps

1. **Identify the agent**: If the user didn't specify which agent, use \
`list_agents` to show available agents and ask which one. If they named \
it, proceed directly.

2. **Show current config**: Call `agent_info` to display the agent's \
current configuration. Present it clearly.

3. **Ask what to change**: Present the editable fields:
   - **Description**: What the agent does
   - **Databases**: Data sources (use `list_databases` to show options)
   - **MCP tools**: Available tools (gmail, calendar, notion, web_search, etc.)
   - **Prompt**: System prompt / instructions
   - **Schedule**: Interval in minutes
   - **Max iterations**: Per-run iteration limit
   - **Messaging**: Enable/disable messaging tools (start_conversation, end_conversation)
   - **Channel permissions**: Restrict which Slack/Discord channels the \
agent can respond in (read) and post to (write). Two ways:
     - **Group form (preferred):** pass `allowed_channel_groups` as an \
object with keys "dm" and "channel"; values are either `["*"]` (any of \
that type) or a list of specific channel IDs. e.g. \
`{"dm": ["*"], "channel": ["C_engineering"]}` = "any DM, only \
#engineering." Pass `allowed_output_channel_groups` separately if write \
should differ from read; omit it to mirror read.
     - **Flat list (legacy):** pass `allowed_channel_ids` as a list of \
specific IDs. Use `list_channels` to find IDs first. Output side: \
`allowed_output_channel_ids`.
     - Pass `null`/empty to remove restrictions in either form.
   - **MCP tool allow list**: Restrict which tools on each MCP server \
the agent may call. Pass `mcp_tool_allowlist` as an object keyed by \
server name (matching entries in `mcp_tools`), with values being lists \
of tool names. e.g. `{"po-manager": ["list_vendors", "list_parts"]}` \
means the agent can call those two tools and nothing else on \
po-manager. Built-in integrations (`gmail`, `calendar`) don't need \
entries — they have their own runtime gates. Pass `null` to remove \
the restriction (legacy allow-all).
   - **Name**: Rename the agent (uses `rename_agent`)
   - **Enable/Disable**: Toggle the agent on/off

   Note: `agent_id` is immutable and cannot be edited. You cannot edit the
   agent that is currently running (i.e. yourself) — only the user can do
   that directly.

   TODO: once agent tiers/ranks are introduced, add tier-aware
   create/edit permissions to this workflow so lower-ranked agents can
   only be edited by higher-ranked ones.

   Let the user pick what they want to change.

4. **Make changes**: For each change the user requests:
   - For name changes: use `rename_agent`
   - For enable/disable: use `enable_agent` or `disable_agent`
   - For all other fields: collect the changes and call `update_agent` \
     with all modified fields at once
   - Confirm before applying changes

5. **Verify**: After changes are saved, call `agent_info` again to \
show the updated configuration.

6. **Offer more changes**: Ask if they want to change anything else. \
If yes, go back to step 4. If no, proceed to complete.

7. **Complete**: Call `complete_interview` to end.

### Notes

- For database changes, use `list_databases` to show what's available. \
Databases are access permissions — just use plain names (e.g., "journal", "gmail").
- When editing the prompt, show the current prompt first and ask how \
they want to modify it. They can provide a full replacement or describe \
changes and you can draft the new version.
- Multiple fields can be updated in a single `update_agent` call.
"""

register_workflow(
    name="agent_edit",
    description="Edit an existing agent's configuration (databases, tools, prompt, schedule, etc.)",
    system_prompt_insert=AGENT_EDIT_PROMPT,
)
