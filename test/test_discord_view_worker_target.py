"""A worker with its own read-only view-channel thread stays addressable by its creator.

``create_session`` binds a one-way mirror from the worker to a thread under the shared view
channel. That mirror must not make the worker unaddressable (``mirrored_target``), while every
other mirrored target keeps its refusal.
"""

from __future__ import annotations

import asyncio

import pytest
from test_session_control_owner_dm import (
    DISCORD_DM_CONVERSATION,
    DISCORD_DM_PAIRING,
    GUEST,
    OWNER,
    VIEW_CHANNEL,
    WORKER_THREAD,
    _in_runner_turn,
    _key,
    _ledger_gate,
    _mirrored_slot,
    _state,
    _ViewTransport,
)

from kiro_crew.dashboard import create_rate_limit
from kiro_crew.dashboard import session_control as sc
from kiro_crew.dashboard.chat_utils import slot_history_key
from kiro_crew.dashboard.handlers import work_ledger as ledger_routes
from kiro_crew.messaging.link import ChannelLink

VERBS = ("send", "read", "stop", "close")


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(sc, "session_control_enabled", lambda: True)
    create_rate_limit.reset_for_tests()
    yield
    create_rate_limit.reset_for_tests()


@pytest.fixture
def open_ledger_gate(monkeypatch):
    async def _recognized(*a, **k):
        return None

    monkeypatch.setattr(ledger_routes, "_recognize_session", _recognized)
    monkeypatch.setattr(ledger_routes, "_is_restricted_session", lambda *a: False)


class _Transport(_ViewTransport):
    """A view transport that knows which conversations are bot-opened view threads."""

    def __init__(self, writers: dict[str, str], threads=()) -> None:
        super().__init__(writers)
        self.threads = set(threads)

    def is_view_worker_thread(self, conversation_id: str) -> bool:
        return conversation_id in self.threads


def _make_worker(tmp_path, monkeypatch, *, writers=None, threads=(WORKER_THREAD,)):
    """A conductor shared in the view channel and the worker it creates."""
    state = _state(tmp_path, monkeypatch)
    transport = _Transport(
        {VIEW_CHANNEL: OWNER, WORKER_THREAD: OWNER} if writers is None else writers, threads
    )
    state.register_channel_transport(transport)
    caller = _mirrored_slot(
        state, "chat-conductor", ChannelLink("discord", channel_id=VIEW_CHANNEL, principal=OWNER)
    )
    created = asyncio.run(sc.create_session(state, caller_session_key=_key(caller), title="Fix"))
    return state, transport, caller, state.get_slot(created["target"])


def _refusal(state, caller, child, op) -> str:
    try:
        sc.authorize_target(state, caller_session_key=_key(caller), target=child.key, operation=op)
    except sc.SessionControlError as exc:
        return exc.code
    return ""


@pytest.mark.parametrize("op", VERBS)
def test_the_creator_reaches_its_worker_in_a_read_only_thread(tmp_path, monkeypatch, op):
    state, _t, caller, child = _make_worker(tmp_path, monkeypatch)
    assert state.sessions.get_mirror_link(slot_history_key(child)).channel_id == WORKER_THREAD
    assert _refusal(state, caller, child, op) == ""


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("two_writer_thread", "mirrored_target"),
        ("no_writer_thread", "mirrored_target"),
        ("not_a_bot_opened_thread", "mirrored_target"),
        ("writer_is_not_the_owner", "mirrored_target"),
        ("inbound_mirror", "mirrored_target"),
        ("slack_thread_beside", "mirrored_target"),
        ("dm_mirror", "mirrored_target"),
    ],
)
@pytest.mark.parametrize("op", VERBS)
def test_every_other_mirrored_target_keeps_its_refusal(tmp_path, monkeypatch, case, code, op):
    state, transport, caller, child = _make_worker(tmp_path, monkeypatch)
    key = slot_history_key(child)
    if case == "two_writer_thread":
        transport._writers.pop(WORKER_THREAD)
    elif case == "no_writer_thread":
        transport._writers[WORKER_THREAD] = ""
    elif case == "not_a_bot_opened_thread":
        transport.threads.clear()
    elif case == "writer_is_not_the_owner":
        transport._writers[WORKER_THREAD] = GUEST
    elif case == "inbound_mirror":
        state.sessions.set_mirror_link(
            key, ChannelLink("discord", channel_id=WORKER_THREAD), accepts_inbound=True
        )
    elif case == "slack_thread_beside":
        state.sessions.set_slack_link(key, "1700000000.000100", "C0SLACK")
    elif case == "dm_mirror":
        transport._dm_pairings = DISCORD_DM_PAIRING
        state.sessions.set_mirror_link(key, DISCORD_DM_CONVERSATION)
    assert _refusal(state, caller, child, op) == code


def test_a_session_nobody_created_with_the_same_thread_stays_refused(tmp_path, monkeypatch):
    state, _t, caller, child = _make_worker(tmp_path, monkeypatch)
    child._created_by = ""
    assert _refusal(state, caller, child, "read") == "mirrored_target"


def test_the_work_ledger_gate_admits_the_worker(tmp_path, monkeypatch, open_ledger_gate):
    state, transport, _caller, child = _make_worker(tmp_path, monkeypatch)
    assert _ledger_gate(state, _key(child))[1] is None
    transport._writers.pop(WORKER_THREAD)
    assert _ledger_gate(state, _key(child))[1] is not None


def test_reading_the_worker_does_not_withhold_the_creators_reply(tmp_path, monkeypatch):
    state, _t, caller, child = _make_worker(tmp_path, monkeypatch)
    _in_runner_turn(caller)
    assert _refusal(state, caller, child, "read") == ""
    assert caller._steer_audience_fences
    binding = sc._read_mirror_binding(state.sessions, slot_history_key(caller))
    assert sc.publication_withheld(state, caller, binding) is False


def test_a_reply_after_reading_still_withholds_when_the_audience_changes(tmp_path, monkeypatch):
    state, transport, caller, child = _make_worker(tmp_path, monkeypatch)
    _in_runner_turn(caller)
    assert _refusal(state, caller, child, "read") == ""
    transport._dm_pairings = DISCORD_DM_PAIRING
    state.sessions.set_mirror_link(slot_history_key(caller), DISCORD_DM_CONVERSATION)
    binding = sc._read_mirror_binding(state.sessions, slot_history_key(caller))
    assert sc.publication_withheld(state, caller, binding) is True
