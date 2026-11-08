"""Owner-only routes for group chat rooms: ``/api/rooms``.

The user's half of :mod:`kiro_crew.dashboard.group_rooms`; the Bots' half is
:mod:`handlers.member_rooms`. ``crew-mode.md``, "Group chat rooms".

* ``GET /api/rooms`` -- every room, without its messages.
* ``POST /api/rooms`` -- ``{name, members}``: make a room of two to six Bots.
* ``GET /api/rooms/{room_id}`` -- one room with its messages.
* ``POST /api/rooms/{room_id}/messages`` -- ``{message}``: the user speaks; every member
  Bot is woken.
* ``DELETE /api/rooms/{room_id}`` -- remove the room and its log.

Owner-only: a collaborator's or an app's token is refused, and a Bot reaches rooms only
through its own tools.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from kiro_crew.config.loader import KiroCrewConfig
from kiro_crew.dashboard import bot_messages, group_rooms
from kiro_crew.dashboard.handlers._shared_access import require_owner_dashboard_request


def _fail(exc: group_rooms.RoomError) -> web.Response:
    return web.json_response({"error": str(exc), "code": exc.code}, status=exc.status)


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _switch_off(cfg: KiroCrewConfig) -> web.Response | None:
    if bot_messages.enabled(cfg, "group_rooms"):
        return None
    return web.json_response(
        {"error": "Group chat rooms are turned off (agent.group_rooms)", "code": "group_rooms_off"},
        status=403,
    )


async def api_rooms_list(request: web.Request) -> web.Response:
    """GET /api/rooms — every room, without its messages."""
    if (denied := await require_owner_dashboard_request(request, "rooms.list")) is not None:
        return denied
    rooms = await group_rooms.load(request.app["state"])
    return web.json_response({"rooms": [group_rooms.public(r) for r in rooms.values()]})


async def api_rooms_create(request: web.Request) -> web.Response:
    """POST /api/rooms — ``{name, members}``: make a room."""
    if (denied := await require_owner_dashboard_request(request, "rooms.create")) is not None:
        return denied
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if (off := _switch_off(cfg)) is not None:
        return off
    body = await _body(request)
    reachable = await asyncio.to_thread(bot_messages.reachable, cfg)
    try:
        room = await group_rooms.create(
            request.app["state"], body.get("name"), body.get("members"), reachable
        )
    except group_rooms.RoomError as exc:
        return _fail(exc)
    return web.json_response({"ok": True, "room": group_rooms.public(room)}, status=201)


async def api_room_get(request: web.Request) -> web.Response:
    """GET /api/rooms/{room_id} — one room with its messages."""
    if (denied := await require_owner_dashboard_request(request, "rooms.get")) is not None:
        return denied
    rooms = await group_rooms.load(request.app["state"])
    room = rooms.get(request.match_info["room_id"])
    if room is None:
        return web.json_response({"error": "no such room", "code": "unknown_room"}, status=404)
    page = {"before": 0, "limit": group_rooms.PAGE_SIZE}
    for name, low, high in (("before", 0, None), ("limit", 1, group_rooms.MAX_PAGE_SIZE)):
        raw = request.query.get(name)
        if raw is None:
            continue
        if not raw.isdecimal() or int(raw) < low or (high is not None and int(raw) > high):
            return web.json_response(
                {"error": f"{name} must be a whole number from {low}", "code": "invalid_page"},
                status=400,
            )
        page[name] = int(raw)
    return web.json_response({"room": group_rooms.public(room, messages=True, **page)})


async def api_room_post(request: web.Request) -> web.Response:
    """POST /api/rooms/{room_id}/messages — ``{message}``: the user speaks to the room."""
    if (denied := await require_owner_dashboard_request(request, "rooms.post")) is not None:
        return denied
    cfg = await asyncio.to_thread(KiroCrewConfig.load)
    if (off := _switch_off(cfg)) is not None:
        return off
    state = request.app["state"]
    room_id = request.match_info["room_id"]
    if room_id not in await group_rooms.load(state):
        return web.json_response({"error": "no such room", "code": "unknown_room"}, status=404)
    message = (await _body(request)).get("message")
    if not isinstance(message, str) or not message.strip():
        return web.json_response(
            {"error": "the message is empty", "code": "invalid_message"}, status=400
        )
    try:
        # The user's own words are kept as typed, whatever their length (the request
        # size limit of the gateway still applies); only a Bot's text is scrubbed and capped.
        posted = await group_rooms.add_message(state, room_id, group_rooms.USER, message.strip(), 0)
    except group_rooms.RoomError as exc:
        return _fail(exc)
    woken = await group_rooms.wake_members(state, cfg, room_id, group_rooms.USER, posted)
    return web.json_response({"ok": True, "message": posted, "woke": woken})


async def api_room_delete(request: web.Request) -> web.Response:
    """DELETE /api/rooms/{room_id} — remove the room and its log."""
    if (denied := await require_owner_dashboard_request(request, "rooms.delete")) is not None:
        return denied
    if not await group_rooms.delete(request.app["state"], request.match_info["room_id"]):
        return web.json_response({"error": "no such room", "code": "unknown_room"}, status=404)
    return web.json_response({"ok": True})
