"""Reusable TUI widgets for maia setup.

- UnifiedSourceSelector: browse + paste link + load more, all in one screen
- SetupProgress: persistent footer showing setup step progress
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from rich.console import Console


# ── Unified Source Selector ──────────────────────────────────────────────


async def unified_source_selector(
    title: str,
    items: List[Dict[str, Any]],
    load_more_callback: Optional[Callable] = None,
    load_more_label: str = "Load more...",
    paste_link_callback: Optional[Callable] = None,
    max_visible: int = 20,
) -> List[Dict[str, Any]]:
    """Interactive source selector with browse + paste link modes.

    Args:
        title: Header text (e.g., "Notion — Select Sources")
        items: List of dicts with keys:
            - id: unique identifier
            - label: display text
            - group: group name (empty string for ungrouped)
            - icon: optional prefix icon (e.g., "📊", "📁")
            - meta: optional right-aligned metadata text
            - selected: optional bool, pre-selected state
            - is_folder: optional bool, for Drive cd-into behavior
        load_more_callback: async fn() -> List[Dict] that returns new items to append.
            Called when user selects the "load more" row. Set to None to hide.
        load_more_label: Text for the load more row
        paste_link_callback: async fn(url: str) -> Optional[Dict] that resolves a
            pasted URL into an item dict. Returns None if invalid. Set to None to
            disable paste mode.
        max_visible: max rows visible in viewport

    Returns:
        List of selected item dicts (with original keys intact).
    """

    # State
    selected = [item.get("selected", False) for item in items]
    all_items = list(items)  # mutable copy
    mode = "browse"  # "browse" or "paste"
    confirmed = False
    current = [0]
    paste_input = [""]
    paste_status = [""]  # feedback message after paste

    # Groups
    def _build_groups():
        flat = [i for i, it in enumerate(all_items) if it.get("group", "") == ""]
        grps = []
        grp_map = {}
        for i, it in enumerate(all_items):
            g = it.get("group", "")
            if g == "":
                continue
            if g not in grp_map:
                grp_map[g] = len(grps)
                grps.append((g, []))
            grps[grp_map[g]][1].append(i)
        return flat, grps, grp_map

    flat_indices, groups, group_map = _build_groups()
    expanded = {g: False for g, _ in groups}

    def _rebuild_groups():
        nonlocal flat_indices, groups, group_map, expanded
        flat_indices, groups, group_map = _build_groups()
        for g, _ in groups:
            if g not in expanded:
                expanded[g] = False

    def _build_nav():
        nav = []
        for idx in flat_indices:
            nav.append(("item", idx))
        if groups and flat_indices:
            nav.append(("sep", None))
        for gname, idxs in groups:
            nav.append(("group", gname))
            if expanded.get(gname, False):
                for idx in idxs:
                    nav.append(("item", idx))
        if load_more_callback is not None:
            if nav:
                nav.append(("sep", None))
            nav.append(("load_more", None))
        return nav

    nav_items = _build_nav()

    def _get_browse_text():
        items_list = nav_items
        total = len(items_list)
        cur = current[0]

        half = max_visible // 2
        if total <= max_visible:
            start = 0
        elif cur < half:
            start = 0
        elif cur >= total - half:
            start = max(0, total - max_visible)
        else:
            start = cur - half
        end = min(start + max_visible, total)

        lines = []
        if start > 0:
            lines.append("  ... more above")

        for i in range(start, end):
            itype, value = items_list[i]
            is_cur = (i == cur)
            arrow = " >" if is_cur else "  "

            if itype == "sep":
                lines.append("")
            elif itype == "group":
                icon = "v" if expanded.get(value, False) else ">"
                idxs = groups[group_map[value]][1]
                count = len(idxs)
                sel_count = sum(1 for idx in idxs if selected[idx])
                sel_info = f" ({sel_count}/{count})" if sel_count > 0 else f" ({count})"
                lines.append(f" {arrow} {icon} {value}{sel_info}")
            elif itype == "load_more":
                lines.append(f" {arrow} ── {load_more_label} ──")
            else:
                idx = value
                it = all_items[idx]
                check = "[x]" if selected[idx] else "[ ]"
                icon = it.get("icon", "")
                icon_str = f"{icon} " if icon else ""
                meta = it.get("meta", "")
                meta_str = f"  {meta}" if meta else ""
                lines.append(f" {arrow} {check} {icon_str}{it['label']}{meta_str}")

        if end < total:
            lines.append("  ... more below")

        return "\n".join(lines)

    def _get_paste_instructions():
        lines = ["  Paste a link (or press Tab to go back to browse):", ""]
        if paste_status[0]:
            lines.append(f"  {paste_status[0]}")
            lines.append("")
        return "\n".join(lines)

    # Buffer for paste input — gives us a real cursor
    paste_buffer = Buffer(name="paste_input")

    def _get_viewport_text():
        if mode == "browse":
            return _get_browse_text()
        else:
            return _get_paste_instructions()

    def _get_header():
        if paste_link_callback:
            browse_hl = "[Browse]" if mode == "browse" else " Browse "
            paste_hl = "[Paste Link]" if mode == "paste" else " Paste Link "
            return f"  {title}    {browse_hl}  {paste_hl}    Tab to switch"
        return f"  {title}"

    def _get_status():
        sel_count = sum(selected)
        if mode == "browse":
            return f" SPACE select  >/< expand  ENTER confirm ({sel_count} selected)  ESC cancel"
        else:
            return f" Type/paste URL, ENTER to add  TAB back to browse  ESC cancel"

    # Persistent input window for paste mode (needs to exist across layouts for focus)
    paste_input_window = Window(FormattedTextControl(text=_get_paste_input), height=1)

    def _make_layout():
        header = Window(FormattedTextControl(text=_get_header), height=2, style="bold")
        status = Window(FormattedTextControl(text=_get_status), height=1, style="fg:gray")
        if mode == "paste":
            # Paste mode: compact instructions + real text input with cursor
            instructions = Window(FormattedTextControl(text=_get_paste_instructions), height=4)
            input_line = Window(BufferControl(buffer=paste_buffer), height=1)
            return Layout(HSplit([header, instructions, input_line, status]), focused_element=input_line)
        else:
            visible = min(len(nav_items) + 2, max_visible) + 3
            viewport = Window(FormattedTextControl(text=_get_browse_text), height=visible)
            return Layout(HSplit([header, viewport, status]), focused_element=viewport)

    kb = KeyBindings()

    def _skip_seps(direction):
        while 0 <= current[0] < len(nav_items) and nav_items[current[0]][0] == "sep":
            current[0] += direction

    @kb.add(Keys.Up)
    def _up(event):
        if mode != "browse":
            return
        if current[0] > 0:
            current[0] -= 1
            _skip_seps(-1)
            event.app.layout = _make_layout()

    @kb.add(Keys.Down)
    def _down(event):
        if mode != "browse":
            return
        if current[0] < len(nav_items) - 1:
            current[0] += 1
            _skip_seps(1)
            event.app.layout = _make_layout()

    @kb.add(Keys.Right)
    def _expand(event):
        nonlocal nav_items
        if mode != "browse" or current[0] >= len(nav_items):
            return
        itype, value = nav_items[current[0]]
        if itype == "group":
            expanded[value] = not expanded[value]
            nav_items = _build_nav()
            for i, (t, v) in enumerate(nav_items):
                if t == "group" and v == value:
                    current[0] = i
                    break
            event.app.layout = _make_layout()

    @kb.add(Keys.Left)
    def _collapse(event):
        nonlocal nav_items
        if mode != "browse" or current[0] >= len(nav_items):
            return
        itype, value = nav_items[current[0]]
        if itype == "group" and expanded.get(value, False):
            expanded[value] = False
            nav_items = _build_nav()
            event.app.layout = _make_layout()
        elif itype == "item":
            grp = all_items[value].get("group", "")
            if grp and grp in expanded:
                expanded[grp] = False
                nav_items = _build_nav()
                for i, (t, v) in enumerate(nav_items):
                    if t == "group" and v == grp:
                        current[0] = i
                        break
                event.app.layout = _make_layout()

    @kb.add(" ")
    def _toggle(event):
        nonlocal nav_items, selected, load_more_callback
        if mode == "paste":
            return  # Buffer handles typing natively
        if mode != "browse" or current[0] >= len(nav_items):
            return
        itype, value = nav_items[current[0]]
        if itype == "item":
            selected[value] = not selected[value]
        elif itype == "group":
            idxs = groups[group_map[value]][1]
            all_sel = all(selected[i] for i in idxs)
            for i in idxs:
                selected[i] = not all_sel
            if not all_sel and not expanded.get(value, False):
                expanded[value] = True
                nav_items = _build_nav()
                for i, (t, v) in enumerate(nav_items):
                    if t == "group" and v == value:
                        current[0] = i
                        break
        elif itype == "load_more" and load_more_callback is not None:
            # Run load_more synchronously in the app context
            # We'll handle this via a sentinel and run after app exits
            event.app.exit(result="__LOAD_MORE__")
            return
        event.app.layout = _make_layout()

    @kb.add(Keys.Tab)
    def _switch_mode(event):
        nonlocal mode
        if paste_link_callback is None:
            return
        mode = "paste" if mode == "browse" else "browse"
        paste_buffer.reset()
        paste_status[0] = ""
        event.app.layout = _make_layout()

    @kb.add(Keys.Enter)
    def _enter(event):
        nonlocal confirmed
        if mode == "browse":
            confirmed = True
            event.app.exit()
        else:
            # Paste mode: read from buffer and submit
            text = paste_buffer.text.strip()
            if text:
                paste_input[0] = text  # sync for the handler below
                event.app.exit(result="__PASTE__")

    @kb.add(Keys.Escape)
    def _cancel(event):
        event.app.exit()

    # In paste mode, the Buffer handles typing, backspace, and clipboard natively.
    # No manual character handling needed.

    # Main loop — handles load_more and paste re-entry
    while True:
        app = Application(
            layout=_make_layout(),
            key_bindings=kb,
            full_screen=False,
            mouse_support=False,
        )
        result = await app.run_async()

        if result == "__LOAD_MORE__" and load_more_callback is not None:
            new_items = await load_more_callback()
            if new_items:
                start_idx = len(all_items)
                all_items.extend(new_items)
                selected.extend([False] * len(new_items))
                _rebuild_groups()
                nav_items = _build_nav()
                # Position cursor at first new item
                for i, (t, v) in enumerate(nav_items):
                    if t == "item" and v >= start_idx:
                        current[0] = i
                        break
            else:
                # No more to load — remove load more option
                load_more_callback = None
                nav_items = _build_nav()
            continue

        elif result == "__PASTE__" and paste_link_callback is not None:
            url = paste_input[0].strip()
            paste_input[0] = ""
            paste_buffer.reset()
            resolved = await paste_link_callback(url)
            if resolved:
                # Add to items as pre-selected
                resolved["selected"] = True
                all_items.append(resolved)
                selected.append(True)
                _rebuild_groups()
                nav_items = _build_nav()
                paste_status[0] = f"  Added: {resolved.get('label', url)}"
            else:
                paste_status[0] = "  Could not resolve that link."
            # Stay in paste mode for another link
            continue

        else:
            # Confirmed or cancelled
            break

    if confirmed:
        return [all_items[i] for i in range(len(all_items)) if selected[i]]
    return []


# ── Setup Progress Footer ────────────────────────────────────────────────


class SetupProgress:
    """Tracks and renders setup progress as a footer line."""

    FULL_STEPS = ["Workspace", "AI", "Notion", "Google", "Slack", "Sync", "Agent"]
    # Compact labels for narrow terminals
    COMPACT_LABELS = {"Workspace": "WS", "Notion": "Not", "Google": "Goo", "Slack": "Slk", "Agent": "Agt"}

    def __init__(self, steps: Optional[List[str]] = None, console: Optional[Console] = None):
        self.steps = steps or self.FULL_STEPS
        self.console = console or Console()
        self.states: Dict[str, str] = {s: "pending" for s in self.steps}
        self.current_idx = 0
        self.descriptions: Dict[str, str] = {}
        if self.steps:
            self.states[self.steps[0]] = "current"

    def set_description(self, step: str, desc: str):
        """Set a one-liner description for a step."""
        self.descriptions[step] = desc

    def advance(self):
        """Mark current step done, move to next."""
        if self.current_idx < len(self.steps):
            self.states[self.steps[self.current_idx]] = "done"
        self.current_idx += 1
        if self.current_idx < len(self.steps):
            self.states[self.steps[self.current_idx]] = "current"

    def skip(self):
        """Mark current step skipped, move to next."""
        if self.current_idx < len(self.steps):
            self.states[self.steps[self.current_idx]] = "skipped"
        self.current_idx += 1
        if self.current_idx < len(self.steps):
            self.states[self.steps[self.current_idx]] = "current"

    def render(self):
        """Print the progress footer."""
        icons = {
            "done": "[green]✓[/green]",
            "current": "[bold cyan]●[/bold cyan]",
            "pending": "[dim]○[/dim]",
            "skipped": "[dim]–[/dim]",
        }
        # Use compact labels if terminal is narrow
        try:
            term_width = self.console.width or 80
        except Exception:
            term_width = 80
        use_compact = term_width < 75

        parts = []
        for step in self.steps:
            icon = icons.get(self.states[step], "○")
            label = self.COMPACT_LABELS.get(step, step) if use_compact else step
            state = self.states[step]
            if state == "current":
                parts.append(f"[bold]{label}[/bold] {icon}")
            elif state in ("pending", "skipped"):
                parts.append(f"[dim]{label}[/dim] {icon}")
            else:
                parts.append(f"{label} {icon}")

        sep = "─" if use_compact else "──"
        line = f" {sep} ".join(parts)
        self.console.print()
        self.console.print(f"  [dim]{'─' * min(60, term_width - 4)}[/dim]")
        self.console.print(f"  {line}")

        # Show current step description
        if self.current_idx < len(self.steps):
            current_step = self.steps[self.current_idx]
            desc = self.descriptions.get(current_step, "")
            if desc:
                self.console.print(f"  [dim]{desc}[/dim]")
        self.console.print()
