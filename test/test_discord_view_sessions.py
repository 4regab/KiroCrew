"""Sharing a session into a Discord view channel: ``/sessions``, a thread per session.

Drives a real dispatcher and transport against the in-memory fakes of
``discord_sessions_helpers``, with a client that records the REST calls the real one
would make. No network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord_sessions_helpers
import pytest
from discord_sessions_helpers import _Client, _ConversationLog, _dispatcher

from kiro_crew.discord import view_policy as vp
from kiro_crew.discord.client import (
    DISCORD_OK,
    DiscordApiResult,
    DiscordInbound,
    DiscordInteraction,
    ThreadOrigin,
)
from kiro_crew.discord.transport import DiscordTransport
from kiro_crew.discord.view_policy import ViewPolicy
from kiro_crew.discord.view_sessions import UNSHARE_ID, rehydrate_view_threads
from kiro_crew.messaging.link import ChannelLink
from kiro_crew.messaging.transport import InboundMessage
from kiro_crew.testing.synthetic_credentials import github_classic

_isolated_home = discord_sessions_helpers._isolated_home

OWNER, BOSS = "100", "200"
VIEW = "5001"
KEY = "dashboard:chat-1"


class _ViewClient(_Client):
    """The session helpers' client plus the interaction, thread and REST verbs."""

    bot_user_id = "bot"

    def __init__(self) -> None:
        super().__init__()
        self.posts: list[tuple[str, str, Any]] = []
        self.api_calls: list[tuple[str, str, Any]] = []
        self.webhook: list[tuple[str, str, Any]] = []
        self.defers: list[str] = []
        self.responses: list[str] = []
        self.thread_origins: dict[str, ThreadOrigin] = {}
        self.fail_thread_create = False
        self.api_data: dict[tuple[str, str], Any] = {}
        self._threads = 7000

    async def send_message(
        self, channel_id: str, text: str, *, components: Any = None, reply_to_message_id: Any = None
    ) -> str | None:
        self.posts.append((channel_id, text, components))
        return await super().send_message(channel_id, text, components=components)

    async def thread_origin(self, channel_id: str) -> Any:
        return self.thread_origins.get(channel_id)

    async def defer_interaction(self, interaction_id: str, token: str, *, destination: str) -> bool:
        self.defers.append(destination)
        return True

    async def respond_interaction(
        self, interaction_id: str, token: str, text: str, **kw: Any
    ) -> bool:
        self.responses.append(text)
        return True

    async def edit_interaction_original(
        self,
        token: str,
        text: str,
        *,
        components: Any = None,
        destination: str,
        caller_permitted: Any = None,
    ) -> str | None:
        self.webhook.append((token, text, components))
        self._mid += 1
        return str(self._mid)

    async def api_json(self, method: str, path: str, payload: Any, **_kw: Any) -> DiscordApiResult:
        self.api_calls.append((method, path, payload))
        if method == "POST" and path.endswith("/threads"):
            if self.fail_thread_create:
                return DiscordApiResult(DISCORD_OK, data=None, status=500)
            self._threads += 1
            return DiscordApiResult(DISCORD_OK, data={"id": str(self._threads)}, status=201)
        return DiscordApiResult(DISCORD_OK, data=self.api_data.get((method, path), {}), status=200)

    def threads_created(self) -> list[Any]:
        return [
            p
            for m, path, p in self.api_calls
            if m == "POST" and path == f"/channels/{VIEW}/threads"
        ]


def _msgs(n: int, *, extra: str = "") -> list[dict[str, str]]:
    rows = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}{extra}"}
        for i in range(n)
    ]
    return rows


def _wired(
    *, users: set[str] | None = None, writers: Any = None, messages: list[dict] | None = None
) -> tuple[Any, _ViewClient, Any, DiscordTransport]:
    log = _ConversationLog(
        [{"key": "dashboard_chat-1", "title": "Launch plan", "memory_mode": "persistent"}],
        {KEY: messages if messages is not None else _msgs(7)},
    )
    allowed = users or {OWNER}
    d, _old, sessions = _dispatcher(allowed, log)
    client = _ViewClient()
    d.client = client
    entry: dict[str, Any] = {"id": VIEW}
    if writers is not None:
        entry["writers"] = writers
    policy = ViewPolicy()
    policy.apply([entry], sorted(allowed))
    d.view = policy
    transport = DiscordTransport(
        client,  # type: ignore[arg-type]
        allowed_user_ids=sorted(allowed),
        view_policy=policy,
        on_thread_created=d.register_allowed_thread,
    )
    d.transport = transport
    return d, client, sessions, transport


def _slash(user: str = OWNER, channel: str = VIEW, name: str = "sessions") -> DiscordInteraction:
    return DiscordInteraction(
        interaction_id="i1",
        interaction_token="tok",
        channel_id=channel,
        user_id=user,
        message_id="",
        guild_id="g1",
        kind=2,
        command_name=name,
    )


def _press(
    custom_id: str, message_id: str, *, user: str = OWNER, channel: str = VIEW
) -> DiscordInteraction:
    return DiscordInteraction(
        interaction_id="i2",
        interaction_token="tok2",
        channel_id=channel,
        user_id=user,
        message_id=message_id,
        guild_id="g1",
        custom_id=custom_id,
    )


async def _list(d: Any, client: _ViewClient) -> tuple[str, str]:
    """Run ``/sessions``; return the first button's custom id and the picker's message id."""
    await d.on_interaction(_slash())
    _token, _text, components = client.webhook[-1]
    return components[0]["components"][0]["custom_id"], str(client._mid)


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(vp, "_now", lambda: now[0])
    return now


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    fake = SimpleNamespace(log_api_access=lambda **kw: seen.append(kw))
    monkeypatch.setattr(vp, "sel", lambda: fake)
    monkeypatch.setattr("kiro_crew.discord.view_sessions.sel", lambda: fake)
    return seen


class TestListing:
    @pytest.mark.asyncio
    async def test_the_list_is_private_to_the_writer(self) -> None:
        d, client, _sessions, _t = _wired()
        await d.on_interaction(_slash())
        assert client.defers == [VIEW]
        token, text, components = client.webhook[-1]
        assert token == "tok" and "Launch plan" in components[0]["components"][0]["label"]
        assert "WHOLE shared session" in text
        # Nothing is posted into the channel itself.
        assert client.posts == [] and client.responses == []

    @pytest.mark.asyncio
    async def test_a_non_writer_on_the_global_list_gets_nothing(self) -> None:
        d, client, _sessions, _t = _wired(users={OWNER, BOSS}, writers=[OWNER])
        await d.on_interaction(_slash(user=BOSS))
        assert client.defers == [] and client.webhook == [] and client.posts == []

    @pytest.mark.asyncio
    async def test_several_allowed_users_means_the_owner_rule_refuses(self) -> None:
        d, client, _sessions, _t = _wired(users={OWNER, BOSS}, writers=[OWNER])
        await d.on_interaction(_slash())
        _token, text, components = client.webhook[-1]
        assert "exactly one" in text and not components

    @pytest.mark.asyncio
    async def test_other_commands_in_the_view_channel_keep_the_normal_refusal(self) -> None:
        d, client, _sessions, _t = _wired()
        await d.on_interaction(_slash(name="new"))
        assert client.defers == [] and "approved thread" in client.responses[0]

    @pytest.mark.asyncio
    async def test_text_sessions_in_the_view_channel_lists_nothing(self) -> None:
        d, client, _sessions, transport = _wired()
        await transport.receive(
            DiscordInbound(
                channel_id=VIEW, user_id=OWNER, text="!sessions", message_id="m1", guild_id="g1"
            )
        )
        assert [c for c, _t, _k in client.posts] == [VIEW]
        text = client.posts[0][1]
        assert "/sessions" in text and "Launch plan" not in text and client.posts[0][2] is None

    @pytest.mark.asyncio
    async def test_text_sessions_from_a_non_writer_is_ignored(self) -> None:
        _d, client, _sessions, transport = _wired(users={OWNER, BOSS}, writers=[OWNER])
        await transport.receive(
            DiscordInbound(
                channel_id=VIEW, user_id=BOSS, text="!sessions", message_id="m1", guild_id="g1"
            )
        )
        assert client.posts == []

    @pytest.mark.asyncio
    async def test_a_dm_sessions_command_is_unchanged(self) -> None:
        d, client, _sessions, _t = _wired()
        await d.handle_message(
            InboundMessage(
                channel_type="discord", user_id=OWNER, conversation_id="dm1", text="!sessions"
            )
        )
        assert "Launch plan" in client.sent[-1][1][0]["components"][0]["label"]
        assert client.webhook == [] and client.defers == []


class TestSharing:
    @pytest.mark.asyncio
    async def test_a_pick_opens_a_thread_links_the_session_and_replays_five(self) -> None:
        d, client, sessions, transport = _wired()
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))

        assert client.threads_created() == [
            {"name": "Launch plan", "type": 11, "auto_archive_duration": 10080}
        ]
        thread = "7001"
        assert sessions.mirror_links[KEY] == ChannelLink("discord", channel_id=thread)
        assert KEY in sessions.inbound_keys
        assert thread in d._allowed_threads and transport.may_send_to(thread)
        posted = [(c, t) for c, t, _k in client.posts]
        assert all(c == thread for c, _ in posted)
        # A header with the Stop button, then the last five messages: 7 -> 5.
        assert "Shared session" in posted[0][1]
        assert client.posts[0][2][0]["components"][0]["custom_id"] == UNSHARE_ID
        assert [t.split("\n", 1)[1] for _c, t in posted[1:]] == [
            f"message {i}" for i in range(2, 7)
        ]
        # The picker message is replaced by the result, with no buttons left.
        assert client.webhook[-1][2] == [] and "Shared in a new thread" in client.webhook[-1][1]

    @pytest.mark.asyncio
    async def test_the_replay_is_redacted_and_defuses_mentions_for_both_roles(self) -> None:
        secret = github_classic()
        d, client, _sessions, _t = _wired(
            messages=[
                {"role": "user", "content": f"my token is {secret} cc @everyone"},
                {"role": "assistant", "content": f"noted {secret} @here"},
            ]
        )
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        replay = " ".join(t for _c, t, _k in client.posts[1:])
        assert secret not in replay
        assert "@everyone" not in replay and "@here" not in replay
        assert replay.count("🧑") == 1 and replay.count("🤖") == 1

    @pytest.mark.asyncio
    async def test_replay_length_follows_the_channel_setting(self) -> None:
        d, client, _sessions, _t = _wired()
        d.view.apply([{"id": VIEW, "replay_messages": 0}], [OWNER])
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        assert len(client.posts) == 1  # the header only

    @pytest.mark.asyncio
    async def test_a_forged_or_replayed_press_shares_nothing(self) -> None:
        d, client, sessions, _t = _wired(users={OWNER}, writers=[OWNER, BOSS])
        custom_id, message_id = await _list(d, client)
        nonce = custom_id.split(":")[1]
        for forged in (
            _press(custom_id, "999"),  # another message
            _press(f"s:{nonce}:9", message_id),  # out-of-range index
            _press("s:nonsense:0", message_id),  # unknown nonce
            _press(custom_id, message_id, user=BOSS),  # a writer who is not the owner
            _press(custom_id, message_id, channel="6001"),  # another channel
        ):
            await d.on_interaction(forged)
        assert client.threads_created() == [] and sessions.mirror_links == {}
        await d.on_interaction(_press(custom_id, message_id))
        assert len(client.threads_created()) == 1
        # The same button a second time: the choice was consumed.
        await d.on_interaction(_press(custom_id, message_id))
        assert len(client.threads_created()) == 1
        assert "expired" in client.webhook[-1][1]

    @pytest.mark.asyncio
    async def test_a_non_writer_press_is_ignored(self) -> None:
        d, client, sessions, _t = _wired(users={OWNER, BOSS}, writers=[OWNER])
        custom_id, message_id = await _list_with(d, client)
        await d.on_interaction(_press(custom_id, message_id, user=BOSS))
        assert client.threads_created() == [] and sessions.mirror_links == {}

    @pytest.mark.asyncio
    async def test_a_failed_thread_create_shares_nothing(self) -> None:
        d, client, sessions, _t = _wired()
        custom_id, message_id = await _list(d, client)
        client.fail_thread_create = True
        await d.on_interaction(_press(custom_id, message_id))
        assert sessions.mirror_links == {} and "nothing was shared" in client.webhook[-1][1]

    @pytest.mark.asyncio
    async def test_a_failed_bind_deletes_the_thread_it_opened(self) -> None:
        d, client, sessions, _t = _wired()
        custom_id, message_id = await _list(d, client)
        d._session_resume.conv_log.messages.pop(KEY)  # the transcript vanished
        await d.on_interaction(_press(custom_id, message_id))
        assert ("DELETE", "/channels/7001", None) in client.api_calls
        assert sessions.mirror_links == {} and d._allowed_threads == set()

    @pytest.mark.asyncio
    async def test_shares_are_rate_limited_per_channel(self, _clock: list[float]) -> None:
        d, client, _sessions, _t = _wired()
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        assert len(client.threads_created()) == 1 and "too fast" in client.webhook[-1][1]
        _clock[0] += 16
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        assert len(client.threads_created()) == 2

    @pytest.mark.asyncio
    async def test_a_writer_message_in_the_thread_drives_the_shared_session(self) -> None:
        d, client, sessions, _t = _wired()
        custom_id, message_id = await _list(d, client)
        await d.on_interaction(_press(custom_id, message_id))
        await d.handle_message(
            InboundMessage(
                channel_type="discord",
                user_id=OWNER,
                conversation_id="7001",
                text="continue please",
                thread_id="7001",
                person_origin=True,
            )
        )
        assert sessions.last_key == KEY


async def _list_with(d: Any, client: _ViewClient) -> tuple[str, str]:
    """``/sessions`` as the owner, for a setup whose config lets two ids be allowed."""
    d._session_resume.owner_id = OWNER
    return await _list(d, client)


async def _shared(
    users: set[str] | None = None, writers: Any = None
) -> tuple[Any, _ViewClient, Any, DiscordTransport]:
    d, client, sessions, transport = _wired(users=users, writers=writers)
    custom_id, message_id = await _list_with(d, client)
    await d.on_interaction(_press(custom_id, message_id))
    client.posts.clear()
    client.api_calls.clear()
    return d, client, sessions, transport


class TestStopSharing:
    @pytest.mark.asyncio
    async def test_the_button_unlinks_archives_and_locks(self) -> None:
        d, client, sessions, transport = await _shared()
        client.thread_origins["7001"] = ThreadOrigin(11, VIEW, "bot")
        await d.on_interaction(_press(UNSHARE_ID, "55", channel="7001"))
        assert KEY not in sessions.mirror_links
        assert ("PATCH", "/channels/7001", {"archived": True, "locked": True}) in client.api_calls
        assert "7001" not in d._allowed_threads and not transport.may_send_to("7001")
        assert "Sharing stopped" in client.webhook[-1][1]

    @pytest.mark.asyncio
    async def test_a_non_writer_cannot_stop_it(self) -> None:
        d, client, sessions, _t = await _shared(users={OWNER, BOSS}, writers=[OWNER])
        client.thread_origins["7001"] = ThreadOrigin(11, VIEW, "bot")
        await d.on_interaction(_press(UNSHARE_ID, "55", user=BOSS, channel="7001"))
        assert KEY in sessions.mirror_links and client.api_calls == []

    @pytest.mark.asyncio
    async def test_the_button_does_nothing_in_a_thread_outside_the_view_channel(self) -> None:
        d, client, sessions, _t = await _shared()
        d._allowed_threads.add("8001")
        client.thread_origins["8001"] = ThreadOrigin(11, "9999", "bot")
        await d.on_interaction(_press(UNSHARE_ID, "55", channel="8001"))
        assert KEY in sessions.mirror_links and client.api_calls == []

    @pytest.mark.asyncio
    async def test_a_stopped_thread_is_not_readmitted(self) -> None:
        d, client, _sessions, transport = await _shared()
        client.thread_origins["7001"] = ThreadOrigin(11, VIEW, "bot")
        await d.on_interaction(_press(UNSHARE_ID, "55", channel="7001"))
        assert await transport.readmit_own_thread("7001", OWNER) is False


class TestRestart:
    @pytest.mark.asyncio
    async def test_a_view_thread_is_readmitted_for_a_writer_after_a_restart(self) -> None:
        _d, client, _sessions, transport = _wired()
        client.thread_origins["7001"] = ThreadOrigin(11, VIEW, "bot")
        assert transport.may_send_to("7001") is False
        assert await transport.readmit_own_thread("7001", OWNER) is True
        assert transport.may_send_to("7001") is True

    @pytest.mark.asyncio
    async def test_a_thread_someone_else_opened_is_not_readmitted(self) -> None:
        _d, client, _sessions, transport = _wired()
        client.thread_origins["7001"] = ThreadOrigin(11, VIEW, BOSS)
        assert await transport.readmit_own_thread("7001", OWNER) is False

    @pytest.mark.asyncio
    async def test_startup_readmits_only_the_bots_active_threads_under_a_view_channel(self) -> None:
        _d, client, _sessions, transport = _wired()
        client.api_data[("GET", f"/channels/{VIEW}")] = {"guild_id": "g1"}
        client.api_data[("GET", "/guilds/g1/threads/active")] = {
            "threads": [
                {"id": "7001", "owner_id": "bot", "parent_id": VIEW},
                {"id": "7002", "owner_id": BOSS, "parent_id": VIEW},
                {"id": "7003", "owner_id": "bot", "parent_id": "9999"},
            ]
        }
        assert await rehydrate_view_threads(client, transport) == 1  # type: ignore[arg-type]
        assert transport.may_send_to("7001")
        assert not transport.may_send_to("7002") and not transport.may_send_to("7003")

    @pytest.mark.asyncio
    async def test_the_view_channel_itself_is_not_a_proactive_send_target(self) -> None:
        _d, _client, _sessions, transport = _wired()
        assert transport.may_send_to(VIEW) is False
        assert not any(t.target_id.endswith(VIEW) for t in transport.configured_targets())
