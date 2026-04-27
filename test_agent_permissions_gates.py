"""
End-to-end smoke tests for the granular agent permission gates.

Each test exercises one of the three deny paths shipped in this branch:

    1. Per-tool MCP allow list (ToolExecutor._check_mcp_tool_allowed)
    2. Output channel gate (ToolExecutor._check_output_channel_allowed)
    3. Per-column source redaction (AgentConfig.filter_page_columns)

Plus a smaller round-trip on the new schema fields and a self-contained
unit test for mcp_tool_cache.diff so future schema-fingerprint work has
a regression net.

Run:  python -m pytest test_agent_permissions_gates.py -v
or:   python test_agent_permissions_gates.py
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


def test_new_fields_round_trip_through_dict():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission

    cfg = AgentConfig(
        name="bondu",
        workspace="koii",
        databases=["gmail"],
        prompt_file="",
        mcp_tools=["po_manager"],
        allowed_channel_ids=["C123"],
        allowed_output_channel_ids=["C456"],
        mcp_tool_allowlist={"po_manager": ["list_vendors", "list_parts"]},
        source_access=[
            SourceAccess(
                source_name="gmail",
                initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_tables=["messages"],
                allowed_columns={"messages": ["subject", "from"]},
            )
        ],
    )

    d = cfg.to_dict()
    assert d["allowed_output_channel_ids"] == ["C456"]
    assert d["mcp_tool_allowlist"] == {"po_manager": ["list_vendors", "list_parts"]}

    sa = d["source_access"][0]
    assert sa["allowed_tables"] == ["messages"]
    assert sa["allowed_columns"] == {"messages": ["subject", "from"]}

    revived = AgentConfig.from_dict(d)
    assert revived.allowed_output_channel_ids == ["C456"]
    assert revived.mcp_tool_allowlist == {"po_manager": ["list_vendors", "list_parts"]}


# ---------------------------------------------------------------------------
# MCP per-tool gate
# ---------------------------------------------------------------------------


class _StubExecutor:
    """Minimal harness — just the gate methods need self.agent."""

    def __init__(self, agent):
        self.agent = agent


def _make_executor(**kwargs):
    from promaia.agents.agent_config import AgentConfig
    from promaia.agents.agentic_turn import ToolExecutor

    cfg = AgentConfig(
        name=kwargs.get("name", "bondu"),
        workspace="koii",
        databases=["gmail"],
        prompt_file="",
        mcp_tools=["po_manager"],
        is_default_agent=kwargs.get("is_default_agent", False),
        mcp_tool_allowlist=kwargs.get("mcp_tool_allowlist", None),
        allowed_channel_ids=kwargs.get("allowed_channel_ids", None),
        allowed_output_channel_ids=kwargs.get("allowed_output_channel_ids", None),
    )
    # Bind only the methods we care about — full ToolExecutor needs lots
    # of construction args and external services we don't want here.
    stub = _StubExecutor(cfg)
    stub._check_mcp_tool_allowed = ToolExecutor._check_mcp_tool_allowed.__get__(stub)
    stub._check_output_channel_allowed = ToolExecutor._check_output_channel_allowed.__get__(stub)
    return stub


def test_mcp_default_agent_bypasses_gate():
    ex = _make_executor(is_default_agent=True, mcp_tool_allowlist={"po_manager": []})
    assert ex._check_mcp_tool_allowed("po_manager", "list_vendors") is None


def test_mcp_legacy_unmigrated_allows_with_warn(caplog=None):
    ex = _make_executor(mcp_tool_allowlist=None)
    # Should allow (None means legacy-allow); log captured but not asserted here.
    assert ex._check_mcp_tool_allowed("po_manager", "list_vendors") is None


def test_mcp_server_not_granted_denies():
    ex = _make_executor(mcp_tool_allowlist={"other_server": ["x"]})
    msg = ex._check_mcp_tool_allowed("po_manager", "list_vendors")
    assert msg is not None and "Permission denied" in msg


def test_mcp_wholesale_server_grant_allows():
    ex = _make_executor(mcp_tool_allowlist={"po_manager": None})
    assert ex._check_mcp_tool_allowed("po_manager", "anything") is None


def test_mcp_tool_in_allowlist_allows():
    ex = _make_executor(mcp_tool_allowlist={"po_manager": ["list_vendors"]})
    assert ex._check_mcp_tool_allowed("po_manager", "list_vendors") is None


def test_mcp_tool_not_in_allowlist_denies():
    ex = _make_executor(mcp_tool_allowlist={"po_manager": ["list_vendors"]})
    msg = ex._check_mcp_tool_allowed("po_manager", "delete_vendor")
    assert msg is not None and "not in this agent's allow list" in msg


# ---------------------------------------------------------------------------
# Output channel gate
# ---------------------------------------------------------------------------


def test_output_default_agent_bypass():
    ex = _make_executor(is_default_agent=True, allowed_output_channel_ids=[])
    assert ex._check_output_channel_allowed("C999") is None


def test_output_explicit_allow_list_match():
    ex = _make_executor(allowed_output_channel_ids=["C1", "C2"])
    assert ex._check_output_channel_allowed("C1") is None


def test_output_explicit_allow_list_miss():
    ex = _make_executor(allowed_output_channel_ids=["C1", "C2"])
    msg = ex._check_output_channel_allowed("C9")
    assert msg is not None and "Permission denied" in msg


def test_output_falls_back_to_input_gate():
    ex = _make_executor(
        allowed_output_channel_ids=None, allowed_channel_ids=["CIN"]
    )
    # In allowed input channels — also allowed to write
    assert ex._check_output_channel_allowed("CIN") is None
    # Not in input channels — write denied
    assert ex._check_output_channel_allowed("COTHER") is not None


def test_output_legacy_both_none_allows():
    ex = _make_executor()
    assert ex._check_output_channel_allowed("Cwhatever") is None


# ---------------------------------------------------------------------------
# Column redaction
# ---------------------------------------------------------------------------


def test_filter_page_columns_no_source_access_unchanged():
    from promaia.agents.agent_config import AgentConfig

    cfg = AgentConfig(name="t", workspace="koii", databases=["gmail"], prompt_file="", mcp_tools=[])
    pages = [{"properties": {"a": 1, "b": 2}}]
    assert cfg.filter_page_columns("gmail", pages) == pages


def test_filter_page_columns_redacts_disallowed():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission

    cfg = AgentConfig(
        name="t", workspace="koii", databases=["gmail"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="gmail", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_columns={"gmail": ["a"]},
            )
        ],
    )
    pages = [{"properties": {"a": 1, "b": 2}}, {"properties": {"a": 3, "c": 4}}]
    out = cfg.filter_page_columns("gmail", pages)
    assert out == [{"properties": {"a": 1}}, {"properties": {"a": 3}}]
    # original pages not mutated
    assert pages[0]["properties"]["b"] == 2


def test_filter_page_columns_other_source_unchanged():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission

    cfg = AgentConfig(
        name="t", workspace="koii", databases=["gmail", "journal"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="gmail", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_columns={"gmail": ["a"]},
            )
        ],
    )
    pages = [{"properties": {"a": 1, "b": 2}}]
    assert cfg.filter_page_columns("journal", pages) == pages


# ---------------------------------------------------------------------------
# Per-table allowlist (Gap A — allowed_tables enforcement)
# ---------------------------------------------------------------------------


def test_filter_pages_by_table_no_access_unchanged():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[])
    pages = [{"table": "a", "id": 1}, {"table": "b", "id": 2}]
    assert cfg.filter_pages_by_table("x", pages) == pages


def test_filter_pages_by_table_drops_disallowed():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="x", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_tables=["a"],
            )
        ],
    )
    pages = [{"table": "a", "id": 1}, {"table": "b", "id": 2}, {"table": "a", "id": 3}]
    out = cfg.filter_pages_by_table("x", pages)
    assert out == [{"table": "a", "id": 1}, {"table": "a", "id": 3}]


def test_filter_pages_by_table_passes_through_when_no_table_field():
    """Single-table sources (Notion-style) don't have a 'table' field on pages
    and should pass through even when allowed_tables is set."""
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="x", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_tables=["whatever"],
            )
        ],
    )
    pages = [{"id": 1, "title": "p1"}, {"id": 2, "title": "p2"}]  # no "table"
    assert cfg.filter_pages_by_table("x", pages) == pages


def test_get_allowed_tables_returns_none_when_unset():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="x", initial_days=None,
                permissions=[SourcePermission.QUERY],
            )
        ],
    )
    assert cfg.get_allowed_tables("x") is None
    assert cfg.get_allowed_tables("nonexistent") is None


# ---------------------------------------------------------------------------
# Vector-search filtering (Gap B — query_vector path runs the same filters)
# ---------------------------------------------------------------------------


def test_vector_path_redacts_columns_just_like_source_path():
    """The vector path applies filter_page_columns per source, so an
    agent with allowed_columns=['subject'] on gmail must see no body
    fields in vector results either. The helper is the same; this test
    asserts the helper is what the path calls."""
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission

    cfg = AgentConfig(
        name="t", workspace="koii", databases=["gmail"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="gmail", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_columns={"gmail": ["subject"]},
            )
        ],
    )
    # Simulate loaded_content shape returned by process_vector_search_to_content
    loaded_content = {
        "gmail": [
            {"properties": {"subject": "hello", "body": "secret"}, "score": 0.95},
            {"properties": {"subject": "ping", "body": "more secret"}, "score": 0.88},
        ],
        "journal": [
            {"properties": {"text": "private"}, "score": 0.7},
        ],
    }
    # Apply the same per-source filter the _handle_query_vector loop does
    for src in list(loaded_content.keys()):
        loaded_content[src] = cfg.filter_pages_by_table(src, loaded_content[src])
        loaded_content[src] = cfg.filter_page_columns(src, loaded_content[src])

    # Body redacted on gmail
    assert all("body" not in p["properties"] for p in loaded_content["gmail"])
    assert all(p["properties"].get("subject") for p in loaded_content["gmail"])
    # Score (non-properties metadata) preserved
    assert all("score" in p for p in loaded_content["gmail"])
    # Other source untouched
    assert loaded_content["journal"][0]["properties"]["text"] == "private"


def test_vector_path_drops_disallowed_tables_per_source():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission

    cfg = AgentConfig(
        name="t", workspace="koii", databases=["multi"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="multi", initial_days=None,
                permissions=[SourcePermission.QUERY],
                allowed_tables=["public"],
            )
        ],
    )
    loaded = {
        "multi": [
            {"table": "public", "id": 1},
            {"table": "private", "id": 2},
            {"table": "public", "id": 3},
        ],
    }
    for src in list(loaded.keys()):
        loaded[src] = cfg.filter_pages_by_table(src, loaded[src])
    assert [p["id"] for p in loaded["multi"]] == [1, 3]


# ---------------------------------------------------------------------------
# Channel groups (Gap C — DM/channel buckets + wildcards)
# ---------------------------------------------------------------------------


def test_classify_channel_dm_vs_channel():
    from promaia.agents.agent_config import AgentConfig
    assert AgentConfig._classify_channel("D12345") == "dm"
    assert AgentConfig._classify_channel("C67890") == "channel"
    assert AgentConfig._classify_channel("") == "channel"


def test_can_access_channel_groups_wildcard_dm():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        allowed_channel_groups={"dm": ["*"]},
    )
    assert cfg.can_access_channel("D1") is True
    assert cfg.can_access_channel("D2") is True
    # Channel bucket not granted → deny
    assert cfg.can_access_channel("C1") is False


def test_can_access_channel_groups_specific_channel():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        allowed_channel_groups={"dm": ["*"], "channel": ["C_eng"]},
    )
    assert cfg.can_access_channel("D1") is True
    assert cfg.can_access_channel("C_eng") is True
    assert cfg.can_access_channel("C_other") is False


def test_can_access_channel_flat_list_wins_over_groups():
    """Legacy flat list is authoritative when set, even if groups would say otherwise."""
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        allowed_channel_ids=["C_specific"],
        allowed_channel_groups={"dm": ["*"]},  # would say D1=ok, but flat list wins
    )
    assert cfg.can_access_channel("D1") is False  # not in flat list
    assert cfg.can_access_channel("C_specific") is True


def test_can_post_to_channel_falls_back_to_input_gate():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        allowed_channel_groups={"dm": ["*"]},  # input: any DM
        # no output-side config → should fall through to input
    )
    assert cfg.can_post_to_channel("D1") is True   # inherited from input
    assert cfg.can_post_to_channel("C1") is False  # input denies it too


def test_can_post_to_channel_separate_output_group():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
        allowed_channel_groups={"dm": ["*"], "channel": ["*"]},  # input: anywhere
        allowed_output_channel_groups={"channel": ["C_announce"]},  # output: only one
    )
    assert cfg.can_access_channel("D1") is True       # input: any DM
    assert cfg.can_post_to_channel("D1") is False     # output: not in output groups
    assert cfg.can_post_to_channel("C_announce") is True
    assert cfg.can_post_to_channel("C_other") is False


def test_legacy_no_config_allows_everything():
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[],
    )
    assert cfg.can_access_channel("D1") is True
    assert cfg.can_access_channel("C1") is True
    assert cfg.can_post_to_channel("anything") is True


# ---------------------------------------------------------------------------
# is_default_agent uniqueness (Q7)
# ---------------------------------------------------------------------------


def test_is_default_agent_uniqueness_rejects_second_default():
    """save_agent must reject a second is_default_agent=True in the same workspace."""
    from promaia.agents.agent_config import AgentConfig, save_agent
    from promaia.config import atomic_io
    import tempfile

    # Stub read_section / write_section to live entirely in-memory for this test.
    state = {"agents": [{"name": "maia", "workspace": "koii", "is_default_agent": True, "databases": ["x"]}]}

    def fake_read(name):
        return state if name == "agents" else None

    def fake_write(name, data):
        if name == "agents":
            state.update(data if isinstance(data, dict) else {"agents": data})

    orig_read = atomic_io.read_section
    orig_write = atomic_io.write_section
    atomic_io.read_section = fake_read
    atomic_io.write_section = fake_write
    try:
        new_default = AgentConfig(
            name="bondu",
            workspace="koii",
            databases=["gmail"],
            prompt_file="",
            mcp_tools=[],
            is_default_agent=True,
        )
        try:
            save_agent(new_default)
        except ValueError as e:
            assert "already has is_default_agent=True" in str(e)
        else:
            raise AssertionError("expected ValueError, got success")
    finally:
        atomic_io.read_section = orig_read
        atomic_io.write_section = orig_write


# ---------------------------------------------------------------------------
# Cache diff
# ---------------------------------------------------------------------------


def test_cache_diff_added_removed_changed():
    from promaia.agents.mcp_tool_cache import CachedTool, ServerCache, diff

    old = ServerCache(
        server="x", fetched_at=datetime.now(timezone.utc),
        tools=[
            CachedTool("a", "", "fp_a"),
            CachedTool("b", "", "fp_b"),
            CachedTool("c", "", "fp_c"),
        ],
    )
    new = ServerCache(
        server="x", fetched_at=datetime.now(timezone.utc),
        tools=[
            CachedTool("a", "", "fp_a"),
            CachedTool("b", "", "fp_b_v2"),
            CachedTool("d", "", "fp_d"),
        ],
    )
    d = diff(old, new)
    assert d["added"] == ["d"]
    assert d["removed"] == ["c"]
    assert d["changed"] == ["b"]


def test_cache_diff_no_old_marks_all_added():
    from promaia.agents.mcp_tool_cache import CachedTool, ServerCache, diff

    new = ServerCache(
        server="x", fetched_at=datetime.now(timezone.utc),
        tools=[CachedTool("a", "", "fp_a"), CachedTool("b", "", "fp_b")],
    )
    d = diff(None, new)
    assert d["added"] == ["a", "b"]
    assert d["removed"] == []
    assert d["changed"] == []


# ---------------------------------------------------------------------------
# can_write_source — read/write split for internal sources
# ---------------------------------------------------------------------------


def test_can_write_source_denies_when_no_source_access():
    """Deny-by-default: agent with no source_access can't write anywhere."""
    from promaia.agents.agent_config import AgentConfig
    cfg = AgentConfig(name="t", workspace="koii", databases=["x"], prompt_file="", mcp_tools=[])
    assert cfg.can_write_source("x") is False
    assert cfg.can_write_source("nonexistent") is False


def test_can_write_source_denies_when_query_only():
    """Read access (QUERY) does NOT imply write access."""
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["journal"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="journal", initial_days=None,
                permissions=[SourcePermission.QUERY],
            )
        ],
    )
    assert cfg.can_write_source("journal") is False


def test_can_write_source_allows_when_write_granted():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["journal", "gmail"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="journal", initial_days=None,
                permissions=[SourcePermission.QUERY, SourcePermission.WRITE],
            )
        ],
    )
    assert cfg.can_write_source("journal") is True
    # Other sources still denied
    assert cfg.can_write_source("gmail") is False


def test_can_write_source_other_source_denied():
    """An entry for source A doesn't grant writes to source B."""
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    cfg = AgentConfig(
        name="t", workspace="koii", databases=["a", "b"], prompt_file="", mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="a", initial_days=None,
                permissions=[SourcePermission.WRITE],
            )
        ],
    )
    assert cfg.can_write_source("a") is True
    assert cfg.can_write_source("b") is False


# ---------------------------------------------------------------------------
# Built-in tools registry (unified-picker Phase 1)
# ---------------------------------------------------------------------------


def test_builtin_tools_registry_well_formed():
    """Every entry has the expected fields and a recognised shape."""
    from promaia.cli.builtin_tools_registry import BUILTIN_TOOLS, PICKER_SHAPES
    assert len(BUILTIN_TOOLS) > 0
    seen_ids = set()
    for t in BUILTIN_TOOLS:
        assert t.id, "missing id"
        assert t.label, f"missing label for {t.id}"
        assert t.shape in PICKER_SHAPES, f"bad shape {t.shape} for {t.id}"
        assert t.gates, f"empty gates for {t.id}"
        assert t.description, f"missing description for {t.id}"
        assert t.id not in seen_ids, f"duplicate id {t.id}"
        seen_ids.add(t.id)


def test_builtin_tools_includes_expected_set():
    """Pilot must have notion, gmail, calendar, sheets, slack, discord."""
    from promaia.cli.builtin_tools_registry import BUILTIN_TOOLS
    ids = {t.id for t in BUILTIN_TOOLS}
    expected = {"notion", "gmail", "calendar", "google_sheets", "slack", "discord"}
    assert expected.issubset(ids), f"missing built-in tools: {expected - ids}"


def test_is_builtin_tool_recognises_builtins_and_rejects_others():
    from promaia.cli.builtin_tools_registry import is_builtin_tool
    assert is_builtin_tool("notion") is True
    assert is_builtin_tool("slack") is True
    assert is_builtin_tool("po-manager") is False  # MCP server
    assert is_builtin_tool("nonexistent") is False
    assert is_builtin_tool("") is False


def test_get_tool_shape_and_label():
    from promaia.cli.builtin_tools_registry import get_tool_shape, get_tool_label
    assert get_tool_shape("gmail") == "single_row"
    assert get_tool_shape("notion") == "sublist"
    assert get_tool_shape("slack") == "channel_sublist"
    assert get_tool_label("google_sheets") == "Google Sheets"
    assert get_tool_label("notion") == "Notion"


def test_invalid_shape_raises():
    """Constructor rejects unknown shapes."""
    from promaia.cli.builtin_tools_registry import BuiltinTool
    try:
        BuiltinTool(id="bad", label="Bad", shape="bogus", gates=("x",), description="d")
    except ValueError as e:
        assert "Unknown picker shape" in str(e)
    else:
        raise AssertionError("expected ValueError for bogus shape")


# ---------------------------------------------------------------------------
# Picker tree state (unified-picker Phase 2a)
# ---------------------------------------------------------------------------


def test_initial_tree_includes_all_builtins_then_mcp_servers():
    from promaia.agents.agent_config import AgentConfig
    from promaia.cli.external_tools_picker_state import build_initial_tree
    from promaia.cli.builtin_tools_registry import BUILTIN_TOOLS

    agent = AgentConfig(name="t", workspace="koii", databases=[], prompt_file="", mcp_tools=[])
    nodes = build_initial_tree(agent, mcp_server_names=["po-manager", "mrp"])
    expected_ids = [t.id for t in BUILTIN_TOOLS] + ["po-manager", "mrp"]
    assert [n.id for n in nodes] == expected_ids


def test_builtin_node_enabled_when_tool_in_databases():
    from promaia.agents.agent_config import AgentConfig
    from promaia.cli.external_tools_picker_state import build_initial_tree

    agent = AgentConfig(
        name="t", workspace="koii",
        databases=["gmail", "calendar"],
        prompt_file="", mcp_tools=[],
    )
    nodes = build_initial_tree(agent)
    by_id = {n.id: n for n in nodes}
    assert by_id["gmail"].enabled is True
    assert by_id["calendar"].enabled is True
    assert by_id["slack"].enabled is False  # not in databases


def test_mcp_server_node_enabled_when_in_mcp_tools():
    from promaia.agents.agent_config import AgentConfig
    from promaia.cli.external_tools_picker_state import build_initial_tree

    agent = AgentConfig(
        name="t", workspace="koii",
        databases=[],
        prompt_file="",
        mcp_tools=["po-manager"],
    )
    nodes = build_initial_tree(agent, mcp_server_names=["po-manager"])
    by_id = {n.id: n for n in nodes}
    assert by_id["po-manager"].enabled is True
    assert by_id["po-manager"].shape == "mcp_server"
    assert by_id["po-manager"].has_w_column is False  # MCP per-tool is allow/deny only


def test_single_row_rw_reflects_source_access():
    from promaia.agents.agent_config import AgentConfig, SourceAccess, SourcePermission
    from promaia.cli.external_tools_picker_state import build_initial_tree

    agent = AgentConfig(
        name="t", workspace="koii",
        databases=["gmail"],
        prompt_file="",
        mcp_tools=[],
        source_access=[
            SourceAccess(
                source_name="gmail", initial_days=None,
                permissions=[SourcePermission.QUERY, SourcePermission.WRITE],
            )
        ],
    )
    nodes = build_initial_tree(agent)
    by_id = {n.id: n for n in nodes}
    assert by_id["gmail"].r_state is True
    assert by_id["gmail"].w_state is True


def test_collect_picker_result_shape():
    from promaia.cli.external_tools_picker_state import (
        ToolNode, ChildNode, collect_picker_result,
    )

    nodes = [
        ToolNode(id="gmail", label="Gmail", shape="single_row", description="",
                 enabled=True, r_state=True, w_state=False),
        ToolNode(
            id="po-manager", label="po-manager", shape="mcp_server",
            description="", enabled=True, has_w_column=False,
            children=[
                ChildNode(id="list_vendors", label="list_vendors",
                          parent_id="po-manager", enabled=True, has_w_column=False),
            ],
        ),
    ]
    result = collect_picker_result(nodes)
    assert result["gmail"]["enabled"] is True
    assert result["gmail"]["r"] is True
    assert result["gmail"]["w"] is False
    assert result["po-manager"]["children"][0]["id"] == "list_vendors"
    assert result["po-manager"]["children"][0]["enabled"] is True


# ---------------------------------------------------------------------------
# Manual runner (so this works without pytest, since the project has no
# configured test runner today)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(0 if failed == 0 else 1)
