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
