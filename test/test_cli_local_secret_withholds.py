"""A CLI-local process withholds a ``secret://`` MCP server it cannot resolve.

``kirocrew chat`` / ``run`` / ``eval`` / knowledge consolidate run gateway-less
(``KiroCrewConfig.without_stub_servers()``), so kiro-cli launches the agent
spec's own servers. One whose env holds a ``secret://`` reference only gatewayd
resolves must not start with the literal string as its credential; it is
overridden with a refusing stub. A server with no such env still starts, and a
dashboard or routed session (overlay set) is untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from kiro_crew.acp import client_mcp_array as cma

_SPEC = {
    "mcpServers": {
        "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "secret://github"}},
        "plain": {"command": "plain-mcp", "env": {"REGION": "us-west-2"}},
    }
}


def _client(overlay: str | None, backend: str = "") -> Any:
    return SimpleNamespace(
        _mcp_gateway_overlay=overlay,
        backend=backend,
        _agent="kiro_default",
        _work_dir="/tmp",
    )


def test_cli_local_withholds_only_the_secret_server(monkeypatch) -> None:
    from kiro_crew.mcp_gateway.hashing import expand_stub_flags

    monkeypatch.setattr(cma, "agent_spec_snapshot", lambda *a, **k: _SPEC)
    out = cma._cli_local_secret_withholds(_client(overlay=None))
    # Exactly one refusing override, for the secret:// server; the plain one is
    # left to start directly (no override).
    assert len(out) == 1
    element = out[0]
    assert element["name"] == "gh"
    # ACP-shaped: env is an array of {name,value} pairs (never a mapping) and the
    # internal wrapper marker is dropped, so kiro-cli/KAS accept session/new
    # instead of rejecting the raw overlay dict with -32602.
    assert isinstance(element.get("env"), list)
    assert all(isinstance(e, dict) and "name" in e and "value" in e for e in element["env"])
    assert "_kirocrew_mcp_gateway_wrapped" not in element
    flags = expand_stub_flags(element["args"])
    assert "--refuse" in flags
    assert "gh" in flags and "plain" not in flags


def test_a_routed_session_gets_no_override(monkeypatch) -> None:
    monkeypatch.setattr(cma, "agent_spec_snapshot", lambda *a, **k: _SPEC)
    # Overlay set = the gateway is in this process's path (dashboard / routed):
    # the stub carries the declared env, so nothing is withheld here.
    assert cma._cli_local_secret_withholds(_client(overlay="/overlay")) == []


def test_a_mirror_backend_gets_no_override(monkeypatch) -> None:
    monkeypatch.setattr(cma, "agent_spec_snapshot", lambda *a, **k: _SPEC)
    # A mirror reads no --agent spec; its servers ride the wire array instead.
    assert cma._cli_local_secret_withholds(_client(overlay=None, backend="codex")) == []


def test_no_secret_server_means_no_override(monkeypatch) -> None:
    monkeypatch.setattr(
        cma,
        "agent_spec_snapshot",
        lambda *a, **k: {"mcpServers": {"plain": {"command": "p", "env": {"R": "x"}}}},
    )
    assert cma._cli_local_secret_withholds(_client(overlay=None)) == []
