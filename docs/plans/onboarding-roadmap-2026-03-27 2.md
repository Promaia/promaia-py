# Onboarding Roadmap — 2026-03-27

## NOW (this branch)

### 1. Skip auth if already configured

**Problem**: `maia setup notion` forces re-auth even when Notion is already connected.

**Fix**: In `_run_single_service_setup()` and in the full `_run_setup()`, check if credentials already exist and are valid. If so, skip straight to the source browser.

**How it works today**: `configure_credential()` in `auth/flow.py` already checks `integration.get_default_credential()` — if it finds one, it asks "Reconfigure?". But the setup flow calls `configure_credential()` unconditionally.

**Change**: Before calling `configure_credential()`, check if creds exist and are valid. If yes, print "✓ Notion already connected" and ask "Reconfigure? [y/N]". Default is skip to source selection.

**Files**: `promaia/cli/setup_commands.py` (lines ~120-146 for full setup, ~260-275 for single-service)

---

### 2. Progress indicator

**Problem**: User has no idea where they are in a 10-step flow.

**Design**: A persistent **footer** (bottom of terminal) showing steps as dots/lines:

```
  Notion — Select Databases

  ... (content scrolls above) ...

  ─────────────────────────────────────────────────────────
  Workspace ✓ ── AI ✓ ── Notion ● ── Google ○ ── Slack ○ ── Sync ○ ── Agent ○
```

- `✓` = completed
- `●` = current
- Dim/unfilled = remaining
- For `maia setup [connector]`, show sub-steps only: `Auth ── Sources ── Sync`

**Implementation**: A `SetupProgress` class that tracks current step and renders a formatted header. Print it before each step. Clear/reprint as steps complete.

**Files**: `promaia/cli/setup_commands.py` (new class + calls at each step boundary)

---

### 3. Back/skip navigation

**Problem**: No way to skip a step. Slack forces bot creation even if user just wants Notion.

**Fix**: Each connector step wraps in a skip-check:
- Before starting: "Connect Notion? [Y/n/skip]"
- On skip: mark as skipped in progress, move to next step
- On Ctrl+C within a step: offer "Skip this step?" instead of aborting entirely

**Files**: `promaia/cli/setup_commands.py`

---

### 4. Connector one-liner descriptions

**Problem**: Setup shows bare names ("Notion", "Google") with no context on what they enable.

**Add descriptions shown before each connector step:**

| Connector | One-liner |
|-----------|-----------|
| Notion | "Select the databases you use most" |
| Google | "Select the sheets and folders you use often" |
| Slack | "Option 1 for where you'll interact with Promaia — select the channels you want Promaia to have access to" |
| Discord | "Option 2 for where you'll interact with Promaia — select the channels you want Promaia to have access to" |
| AI Provider | "Which AI model powers Promaia's brain — we recommend Anthropic or OpenRouter" |

**Where**: Print as part of the step header, right after the progress indicator.

**Files**: `promaia/cli/setup_commands.py`

---

### 5. Slack setup instructions

**Problem**: Users don't know how to create a Slack bot. The redirect to the manifest URL is unexplained.

**Add bullet-point guide before the bot creation step:**

```
Slack Setup — Where you'll chat with Promaia

To connect Slack, you'll create a bot app:
  1. Click the link below (or scan the QR code)
  2. Pick your Slack workspace when prompted
  3. Click "Create" to install the bot
  4. Go to "OAuth & Permissions" → copy the Bot Token (starts with xoxb-)
  5. Go to "Basic Information" → scroll to "App-Level Tokens" → create one with
     connections:write scope → copy it (starts with xapp-)
  6. Paste both tokens below
```

**Files**: `promaia/cli/setup_commands.py` (`_setup_slack()`)

---

### 6. Google Drive source browser (unified — includes Sheets)

**Problem**: No way to browse and select Drive folders or Sheets during setup. Sheets ARE Drive files — they live in the folder hierarchy naturally. Two separate browsers would be artificial.

**Design**: One unified Drive browser, layer-by-layer. Sheets show inline alongside folders.

**Drive browser** (`_browse_google_drive()`):
- Initial view: starred/recent items + root-level contents
- Folders and Sheets both visible — Sheets get a `📊` indicator, folders get `📁`
- Selecting a folder = `cd` into it (navigates deeper, doesn't add as source)
- Selecting a Sheet = add as source (`source_type="google_sheets"`)
- Adding a folder as source = checkbox on folder row (`source_type="google_drive"`)
- Breadcrumb trail: `My Drive > Projects > Q1` with back option
- Uses unified selector with [Browse] / [Paste Link] tabs
- `source_type` set automatically based on what's selected (folder vs sheet)

**Notion browser** (update existing):
- Same layer-by-layer: top-level first, "load more" goes down one level
- Consistent with Drive approach

**Principle**: Never load the entire tree. Show the most relevant stuff first, let the user drill deeper or paste a link.

**Files**: `promaia/cli/setup_commands.py` (new function), `promaia/auth/integrations/google.py` (Drive API list helpers if not present)

---

### 7. Unified source selector: browse + paste + load more — all in one screen

**Problem**: Currently the browser shows top-level sources, then asks "load more?" on a separate prompt. Pasting links requires a different flow. User should be able to do everything from one widget.

**Design**: Single interactive selector with two tabs and inline actions:

```
  Notion — Select Sources            [Browse]  [Paste Link]    ← Tab to switch

  ☑ Journal                          8 entries, synced daily
  ☐ Stories                          1 entry
  ☐ Projects                         12 entries
  ── Load 14 more from sub-pages ──  ← selectable row, inline

  ↑↓ Navigate   SPACE Select   TAB Switch mode   ENTER Confirm   ESC Cancel
```

**Browse mode** (default tab):
- Shows top-level sources with checkboxes
- "Load more from sub-pages" is a selectable row in the list (not a separate prompt)
- Selecting it expands nested sources inline, grouped by parent
- Same `_multi_select_flat()` widget, extended

**Paste Link mode** (Tab to switch):
- Text input for pasting a URL
- On paste: extract ID, look up metadata via API, show confirmation
- "Add another? [paste or Enter to go back to browse]"
- Can paste as many as you like, one at a time
- Pasted sources appear as pre-selected in the browse list when you Tab back

Applies to: Notion, Google Drive, Google Sheets — all use the same widget.

**Files**: `promaia/cli/setup_commands.py` (refactor `_multi_select_flat()` into unified source selector)

---

---

## LATER
- `onboard_tutorial` workflow (post-agent-creation agentic walkthrough) — urgent but separate scope
- README overhaul (separate PR)
- Resumable setup (persistent progress across interruptions)
- First-run auto-detection (install.sh already handles this)
