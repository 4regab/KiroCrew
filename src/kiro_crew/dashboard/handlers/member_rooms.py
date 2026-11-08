"""HTTP routes behind the ``room_list`` and ``room_send`` tools: a Bot takes part in a room.

MCP-only, under the strict ``/api/member/self`` prefix, like the Bot-message tools
(:mod:`handlers.member_messages`): the poster is the Bot whose pinned thread the
verified caller's slot is, never a name from the body, and the tools are admitted on any
turn in that thread (a message from the user, a check-in, a room wake), but not from a
channel, a schedule, a subagent or an app. ``crew-mode.md``, "Group chat rooms".

* ``POST /api/member/self/rooms/list`` -- the rooms the Bot is in.
* ``POST /api/member/self/rooms/send`` -- ``{room, message, reply_to?}``: post text to the room and
  wake the other members. Answers at once.

Every post, refused ones included, writes one ``member/room_post`` event to the poster's
member log and one audit line. A Bot reads a room only through the wake the gateway
starts in its own thread, never another Bot's memory or files.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from kiro_crew import member_goals
from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.dashboard import bot_events, bot_messages, group_rooms
from kiro_crew.dashboard.handlers.guide import _audit
from kiro_crew.dashboard.handlers.member_goals import _agent_body, _agent_member, _deny

#: Operation names, as audited. Absent from ``handlers.guide._USER_TURN_OPERATIONS``
#: on purpose: a Bot answering a room runs in a turn the user did not send.
OP_ROOM_LIST = "member.room_list"
OP_ROOM_SEND = "member.room_send"

#: Longest room name echoed into the crew log for a post to a name that matched no room.
_LOGGED_NAME_CHARS = 80


async def _caller(
    request: web.Request, operation: str
) -> tuple[tuple[str, str, str], KiroCrewConfig] | web.Response:
    """``((session_key, slug, member), config)`` of the calling Bot, or the refusal."""
    who = await _agent_member(request, operation)
    if isinstance(who, web.Response):
        return who
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if not bot_messages.enabled(cfg, "group_rooms"):
        _audit(who[0], operation, "denied", "group rooms are off")
        return _deny(
            403,
            "group_rooms_off",
            "Group chat rooms are turned off on this gateway (agent.group_rooms); tell the user instead",
        )
    return who, cfg


async def api_member_self_room_list(request: web.Request) -> web.Response:
    """POST /api/member/self/rooms/list — the rooms the calling Bot is in."""
    got = await _caller(request, OP_ROOM_LIST)
    if isinstance(got, web.Response):
        return got
    (sk, _slug, member), _cfg = got
    rooms = await group_rooms.load(request.app["state"])
    rows = [
        {
            "name": room["name"],
            "others": [n for n in room["members"] if n != member],
            "messages": len(room["messages"]),
        }
        for room in rooms.values()
        if member in room["members"]
    ]
    _audit(sk, OP_ROOM_LIST, "ok", "")
    return web.json_response({"ok": True, "rooms": rows})


async def api_member_self_room_send(request: web.Request) -> web.Response:
    """POST /api/member/self/rooms/send — ``{room, message}``: post to a room."""
    got = await _caller(request, OP_ROOM_SEND)
    if isinstance(got, web.Response):
        return got
    (sk, slug, member), cfg = got
    body = await _agent_body(request)
    ref, message, reply_to = body.get("room"), body.get("message"), body.get("reply_to")
    if not isinstance(ref, str) or not ref.strip():
        return _deny(400, "invalid_room", "name the room to post in (room_list shows the names)")
    if not isinstance(message, str) or not message.strip():
        return _deny(400, "invalid_message", "the message is empty")
    if reply_to is not None and (isinstance(reply_to, bool) or not isinstance(reply_to, int)):
        return _deny(400, "invalid_reply", "reply_to is a message number from the room")
    chars = len(message)

    async def answer(
        outcome: str, response: web.Response, room: str = "", depth: int = 0
    ) -> web.Response:
        """Write the post's one crew-log event and audit line, then reply."""
        from kiro_crew import eventlog_hooks
        from kiro_crew.eventlog.types import MEMBER_ROOM_POST

        await asyncio.to_thread(
            eventlog_hooks.emit,
            slug,
            member,
            MEMBER_ROOM_POST,
            {
                "room": (room or ref)[:_LOGGED_NAME_CHARS],
                "chars": chars,
                "depth": depth,
                "outcome": outcome,
            },
        )
        _audit(sk, OP_ROOM_SEND, "ok" if response.status == 200 else "denied", outcome)
        # The Bot's own chat shows what it posted, or why it was not posted.
        event = (
            bot_events.room_post(member, room, message)
            if outcome == "posted"
            else bot_events.refused(member, outcome, room=room or ref)
        )
        bot_events.record_for_session(request.app["state"], sk, event)
        return response

    if chars > bot_messages.MAX_MESSAGE_CHARS:
        return await answer(
            "message_too_long",
            _deny(
                400,
                "message_too_long",
                f"the message is {chars} characters; the limit is {bot_messages.MAX_MESSAGE_CHARS}. "
                "Shorten it and post again",
            ),
        )
    state = request.app["state"]
    rooms = await group_rooms.load(state)
    room_id = group_rooms.find(rooms, ref, member)
    if room_id is None:
        mine = sorted(r["name"] for r in rooms.values() if member in r["members"])
        return await answer(
            "unknown_room",
            _deny(
                404,
                "unknown_room",
                f"no room named {ref.strip()[:_LOGGED_NAME_CHARS]!r}. Rooms you are in: "
                + (", ".join(mine) or "none"),
            ),
        )
    room_name = rooms[room_id]["name"]
    # No await from here to the counter, so two posts in one turn cannot both pass the cap.
    from kiro_crew.dashboard.session_control import caller_slot_key

    slot = state.get_slot(caller_slot_key(state, sk))
    if slot is None:
        return await answer(
            "thread_unavailable",
            _deny(409, "thread_unavailable", "your thread cannot be opened right now"),
            room_name,
        )
    depth = group_rooms.depth_of(slot) + 1
    if group_rooms.sent_in_turn(slot) >= group_rooms.TURN_CAP:
        return await answer(
            "turn_cap",
            _deny(
                429,
                "turn_cap",
                f"you already posted {group_rooms.TURN_CAP} messages this turn, the most allowed; "
                "end your turn",
            ),
            room_name,
            depth,
        )
    if depth > group_rooms.MAX_DEPTH:
        return await answer(
            "round_limit",
            _deny(
                409,
                "round_limit",
                "this room's round of replies is over; end your turn, or tell the user instead",
            ),
            room_name,
            depth,
        )
    if not bot_messages.take_rate_slot(state, member, f"room:{room_id}", group_rooms.POST_LIMIT):
        return await answer(
            "rate_limited",
            _deny(
                429,
                "rate_limited",
                f"you posted {group_rooms.POST_LIMIT} messages to this room in the last "
                f"{bot_messages.PAIR_WINDOW_SECS // 60} minutes, the most allowed; wait or tell the user",
            ),
            room_name,
            depth,
        )
    group_rooms.count_post(slot)
    text = member_goals.scrub_text(message).strip()
    try:
        posted = await group_rooms.add_message(state, room_id, member, text, depth, reply_to)
    except group_rooms.RoomError as exc:
        return await answer(exc.code, _deny(exc.status, exc.code, str(exc)), room_name, depth)
    woken = await group_rooms.wake_members(state, cfg, room_id, member, posted)
    return await answer(
        "posted",
        web.json_response(
            {"ok": True, "room": room_name, "seq": posted["seq"], "depth": depth, "woke": woken}
        ),
        room_name,
        depth,
    )
