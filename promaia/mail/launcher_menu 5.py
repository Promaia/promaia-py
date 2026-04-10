"""
Maia Mail launcher menu — interactive entry point for `maia mail`.

Shows a selector with workspace items and setup, using the same
prompt_toolkit pattern as setup_ui._inline_select.
"""
import sys
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


async def launch_mail_menu() -> Optional[Dict]:
    """Show the maia mail launcher menu.

    Returns a dict describing the user's choice:
        {'action': 'process_review', 'workspace': 'glacier'}
        {'action': 'review', 'workspace': 'glacier'}
        {'action': 'preview', 'workspace': 'glacier'}
        {'action': 'setup'}
        {'action': 'quit'}
        None  (Escape)
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    from promaia.config.workspaces import get_workspace_manager

    # ── Build menu items ──────────────────────────────────────────────
    workspace_manager = get_workspace_manager()
    workspace_list = workspace_manager.list_workspaces() or []

    # Get mail-enabled status for each workspace
    workspace_items = []
    for ws in workspace_list:
        ws_config = workspace_manager.get_workspace(ws)
        mail_on = getattr(ws_config, 'mail_enabled', False) if ws_config else False
        workspace_items.append({'name': ws, 'mail_enabled': mail_on})

    # Build rows: mix of static strings and selectable dicts
    rows: List = []
    selectable_indices: List[int] = []

    rows.append("")
    rows.append("  \033[1;36m📬 Maia Mail\033[0m")
    rows.append("")

    if workspace_items:
        rows.append("  \033[1m📋 Process and Review\033[0m")
        for ws_info in workspace_items:
            name = ws_info['name']
            tag = "  \033[2m(auto-process: on)\033[0m" if ws_info['mail_enabled'] else ""
            idx = len(rows)
            selectable_indices.append(idx)
            rows.append({
                'label': f"{name}{tag}",
                'action': 'process_review',
                'workspace': name,
                'indent': 4,
            })
        rows.append("")
    else:
        rows.append("  \033[2mNo workspaces configured\033[0m")
        rows.append("")

    # Setup item — aligned with 📋 header (indent 0, emoji in label)
    idx = len(rows)
    selectable_indices.append(idx)
    rows.append({
        'label': '⚙  Setup',
        'action': 'setup',
        'indent': 0,
    })

    # Cancel item
    rows.append("")
    idx = len(rows)
    selectable_indices.append(idx)
    rows.append({
        'label': 'Cancel',
        'action': 'quit',
        'indent': 0,
    })

    rows.append("")

    # ── Render function ───────────────────────────────────────────────
    cursor = [0]  # index into selectable_indices
    result: Dict = {'value': None}

    def _render():
        active_row = selectable_indices[cursor[0]]
        out = []
        for i, row in enumerate(rows):
            if isinstance(row, str):
                out.append(row)
            else:
                indent = " " * row.get("indent", 0)
                label = row["label"]
                if i == active_row:
                    out.append(f"{indent}\033[96m❯ \033[1m{label}\033[0m")
                else:
                    out.append(f"{indent}  {label}")
        out.append("  \033[2m↑/↓ navigate  •  Enter select  •  q quit\033[0m")
        out.append("  \033[2mr review only  •  p process only  •  d dry run\033[0m")
        return "\n".join(out)

    def _redraw():
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.write(_render())
        sys.stdout.write("\n")
        sys.stdout.flush()

    # ── Key bindings ──────────────────────────────────────────────────
    kb = KeyBindings()

    @kb.add(Keys.Up)
    def _up(event):
        cursor[0] = (cursor[0] - 1) % len(selectable_indices)
        _redraw()

    @kb.add(Keys.Down)
    def _down(event):
        cursor[0] = (cursor[0] + 1) % len(selectable_indices)
        _redraw()

    @kb.add(Keys.Enter)
    def _enter(event):
        selected = rows[selectable_indices[cursor[0]]]
        result['value'] = selected
        event.app.exit()

    @kb.add('p')
    def _p(event):
        """Process only for the focused workspace."""
        selected = rows[selectable_indices[cursor[0]]]
        if isinstance(selected, dict) and selected.get('workspace'):
            result['value'] = {
                'action': 'preview',
                'workspace': selected['workspace'],
            }
            event.app.exit()

    @kb.add('r')
    def _r(event):
        """Review only (no processing) for the focused workspace."""
        selected = rows[selectable_indices[cursor[0]]]
        if isinstance(selected, dict) and selected.get('workspace'):
            result['value'] = {
                'action': 'review',
                'workspace': selected['workspace'],
            }
            event.app.exit()

    @kb.add('d')
    def _d(event):
        """Dry run (process without saving) for the focused workspace."""
        selected = rows[selectable_indices[cursor[0]]]
        if isinstance(selected, dict) and selected.get('workspace'):
            result['value'] = {
                'action': 'dry_run',
                'workspace': selected['workspace'],
            }
            event.app.exit()

    @kb.add('q')
    def _quit(event):
        result['value'] = {'action': 'quit'}
        event.app.exit()

    @kb.add(Keys.Escape)
    def _esc(event):
        result['value'] = None
        event.app.exit()

    @kb.add(Keys.ControlC)
    def _ctrl_c(event):
        result['value'] = {'action': 'quit'}
        event.app.exit()

    # ── Run ───────────────────────────────────────────────────────────
    _redraw()

    app = Application(
        layout=Layout(Window(FormattedTextControl(text=''))),
        key_bindings=kb,
        full_screen=False,
        mouse_support=False,
    )

    try:
        await app.run_async()
    except KeyboardInterrupt:
        return {'action': 'quit'}

    return result['value']
