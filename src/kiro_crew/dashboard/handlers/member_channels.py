"""HTTP routes behind the ``channel_list`` and ``channel_send`` tools: a Bot posts to a channel.

MCP-only, under the strict ``/api/member/self`` prefix, like the Bot-message and room
tools: the poster is the Bot whose pinned thread the verified caller's slot is, never a
name from the body, and the tools are admitted on any turn in that thread.
``messaging.md``, "Bot channel posts".

* ``POST /api/member/self/channels/list`` -- the connected channels a Bot may name.
* ``POST /api/member/self/channels/send`` -- ``{channel, message}``: show the user a
  card with the exact channel and text, wait for the answer, and send only on
  "Send it". The call returns after the answer, or after
  :data:`channel_posts.APPROVAL_WAIT_SECS`.

Nothing here sends without the card. The card is opened and read by the gateway, so the
Bot cannot answer it, and the text sent is the text the card showed. Every request,
approved or not, writes one ``member/channel_post`` event to the poster's member log and
one audit line.
"""

from __future__ import annotations

import asyncio
import uuid

from aiohttp import web

from kiro_crew import member_goals
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.dashboard import bot_messages, channel_posts
from kiro_crew.dashboard.handlers.guide import _audit
from kiro_crew.dashboard.handlers.member_goals import _agent_body, _agent_member, _deny

#: Operation names, as audited. Absent from ``handlers.guide._USER_TURN_OPERATIONS`` on
#: purpose, as the other Bot tools are: the user's answer on the card is the gate, not
#: who started the turn.
OP_CHANNEL_LIST = "member.channel_list"
OP_CHANNEL_SEND = "member.channel_send"

#: Longest channel name echoed into the crew log for a name that matched nothing.
_LOGGED_NAME_CHARS = 80


async def _caller(
    request: web.Request, operation: str
) -> tuple[tuple[str, str, str], KiroCrewConfig] | web.Response:
    """``((session_key, slug, member), config)`` of the calling Bot, or the refusal."""
    who = await _agent_member(request, operation)
    if isinstance(who, web.Response):
        return who
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if not bot_messages.enabled(cfg, "channel_posts"):
        _audit(who[0], operation, "denied", "channel posts are off")
        return _deny(
            403,
            "channel_posts_off",
            "Bot channel posts are turned off on this gateway (agent.channel_posts); tell the user instead",
        )
    return who, cfg


async def api_member_self_channel_list(request: web.Request) -> web.Response:
    """POST /api/member/self/channels/list — the connected channels a Bot may post to."""
    got = await _caller(request, OP_CHANNEL_LIST)
    if isinstance(got, web.Response):
        return got
    (sk, _slug, _member), _cfg = got
    postable, blocked = channel_posts.channels(request.app["state"])
    _audit(sk, OP_CHANNEL_LIST, "ok", "")
    return web.json_response(
        {
            "ok": True,
            "channels": postable,
            "connected_but_not_available": blocked,
            "note": "A post goes to the user's own messages on that channel, and only after "
            "the user approves the exact text.",
        }
    )


async def api_member_self_channel_send(request: web.Request) -> web.Response:
    """POST /api/member/self/channels/send — ``{channel, message}``: ask to post, then post."""
    got = await _caller(request, OP_CHANNEL_SEND)
    if isinstance(got, web.Response):
        return got
    (sk, slug, member), _cfg = got
    body = await _agent_body(request)
    ref, message = body.get("channel"), body.get("message")
    if not isinstance(ref, str) or not ref.strip():
        return _deny(400, "invalid_channel", "name the channel (channel_list shows the names)")
    if not isinstance(message, str) or not message.strip():
        return _deny(400, "invalid_message", "the message is empty")
    chars = len(message)

    async def answer(outcome: str, response: web.Response, channel: str = "") -> web.Response:
        """Write the request's one crew-log event and audit line, then reply."""
        from kiro_crew import eventlog_hooks
        from kiro_crew.eventlog.types import MEMBER_CHANNEL_POST

        await asyncio.to_thread(
            eventlog_hooks.emit,
            slug,
            member,
            MEMBER_CHANNEL_POST,
            {"channel": (channel or ref)[:_LOGGED_NAME_CHARS], "chars": chars, "outcome": outcome},
        )
        _audit(sk, OP_CHANNEL_SEND, "ok" if response.status == 200 else "denied", outcome)
        return response

    if chars > channel_posts.MAX_POST_CHARS:
        return await answer(
            "message_too_long",
            _deny(
                400,
                "message_too_long",
                f"the message is {chars} characters; the limit is {channel_posts.MAX_POST_CHARS}. "
                "Shorten it and ask again",
            ),
        )
    state = request.app["state"]
    postable, blocked = channel_posts.channels(state)
    channel = bot_messages.resolve(ref, postable)
    if channel is None:
        if bot_messages.resolve(ref, blocked) is not None:
            return await answer(
                "not_supported",
                _deny(
                    403,
                    "channel_not_supported",
                    f"{ref.strip()[:_LOGGED_NAME_CHARS]!r} is connected, but a Bot cannot post to "
                    f"it: {channel_posts.NOT_AVAILABLE_WHY}. Tell the user in this chat instead",
                ),
            )
        return await answer(
            "not_connected",
            _deny(
                404,
                "channel_not_connected",
                f"no connected channel named {ref.strip()[:_LOGGED_NAME_CHARS]!r}. Connected "
                "channels you can post to: " + (", ".join(postable) or "none"),
            ),
        )
    refusal = await asyncio.to_thread(channel_posts.governance_refusal, sk, channel)
    if refusal:
        return await answer("governance_denied", _deny(403, "governance_denied", refusal), channel)
    # What the user sees is what is sent: scrubbed once, here, before the card is made.
    text = member_goals.scrub_text(message).strip()
    if not bot_messages.take_rate_slot(
        state, member, f"channel:{channel}", channel_posts.POST_LIMIT
    ):
        return await answer(
            "rate_limited",
            _deny(
                429,
                "rate_limited",
                f"you asked to post to {channel} {channel_posts.POST_LIMIT} times in the last "
                f"{bot_messages.PAIR_WINDOW_SECS // 60} minutes, the most allowed; wait or tell the user in this chat",
            ),
            channel,
        )
    from kiro_crew.dashboard.session_control import caller_slot_key

    ask_id = uuid.uuid4().hex
    card = channel_posts.questions(member, channel, text)
    opened = await state.open_agent_ask(ask_id, caller_slot_key(state, sk), sk, card)
    if not opened:
        return await answer(
            "approval_unavailable",
            _deny(
                409 if opened == 0 else 429,
                "approval_unavailable",
                "the user could not be asked (no dashboard window is open to show the approval "
                "card, or too many questions are waiting). Nothing was sent; tell the user in this chat",
            ),
            channel,
        )
    if channel_posts.card_text(state, ask_id) != card[0]["question"]:
        # The card redacts what it shows. A card that differs from the text would not be
        # an approval of that text, so it is withdrawn rather than shown.
        state.withdraw_agent_ask(ask_id, sk)
        return await answer(
            "text_not_showable",
            _deny(
                409,
                "text_not_showable",
                "the approval card cannot show this text as it is (it holds something that "
                "is redacted on screen). Nothing was sent; reword it and ask again",
            ),
            channel,
        )
    reply = await channel_posts.await_approval(state, request, ask_id, sk)
    if not channel_posts.approved(reply):
        status = str(reply.get("status") or "")
        outcome = status if status in ("expired", "cancelled") else "denied"
        return await answer(
            outcome,
            _deny(
                403,
                "not_approved",
                {
                    "expired": "the user did not answer in time",
                    "cancelled": "the request was cancelled",
                }.get(outcome, "the user did not approve it")
                + ". Nothing was sent. Do not say it was; tell the user in this chat if it matters",
            ),
            channel,
        )
    sent, code, detail = await channel_posts.deliver(
        state, channel, text, member=member, session_key=sk
    )
    if sent:
        return await answer(
            "sent", web.json_response({"ok": True, "channel": channel, "status": "sent"}), channel
        )
    if not code:
        return await answer(
            "no_recipient",
            _deny(
                409,
                "no_recipient",
                f"{channel} has no single owner to write to right now. Nothing was sent; "
                "tell the user in this chat",
            ),
            channel,
        )
    unconfirmed = code == "channel_delivery_unconfirmed"
    return await answer(
        "delivery_failed",
        _deny(
            403 if code == "channel_not_permitted" else 502,
            code,
            (
                f"{channel} did not confirm delivery; the message may have been posted. "
                if unconfirmed
                else f"{channel} did not take the message. "
            )
            + (member_goals.scrub_text(detail)[:200] or code),
        ),
        channel,
    )
