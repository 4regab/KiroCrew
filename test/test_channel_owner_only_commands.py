"""``/goal`` and ``/workflow`` run from a channel only when the Crew's owner wrote it.

A Slack thread linked to a dashboard chat runs its messages as that chat's turns,
and the turn's slash gate reads their first word as a command. Both commands start
work that runs on after the turn (a goal loop, a workflow run), so a channel turn
runs them only while every Slack author on record for the chat is the owner. A
dashboard turn -- the owner's or an admitted teammate's -- is not judged here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from turn_harness import SlotSpec, TurnScript, run_turn

from kiro_crew.dashboard import chat_runner
from kiro_crew.dashboard.chat_turn import slash_commands
from kiro_crew.dashboard.session_control_parts import code_channel_exemption as cce
from kiro_crew.slack import handler as slack_handler

OWNER = "U0OWNER"
SHARED = "U0SHARED"
CHANNEL_TURN = {"_directive_user_origin": True, "_directive_channel_origin": True}
DASHBOARD_TURN = {"_directive_user_origin": True}


class _Workflows:
    def __init__(self) -> None:
        self.started: list[str] = []

    def list_definitions(self) -> list[dict[str, Any]]:
        return []

    async def start_definition(self, workflow_ref: str, **_kwargs: Any) -> dict[str, Any]:
        self.started.append(workflow_ref)
        return {"run_id": "wf_1", "slug": workflow_ref, "revision": 1}


@pytest.fixture(autouse=True)
def _owner(_floor_monkeypatch: pytest.MonkeyPatch):
    _floor_monkeypatch.setattr(slack_handler, "is_owner", lambda user_id: user_id == OWNER)
    cce.forget_senders()
    yield
    cce.forget_senders()


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    async def deliver(_state, _slot, _sessions, _key, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(chat_runner, "_deliver_linked_slack_message", deliver)
    return sent


def _wrote(*authors: str, workflows: _Workflows | None = None):
    def setup(ctx) -> None:
        for author in authors:
            cce.note_channel_sender(ctx.slot, author)
        if workflows is not None:
            ctx.state.workflow_service = workflows

    return setup


def _goal_armed(record) -> bool:
    return any(call.name == "add" for call in record.autonudge_calls)


def _replies(record) -> list[str]:
    return [row["content"] for row in record.window if row["role"] == "assistant"]


@pytest.mark.asyncio
@pytest.mark.parametrize("authors", [(SHARED,), (OWNER, SHARED), ()])
async def test_a_channel_goal_the_owner_did_not_alone_write_is_not_armed(
    posted: list[str], authors: tuple[str, ...]
) -> None:
    record = await run_turn(
        TurnScript(message="/goal ship it", run_kwargs=CHANNEL_TURN, setup=_wrote(*authors)),
        slot=SlotSpec(autonudge=True),
    )
    assert not _goal_armed(record)
    [reply] = [text for text in _replies(record) if "was not run" in text]
    assert reply.startswith("`/goal` was not run")
    assert posted == [reply]
    assert record.audits(tool_name="/goal", outcome="denied")
    assert record.provider_calls == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("authors", [(SHARED,), (OWNER, SHARED), ()])
async def test_a_channel_workflow_the_owner_did_not_alone_write_is_not_started(
    posted: list[str], authors: tuple[str, ...]
) -> None:
    workflows = _Workflows()
    record = await run_turn(
        TurnScript(
            message="/workflow nightly go",
            run_kwargs=CHANNEL_TURN,
            setup=_wrote(*authors, workflows=workflows),
        )
    )
    assert workflows.started == []
    assert any(text.startswith("`/workflow` was not run") for text in _replies(record))
    assert len(posted) == 1
    assert record.audits(tool_name="/workflow", outcome="denied")


@pytest.mark.asyncio
async def test_the_owners_own_channel_goal_is_armed(posted: list[str]) -> None:
    record = await run_turn(
        TurnScript(message="/goal ship it", run_kwargs=CHANNEL_TURN, setup=_wrote(OWNER)),
        slot=SlotSpec(autonudge=True),
    )
    assert _goal_armed(record)
    assert posted == []


@pytest.mark.asyncio
async def test_the_owners_own_channel_workflow_is_started() -> None:
    workflows = _Workflows()
    await run_turn(
        TurnScript(
            message="/workflow nightly go",
            run_kwargs=CHANNEL_TURN,
            setup=_wrote(OWNER, workflows=workflows),
        )
    )
    assert workflows.started == ["nightly"]


@pytest.mark.asyncio
async def test_a_dashboard_turn_is_not_judged_by_who_wrote_in_the_channel(
    posted: list[str],
) -> None:
    # The owner's or an admitted teammate's dashboard send, in a chat a shared
    # Slack user also wrote in: the command runs.
    workflows = _Workflows()
    goal = await run_turn(
        TurnScript(message="/goal ship it", run_kwargs=DASHBOARD_TURN, setup=_wrote(SHARED)),
        slot=SlotSpec(autonudge=True),
    )
    await run_turn(
        TurnScript(
            message="/workflow nightly",
            run_kwargs=DASHBOARD_TURN,
            setup=_wrote(SHARED, workflows=workflows),
        )
    )
    assert _goal_armed(goal)
    assert workflows.started == ["nightly"]
    assert posted == []


def _turn(**fields: Any) -> SimpleNamespace:
    base = {"_directive_channel_origin": False, "_directive_source_channels": ()}
    base.update(fields)
    return SimpleNamespace(**base)


class _Slot:
    # A class, not a SimpleNamespace: the author record holds a weak reference.
    def __init__(self, key: str) -> None:
        self.key = key
        self._slack_linked = True
        self._slack_channel = "C0LINKED"


def _slot(key: str = "chat-x") -> _Slot:
    return _Slot(key)


def test_a_stamped_channel_turn_is_judged_even_without_its_channel_mark() -> None:
    # A queued channel entry restored after a restart keeps its source-channel
    # stamp but not its channel-origin mark, and the author record is gone.
    slot = _slot()
    assert slash_commands._refuses_channel_sender(
        _turn(_directive_source_channels=("C0LINKED",)), slot
    )
    cce.note_channel_sender(slot, OWNER)
    assert not slash_commands._refuses_channel_sender(
        _turn(_directive_source_channels=("C0LINKED",)), slot
    )


def test_an_unnamed_author_refuses() -> None:
    slot = _slot()
    cce.note_channel_sender(slot, OWNER)
    cce.note_channel_sender(slot, "")
    assert slash_commands._refuses_channel_sender(_turn(_directive_channel_origin=True), slot)


def test_a_failing_owner_check_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_user_id: str) -> bool:
        raise RuntimeError("owner id unreadable")

    monkeypatch.setattr(slack_handler, "is_owner", broken)
    slot = _slot()
    cce.note_channel_sender(slot, OWNER)
    assert slash_commands._refuses_channel_sender(_turn(_directive_channel_origin=True), slot)
