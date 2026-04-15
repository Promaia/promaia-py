"""
Core workflow: Edit channels on a Discord or Slack source.

Guides the user through selecting which channels to sync for an
existing Discord or Slack database source.
"""

from promaia.chat.workflows import register_workflow

EDIT_CHANNELS_PROMPT = """\
## Active Interview: Edit Channels

You are helping the user configure which channels to sync for a Discord \
or Slack database source.

### Steps

1. **Identify the source**: If the user didn't specify which source, use \
`list_databases` to find Discord/Slack sources and ask which one.

2. **Show current config**: Call `get_configured_channels` to show what's \
currently selected.

3. **Fetch available channels**: Call `list_channels` to get all accessible \
channels from the server.

4. **Let the user pick**: Parse the channel list from `list_channels` and \
call `show_selection` with the full channel list as items, using \
`multi_select: true`. Set the title to include the server/source name. \
Format items as: `{id: channel_id, label: channel_name, group: "Channels"}`. \
Pass `pre_selected` with the list of currently-configured channel IDs \
from step 2, so they appear pre-checked in the widget.

5. **Save selection**: When you receive a message starting with \
"[Selection complete", the user has made their picks via the widget. \
Extract the IDs and names from that message and immediately call \
`update_channels`. Do NOT call `show_selection` again — the user \
already made their choice.

6. **Confirm**: Tell the user what was saved and offer to sync: \
"Want me to sync it now?" If yes, call `sync_database`.

7. **Complete**: Call `complete_interview` to end.

### Notes

- The `show_selection` tool pauses the conversation. The user's selection \
will arrive in the next message as `[User selected: id1, id2, ...]`.
- When building the channel_names dict for `update_channels`, map each \
selected channel ID to its name from the `list_channels` result.
- If the source has no bot token or credentials, tell the user what to configure.
"""

register_workflow(
    name="edit_channels",
    description="Edit which channels to sync for a Discord or Slack source",
    system_prompt_insert=EDIT_CHANNELS_PROMPT,
)
