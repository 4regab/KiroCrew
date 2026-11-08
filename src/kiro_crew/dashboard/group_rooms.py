"""Group chat rooms: the user and two to six Bots talk in one room the gateway owns.

``crew-mode.md``, "Group chat rooms". The ``room_list`` and ``room_send`` tools
(``kirocrew-mate``) reach the gateway through
:mod:`kiro_crew.dashboard.handlers.member_rooms`, and the user's own routes are
:mod:`kiro_crew.dashboard.handlers.rooms`; this module holds what they share:

* **The room**: an id, a name, the member Bots by configured name, and a log of
  messages. Bots do not share transcripts. The room lives only here, one file per
  room under ``member-records/chat-rooms/`` (the gateway-only directory the sandbox
  masks), so no Bot can read it as a file.
* **The wake**: a user message wakes every member, a Bot's message wakes every other
  member. The woken Bot reads the new messages as one hidden turn in its own thread
  (:func:`kiro_crew.dashboard.mate_thread_wakes.room_wake_prompt`), decides for itself
  whether to answer, and answers with ``room_send``. Ending the turn without sending
  is a normal move.
* **The limits**: a Bot posts at most :data:`TURN_CAP` messages per turn. A reply to a
  reply is one level deeper (:data:`WRAP_DEPTH`, :data:`MAX_DEPTH`), so a room cannot
  loop: past the first :data:`WRAP_DEPTH` levels the wake tells the Bot the room is
  wrapping up, and a message at :data:`MAX_DEPTH` wakes nobody.

Per-gateway state lives on the dashboard state object, never in module globals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any

from kiro_crew import member_records
from kiro_crew.atomic_write import atomic_write, read_json_or
from kiro_crew.dashboard import bot_messages

logger = logging.getLogger(__name__)

#: A room holds the user plus this many Bots.
MIN_MEMBERS = 2
MAX_MEMBERS = 6

#: Rooms one gateway keeps. A room's log is never trimmed.
MAX_ROOMS = 20
MAX_NAME_CHARS = 60

#: Messages one ``GET /api/rooms/{id}`` page returns (the newest first); an older page is
#: asked for with ``before``. A page size, not a cap: nothing in the log is dropped.
PAGE_SIZE = 200
MAX_PAGE_SIZE = 1000

#: Messages one Bot may post in one turn.
TURN_CAP = 3

#: A message typed by the user is depth 0 and a Bot's reply is one deeper than the
#: message that woke it. From :data:`WRAP_DEPTH` on, the wake says the room is wrapping
#: up. A message at :data:`MAX_DEPTH` is posted but wakes nobody, so the chain ends.
WRAP_DEPTH = bot_messages.MAX_HOPS
MAX_DEPTH = WRAP_DEPTH + 1

#: Posts one Bot may make to one room inside the window (the window is bot_messages').
POST_LIMIT = 20

#: New messages one wake shows; older ones are counted, not shown.
WAKE_MAX_MESSAGES = 20

#: Longest message text one wake shows a Bot. The room keeps the whole text; a longer one
#: is cut for the wake only, with a note, so one long message cannot fill a Bot's context.
WAKE_MESSAGE_CHARS = 4000

#: The "from" of a message the user typed. A Bot's is its configured name.
USER = ""

#: Slot attributes for the running turn: the depth of the wake that started it and how
#: many messages it has posted. Set when a wake starts the turn, cleared when any turn
#: ends (:func:`end_turn`).
DEPTH_ATTR = "_room_depth"
SENT_ATTR = "_room_sent"

_ID_RE = re.compile(r"[0-9a-f]{16}")
_DIR_NAME = "chat-rooms"


class RoomError(Exception):
    """A request the room rules refuse: an HTTP *status*, a short *code* and a sentence."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def rooms_dir() -> Path:
    """The gateway-only directory the room files live in (not created here)."""
    return member_records.records_root() / _DIR_NAME


def _read_all() -> dict[str, dict[str, Any]]:
    """Every readable room file, keyed by id. Blocking file reads: call off the event loop."""
    found: dict[str, dict[str, Any]] = {}
    try:
        paths = sorted(rooms_dir().glob("*.json"))
    except OSError:
        return found
    for path in paths:
        data = read_json_or(path, None, logger=logger, what="chat room")
        if (
            isinstance(data, dict)
            and _ID_RE.fullmatch(str(data.get("id", "")))
            and data["id"] == path.stem
            and isinstance(data.get("name"), str)
            and isinstance(data.get("members"), list)
            and isinstance(data.get("messages"), list)
        ):
            data.setdefault("seq", 0)
            data.setdefault("cursors", {})
            found[data["id"]] = data
    return found


def _write(room: dict[str, Any]) -> None:
    """Save one room. Blocking file IO: call off the event loop."""
    path = rooms_dir() / f"{room['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(room, ensure_ascii=False))


def _remove(room_id: str) -> None:
    (rooms_dir() / f"{room_id}.json").unlink(missing_ok=True)


def _lock(state: Any) -> asyncio.Lock:
    lock = getattr(state, "_group_rooms_lock", None)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        state._group_rooms_lock = lock
    return lock


async def load(state: Any) -> dict[str, dict[str, Any]]:
    """The gateway's rooms by id, read from disk the first time."""
    rooms: dict[str, dict[str, Any]] | None = getattr(state, "_group_rooms", None)
    if isinstance(rooms, dict):
        return rooms
    async with _lock(state):
        rooms = getattr(state, "_group_rooms", None)
        if not isinstance(rooms, dict):
            rooms = await asyncio.to_thread(_read_all)
            state._group_rooms = rooms
    return rooms


def find(rooms: dict[str, dict[str, Any]], ref: Any, member: str | None = None) -> str | None:
    """The id of the room *ref* names (its id, its name, or its one case-insensitive name).

    With *member*, only rooms that Bot belongs to are considered, so a Bot cannot probe
    rooms it is not in.
    """
    if not isinstance(ref, str):
        return None
    visible = {i: r for i, r in rooms.items() if member is None or member in r["members"]}
    if ref in visible:
        return ref
    names = {i: r["name"] for i, r in visible.items()}
    exact = [i for i, n in names.items() if n == ref]
    if len(exact) == 1:
        return exact[0]
    same = [i for i, n in names.items() if n.casefold() == ref.strip().casefold()]
    return same[0] if len(same) == 1 else None


def public(
    room: dict[str, Any], *, messages: bool = False, before: int = 0, limit: int = PAGE_SIZE
) -> dict[str, Any]:
    """The room as the dashboard and the tools see it (never the cursors).

    With *messages*, the newest *limit* messages older than seq *before* (all of the log
    when *before* is 0), oldest first, and ``has_more`` when older ones remain.
    """
    out: dict[str, Any] = {
        "id": room["id"],
        "name": room["name"],
        "members": list(room["members"]),
        "created": room.get("created", 0),
        "seq": room["seq"],
    }
    if messages:
        older = [m for m in room["messages"] if not before or m["seq"] < before]
        page = older[-limit:]
        out["messages"] = [dict(m) for m in page]
        out["has_more"] = len(older) > len(page)
    return out


async def create(
    state: Any, name: Any, member_refs: Any, reachable: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Make a room named *name* of the Bots *member_refs* names; raise :class:`RoomError`.

    *reachable* is :func:`bot_messages.reachable`: names resolve exactly or by their one
    case-insensitive match, and a name that matches no Bot is refused with the valid ones.
    """
    clean = " ".join(name.split()) if isinstance(name, str) else ""
    if not clean or len(clean) > MAX_NAME_CHARS:
        raise RoomError(400, "invalid_name", f"name the room in 1 to {MAX_NAME_CHARS} characters")
    if not isinstance(member_refs, list) or not all(isinstance(m, str) for m in member_refs):
        raise RoomError(400, "invalid_members", "members must be a list of Bot names")
    names = list(reachable)
    members: list[str] = []
    for ref in member_refs:
        found = bot_messages.resolve(ref, names)
        if found is None:
            raise RoomError(
                404,
                "unknown_bot",
                f"no Bot named {ref.strip()[:80]!r}. Bots in a room: "
                + (", ".join(sorted(names)) or "none"),
            )
        if found not in members:
            members.append(found)
    if len(members) < MIN_MEMBERS:
        raise RoomError(400, "too_few_members", f"a room needs at least {MIN_MEMBERS} Bots")
    if len(members) > MAX_MEMBERS:
        raise RoomError(400, "too_many_members", f"a room holds at most {MAX_MEMBERS} Bots")
    rooms = await load(state)
    async with _lock(state):
        if len(rooms) >= MAX_ROOMS:
            raise RoomError(409, "too_many_rooms", f"this gateway keeps at most {MAX_ROOMS} rooms")
        if any(r["name"].casefold() == clean.casefold() for r in rooms.values()):
            raise RoomError(409, "name_in_use", f"a room named {clean!r} already exists")
        room: dict[str, Any] = {
            "id": secrets.token_hex(8),
            "name": clean,
            "members": members,
            "created": time.time(),
            "seq": 0,
            "messages": [],
            "cursors": {},
        }
        await asyncio.to_thread(_write, room)
        rooms[room["id"]] = room
    return room


async def delete(state: Any, room_id: str) -> bool:
    """Remove a room and its file; False when no such room."""
    rooms = await load(state)
    async with _lock(state):
        if room_id not in rooms:
            return False
        await asyncio.to_thread(_remove, room_id)
        del rooms[room_id]
    return True


async def add_message(
    state: Any, room_id: str, sender: str, text: str, depth: int
) -> dict[str, Any]:
    """Append one message to the room's log and save it; return the message.

    ponytail: the member cursors (what each Bot has been shown) are saved with the room
    on every post, not on every wake, so a restart can show a Bot a few messages twice.
    Pending wakes are in memory like the Bot-message queue; a restart drops them.
    """
    rooms = await load(state)
    async with _lock(state):
        room = rooms.get(room_id)
        if room is None:
            raise RoomError(404, "unknown_room", "that room no longer exists")
        room["seq"] += 1
        message = {
            "seq": room["seq"],
            "from": sender,
            "text": text,
            "depth": depth,
            "ts": time.time(),
        }
        room["messages"].append(message)
        await asyncio.to_thread(_write, room)
    return message


# -- the turn ---------------------------------------------------------------- #


def depth_of(slot: Any) -> int:
    """The depth of the wake that started *slot*'s running turn; 0 for any other turn."""
    depth = getattr(slot, DEPTH_ATTR, 0)
    return depth if isinstance(depth, int) else 0


def sent_in_turn(slot: Any) -> int:
    """How many room messages *slot*'s running turn has posted."""
    sent = getattr(slot, SENT_ATTR, 0)
    return sent if isinstance(sent, int) else 0


def begin_turn(slot: Any, depth: int) -> None:
    """Mark *slot*'s next turn as started by a room wake of *depth*."""
    setattr(slot, DEPTH_ATTR, depth)
    setattr(slot, SENT_ATTR, 0)


def end_turn(slot: Any) -> None:
    """Whatever turn just ended, the next one starts clean."""
    setattr(slot, DEPTH_ATTR, 0)
    setattr(slot, SENT_ATTR, 0)


def count_post(slot: Any) -> None:
    setattr(slot, SENT_ATTR, sent_in_turn(slot) + 1)


# -- the wake ---------------------------------------------------------------- #


def _wakes(state: Any) -> dict[str, dict[str, int]]:
    """Per thread key, the rooms with something new to show it and the depth of the wake."""
    wakes = getattr(state, "_room_wakes", None)
    if not isinstance(wakes, dict):
        wakes = {}
        state._room_wakes = wakes
    return wakes


def has_pending(state: Any, key: str) -> bool:
    """Whether thread *key* is owed a room wake."""
    return bool(_wakes(state).get(key))


def queue_wake(state: Any, key: str, room_id: str, depth: int) -> None:
    """Owe thread *key* a wake for *room_id*; the deepest owed depth is kept."""
    owed = _wakes(state).setdefault(key, {})
    owed[room_id] = max(owed.get(room_id, 0), depth)


def mentions(text: str, name: str) -> bool:
    """Whether *text* names the Bot *name* with ``@``, or the whole room with ``@everyone``."""
    pattern = rf"(?<!\w)@(?:{re.escape(name)}|everyone)(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def take_next(state: Any, key: str, member: str) -> dict[str, Any] | None:
    """Remove and return what to show *member* in its next room wake, else ``None``.

    The entry names the room, the members, the new messages from others oldest first
    (at most :data:`WAKE_MAX_MESSAGES`), how many older ones are left out, the depth of
    the wake and whether a message names the Bot. The Bot's cursor moves to the end of
    the log, so a message is shown to it once.
    """
    owed = _wakes(state).get(key)
    rooms = getattr(state, "_group_rooms", None)
    while owed and isinstance(rooms, dict):
        room_id = next(iter(owed))
        depth = owed.pop(room_id)
        room = rooms.get(room_id)
        if room is None or member not in room["members"]:
            continue
        cursor = room["cursors"].get(member, 0)
        new = [m for m in room["messages"] if m["seq"] > cursor and m["from"] != member]
        room["cursors"][member] = room["seq"]
        if not new:
            continue
        shown = new[-WAKE_MAX_MESSAGES:]
        if not owed:
            _wakes(state).pop(key, None)
        return {
            "room": room["name"],
            "me": member,
            "members": list(room["members"]),
            "messages": [dict(m) for m in shown],
            "omitted": len(new) - len(shown),
            "depth": depth,
            "mentioned": any(mentions(m["text"], member) for m in shown),
        }
    _wakes(state).pop(key, None)
    return None


async def wake_members(
    state: Any, cfg: Any, room_id: str, sender: str, message: dict[str, Any]
) -> list[str]:
    """Owe each other member a wake for *message*; return the Bots whose thread is owed one.

    A message at :data:`MAX_DEPTH` wakes nobody: it is in the log and the next wake of
    any Bot shows it, but it cannot start another round. A member whose thread cannot be
    opened right now is skipped.
    """
    from kiro_crew.dashboard import goal_reminder, mate_thread_wakes

    if message["depth"] >= MAX_DEPTH:
        return []
    room = (await load(state)).get(room_id)
    if room is None:
        return []
    bots = await asyncio.to_thread(bot_messages.reachable, cfg)
    woken: list[str] = []
    for name in room["members"]:
        if name == sender or name not in bots:
            continue
        key = bots[name]["slot_key"]
        if await goal_reminder._load_thread(state, key):
            continue
        queue_wake(state, key, room_id, message["depth"])
        mate_thread_wakes.schedule_flush(state, key)
        woken.append(name)
    return woken
