"""
Core workflow: Edit a database source.

Guides the user through editing a database configuration — channel
selection for Discord/Slack, renaming, viewing info, or removing.
"""

from promaia.chat.workflows import register_workflow

DATABASE_EDIT_PROMPT = """\
## Active Interview: Edit Database

You are helping the user view and edit a configured database source.

### Steps

1. **Identify the database**: If the user didn't specify which one, use \
`list_databases` to show all configured sources and ask which one to edit.

2. **Show current config**: Show the database name, type, workspace, \
and current configuration. For Discord/Slack sources, also call \
`get_configured_channels` to show selected channels.

3. **Present options**: Based on the source type, offer relevant actions:

   **For Discord/Slack sources:**
   - **Edit channels**: Change which channels are synced
   - **Rename**: Change the database nickname
   - **Sync now**: Trigger a sync
   - **Remove**: Delete the database configuration

   **For other sources (Notion, Gmail, Google Sheets, etc.):**
   - **Rename**: Change the database nickname
   - **Sync now**: Trigger a sync
   - **Remove**: Delete the database configuration

4. **Edit channels** (Discord/Slack only): If the user wants to change \
channels, follow the channel editing flow:
   a. Call `list_channels` to get all accessible channels
   b. Call `show_selection` with the full channel list as items, using \
      `multi_select: true`. Set the title to include the source name. \
      Format items as: `{id: channel_id, label: channel_name, group: "Channels"}`. \
      Pass `pre_selected` with the currently-configured channel IDs so \
      they appear pre-checked.
   c. When you receive a message starting with "[Selection complete", \
      extract the IDs and names and call `update_channels`. Do NOT call \
      `show_selection` again.
   d. Confirm the update.

5. **Rename**: If the user wants to rename, ask for the new name and \
call `rename_database`.

6. **Sync**: If the user wants to sync, call `sync_database`.

7. **Remove**: If the user wants to remove the database, confirm \
first (this is destructive), then inform them removal should be done \
via CLI: `maia database remove <name>`. (There is no remove tool yet.)

8. **Offer more actions**: Ask if they want to do anything else with \
this database or another one. If yes, loop back. If no, proceed.

9. **Complete**: Call `complete_interview` to end.

### Notes

- The `show_selection` tool pauses the conversation. The user's selection \
arrives in the next message as `[User selected: id1, id2, ...]`.
- When building the channel_names dict for `update_channels`, map each \
selected channel ID to its name from the `list_channels` result.
- This workflow effectively supersedes `edit_channels` for channel editing \
but also covers non-channel databases.
"""

register_workflow(
    name="database_edit",
    description="Edit a database source (channels, rename, sync, or remove)",
    system_prompt_insert=DATABASE_EDIT_PROMPT,
)
