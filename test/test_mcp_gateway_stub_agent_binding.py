"""The declaring agent of a stub comes from the gateway's agent binding.

gatewayd serves a stub's backend with the declared env of one agent, and stamps
that agent on its app renders. These tests drive the real ``_handle_connection``
over a real unix socket and check that the agent is the one the owning gateway
bound to the stub's session token (an ``agent-binding`` frame), not whatever the
Register frame names:

* a Register naming another agent than the binding gets no declared env and an
  empty declaring agent,
* a Register naming the bound agent gets its declared env, as before,
* a session whose stubs register before any claim (a warm-pool session) is
  served its agent, and the claim that follows names its caller without moving
  the agent,
* a binding from a sender that is not the daemon's owner is not recorded,
* a resource-subscription holder registering from the gateway keeps the agent
  the daemon resolved, and
* the gateway side reads the stub agents off ``session/new`` params and sends
  the binding again with every claim.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from kiro_crew.mcp_gateway import claim as claim_mod
from kiro_crew.mcp_gateway import gatewayd as gw
from kiro_crew.mcp_gateway import launch_approval
from kiro_crew.mcp_gateway.daemon import control, session_describe
from kiro_crew.mcp_gateway.hashing import hash_effective_env
from kiro_crew.mcp_gateway.pool import PoolKey
from kiro_crew.mcp_gateway.rewriter_overlay import (
    env_sidecar_dir,
    env_sidecar_name,
    resolve_overlay_dir,
)

pytestmark = pytest.mark.xdist_group("mcp_gateway")

OWN_AGENT = "own-agent"
OTHER_AGENT = "other-agent"
SERVER = "gh-mcp"
TOKEN = "t" * 64
#: The runtime pid the binding names, and the host chain the peer walk reports.
RUNTIME_PID = 434343


@pytest.fixture(autouse=True)
def _clean_globals(monkeypatch, tmp_path):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    for table in (gw._CONN_INDEX, gw._STUB_PROBES, gw._TOKEN_BINDINGS, gw._AGENT_BINDINGS):
        table.clear()
    claim_mod._BOUND_AGENTS.clear()
    session_describe._LIVE_CONNS.clear()
    # The test process is the gateway that owns the daemon.
    monkeypatch.setattr(gw, "_OWNER_PID", os.getpid())
    # The kernel-attested host chain of every registering peer: under RUNTIME_PID.
    monkeypatch.setattr(gw, "_resolve_peer_identity", lambda _pid: ("", [RUNTIME_PID]))
    monkeypatch.setattr(launch_approval, "launch_approved", lambda *_a, **_k: True)
    yield
    for table in (gw._CONN_INDEX, gw._STUB_PROBES, gw._TOKEN_BINDINGS, gw._AGENT_BINDINGS):
        table.clear()
    claim_mod._BOUND_AGENTS.clear()
    session_describe._LIVE_CONNS.clear()


def _write_sidecar(agent: str) -> dict[str, str]:
    pairs = {"GITHUB_TOKEN": f"secret://{agent}-token", "REGION": "us-west-2"}
    d = env_sidecar_dir(resolve_overlay_dir())
    d.mkdir(parents=True, exist_ok=True)
    (d / env_sidecar_name(agent, SERVER)).write_text(json.dumps(pairs), encoding="utf-8")
    return pairs


def _register(agent: str, pairs: dict[str, str], **extra: Any) -> dict[str, Any]:
    frame = {
        "type": "register",
        "stub_uuid": str(uuid.uuid4()),
        "server_name": SERVER,
        "agent_name": agent,
        "command_args_hash": "c" * 16,
        "effective_env_hash": hash_effective_env(pairs, identity_keys=()),
        "work_dir": "/tmp",
        "binary_version": "1",
        "os_uid": 1000,
        "stub_session_token": TOKEN,
        "ancestor_pids": [RUNTIME_PID],
    }
    frame.update(extra)
    return frame


class _Daemon:
    def __init__(self, sock: Path, acquired: list[dict[str, Any]], backend: MagicMock) -> None:
        self.sock = sock
        self.acquired = acquired
        self.backend = backend

    async def control(self, frame: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.open_unix_connection(str(self.sock))
        try:
            writer.write((json.dumps(frame) + "\n").encode())
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), 10))
        finally:
            writer.close()

    async def register_and_ensure(self, frame: dict[str, Any]) -> tuple[Any, Any]:
        reader, writer = await asyncio.open_unix_connection(str(self.sock))
        writer.write((json.dumps(frame) + "\n").encode())
        await writer.drain()
        registered = json.loads(await asyncio.wait_for(reader.readline(), 10))
        assert registered["type"] == "registered", registered
        writer.write(b'{"type":"ensure_backend"}\n')
        await writer.drain()
        ready = json.loads(await asyncio.wait_for(reader.readline(), 10))
        assert ready["type"] == "ready", ready
        return reader, writer


@pytest_asyncio.fixture
async def daemon(monkeypatch) -> AsyncIterator[_Daemon]:
    acquired: list[dict[str, Any]] = []
    backend = MagicMock()
    backend.attach_stub = AsyncMock(side_effect=lambda *_a, **_k: asyncio.Queue())
    backend.detach_stub = AsyncMock()

    async def _acquire(pool, pool_key, resolver, **kw):
        acquired.append({"pool_key": pool_key, **kw})
        return backend, True

    monkeypatch.setattr(gw, "_acquire_backend", _acquire)
    sock_dir = Path(tempfile.mkdtemp(prefix="kc-", dir="/tmp"))
    sock = sock_dir / "gw.sock"
    pool = MagicMock()
    pool.get = AsyncMock(return_value=None)
    pool.release_exclusive = AsyncMock(return_value=None)
    pool.unreserve = MagicMock()

    def _resolver(pk: PoolKey):
        return "true", [], {}, pk.work_dir

    server = await asyncio.start_unix_server(
        lambda r, w: gw._handle_connection(r, w, pool, _resolver, sock), path=str(sock)
    )
    try:
        yield _Daemon(sock, acquired, backend)
    finally:
        server.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server.wait_closed(), 5)
        sock.unlink(missing_ok=True)
        sock_dir.rmdir()


def _binding(agents: list[str]) -> dict[str, Any]:
    return {
        "type": "agent-binding",
        "stub_session_token": TOKEN,
        "agents": agents,
        "pid": RUNTIME_PID,
        "pid_start_id": None,
    }


def _stubs_are_not_the_owner(monkeypatch) -> None:
    """From here on, connections come from a process other than the daemon's owner.

    The test process sends both the binding and the Register, so a stub's
    Register would otherwise pass as the gateway's own.
    """
    monkeypatch.setattr(gw, "_OWNER_PID", os.getpid() + 100000)
    monkeypatch.setattr(control.platform_compat, "is_exec_supervisor_of", lambda *_a: False)


async def _settle(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_register_naming_another_agent_gets_no_declared_env(daemon, monkeypatch) -> None:
    _write_sidecar(OTHER_AGENT)
    pairs = _write_sidecar(OWN_AGENT)
    assert (await daemon.control(_binding([OWN_AGENT])))["type"] == "agent-bound"
    _stubs_are_not_the_owner(monkeypatch)

    _reader, writer = await daemon.register_and_ensure(_register(OTHER_AGENT, pairs))
    await _settle(writer)

    (seen,) = daemon.acquired
    assert seen["exclusive_stub_uuid"], "premise: a private backend"
    assert seen["declaring_agent"] == ""
    assert gw._declared_env_for_private_backend(seen["pool_key"], declaring_agent="") == {}
    daemon.backend.attach_stub.assert_awaited_once()
    assert daemon.backend.attach_stub.await_args.kwargs["agent"] == ""


@pytest.mark.asyncio
async def test_a_register_naming_the_bound_agent_gets_its_declared_env(daemon, monkeypatch) -> None:
    pairs = _write_sidecar(OWN_AGENT)
    assert (await daemon.control(_binding([OWN_AGENT])))["type"] == "agent-bound"
    _stubs_are_not_the_owner(monkeypatch)

    _reader, writer = await daemon.register_and_ensure(_register(OWN_AGENT, pairs))
    await _settle(writer)

    (seen,) = daemon.acquired
    assert seen["declaring_agent"] == OWN_AGENT
    declared = gw._declared_env_for_private_backend(
        seen["pool_key"], declaring_agent=seen["declaring_agent"]
    )
    assert declared == pairs
    assert daemon.backend.attach_stub.await_args.kwargs["agent"] == OWN_AGENT


@pytest.mark.asyncio
async def test_a_stub_registering_before_its_claim_keeps_its_agent(daemon, monkeypatch) -> None:
    """A warm-pool session: binding, then register with no caller, then the claim."""
    pairs = _write_sidecar(OWN_AGENT)
    assert (await daemon.control(_binding([OWN_AGENT])))["type"] == "agent-bound"
    _stubs_are_not_the_owner(monkeypatch)

    _reader, writer = await daemon.register_and_ensure(_register(OWN_AGENT, pairs))
    (conn,) = gw._CONN_INDEX[RUNTIME_PID]
    assert conn.caller is None, "premise: no claim has named the token yet"
    (seen,) = daemon.acquired
    assert seen["declaring_agent"] == OWN_AGENT
    assert (
        gw._declared_env_for_private_backend(seen["pool_key"], declaring_agent=OWN_AGENT) == pairs
    )

    claim = claim_mod.build_claim_frame(RUNTIME_PID, "dashboard:warm", None, TOKEN)
    claim["pid_start_id"] = None
    reply = await daemon.control(claim)
    assert reply["type"] == "claimed", reply
    assert conn.caller is not None and conn.caller.session_key == "dashboard:warm"
    assert conn.bound_agents == frozenset({OWN_AGENT})
    await _settle(writer)


@pytest.mark.asyncio
async def test_a_binding_from_a_sender_that_is_not_the_owner_is_ignored(
    daemon, monkeypatch
) -> None:
    pairs = _write_sidecar(OWN_AGENT)
    monkeypatch.setattr(gw, "_OWNER_PID", os.getpid() + 100000)
    monkeypatch.setattr(control.platform_compat, "is_exec_supervisor_of", lambda *_a: False)

    reply = await daemon.control(_binding([OWN_AGENT]))
    assert reply["type"] == "agent-binding-rejected"
    assert "neither owner pid" in reply["reason"]
    assert TOKEN not in gw._AGENT_BINDINGS

    _reader, writer = await daemon.register_and_ensure(_register(OWN_AGENT, pairs))
    await _settle(writer)
    (seen,) = daemon.acquired
    assert seen["declaring_agent"] == ""


@pytest.mark.asyncio
async def test_an_unowned_daemon_records_no_binding(daemon, monkeypatch) -> None:
    monkeypatch.setattr(gw, "_OWNER_PID", 0)
    reply = await daemon.control(_binding([OWN_AGENT]))
    assert reply["type"] == "agent-binding-rejected"
    assert "--owner-pid" in reply["reason"]
    assert TOKEN not in gw._AGENT_BINDINGS


@pytest.mark.asyncio
async def test_a_binding_for_another_runtime_does_not_reach_this_stub(daemon, monkeypatch) -> None:
    pairs = _write_sidecar(OWN_AGENT)
    frame = _binding([OWN_AGENT])
    frame["pid"] = RUNTIME_PID + 1
    assert (await daemon.control(frame))["type"] == "agent-bound"
    _stubs_are_not_the_owner(monkeypatch)

    _reader, writer = await daemon.register_and_ensure(_register(OWN_AGENT, pairs))
    await _settle(writer)
    (seen,) = daemon.acquired
    assert seen["declaring_agent"] == ""


@pytest.mark.asyncio
async def test_a_holder_registered_by_the_gateway_keeps_the_resolved_agent(
    daemon, monkeypatch
) -> None:
    """The kept Register names the agent the daemon resolved, not the frame's."""
    pairs = _write_sidecar(OWN_AGENT)
    assert (await daemon.control(_binding([OWN_AGENT])))["type"] == "agent-bound"
    with monkeypatch.context() as stub_side:
        _stubs_are_not_the_owner(stub_side)
        _reader, writer = await daemon.register_and_ensure(
            _register(OTHER_AGENT, pairs, session_key="dashboard:s", session_type="dashboard")
        )
    (conn,) = list(session_describe._LIVE_CONNS)
    assert conn.register is not None and conn.register["agent_name"] == ""
    await _settle(writer)
    assert daemon.acquired[-1]["declaring_agent"] == ""

    # A Register sent from the owning gateway itself, with no token, as a holder's is.
    holder = _register(OWN_AGENT, pairs)
    holder.pop("stub_session_token")
    _reader, writer = await daemon.register_and_ensure(holder)
    await _settle(writer)
    assert daemon.acquired[-1]["declaring_agent"] == OWN_AGENT


def test_stub_entry_agents_reads_every_stub_entry() -> None:
    params = {
        "mcpServers": [
            {"name": "a", "command": "py", "args": ["-m", "stub", "--server", "a", "--agent", "x"]},
            {"name": "plain", "command": "node", "args": ["--agent", "not-a-stub"]},
        ],
        "_meta": {
            "kiro": {
                "customAgents": [{"mcpServers": {"b": {"args": ["--server", "b", "--agent", "y"]}}}]
            }
        },
    }
    assert claim_mod.stub_entry_agents(params) == frozenset({"x", "y"})
    assert claim_mod.stub_entry_agents({"mcpServers": []}) == frozenset()


@pytest.mark.asyncio
async def test_every_claim_sends_the_binding_again(monkeypatch) -> None:
    sent: list[tuple[str, Any]] = []

    async def _claim(*args: Any) -> bool:
        sent.append(("claim", args))
        return True

    async def _inner(socket_path, pid, token, agents):
        sent.append(("bind", (pid, token, agents)))
        return True

    monkeypatch.setattr(claim_mod, "send_claim", _claim)
    monkeypatch.setattr(claim_mod, "_send_agent_binding_inner", _inner)

    assert await claim_mod.send_agent_binding("/s", RUNTIME_PID, TOKEN, frozenset({"x"}))
    sent.clear()
    await claim_mod._claim_and_rebind("/s", RUNTIME_PID, "dashboard:a", None, TOKEN)
    assert [kind for kind, _ in sent] == ["claim", "bind"]
    assert sent[1][1] == (RUNTIME_PID, TOKEN, frozenset({"x"}))

    sent.clear()
    await claim_mod._claim_and_rebind("/s", RUNTIME_PID, "dashboard:a", None, "u" * 64)
    assert [kind for kind, _ in sent] == ["claim"]
