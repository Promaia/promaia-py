"""
Inline-tree picker for the agent's external tools and MCP servers.

This is the screen the user sees when they pick "External tools and MCP"
from `maia agent edit`. One screen, hierarchical rows; L/R expands/
collapses each tool, SPACE toggles cells, TAB switches between R and W
columns, ENTER saves, ESC cancels.

Phase 2b ships the renderer + keybindings against in-memory state.
Phase 2c plugs in the real fetch callbacks (Slack channels, MCP tools,
configured Notion DBs from the database manager). Phase 3 routes the
returned result dict back into AgentConfig fields.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from promaia.cli.external_tools_picker_state import (
    ChildNode,
    ToolNode,
    build_initial_tree,
    collect_picker_result,
)


# Type alias for a child-fetch callback. Phase 2c provides real implementations.
# Stubs in this file return empty lists so the picker renders cleanly when
# the tool has no children configured yet.
FetchCallback = Callable[[ToolNode], Awaitable[List[ChildNode]]]


# ---------------------------------------------------------------------------
# Stub fetch callbacks (replaced in Phase 2c with live data)
# ---------------------------------------------------------------------------


async def _stub_fetch(node: ToolNode) -> List[ChildNode]:
    """Default fetch — returns a placeholder so children show *something* in 2b.

    Phase 2c overrides this with real implementations that hit the
    database manager / Slack API / MCP cache. The picker doesn't care
    where the data comes from — it just renders whatever ChildNodes
    the callback returns.
    """
    return []


# ---------------------------------------------------------------------------
# Display row — the flattened tree as it appears on screen
# ---------------------------------------------------------------------------


class DisplayRow:
    """One visible line in the picker. May be a tool row or a child row."""

    __slots__ = ("kind", "tool_idx", "child_idx")

    def __init__(self, kind: str, tool_idx: int, child_idx: Optional[int] = None):
        # kind: "tool" | "child" | "section_header"
        self.kind = kind
        self.tool_idx = tool_idx
        self.child_idx = child_idx


# ---------------------------------------------------------------------------
# Picker entry point
# ---------------------------------------------------------------------------


async def select_external_tools(
    agent: Any,  # AgentConfig
    workspace: str,
    mcp_server_names: Optional[List[str]] = None,
    fetch_children: Optional[FetchCallback] = None,
) -> Optional[Dict[str, Any]]:
    """Show the inline tree picker. Returns the result dict on save, or
    ``None`` if the user cancels (ESC).

    *fetch_children* is called the first time a tool row is expanded.
    Phase 2c plugs in real fetchers; default is the stub.
    """
    if fetch_children is None:
        fetch_children = _stub_fetch

    nodes = build_initial_tree(agent, mcp_server_names=mcp_server_names or [])
    if not nodes:
        return None

    state = _PickerState(nodes=nodes, fetch_children=fetch_children)
    confirmed = await _run_picker_app(state)
    if not confirmed:
        return None
    return collect_picker_result(state.nodes)


# ---------------------------------------------------------------------------
# Internal state owned by the picker app
# ---------------------------------------------------------------------------


class _PickerState:
    def __init__(self, nodes: List[ToolNode], fetch_children: FetchCallback):
        self.nodes = nodes
        self.fetch_children = fetch_children
        self.cur_row = 0  # index into self.display_rows
        self.cur_col = 1  # 0 = enabled checkbox, 1 = R column, 2 = W column
        self.display_rows: List[DisplayRow] = []
        self._rebuild_display_rows()
        # Land the cursor on the first selectable row (skip section headers).
        for i, row in enumerate(self.display_rows):
            if row.kind in ("tool", "child"):
                self.cur_row = i
                break

    # ------------------------------------------------------------------
    # Display row computation
    # ------------------------------------------------------------------

    def _rebuild_display_rows(self) -> None:
        """Flatten the tree into the visible row list, respecting expand state."""
        rows: List[DisplayRow] = []
        # Section header for built-ins
        rows.append(DisplayRow("section_header", -1))  # "Built-in external tools"
        builtin_count = sum(1 for n in self.nodes if n.shape != "mcp_server")
        for i, node in enumerate(self.nodes):
            # Insert "User-added MCP servers" header right before the first MCP row
            if i == builtin_count:
                rows.append(DisplayRow("section_header", -2))  # "User-added MCP servers"
            rows.append(DisplayRow("tool", i))
            if node.expanded and node.children:
                for ci, _child in enumerate(node.children):
                    rows.append(DisplayRow("child", i, ci))
        self.display_rows = rows
        self.cur_row = max(0, min(self.cur_row, len(rows) - 1))

    # ------------------------------------------------------------------
    # State accessors used by the renderer
    # ------------------------------------------------------------------

    def current_tool(self) -> Optional[ToolNode]:
        if 0 <= self.cur_row < len(self.display_rows):
            row = self.display_rows[self.cur_row]
            if row.kind in ("tool", "child"):
                return self.nodes[row.tool_idx]
        return None

    def current_child(self) -> Optional[ChildNode]:
        if 0 <= self.cur_row < len(self.display_rows):
            row = self.display_rows[self.cur_row]
            if row.kind == "child" and row.child_idx is not None:
                return self.nodes[row.tool_idx].children[row.child_idx]
        return None

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def toggle_expand(self) -> None:
        tool = self.current_tool()
        if tool is None or self.display_rows[self.cur_row].kind != "tool":
            return
        # Single-row tools (gmail / calendar) don't expand
        if tool.shape == "single_row":
            return
        if tool.expanded:
            tool.expanded = False
        else:
            # Lazy-load children on first expand
            if not tool.children_loaded:
                tool.children = await self.fetch_children(tool)
                tool.children_loaded = True
            tool.expanded = True
        self._rebuild_display_rows()

    def toggle_cell(self) -> None:
        """Toggle the cell at (cur_row, cur_col)."""
        if 0 <= self.cur_row < len(self.display_rows):
            row = self.display_rows[self.cur_row]
            if row.kind == "tool":
                tool = self.nodes[row.tool_idx]
                if self.cur_col == 0:
                    tool.enabled = not tool.enabled
                elif self.cur_col == 1 and tool.shape == "single_row":
                    tool.r_state = not tool.r_state
                elif self.cur_col == 2 and tool.shape == "single_row" and tool.has_w_column:
                    tool.w_state = not tool.w_state
            elif row.kind == "child":
                child = self.nodes[row.tool_idx].children[row.child_idx]
                if self.cur_col == 0:
                    child.enabled = not child.enabled
                elif self.cur_col == 1:
                    child.r_state = not child.r_state
                elif self.cur_col == 2 and child.has_w_column:
                    child.w_state = not child.w_state


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


# Column positions (character offsets from line start)
_INDENT_TOOL = ""
_INDENT_CHILD = "    "
_LABEL_WIDTH = 38
_COL_ENABLED = 0
_COL_R = 1
_COL_W = 2


def _render_state(state: _PickerState) -> str:
    """Render the entire picker into a single string for FormattedTextControl."""
    lines = []
    lines.append("External tools and MCP")
    lines.append("─" * 60 + "  R     W")
    lines.append("")

    for row_idx, row in enumerate(state.display_rows):
        is_focused = row_idx == state.cur_row

        if row.kind == "section_header":
            label = "Built-in external tools" if row.tool_idx == -1 else "User-added MCP servers"
            lines.append("")
            lines.append(label)
            continue

        if row.kind == "tool":
            tool = state.nodes[row.tool_idx]
            check = _checkbox(tool.enabled)
            arrow = _expand_arrow(tool)
            label = f"{check} {arrow} {tool.label}".ljust(_LABEL_WIDTH)
            r_box = _rw_box(tool.r_state, show=tool.shape == "single_row")
            w_box = _rw_box(tool.w_state, show=tool.shape == "single_row" and tool.has_w_column)
            line = f"{_INDENT_TOOL}{label}{r_box}   {w_box}"
            if is_focused:
                line = _focus_marker(line, state.cur_col)
            lines.append(line)
            continue

        if row.kind == "child":
            child = state.nodes[row.tool_idx].children[row.child_idx]
            check = _checkbox(child.enabled)
            label = f"{check} {child.label}".ljust(_LABEL_WIDTH)
            r_box = _rw_box(child.r_state, show=True)
            w_box = _rw_box(child.w_state, show=child.has_w_column)
            line = f"{_INDENT_CHILD}{label}{r_box}   {w_box}"
            if is_focused:
                line = _focus_marker(line, state.cur_col)
            lines.append(line)
            continue

    lines.append("")
    lines.append("─" * 70)
    lines.append(
        " ↑↓ row    L/R expand/collapse    SPACE toggle    TAB column    ENTER save    ESC cancel"
    )
    return "\n".join(lines)


def _checkbox(on: bool) -> str:
    return "[x]" if on else "[ ]"


def _rw_box(on: bool, show: bool = True) -> str:
    if not show:
        return "   "
    return "[x]" if on else "[ ]"


def _expand_arrow(tool: ToolNode) -> str:
    if tool.shape == "single_row":
        return " "
    return "▼" if tool.expanded else "▶"


def _focus_marker(line: str, col: int) -> str:
    """Mark the focused row + column visually. Simple v1: a leading '> '."""
    return "> " + line[2:] if line.startswith("  ") else "> " + line


# ---------------------------------------------------------------------------
# prompt_toolkit Application
# ---------------------------------------------------------------------------


async def _run_picker_app(state: _PickerState) -> bool:
    """Run the prompt_toolkit Application. Returns True on save, False on cancel."""
    confirmed = [False]

    bindings = KeyBindings()

    def _move(delta: int) -> None:
        i = state.cur_row + delta
        while 0 <= i < len(state.display_rows):
            if state.display_rows[i].kind in ("tool", "child"):
                state.cur_row = i
                return
            i += delta

    @bindings.add(Keys.Up)
    def _up(event):
        _move(-1)
        event.app.invalidate()

    @bindings.add(Keys.Down)
    def _down(event):
        _move(1)
        event.app.invalidate()

    @bindings.add(Keys.Right)
    def _right(event):
        # Expand current row if it's a collapsed tool with a sub-list shape
        async def _expand():
            await state.toggle_expand()
            event.app.invalidate()
        asyncio.ensure_future(_expand())

    @bindings.add(Keys.Left)
    def _left(event):
        # Collapse current row
        async def _collapse():
            await state.toggle_expand()
            event.app.invalidate()
        asyncio.ensure_future(_collapse())

    @bindings.add(Keys.Tab)
    def _tab(event):
        # Cycle through columns: enabled (0) → R (1) → W (2) → 0
        state.cur_col = (state.cur_col + 1) % 3
        event.app.invalidate()

    @bindings.add(Keys.BackTab)
    def _shift_tab(event):
        state.cur_col = (state.cur_col - 1) % 3
        event.app.invalidate()

    @bindings.add(" ")
    def _space(event):
        state.toggle_cell()
        event.app.invalidate()

    @bindings.add(Keys.Enter)
    def _save(event):
        confirmed[0] = True
        event.app.exit()

    @bindings.add(Keys.Escape, eager=True)
    def _cancel(event):
        event.app.exit()

    text_control = FormattedTextControl(text=lambda: _render_state(state))
    layout = Layout(HSplit([Window(content=text_control)]))

    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=False,
        mouse_support=False,
    )
    await app.run_async()
    return confirmed[0]
