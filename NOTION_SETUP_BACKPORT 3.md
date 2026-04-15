# Notion Setup Backport: `../promaia` → `promaia-py`

Source commit: `d948485` "Unify Promaia page setup into single orchestrator" (2026-02-27)

## Overview

`notion_setup.py` was rewritten from a 495-line template-duplication-only approach to a 939-line unified orchestrator with API creation, discovery, and fallback. The old two-function design (`setup_promaia_page` + `ensure_agents_database_exists`) was replaced by a single `ensure_promaia_page_exists()` entry point.

## Architectural Shift

| | promaia-py (current) | promaia (target) |
|---|---|---|
| Entry point | Two disconnected functions | Single orchestrator |
| Creation method | Template duplication only | API creation first, template fallback |
| Discovery | Manual child page scanning | `_discover_promaia_components()` with partial recovery |
| Databases | Created as subpages | Created inline (`is_inline=True`) |
| Tracked IDs | 3 (promaia_page, agents_db, main_prompt) | 4 (+ `prompts_database_id`) |
| CLI flags | `--workspace` only | `--reset`, `--page`, `--set-ids` |
| UX | Plain URLs | QR codes + interactive page selector |

---

## Changes to `promaia/agents/notion_setup.py`

### New functions to add

#### 1. `_render_qr(url: str) -> None`
- Renders QR code in terminal using `qrcode` library
- Light grey styling via Rich console
- Silently skips on import failure
- **Deps:** `qrcode` (optional)

#### 2. `_select_parent_page(pages: List[Tuple[str, str]]) -> Optional[str]`
- Arrow-key TUI selector for choosing where to create the Promaia page
- Uses `prompt_toolkit` (Application, KeyBindings, HSplit)
- Returns page_id or None if cancelled
- **Deps:** `prompt_toolkit`

#### 3. `_create_promaia_structure(client, parent_page_id: str) -> Dict[str, str]`
- Creates the full Promaia page via Notion API:
  - Parent page with octopus emoji
  - Inline "Prompts" database (Name title column)
  - "Main prompt" page inside Prompts DB
  - Inline "Agents" database (Name, Agent ID, Last Run, Status columns)
  - Info heading + help bullets
- Returns dict with all IDs + page URL
- **Key detail:** Uses `is_inline=True` for databases so they render embedded

#### 4. `_discover_promaia_components(client, promaia_page_id: str) -> Dict[str, Optional[str]]`
- Scans existing Promaia page blocks to find child databases and pages
- Handles both API-created (child_database) and template-duplicated (child_page) structures
- Queries prompts DB for "Main prompt" page by name
- Returns dict with discovered IDs (None for missing ones)
- **Key innovation:** Enables partial recovery if setup was interrupted

#### 5. `_template_fallback_method(workspace: str) -> Dict[str, str]`
- Shows QR code + template link for manual duplication
- Prompts user for duplicated page URL
- Calls `_discover_promaia_components()` to find all child IDs
- Validates Agents DB and Main prompt exist
- Saves all IDs to workspace config

#### 6. `ensure_promaia_page_exists(workspace: str) -> Dict[str, str]`
- **The orchestrator** — 191 lines, handles 4 scenarios:
  1. **Already configured** — all IDs present, early return (backfills `prompts_database_id` if missing)
  2. **Partial recovery** — `promaia_page_id` exists but child IDs missing; discover + create missing pieces
  3. **Full creation** — nothing configured; interactive page selection → API creation → save
  4. **Fallback** — API creation fails; fall back to `_template_fallback_method()`

### Functions to refactor

#### 7. `ensure_agents_database_exists(workspace: str) -> str`
- **Current:** 102-line standalone implementation with template duplication
- **Target:** Thin wrapper (~15 lines) that calls `ensure_promaia_page_exists()` and returns `result["agents_database_id"]`

#### 8. `setup_promaia_page(workspace: str) -> tuple[str, str]`
- **Current:** 134-line standalone implementation with template duplication
- **Target:** Thin wrapper (~15 lines) that calls `ensure_promaia_page_exists()` and returns `(result["promaia_page_id"], result["main_prompt_page_id"])`

### Unchanged functions (identical in both repos)
- `_extract_page_id_from_url(url: str) -> str`
- `create_agent_in_notion(agent_config, workspace: str) -> str`
- `markdown_to_notion_blocks(markdown: str) -> List[Dict[str, Any]]`
- `generate_agent_id(name: str, existing_agents: List[Any]) -> str`

### Import changes
- Add `Tuple` to typing imports

---

## Changes to `promaia/cli/workspace_commands.py`

### 1. `handle_workspace_setup_promaia(args)`
- **Current:** 34 lines, simple try/catch around `setup_promaia_page()`
- **Target:** 105 lines, supports 3 new flags:
  - `--reset` — clears all Promaia IDs from workspace config
  - `--page <url>` — discovers components from an existing Promaia page URL
  - `--set-ids` — manually set all 3 IDs (promaia_page, agents_db, prompts_db)
- Imports `_discover_promaia_components` and `_extract_page_id_from_url` from notion_setup

### 2. `handle_workspace_add(args)`
- **Target adds:** Workspace name auto-detection from Notion API
- New imports: `fetch_workspace_name`, `_sanitize_workspace_name`

### 3. `handle_workspace_info(args)`
- **Target adds:** Promaia setup status section showing page ID and database IDs

### 4. Argparser updates
- `setup-promaia` subcommand gains `--reset`, `--page`, `--set-ids` arguments

---

## Changes to `promaia/config/workspaces.py`

### 1. `WorkspaceConfig.__init__()`
- Add: `self.prompts_database_id = config_data.get("prompts_database_id")`

### 2. `WorkspaceConfig.to_dict()`
- Add conditional save:
  ```python
  if self.prompts_database_id:
      data["prompts_database_id"] = self.prompts_database_id
  ```

---

## Dependency checklist

| Dependency | Required? | Already in promaia-py? |
|---|---|---|
| `qrcode` | Optional (QR display) | Check requirements.txt |
| `prompt_toolkit` | Yes (page selector) | Yes (used in setup_commands.py) |
| `rich` | Yes | Yes |
| Notion API client | Yes | Yes |

---

## Backport order

1. `workspaces.py` — add `prompts_database_id` field (2 lines, no deps)
2. `notion_setup.py` — add new functions, then refactor wrappers (replace file)
3. `workspace_commands.py` — add CLI flags and handlers
4. Verify `qrcode` in requirements.txt
