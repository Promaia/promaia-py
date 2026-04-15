"""
Inline selection widget for chat workflows.

Renders a prompt_toolkit-based picker that the AI can trigger
mid-conversation for complex selections (4+ items, multi-select).
Styled to match the workspace browser (Ctrl+B).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style

logger = logging.getLogger(__name__)

# Column widths (matching browser proportions)
_COL_NAME = 36
_COL_TOTAL = _COL_NAME + 4


@dataclass
class SelectionItem:
    id: str
    label: str
    description: str = ""
    group: str = ""


@dataclass
class SelectionResult:
    selected: List[str] = field(default_factory=list)
    cancelled: bool = False


def _make_separator():
    return "\u2500" * _COL_TOTAL


async def show_inline_selection(
    title: str,
    items: List[Dict],
    multi_select: bool = False,
    pre_selected: Optional[List[str]] = None,
) -> SelectionResult:
    """Render an interactive selection menu in the terminal.

    Args:
        title: Menu title
        items: List of dicts with 'id', 'label', optional 'description' and 'group'
        multi_select: If True, allow multiple selections (checkbox). If False, single (radio).
        pre_selected: List of item IDs to pre-select.

    Returns:
        SelectionResult with selected IDs, or cancelled=True if user pressed Esc.
    """
    if not items:
        return SelectionResult(cancelled=True)

    pre_selected_set = set(pre_selected or [])

    # Parse items
    entries = [
        SelectionItem(
            id=item.get("id", str(i)),
            label=item.get("label", f"Item {i}"),
            description=item.get("description", ""),
            group=item.get("group", ""),
        )
        for i, item in enumerate(items)
    ]

    # Build display rows: (entry_index | -1 for headers, group_label)
    display_rows = []
    if any(e.group for e in entries):
        groups: Dict[str, List[int]] = {}
        groups_order = []
        for i, e in enumerate(entries):
            g = e.group or "Other"
            if g not in groups:
                groups[g] = []
                groups_order.append(g)
            groups[g].append(i)
        for g in groups_order:
            display_rows.append((-1, g))
            for idx in groups[g]:
                display_rows.append((idx, None))
    else:
        for i in range(len(entries)):
            display_rows.append((i, None))

    # State
    current_focus = 0
    selected_states = [e.id in pre_selected_set for e in entries]
    confirmed = False

    # Skip headers for initial focus
    while current_focus < len(display_rows) and display_rows[current_focus][0] == -1:
        current_focus += 1

    def _next_selectable(pos, direction=1):
        pos += direction
        while 0 <= pos < len(display_rows):
            if display_rows[pos][0] != -1:
                return pos
            pos += direction
        return None

    bindings = KeyBindings()

    @bindings.add(Keys.Up)
    def move_up(event):
        nonlocal current_focus
        nxt = _next_selectable(current_focus, -1)
        if nxt is not None:
            current_focus = nxt
            event.app.layout = create_layout()

    @bindings.add(Keys.Down)
    def move_down(event):
        nonlocal current_focus
        nxt = _next_selectable(current_focus, 1)
        if nxt is not None:
            current_focus = nxt
            event.app.layout = create_layout()

    @bindings.add(' ')
    def toggle(event):
        entry_idx = display_rows[current_focus][0]
        if entry_idx == -1:
            return
        if multi_select:
            selected_states[entry_idx] = not selected_states[entry_idx]
        else:
            for i in range(len(selected_states)):
                selected_states[i] = False
            selected_states[entry_idx] = True
        event.app.layout = create_layout()

    @bindings.add(Keys.Enter)
    def confirm_sel(event):
        nonlocal confirmed
        if not multi_select and not any(selected_states):
            entry_idx = display_rows[current_focus][0]
            if entry_idx != -1:
                selected_states[entry_idx] = True
        confirmed = True
        event.app.exit()

    @bindings.add(Keys.Escape)
    def cancel(event):
        event.app.exit()

    def create_layout():
        windows = []

        # Header line: "  Item" padded
        header_text = f"  {'Item'.ljust(_COL_NAME)}"
        windows.append(Window(
            FormattedTextControl(lambda h=header_text: h),
            height=1,
            style="class:header",
        ))

        # Separator
        windows.append(Window(
            FormattedTextControl(lambda: _make_separator()),
            height=1,
            style="class:separator",
        ))

        # Rows
        for row_idx, (entry_idx, header_label) in enumerate(display_rows):
            if entry_idx == -1:
                # Group header
                label = header_label
                windows.append(Window(
                    FormattedTextControl(
                        lambda l=label: f"{l}"
                    ),
                    height=1,
                    style="class:group-header",
                ))
            else:
                entry = entries[entry_idx]
                is_focused = row_idx == current_focus
                is_selected = selected_states[entry_idx]

                dot = "\u25cf" if is_selected else "\u25cb"
                has_groups = any(e.group for e in entries)
                indent = "  " if has_groups else ""
                pointer = "\u2192 " if is_focused else "  "
                name = entry.label

                row_style = "class:focused-row" if is_focused else ""

                windows.append(Window(
                    FormattedTextControl(
                        lambda p=pointer, i=indent, d=dot, n=name:
                            f"{p}{i}{d} {n}"
                    ),
                    height=1,
                    style=row_style,
                ))

        # Spacer
        windows.append(Window(height=1))

        # Status bar
        selected_count = sum(selected_states)
        total = len(entries)
        if multi_select:
            hint = f"{selected_count}/{total} selected | \u2191\u2193 Nav  Space Toggle  Enter OK  Esc Cancel"
        else:
            hint = f"\u2191\u2193 Nav  Space/Enter Select  Esc Cancel"
        status_text = f"  {title}  |  {hint}" if multi_select else f"  {title}  |  {hint}"

        windows.append(Window(
            FormattedTextControl(lambda s=status_text: s),
            height=1,
            style="class:status",
        ))

        return Layout(HSplit(windows))

    style = Style.from_dict({
        "header": "bold",
        "separator": "",
        "group-header": "bold",
        "focused-row": "bold",
        "status": "reverse",
    })

    app = Application(
        layout=create_layout(),
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
        style=style,
    )

    await app.run_async()

    if confirmed:
        selected_ids = [entries[i].id for i, s in enumerate(selected_states) if s]
        return SelectionResult(selected=selected_ids)
    else:
        return SelectionResult(cancelled=True)
