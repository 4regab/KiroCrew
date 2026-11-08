"""Bot event rows: what a Bot sent, received or was refused, drawn in its own chat.

A Bot message or a room wake reaches a Bot as a hidden turn, so its own transcript shows
nothing between its reply and the user's next message. These rows fill that gap. Each is
one assistant-role system notice (``meta.kind == "bot_event"``, the shape of the
``memory_change`` notice) written to the Bot's own slot with ``slot.append``, so it is saved
with the transcript and a reload or a gateway restart draws it again. The facts ride in
``meta.bot_event``; ``content`` is the English fallback line.

The text in a row is DATA from a peer, kept as plain text: the dashboard draws it as text
and never as markdown, never as the user's words and never as the Bot's own. A row is
written from the places that already log ``member/bot_message`` and ``member/room_post``
(the sender's side and a refusal) and from the wake that hands the text to the receiver.
Writing a row never raises and never changes the outcome of the send.

Decision: ``docs/decisions/2026-11-08-the-bot-chat-draws-bot-event-rows.md``.
"""

from __future__ import annotations

import logging
from typing import Any

from kiro_crew.dashboard.system_notices import BOT_EVENT_KIND

logger = logging.getLogger(__name__)

#: The key under ``meta`` that holds the event's facts.
META_KEY = "bot_event"

#: Event types.
SENT = "sent"
RECEIVED = "received"
ROOM_POST = "room_post"
ROOM_WAKE = "room_wake"
REFUSED = "refused"

#: Longest text one row keeps. A Bot message is at most 2,000 characters already; a room
#: wake shows each message up to this many, and the room keeps the whole text.
TEXT_CHARS = 2000
WAKE_TEXT_CHARS = 1000

#: Most messages one room-wake row lists (the wake shows no more).
MAX_WAKE_MESSAGES = 20

_NAME_CHARS = 80


def _clip(value: Any, cap: int) -> str:
    """*value* scrubbed of secrets (as the wake does) and cut to *cap* characters."""
    from kiro_crew.member_goals import scrub_text

    return scrub_text(value).strip()[:cap] if isinstance(value, str) else ""


def _name(value: Any) -> str:
    return " ".join(value.split())[:_NAME_CHARS] if isinstance(value, str) else ""


def sent(me: str, peer: str, text: str, *, queued: bool) -> dict[str, Any]:
    """*me* sent *text* to Bot *peer* (``queued`` when *peer* was busy)."""
    return {
        "type": SENT,
        "me": _name(me),
        "peer": _name(peer),
        "text": _clip(text, TEXT_CHARS),
        "queued": bool(queued),
    }


def received(me: str, peer: str, text: str, *, hop: int) -> dict[str, Any]:
    """Bot *peer* sent *me* *text*; the wake that shows it is this turn."""
    return {
        "type": RECEIVED,
        "me": _name(me),
        "peer": _name(peer),
        "text": _clip(text, TEXT_CHARS),
        "hop": int(hop),
    }


def room_post(me: str, room: str, text: str) -> dict[str, Any]:
    """*me* posted *text* in room *room*."""
    return {
        "type": ROOM_POST,
        "me": _name(me),
        "room": _name(room),
        "text": _clip(text, TEXT_CHARS),
    }


def room_wake(me: str, room: str, messages: list[dict[str, Any]], omitted: int) -> dict[str, Any]:
    """*me* was shown *messages* of room *room*, and *omitted* older ones were not shown."""
    shown = [
        {"from": _name(m.get("from")), "text": _clip(m.get("text"), WAKE_TEXT_CHARS)}
        for m in messages[:MAX_WAKE_MESSAGES]
    ]
    return {
        "type": ROOM_WAKE,
        "me": _name(me),
        "room": _name(room),
        "messages": shown,
        "omitted": max(0, int(omitted)),
    }


def refused(me: str, reason: str, *, peer: str = "", room: str = "") -> dict[str, Any]:
    """A send to Bot *peer* or a post to room *room* was refused; *reason* is the gateway's code."""
    return {
        "type": REFUSED,
        "me": _name(me),
        "peer": _name(peer),
        "room": _name(room),
        "reason": _clip(reason, 40),
    }


def fallback_text(event: dict[str, Any]) -> str:
    """The English line a client without the card (or an older one) shows."""
    kind = event.get("type")
    if kind == SENT:
        return f"{event['me']} sent a message to {event['peer']}"
    if kind == RECEIVED:
        return f"Message from {event['peer']}"
    if kind == ROOM_POST:
        return f"{event['me']} posted in {event['room']}"
    if kind == ROOM_WAKE:
        return f"Group chat {event['room']}: {len(event['messages']) + event['omitted']} new"
    target = event.get("peer") or event.get("room")
    return f"Not sent to {target}: {event.get('reason', '')}"


def record(slot: Any, event: dict[str, Any]) -> bool:
    """Write *event* as a row in *slot*'s transcript; ``False`` when it could not. Never raises."""
    if slot is None:
        return False
    try:
        slot.append(
            "assistant",
            fallback_text(event),
            "msg msg-a",
            meta={"kind": BOT_EVENT_KIND, META_KEY: event},
        )
        return True
    except Exception:  # noqa: BLE001 -- a missing row must not fail the send or the wake
        logger.debug("bot event row not written for %s", getattr(slot, "key", "?"), exc_info=True)
        return False


def record_for_session(state: Any, session_key: str, event: dict[str, Any]) -> bool:
    """Write *event* to the slot the verified caller's *session_key* belongs to."""
    from kiro_crew.dashboard.session_control import caller_slot_key

    try:
        slot = state.get_slot(caller_slot_key(state, session_key))
    except Exception:  # noqa: BLE001
        logger.debug("bot event slot lookup failed", exc_info=True)
        return False
    return record(slot, event)
