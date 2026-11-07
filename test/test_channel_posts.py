"""A Bot posts to a connected channel: ``channel_list`` and ``channel_send``.

Pins the gateway half (``dashboard.handlers.member_channels``) and what it shares
(``dashboard.channel_posts``): the names a Bot can use, the error that lists the connected
channels, the approval card that must be answered "Send it" before anything leaves, the
denial paths, the rate limit, the text the user sees being the text that is sent, and the
``agent.channel_posts`` switch.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request
from config_loader_helpers import _load_from_dict

from kiro_crew.constants import CHANNEL_OWNER_DM_NAMESPACES
from kiro_crew.dashboard import bot_messages, channel_posts
from kiro_crew.dashboard.handlers import member_channels as handler
from kiro_crew.eventlog.types import ALL_EVENT_TYPES, MEMBER_CHANNEL_POST

#: Longest any test waits for the gateway, so a broken guard fails fast instead of hanging.
_WAIT = 5


class _Asks:
    """The gateway's blocking question cards, with a user who answers when the test says so."""

    def __init__(self):
        self.cards: dict[str, dict] = {}
        self.opened = asyncio.Event()
        self.clients: int | None = 1
        self.withdrawn: list[str] = []
        self.redact = lambda text: text
        self.pending_forever = False

    @property
    def _agent_asks(self):
        return self.cards

    async def open_agent_ask(self, ask_id, slot_key, session_key, questions):
        if not self.clients:
            return self.clients
        shown = [dict(q, question=self.redact(q["question"])) for q in questions]
        self.cards[ask_id] = {
            "questions": shown,
            "session_key": session_key,
            "slot": slot_key,
            "future": asyncio.get_running_loop().create_future(),
        }
        self.opened.set()
        return self.clients

    async def wait_agent_ask(self, ask_id, session_key, slice_secs):
        card = self.cards.get(ask_id)
        if card is None or card["session_key"] != session_key:
            return None
        if self.pending_forever:
            return {"status": "pending"}
        status, answers = await card["future"]
        return {
            "status": status,
            "questions": [{"question": q["question"]} for q in card["questions"]],
            "answers": answers,
        }

    def withdraw_agent_ask(self, ask_id, session_key):
        self.withdrawn.append(ask_id)
        return self.cards.pop(ask_id, None) is not None

    # -- the user, at the dashboard -- #

    def the_card(self) -> dict:
        (card,) = self.cards.values()
        return card

    def user_answers(self, label: str) -> None:
        card = self.the_card()
        card["future"].set_result(("answered", {card["questions"][0]["question"]: label}))

    def user_closes(self, status: str = "dismissed") -> None:
        self.the_card()["future"].set_result((status, {}))


class _State:
    def __init__(self, asks: _Asks):
        self.asks = asks
        self.connected = {
            "slack": True,
            "discord": True,
            "telegram": True,
            "whatsapp": False,
            "weixin": True,
        }
        self._slots = {"member-atlas": object()}
        self.slack_client = None
        self.owner_id = ""
        for name in ("open_agent_ask", "wait_agent_ask", "withdraw_agent_ask"):
            setattr(self, name, getattr(asks, name))

    @property
    def _agent_asks(self):
        return self.asks.cards

    def channel_status(self):
        return {n: {"connected": c, "error": ""} for n, c in self.connected.items()}


class _Net:
    """One Bot on a gateway with some channels connected, every collaborator faked."""

    def __init__(self, monkeypatch):
        self.asks = _Asks()
        self.state = _State(self.asks)
        self.events: list[dict] = []
        self.sent: list[dict] = []
        self.delivery = (True, "", "")
        self.clock = [1000.0]
        self.caller = "atlas"
        self.body: dict = {}
        self.refusal = ""
        self.hung_up = False
        self.config = _load_from_dict({})
        self.config_off = _load_from_dict({"agent": {"channel_posts": False}})
        monkeypatch.setattr(bot_messages, "_now", lambda: self.clock[0])
        monkeypatch.setattr(handler.KiroCrewConfig, "load", classmethod(lambda cls: self.config))

        async def who(_request, _operation):
            return f"sk-{self.caller}", self.caller, self.caller

        async def body(_request):
            return self.body

        async def deliver(state, channel, text, *, member, session_key):
            self.sent.append(
                {"channel": channel, "text": text, "member": member, "session": session_key}
            )
            return self.delivery

        monkeypatch.setattr(handler, "_agent_member", who)
        monkeypatch.setattr(handler, "_agent_body", body)
        monkeypatch.setattr(handler, "_audit", lambda *a, **k: None)
        monkeypatch.setattr(channel_posts, "governance_refusal", lambda sk, ch: self.refusal)
        monkeypatch.setattr(channel_posts, "caller_gone", lambda request: self.hung_up)
        monkeypatch.setattr(channel_posts, "deliver", deliver)
        monkeypatch.setattr(
            "kiro_crew.dashboard.session_control.caller_slot_key",
            lambda _state, _sk: "member-atlas",
        )
        monkeypatch.setattr(
            "kiro_crew.eventlog_hooks.emit",
            lambda slug, name, type_, data: self.events.append({"type": type_, **data}) or True,
        )

    def request(self, path: str):
        return make_mocked_request("POST", path, app={"state": self.state})

    async def card_opened(self) -> None:
        """Wait for the approval card to appear; a card that never does fails the test."""
        await asyncio.wait_for(self.asks.opened.wait(), _WAIT)

    async def done(self, task):
        """The send's answer; a send still holding on a card nobody answers fails the test."""
        return await asyncio.wait_for(task, _WAIT)

    async def ask(self, channel: str, message: str):
        """Start a send; it holds on the card until the test answers."""
        self.body = {"channel": channel, "message": message}
        return asyncio.ensure_future(
            handler.api_member_self_channel_send(self.request("/api/member/self/channels/send"))
        )

    async def send(self, channel: str, message: str, label: str | None = None):
        """A whole send: the user answers *label* (or never, when None)."""
        task = await self.ask(channel, message)
        if label is not None:
            await self.card_opened()
            self.asks.user_answers(label)
        response = await self.done(task)
        return response.status, json.loads(response.text)

    async def refused(self, channel: str, message: str):
        """A send that never reaches the card."""
        self.body = {"channel": channel, "message": message}
        response = await self.done(
            asyncio.ensure_future(
                handler.api_member_self_channel_send(self.request("/api/member/self/channels/send"))
            )
        )
        return response.status, json.loads(response.text)


@pytest.fixture
def net(monkeypatch) -> _Net:
    return _Net(monkeypatch)


# -- which channels a Bot can name -------------------------------------------- #


@pytest.mark.asyncio
async def test_channel_list_names_the_connected_channels_a_bot_may_post_to(net):
    response = await handler.api_member_self_channel_list(
        net.request("/api/member/self/channels/list")
    )
    listed = json.loads(response.text)

    assert listed["channels"] == ["discord", "slack", "telegram"]
    # WhatsApp is not connected, so it is not offered; Weixin is connected but refused.
    assert "whatsapp" not in listed["channels"]
    assert listed["connected_but_not_available"] == ["weixin"]


def test_the_postable_roster_is_slack_and_the_owner_dm_channels():
    assert channel_posts.POSTABLE == {"slack", *CHANNEL_OWNER_DM_NAMESPACES}
    assert {"discord", "telegram", "whatsapp", "feishu"} <= channel_posts.POSTABLE
    # The owner-DM send refuses these on purpose: their recipient list includes people
    # who only messaged the bot.
    assert not {"weixin", "wecom"} & channel_posts.POSTABLE


@pytest.mark.asyncio
async def test_an_unconnected_or_unknown_channel_gets_an_error_listing_the_connected_ones(net):
    for name in ("whatsapp", "nowhere"):
        status, answer = await net.refused(name, "hello")
        assert status == 404 and answer["code"] == "channel_not_connected"
        assert "discord, slack, telegram" in answer["error"]
    assert net.asks.cards == {} and net.sent == []
    assert [e["outcome"] for e in net.events] == ["not_connected", "not_connected"]

    net.state.connected = {}
    status, answer = await net.refused("slack", "hello")
    assert status == 404 and answer["error"].endswith("none")


@pytest.mark.asyncio
async def test_a_connected_channel_the_owner_dm_send_refuses_is_refused_with_the_reason(net):
    status, answer = await net.refused("weixin", "hello")

    assert status == 403 and answer["code"] == "channel_not_supported"
    assert "cannot tell which one is the owner" in answer["error"]
    assert net.asks.cards == {} and net.sent == []
    assert net.events[0]["outcome"] == "not_supported"


@pytest.mark.asyncio
async def test_a_channel_name_resolves_ignoring_case_and_spaces(net):
    task = await net.ask(" Telegram ", "hello")
    await net.card_opened()
    net.asks.user_answers(channel_posts.APPROVE)
    response = await net.done(task)
    assert response.status == 200
    assert net.sent[0]["channel"] == "telegram"


# -- nothing leaves without the user's yes ------------------------------------ #


@pytest.mark.asyncio
async def test_the_post_waits_for_the_card_and_the_card_shows_the_exact_channel_and_text(net):
    task = await net.ask("discord", "Build 4412 is green.")
    await net.card_opened()

    # The call is held: nothing is sent while the user has not answered.
    assert not task.done() and net.sent == []
    (question,) = net.asks.the_card()["questions"]
    assert question["header"] == "Post to Discord"
    assert question["question"] == "atlas wants to post this to Discord:\n\nBuild 4412 is green."
    assert [o["label"] for o in question["options"]] == [
        channel_posts.APPROVE,
        channel_posts.REJECT,
    ]

    net.asks.user_answers(channel_posts.APPROVE)
    response = await net.done(task)
    assert response.status == 200 and json.loads(response.text)["status"] == "sent"
    assert net.sent == [
        {
            "channel": "discord",
            "text": "Build 4412 is green.",
            "member": "atlas",
            "session": "sk-atlas",
        }
    ]
    assert net.events == [
        {"type": MEMBER_CHANNEL_POST, "channel": "discord", "chars": 20, "outcome": "sent"}
    ]
    assert net.asks.withdrawn and net.asks.cards == {}


@pytest.mark.asyncio
async def test_pressing_dont_send_sends_nothing_and_says_so(net):
    status, answer = await net.send("discord", "Build 4412 is green.", channel_posts.REJECT)

    assert status == 403 and answer["code"] == "not_approved"
    assert "Nothing was sent" in answer["error"]
    assert net.sent == []
    assert net.events[0]["outcome"] == "denied" and "text" not in net.events[0]


@pytest.mark.asyncio
async def test_only_the_exact_send_it_answer_approves(net):
    # A typed reply that merely sounds like a yes is not the press of the button.
    for typed in ("yes", "Send it please", "send it", ""):
        net.asks.cards.clear()
        net.asks.opened.clear()
        net.clock[0] += bot_messages.PAIR_WINDOW_SECS
        status, _ = await net.send("slack", "hello", typed)
        assert status == 403, typed
    assert net.sent == []


@pytest.mark.asyncio
async def test_a_closed_card_or_an_answer_in_chat_sends_nothing(net):
    for how in ("dismissed", "composer", "expired", "withdrawn"):
        net.asks.cards.clear()
        net.asks.opened.clear()
        net.clock[0] += bot_messages.PAIR_WINDOW_SECS
        task = await net.ask("slack", "hello")
        await net.card_opened()
        net.asks.user_closes(how)
        response = await net.done(task)
        assert response.status == 403, how
    assert net.sent == []


@pytest.mark.asyncio
async def test_no_answer_in_time_withdraws_the_card_and_sends_nothing(net, monkeypatch):
    monkeypatch.setattr(channel_posts, "APPROVAL_WAIT_SECS", 0)
    net.asks.pending_forever = True

    status, answer = await net.send("slack", "hello")

    assert status == 403 and "did not answer in time" in answer["error"]
    assert net.sent == [] and net.asks.withdrawn and net.events[0]["outcome"] == "expired"


@pytest.mark.asyncio
async def test_a_bot_call_that_hangs_up_takes_its_card_down_and_sends_nothing(net):
    net.hung_up = True
    status, answer = await net.send("slack", "hello")

    assert status == 403 and answer["error"].startswith("the request was cancelled")
    assert net.sent == [] and net.asks.withdrawn and net.events[0]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_with_no_dashboard_window_or_no_room_for_a_card_nothing_is_sent(net):
    net.asks.clients = 0
    status, answer = await net.refused("slack", "hello")
    assert status == 409 and answer["code"] == "approval_unavailable"

    net.asks.clients = None
    net.clock[0] += bot_messages.PAIR_WINDOW_SECS
    status, answer = await net.refused("slack", "hello")
    assert status == 429 and answer["code"] == "approval_unavailable"

    assert net.sent == []
    assert [e["outcome"] for e in net.events] == ["approval_unavailable"] * 2


@pytest.mark.asyncio
async def test_a_bot_cannot_approve_its_own_post(net):
    # Nothing in the call can carry an approval: extra fields are ignored by the gateway
    # and refused by the tool's schema.
    from kiro_crew.validation import MCP_MATE_SCHEMAS, ValidationError, validate_tool_args

    with pytest.raises(ValidationError):
        validate_tool_args(
            {"channel": "slack", "message": "hi", "approved": True},
            MCP_MATE_SCHEMAS["channel_send"],
        )

    net.body = {"channel": "slack", "message": "hi", "approved": True, "answer": "Send it"}
    task = asyncio.ensure_future(
        handler.api_member_self_channel_send(net.request("/api/member/self/channels/send"))
    )
    await net.card_opened()
    assert not task.done() and net.sent == []

    # The id of the card is never given to the Bot, in the call's answer or anywhere else.
    (ask_id,) = net.asks.cards
    net.asks.user_answers(channel_posts.REJECT)
    response = await net.done(task)
    assert ask_id not in response.text


def test_the_route_that_answers_a_card_refuses_a_caller_that_is_not_the_dashboard_user():
    # A Bot reaches the gateway with its internal secret and no dashboard login; the
    # answer route admits the owner or a Shared Crew collaborator only.
    from kiro_crew.dashboard.handlers import ask_question

    request = make_mocked_request(
        "POST", "/api/ask-question/abc/answer", app={"state": SimpleNamespace(owner_id="")}
    )
    request["internal_auth"] = True
    refused = ask_question._deny_non_collaborator(request, "ask_question_answer")
    assert refused is not None and refused.status in (401, 403)


# -- the text the user sees is the text that is sent ------------------------------- #


@pytest.mark.asyncio
async def test_the_text_is_scrubbed_once_before_the_card_and_that_text_is_sent(net):
    task = await net.ask("slack", "token AKIAIOSFODNN7EXAMPLE for the build")
    await net.card_opened()
    shown = net.asks.the_card()["questions"][0]["question"]
    net.asks.user_answers(channel_posts.APPROVE)
    await task

    assert "AKIAIOSFODNN7EXAMPLE" not in shown
    assert net.sent[0]["text"] in shown


@pytest.mark.asyncio
async def test_a_card_that_would_show_other_text_than_is_sent_is_withdrawn(net):
    net.asks.redact = lambda text: text.replace("secret-ish", "[hidden]")

    status, answer = await net.refused("slack", "the secret-ish thing")

    assert status == 409 and answer["code"] == "text_not_showable"
    assert net.sent == [] and net.asks.withdrawn
    assert net.events[0]["outcome"] == "text_not_showable"


@pytest.mark.asyncio
async def test_a_post_past_the_length_cap_is_refused_not_cut_and_the_longest_one_fits_the_card(net):
    cap = channel_posts.MAX_POST_CHARS
    status, answer = await net.refused("slack", "x" * (cap + 1))
    assert status == 400 and answer["code"] == "message_too_long" and str(cap) in answer["error"]
    assert net.asks.cards == {} and net.events[0]["outcome"] == "message_too_long"

    # The card's question holds 500 characters: the longest text, the longest channel
    # name and a long Bot name must still fit whole.
    from kiro_crew.validation import validate_ask_user_question

    card = channel_posts.questions("B" * 200, "whatsapp", "x" * cap)
    (kept,) = validate_ask_user_question({"questions": card})
    assert kept["question"] == card[0]["question"] and len(kept["question"]) <= 500
    assert kept["header"] == card[0]["header"]


# -- the rate limit --------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_one_bot_can_ask_only_a_few_times_per_channel_and_the_limit_lifts_with_time(net):
    for n in range(channel_posts.POST_LIMIT):
        net.asks.cards.clear()
        net.asks.opened.clear()
        status, _ = await net.send("discord", f"post {n}", channel_posts.REJECT)
        assert status == 403  # declined requests count: a Bot cannot flood the user with cards

    status, answer = await net.refused("discord", "one too many")
    assert status == 429 and answer["code"] == "rate_limited"
    assert net.events[-1]["outcome"] == "rate_limited"
    # Another channel is another count.
    net.asks.cards.clear()
    net.asks.opened.clear()
    assert (await net.send("slack", "hello", channel_posts.APPROVE))[0] == 200

    net.clock[0] += bot_messages.PAIR_WINDOW_SECS
    net.asks.cards.clear()
    net.asks.opened.clear()
    assert (await net.send("discord", "later", channel_posts.APPROVE))[0] == 200


# -- governance and the switch ----------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_channel_the_governance_profile_forbids_is_refused_before_any_card(net):
    net.refusal = "messaging via transport 'slack' blocked by governance policy"

    status, answer = await net.refused("slack", "hello")

    assert status == 403 and answer["code"] == "governance_denied"
    assert net.asks.cards == {} and net.sent == []
    assert net.events[0]["outcome"] == "governance_denied"


@pytest.mark.asyncio
async def test_with_the_switch_off_both_tools_refuse_and_nothing_is_asked(net):
    assert net.config.agent.channel_posts is True
    net.config = net.config_off

    status, answer = await net.refused("slack", "hello")
    assert status == 403 and answer["code"] == "channel_posts_off"
    listed = await handler.api_member_self_channel_list(
        net.request("/api/member/self/channels/list")
    )
    assert listed.status == 403
    assert net.asks.cards == {} and net.sent == [] and net.events == []


def test_a_config_whose_agent_section_failed_to_load_reads_as_off():
    on = SimpleNamespace(degraded_sections=frozenset(), agent=SimpleNamespace(channel_posts=True))
    assert bot_messages.enabled(on, "channel_posts") is True
    on.degraded_sections = frozenset({"agent"})
    assert bot_messages.enabled(on, "channel_posts") is False


# -- delivery ----------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_channel_with_no_single_owner_or_a_failed_delivery_is_reported_not_hidden(net):
    net.delivery = (False, "", "")
    status, answer = await net.send("telegram", "hello", channel_posts.APPROVE)
    assert status == 409 and answer["code"] == "no_recipient"

    net.asks.cards.clear()
    net.asks.opened.clear()
    net.clock[0] += bot_messages.PAIR_WINDOW_SECS
    net.delivery = (False, "channel_delivery_failed", "boom AKIAIOSFODNN7EXAMPLE")
    status, answer = await net.send("telegram", "hello", channel_posts.APPROVE)
    assert status == 502 and answer["code"] == "channel_delivery_failed"
    assert "AKIAIOSFODNN7EXAMPLE" not in answer["error"]

    net.asks.cards.clear()
    net.asks.opened.clear()
    net.clock[0] += bot_messages.PAIR_WINDOW_SECS
    net.delivery = (False, "channel_delivery_unconfirmed", "timeout")
    status, answer = await net.send("telegram", "hello", channel_posts.APPROVE)
    assert status == 502 and "may have been posted" in answer["error"]
    assert [e["outcome"] for e in net.events] == [
        "no_recipient",
        "delivery_failed",
        "delivery_failed",
    ]


@pytest.mark.asyncio
async def test_a_governance_refusal_at_the_send_is_a_403(net):
    net.delivery = (False, "channel_not_permitted", "telegram is not permitted")
    status, answer = await net.send("telegram", "hello", channel_posts.APPROVE)
    assert status == 403 and answer["code"] == "channel_not_permitted"


@pytest.fixture
def facade(monkeypatch):
    """The existing sender's functions, replaced by recorders (``deliver`` reuses them)."""
    from kiro_crew.dashboard.handlers import messaging

    calls: dict[str, list] = {"dm": [], "slack": []}

    async def dm(state, channel_type, text, *, caller_session, markdown=False):
        calls["dm"].append((channel_type, text, caller_session))
        return True, "", ""

    async def slack(state, outcome, **kw):
        calls["slack"].append(kw)
        outcome.sent_slack = True

    monkeypatch.setattr(messaging, "_deliver_channel_dm", dm)
    monkeypatch.setattr(messaging, "_post_send_message_to_slack", slack)
    return calls


@pytest.mark.asyncio
async def test_a_post_leaves_through_the_existing_owner_dm_sender_naming_the_bot(facade):
    state = SimpleNamespace(slack_client=None, owner_id="")
    sent = await channel_posts.deliver(
        state, "telegram", "hello", member="atlas", session_key="sk-a"
    )

    assert sent == (True, "", "")
    assert facade["dm"] == [("telegram", "hello\n\nSent by Bot: atlas", "sk-a")]


@pytest.mark.asyncio
async def test_a_slack_post_goes_to_the_owners_dm_through_the_existing_slack_leg(facade):
    state = SimpleNamespace(slack_client=object(), owner_id="U1")
    sent = await channel_posts.deliver(state, "slack", "hello", member="atlas", session_key="sk-a")

    assert sent == (True, "", "")
    (call,) = facade["slack"]
    # No channel and no user named: the leg's own default, the owner's DM.
    assert call["target_channel"] == "" and call["target_user"] == ""
    assert call["text"] == "hello\n\nSent by Bot: atlas" and call["session_link_url"] == ""

    # Without a Slack client, or without an owner, there is nobody to write to.
    facade["slack"].clear()
    for missing in (
        SimpleNamespace(slack_client=None, owner_id="U1"),
        SimpleNamespace(slack_client=object(), owner_id=""),
    ):
        assert await channel_posts.deliver(missing, "slack", "hi", member="a", session_key="s") == (
            False,
            "",
            "",
        )
    assert facade["slack"] == []


# -- the tools ----------------------------------------------------------------------- #


def test_the_tools_are_listed_validated_granted_and_capped_alike():
    from kiro_crew import agent, mcp_mate
    from kiro_crew.validation import MCP_MATE_SCHEMAS

    defined = {t["name"]: t for t in mcp_mate._tool_definitions()}
    for name in ("channel_list", "channel_send"):
        assert name in defined and name in MCP_MATE_SCHEMAS
        assert f"@kirocrew-mate/{name}" in agent._MATE_AUTO_GRANTS
    cap = channel_posts.MAX_POST_CHARS
    assert defined["channel_send"]["inputSchema"]["properties"]["message"]["maxLength"] == cap
    message_field = next(f for f in MCP_MATE_SCHEMAS["channel_send"].fields if f.name == "message")
    assert message_field.max_len == cap


def test_channel_send_waits_long_enough_for_the_users_answer(monkeypatch):
    from kiro_crew import mcp_mate

    calls = []

    def fake_post(path, payload, session_key="", timeout=30):
        calls.append((path, payload, session_key, timeout))
        return {"ok": True, "channel": "slack", "status": "sent"}

    monkeypatch.setattr(mcp_mate, "_post", fake_post)
    monkeypatch.setattr(mcp_mate, "_strict_session_key", lambda: ("dashboard_chat-1", ""))
    out = mcp_mate._call_tool_inner("channel_send", {"channel": "slack", "message": "hi"})

    ((path, payload, key, timeout),) = calls
    assert path == "/api/member/self/channels/send" and payload == {
        "channel": "slack",
        "message": "hi",
    }
    assert key == "dashboard_chat-1" and timeout > channel_posts.APPROVAL_WAIT_SECS
    assert json.loads(out)["status"] == "sent"


def test_a_refused_post_tells_the_model_it_was_not_posted(monkeypatch):
    from kiro_crew import mcp_mate

    monkeypatch.setattr(
        mcp_mate,
        "_post",
        lambda *a, **k: {"error": "the user did not approve it", "code": "not_approved"},
    )
    monkeypatch.setattr(mcp_mate, "_strict_session_key", lambda: ("dashboard_chat-1", ""))
    out = mcp_mate._call_tool_inner("channel_send", {"channel": "slack", "message": "hi"})
    assert out.startswith("Error:") and "not posted" in out


def test_the_request_is_a_known_event_the_member_log_accepts():
    from kiro_crew import eventlog_hooks
    from kiro_crew import members as members_mod

    assert MEMBER_CHANNEL_POST in ALL_EVENT_TYPES
    members_mod.members_root().mkdir(parents=True, exist_ok=True)
    landed = eventlog_hooks.emit(
        "atlas",
        "atlas",
        MEMBER_CHANNEL_POST,
        {"channel": "slack", "chars": 3, "outcome": "sent"},
    )
    assert landed is True
