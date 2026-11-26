"""The Discord view-channel writers gate.

In a view channel, and in every thread under one, only a listed writer can send a
message, run a command or press a button. Everyone else is ignored and audited, even
when the id is on the global ``allowed_user_ids``. These tests drive the transport's
``receive`` and the dispatcher's ``on_interaction`` / ``handle_message`` with the fake
Discord client; no network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from discord_helpers import _drop_live_config_snapshot  # noqa: F401
from discord_helpers import FakeClient, _dispatcher, _inbound

from kiro_crew.discord import view_policy as vp
from kiro_crew.discord.client import DiscordInbound, DiscordInteraction, ThreadOrigin
from kiro_crew.discord.transport import DiscordTransport
from kiro_crew.discord.view_policy import ViewPolicy, parse_view_channels
from kiro_crew.messaging.transport import InboundMessage

OWNER, BOSS = "100", "200"
VIEW, OTHER = "5001", "5002"
VIEW_THREAD, OTHER_THREAD = "6001", "6002"


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        vp, "sel", lambda: SimpleNamespace(log_api_access=lambda **kw: seen.append(kw))
    )
    return seen


def _policy(view_channels: Any, users: Any = (OWNER, BOSS)) -> ViewPolicy:
    policy = ViewPolicy()
    policy.apply(view_channels, list(users))
    return policy


def _client() -> FakeClient:
    client = FakeClient()
    client.thread_channels.update({VIEW_THREAD, OTHER_THREAD})
    client.thread_origins[VIEW_THREAD] = ThreadOrigin(11, VIEW, "bot")
    client.thread_origins[OTHER_THREAD] = ThreadOrigin(11, OTHER, "bot")
    return client


def _transport(
    policy: ViewPolicy, *, users: tuple[str, ...] = (OWNER, BOSS)
) -> tuple[DiscordTransport, list[InboundMessage], FakeClient]:
    dispatched: list[InboundMessage] = []

    async def _dispatch(message: InboundMessage) -> None:
        dispatched.append(message)

    client = _client()
    transport = DiscordTransport(
        client,  # type: ignore[arg-type]
        allowed_user_ids=users,
        allowed_thread_ids=[VIEW_THREAD, OTHER_THREAD],
        allowed_channel_ids=[VIEW, OTHER],
        dispatch=_dispatch,
        view_policy=policy,
    )
    return transport, dispatched, client


def _msg(user: str, channel: str, text: str = "hello") -> DiscordInbound:
    return DiscordInbound(
        channel_id=channel, user_id=user, text=text, message_id="m1", guild_id="g1"
    )


class TestConfigParsing:
    def test_omitted_writers_means_the_single_allowed_user(self) -> None:
        policy = _policy([{"id": VIEW}], users=(OWNER,))
        view = policy.channel(VIEW)
        assert view is not None
        assert policy.writers_of(view) == {OWNER}

    @pytest.mark.parametrize("users", [(), (OWNER, BOSS)])
    def test_omitted_writers_without_one_owner_means_nobody(self, users: tuple[str, ...]) -> None:
        policy = _policy([{"id": VIEW}], users=users)
        view = policy.channel(VIEW)
        assert view is not None
        assert policy.writers_of(view) == frozenset()

    def test_explicit_writers_win_and_are_coerced(self) -> None:
        policy = _policy([{"id": 5001, "writers": [OWNER, 300, "x", None]}])
        view = policy.channel(VIEW)
        assert view is not None
        assert policy.writers_of(view) == {OWNER, "300"}

    @pytest.mark.parametrize("writers", ["100", {"a": 1}, 7, None])
    def test_a_writers_value_that_is_not_a_list_means_nobody(self, writers: Any) -> None:
        policy = _policy([{"id": VIEW, "writers": writers}], users=(OWNER,))
        view = policy.channel(VIEW)
        assert view is not None
        assert policy.writers_of(view) == frozenset()

    def test_bare_ids_and_duplicates_are_read_only_and_idless_entries_are_dropped(self) -> None:
        parsed = parse_view_channels(
            [
                VIEW,
                {"id": OTHER, "writers": [OWNER]},
                {"id": OTHER, "writers": [OWNER]},
                {"x": 1},
                3.5,
            ]
        )
        assert parsed is not None
        assert set(parsed) == {VIEW, OTHER}
        assert parsed[VIEW].writers == frozenset()
        assert parsed[OTHER].writers == frozenset()

    def test_replay_messages_is_bounded(self) -> None:
        parsed = parse_view_channels(
            [
                {"id": "1", "replay_messages": 99},
                {"id": "2", "replay_messages": -4},
                {"id": "3", "replay_messages": "x"},
                {"id": "4"},
            ]
        )
        assert parsed is not None
        assert [parsed[k].replay_messages for k in "1234"] == [vp.REPLAY_MAX, 0, 5, 5]

    def test_a_bad_reload_keeps_the_last_good_set(self) -> None:
        policy = _policy([{"id": VIEW, "writers": [OWNER]}])
        policy.reconfigure(SimpleNamespace(view_channels="oops", allowed_user_ids=[OWNER]))
        assert policy.channel(VIEW) is not None
        policy.reconfigure(SimpleNamespace(view_channels=[], allowed_user_ids=[OWNER]))
        assert not policy.active


class TestMessages:
    @pytest.mark.asyncio
    async def test_non_writer_on_the_global_allowlist_is_ignored_in_the_view_channel(
        self, audit: list[dict[str, Any]]
    ) -> None:
        transport, dispatched, client = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await transport.receive(_msg(BOSS, VIEW, "do the thing"))
        assert dispatched == []
        assert client.created_threads == []
        assert [(e["operation"], e["caller"], e["resources"]) for e in audit] == [
            ("discord_view.denied", BOSS, f"channel={VIEW};surface=message")
        ]

    @pytest.mark.asyncio
    async def test_a_writer_still_reaches_the_normal_flow(self) -> None:
        transport, _dispatched, client = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await transport.receive(_msg(OWNER, VIEW))
        assert client.created_threads == [(VIEW, "m1", "hello")]

    @pytest.mark.asyncio
    async def test_a_thread_under_the_view_channel_inherits_view(
        self, audit: list[dict[str, Any]]
    ) -> None:
        transport, dispatched, client = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        assert dispatched == []
        assert len(audit) == 1
        await transport.receive(_msg(OWNER, VIEW_THREAD))
        assert [m.user_id for m in dispatched] == [OWNER]
        # The parent is read once per thread, not once per message.
        assert client.origin_lookups == [VIEW_THREAD]

    @pytest.mark.asyncio
    async def test_a_thread_whose_parent_cannot_be_read_is_closed_to_non_writers(self) -> None:
        transport, dispatched, client = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        client.thread_origins.pop(VIEW_THREAD)
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_other_channels_and_threads_are_not_affected(self) -> None:
        transport, dispatched, client = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await transport.receive(_msg(BOSS, OTHER_THREAD))
        await transport.receive(_msg(BOSS, OTHER))
        assert [m.user_id for m in dispatched] == [BOSS, BOSS]
        assert client.created_threads == [(OTHER, "m1", "hello")]

    @pytest.mark.asyncio
    async def test_dms_are_not_affected(self) -> None:
        transport, dispatched, _client_ = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await transport.receive(DiscordInbound(channel_id="dm1", user_id=BOSS, text="hi"))
        assert [m.user_id for m in dispatched] == [BOSS]

    @pytest.mark.asyncio
    async def test_no_view_channels_means_no_change(self) -> None:
        transport, dispatched, client = _transport(ViewPolicy())
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        assert [m.user_id for m in dispatched] == [BOSS]
        assert client.origin_lookups == []

    @pytest.mark.asyncio
    async def test_malformed_config_leaves_the_listed_channel_read_only_for_all(self) -> None:
        transport, dispatched, _client_ = _transport(_policy([{"id": VIEW, "writers": "oops"}]))
        await transport.receive(_msg(OWNER, VIEW_THREAD))
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        assert dispatched == []

    @pytest.mark.asyncio
    async def test_a_reload_takes_effect_on_the_next_message(self) -> None:
        transport, dispatched, _client_ = _transport(
            _policy([{"id": VIEW, "writers": [OWNER, BOSS]}])
        )
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        transport.reconfigure(
            SimpleNamespace(
                view_channels=[{"id": VIEW, "writers": [OWNER]}],
                allowed_user_ids=[OWNER, BOSS],
                allowed_channel_ids=[VIEW, OTHER],
                allowed_thread_ids=[VIEW_THREAD, OTHER_THREAD],
                auto_thread=True,
            )
        )
        await transport.receive(_msg(BOSS, VIEW_THREAD))
        assert [m.user_id for m in dispatched] == [BOSS]

    @pytest.mark.asyncio
    async def test_the_audit_is_throttled_and_holds_no_content(
        self, audit: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = [1000.0]
        monkeypatch.setattr(vp, "_now", lambda: clock[0])
        transport, _dispatched, _client_ = _transport(_policy([{"id": VIEW, "writers": [OWNER]}]))
        for _ in range(3):
            await transport.receive(_msg(BOSS, VIEW, "secret words"))
        assert len(audit) == 1
        clock[0] += 61
        await transport.receive(_msg(BOSS, VIEW, "secret words"))
        assert len(audit) == 2
        assert "secret" not in repr(audit)


def _interaction(user: str, channel: str, **kw: Any) -> DiscordInteraction:
    return DiscordInteraction(
        interaction_id="i1",
        interaction_token="tok",
        channel_id=channel,
        user_id=user,
        message_id="m9",
        guild_id="g1",
        **kw,
    )


def _wired(policy: ViewPolicy) -> tuple[Any, FakeClient]:
    d, _fake, _sess = _dispatcher({OWNER, BOSS}, allowed_threads={VIEW_THREAD, OTHER_THREAD})
    client = _client()
    d.client = client
    d.view = policy
    return d, client


class TestDispatcher:
    @pytest.mark.asyncio
    async def test_a_non_writer_button_press_does_nothing(
        self, audit: list[dict[str, Any]]
    ) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.on_interaction(_interaction(BOSS, VIEW_THREAD, custom_id="opt:0:abc", label="Yes"))
        assert client.acked == []
        assert client.sent == []
        assert [e["resources"] for e in audit] == [f"channel={VIEW_THREAD};surface=interaction"]

    @pytest.mark.asyncio
    async def test_a_non_writer_slash_command_gets_no_reply(self) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.on_interaction(_interaction(BOSS, VIEW_THREAD, kind=2, command_name="help"))
        assert client.defers == [] and client.responses == [] and client.webhook_writes == []

    @pytest.mark.asyncio
    async def test_a_writer_slash_command_still_runs(self) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.on_interaction(_interaction(OWNER, VIEW_THREAD, kind=2, command_name="help"))
        assert client.defers != []

    @pytest.mark.asyncio
    async def test_the_same_press_outside_a_view_thread_is_not_gated(self) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.on_interaction(_interaction(BOSS, OTHER_THREAD, kind=2, command_name="help"))
        assert client.defers != []

    @pytest.mark.asyncio
    async def test_a_non_writer_message_never_starts_a_turn_or_command(self) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.handle_message(
            InboundMessage(
                channel_type="discord",
                user_id=BOSS,
                conversation_id=VIEW_THREAD,
                text="!help",
                thread_id=VIEW_THREAD,
                person_origin=True,
            )
        )
        assert client.sent == []

    @pytest.mark.asyncio
    async def test_a_dm_command_from_a_non_writer_is_unchanged(self) -> None:
        d, client = _wired(_policy([{"id": VIEW, "writers": [OWNER]}]))
        await d.handle_message(_inbound("!help", user_id=BOSS, conversation_id="dm1"))
        assert client.sent != []
