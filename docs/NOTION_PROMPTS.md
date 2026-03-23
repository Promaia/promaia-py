# Notion Prompts Integration

## Overview

Promaia syncs its main system prompt from Notion instead of using only the local `prompts/prompt.md` file. This allows you to:
- Edit the prompt directly in Notion
- Have changes take effect immediately without code changes
- Keep your prompt organized alongside your other Promaia resources
- Maintain different prompts per workspace

## First-Time Setup

### Prerequisites

1. **Notion Integration**: Create a Notion integration at https://www.notion.so/my-integrations
   - Give it a name (e.g., "Promaia")
   - Copy the integration token (starts with `secret_`)
   - Keep this token secret!

2. **Workspace Configured**: Add your workspace to Promaia
   ```bash
   maia workspace add koii --api-key secret_your_notion_token
   ```

### Setup Steps

#### 1. Duplicate the Promaia Template

The Promaia template page contains:
- **Main prompt** - Your AI assistant's system prompt
- **Agents** database - For scheduled agents
- Other Promaia resources

**Template URL**: https://www.notion.so/koii/Promaia-2f2d133969678183b4b4c6d6931168f5

1. Open the template URL in your browser
2. Click **"Duplicate"** in the top right corner
3. Choose where to save it in your Notion workspace
4. Copy the URL of your duplicated page

#### 2. Share with Your Integration

**Critical**: You must share the duplicated Promaia page with your Notion integration:

1. Go to your duplicated Promaia page
2. Click the **"•••" (three dots)** menu in the top right
3. Click **"Add connections"**
4. Select your Notion integration (e.g., "Promaia")
5. Click **"Confirm"**

This gives the integration access to the page and all its child pages (including Main prompt).

#### 3. Run Setup Command

```bash
maia workspace setup-promaia --workspace koii
```

Or if you have a default workspace set:
```bash
maia workspace setup-promaia
```

The command will:
1. Open the template in your browser
2. Ask you to paste your duplicated page URL
3. Discover child pages (especially "Main prompt")
4. Store the configuration in your workspace

#### 4. Verify Setup

Your prompt will now sync from Notion automatically! To verify:

```bash
maia workspace info koii
```

You should see the Promaia page ID and Main prompt page ID in the output.

## Notion Page Structure

### Required Pages

Your Promaia page should contain at least:

- **Main prompt** (child page) - The system prompt that Promaia uses

### Supported Content Blocks

The Main prompt page can contain:

- **Paragraphs** - Regular text
- **Bulleted lists** - For bullet points
- **Numbered lists** - For sequential items
- **Headings** (H1, H2, H3) - For sections
- **Code blocks** - For examples

### Formatting

- **Bold text** is preserved as `**bold**` in markdown
- Empty lines are preserved for spacing
- Block order is maintained from top to bottom

## Configuration

### Environment Variables

Enable/disable Notion prompt fetching:

```bash
# Enable (default)
export PROMAIA_USE_NOTION_PROMPTS=true

# Disable (use local file only)
export PROMAIA_USE_NOTION_PROMPTS=false
```

### Workspace-Specific Prompts

Each workspace can have its own Promaia page and Main prompt:

```bash
# Set up for workspace "koii"
maia workspace setup-promaia --workspace koii

# Set up for workspace "trass"
maia workspace setup-promaia --workspace trass
```

The workspace configuration is stored in `promaia.config.json`:

```json
{
  "workspaces": {
    "koii": {
      "api_key": "secret_...",
      "promaia_page_id": "2f2d1339...",
      "main_prompt_page_id": "2f4d1339..."
    }
  }
}
```

## How It Works

### Prompt Loading Flow

1. **Notion First**: Promaia tries to fetch from the workspace's Main prompt page
2. **Local Fallback**: If Notion fetch fails, uses local `prompts/prompt.md`
3. **Variable Substitution**: Replaces `{today_date}` and `{current_time}` with current values
4. **Context Appended**: Adds database context and query tools

### When Prompts Sync

Prompts are fetched from Notion:
- Every time you start a new chat session
- When the AI needs to create a system prompt
- When loading context for agents

**Note**: Prompts are NOT cached between sessions, so changes in Notion take effect immediately.

## Benefits

### Live Editing
Edit your prompt in Notion's rich editor without touching code files.

### Version History
Notion automatically tracks all changes with timestamps and revision history.

### Collaboration
Share prompt editing with team members by sharing the Notion page.

### Organization
Keep your prompt alongside other Promaia resources (agents, instructions, journal).

### Safety
Always has local `prompts/prompt.md` as a fallback if Notion is unavailable.

## Troubleshooting

### "Could not find Main prompt page"

**Cause**: The Promaia page doesn't contain a child page named "Main prompt"

**Solution**:
- Ensure you duplicated the correct template
- Check that the child page is named "Main prompt" (case-insensitive)
- Make sure the page is shared with your integration

### "Failed to fetch prompt from Notion"

**Cause**: Integration doesn't have access to the page

**Solution**:
- Share the Promaia page with your integration (see "Share with Your Integration" above)
- Verify your API key is correct: `maia workspace test koii`
- Check that the page IDs are correct: `maia workspace info koii`

### "Page not found" (404 error)

**Cause**: Page ID is incorrect or page was deleted

**Solution**:
- Run setup again: `maia workspace setup-promaia`
- Ensure the Promaia page still exists in your Notion workspace
- Check that it's shared with your integration

### Prompt doesn't update

**Cause**: Changes made in Notion aren't reflecting in Promaia

**Solution**:
- Start a new chat session (prompts load fresh each session)
- Check that `PROMAIA_USE_NOTION_PROMPTS=true` (default)
- Verify the page ID is correct: `maia workspace info koii`

### Using local prompt instead

**Cause**: Notion fetching is disabled or failing silently

**Solution**:
- Check environment variable: `echo $PROMAIA_USE_NOTION_PROMPTS`
- Look for warnings in logs: `tail -f promaia.log`
- Test workspace connection: `maia workspace test koii`

## Files Modified

- `promaia/ai/prompts.py` - Added workspace-aware fetch logic
- `promaia/notion/prompts.py` - Notion fetching module with MCP client
- `promaia/agents/notion_setup.py` - Setup flow for Promaia page discovery
- `promaia/cli/workspace_commands.py` - CLI command for `setup-promaia`
- `promaia/config/workspaces.py` - Added `promaia_page_id` and `main_prompt_page_id` fields

## Example Workflow

### Initial Setup

```bash
# 1. Add workspace
maia workspace add koii --api-key secret_your_token

# 2. Set up Promaia page (opens template in browser)
maia workspace setup-promaia --workspace koii

# 3. Paste duplicated page URL when prompted
# (System discovers Main prompt and other pages)

# 4. Verify
maia workspace info koii
```

### Daily Use

```bash
# Start chat - prompt syncs from Notion automatically
maia chat

# Edit prompt in Notion using rich editor

# Start new chat - changes take effect immediately
maia chat
```

### Multi-Workspace

```bash
# Set up for work workspace
maia workspace setup-promaia --workspace work

# Set up for personal workspace
maia workspace setup-promaia --workspace personal

# Each workspace has its own prompt
maia chat --workspace work    # Uses work prompt
maia chat --workspace personal  # Uses personal prompt
```

## Advanced: Custom Promaia Structure

If you want to customize your Promaia page structure:

1. Duplicate the template
2. Add/remove pages as needed
3. Keep the "Main prompt" child page (required)
4. Share with integration
5. Run `maia workspace setup-promaia`

The system will discover your custom structure and use the Main prompt page.
