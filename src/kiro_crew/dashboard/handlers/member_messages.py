"""HTTP routes behind the ``bot_list`` and ``bot_send`` tools: a Bot messages another Bot.

MCP-only, under the strict ``/api/member/self`` prefix, like the goal tools
(:mod:`handlers.member_goals`): the sender is the Bot whose pinned thread the verified
caller's slot is, never a name from the body, and the tools are admitted on any turn
in that thread (a message from the user, a check-in, another Bot's message), but not
from a channel, a schedule, a subagent or an app. ``crew-mode.md``, "Bot messaging".

* ``POST /api/member/self/bots/list`` -- the other Bots a message can reach.
* ``POST /api/member/self/bots/send`` -- ``{name, message}``: queue the message on
  the named Bot's thread and answer at once. The target reads it as data from
  another Bot (:func:`mate_thread_wakes.bot_message_prompt`), after its own turn
  ends if it is busy.

Every send, refused ones included, writes one ``member/bot_message`` event to the
sender's member log and one audit line. The sender gets a name, a description and a
queued message's ack, and nothing of the target's memory or files.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from kiro_crew import member_goals
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.dashboard import bot_messages, mate_thread_wakes
from kiro_crew.dashboard.handlers.guide import _audit
from kiro_crew.dashboard.handlers.member_goals import _agent_body, _agent_member, _deny

#: Operation names, as audited. Absent from ``handlers.guide._USER_TURN_OPERATIONS``
#: on purpose: a Bot answering a Bot's message runs in a turn the user did not send.
OP_BOT_LIST = "member.bot_list"
OP_BOT_SEND = "member.bot_send"

#: Longest name echoed into the crew log for a send to a name that matched no Bot.
_LOGGED_NAME_CHARS = 80


async def _caller(
    request: web.Request, operation: str
) -> tuple[tuple[str, str, str], KiroCrewConfig] | web.Response:
    """``((session_key, slug, member), config)`` of the calling Bot, or the refusal."""
    who = await _agent_member(request, operation)
    if isinstance(who, web.Response):
        return who
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if not bot_messages.enabled(cfg):
        _audit(who[0], operation, "denied", "bot messaging is off")
        return _deny(
            403,
            "bot_messaging_off",
            "Bot messaging is turned off on this gateway (agent.bot_messaging); tell the user instead",
        )
    return who, cfg


async def api_member_self_bot_list(request: web.Request) -> web.Response:
    """POST /api/member/self/bots/list — the other Bots on this gateway, by name."""
    got = await _caller(request, OP_BOT_LIST)
    if isinstance(got, web.Response):
        return got
    (sk, _slug, member), cfg = got
    bots = await asyncio.to_thread(bot_messages.reachable, cfg)
    rows = [
        {
            "name": name,
            "description": member_goals.scrub_text(found["description"])[
                : bot_messages.DESCRIPTION_CHARS
            ],
        }
        for name, found in bots.items()
        if name != member
    ]
    _audit(sk, OP_BOT_LIST, "ok", "")
    return web.json_response({"ok": True, "bots": rows})


async def api_member_self_bot_send(request: web.Request) -> web.Response:
    """POST /api/member/self/bots/send — ``{name, message}``: queue a message for a Bot."""
    got = await _caller(request, OP_BOT_SEND)
    if isinstance(got, web.Response):
        return got
    (sk, slug, member), cfg = got
    body = await _agent_body(request)
    name, message = body.get("name"), body.get("message")
    if not isinstance(name, str) or not name.strip():
        return _deny(400, "invalid_name", "name the Bot to message (bot_list shows the names)")
    if not isinstance(message, str) or not message.strip():
        return _deny(400, "invalid_message", "the message is empty")
    chars = len(message)

    async def answer(
        outcome: str, response: web.Response, to: str = "", hop: int = 0
    ) -> web.Response:
        """Write the send's one crew-log event and audit line, then reply."""
        from kiro_crew import eventlog_hooks
        from kiro_crew.eventlog.types import MEMBER_BOT_MESSAGE

        await asyncio.to_thread(
            eventlog_hooks.emit,
            slug,
            member,
            MEMBER_BOT_MESSAGE,
            {
                "to": (to or name)[:_LOGGED_NAME_CHARS],
                "chars": chars,
                "hop": hop,
                "outcome": outcome,
            },
        )
        _audit(sk, OP_BOT_SEND, "ok" if response.status == 200 else "denied", outcome)
        return response

    if chars > bot_messages.MAX_MESSAGE_CHARS:
        return await answer(
            "message_too_long",
            _deny(
                400,
                "message_too_long",
                f"the message is {chars} characters; the limit is {bot_messages.MAX_MESSAGE_CHARS}. "
                "Shorten it and send again",
            ),
        )
    bots = await asyncio.to_thread(bot_messages.reachable, cfg)
    target = bot_messages.resolve(name, list(bots))
    if target is None:
        others = sorted(n for n in bots if n != member)
        return await answer(
            "unknown_bot",
            _deny(
                404,
                "unknown_bot",
                f"no Bot named {name.strip()[:_LOGGED_NAME_CHARS]!r}. Bots you can message: "
                + (", ".join(others) or "none"),
            ),
        )
    if target == member:
        return await answer(
            "self_send", _deny(400, "self_send", "a Bot cannot message itself"), target
        )
    from kiro_crew.dashboard.session_control import caller_slot_key

    state = request.app["state"]
    hop = bot_messages.hop_of(state.get_slot(caller_slot_key(state, sk))) + 1
    if hop > bot_messages.MAX_HOPS:
        return await answer(
            "hop_limit",
            _deny(
                409,
                "hop_limit",
                f"this message chain already has {bot_messages.MAX_HOPS} hops, the most allowed; "
                "tell the user instead",
            ),
            target,
            hop,
        )
    from kiro_crew.dashboard import goal_reminder

    slot_key = bots[target]["slot_key"]
    unavailable = await goal_reminder._load_thread(state, slot_key)
    slot = state.get_slot(slot_key)
    if unavailable or slot is None or not mate_thread_wakes.is_crewmate_thread(slot):
        return await answer(
            "target_unavailable",
            _deny(409, "target_unavailable", f"{target}'s thread cannot be opened right now"),
            target,
            hop,
        )
    if not bot_messages.take_rate_slot(state, member, target):
        return await answer(
            "rate_limited",
            _deny(
                429,
                "rate_limited",
                f"you sent {target} {bot_messages.PAIR_LIMIT} messages in the last "
                f"{bot_messages.PAIR_WINDOW_SECS // 60} minutes, the most allowed; wait or tell the user",
            ),
            target,
            hop,
        )
    busy = not mate_thread_wakes._idle(slot)
    replaced = bot_messages.enqueue(state, slot_key, member, message, hop)
    mate_thread_wakes.schedule_flush(state, slot_key)
    outcome = "queued" if busy else "sent"
    return await answer(
        outcome,
        web.json_response(
            {
                "ok": True,
                "to": target,
                "status": outcome,
                "hop": hop,
                "replaced_your_earlier_message": replaced,
            }
        ),
        target,
        hop,
    )
