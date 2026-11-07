"""Bot-to-Bot messages: one Bot hands another Bot a message by name.

``crew-mode.md``, "Bot messaging". The ``bot_list`` and ``bot_send`` tools
(``kirocrew-mate``) reach the gateway through
:mod:`kiro_crew.dashboard.handlers.member_messages`; this module holds what that
handler and the wake path share:

* **Who can be reached**: a Bot on this gateway is a configured crew member with a
  pinned thread (:func:`reachable`). Callers say a short name, never an id.
* **The limits**: a message is at most :data:`MAX_MESSAGE_CHARS` long, a chain of
  replies stops at :data:`MAX_HOPS`, and one sender may send one target at most
  :data:`PAIR_LIMIT` messages in :data:`PAIR_WINDOW_SECS`.
* **The queue**: one pending message per sender, per target thread, delivered by
  :mod:`kiro_crew.dashboard.mate_thread_wakes` when the target's turn ends.

The text a target reads is built in ``mate_thread_wakes`` (``bot_message_prompt``).
Per-gateway state lives on the dashboard state object, never in module globals.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from kiro_crew import members as members_mod

#: A chain A -> B -> C -> D is three hops; the fourth send is refused.
MAX_HOPS = 3

#: Longest message a Bot may send, in characters. A longer one is refused, not cut.
MAX_MESSAGE_CHARS = 2000

#: Messages one sender may send one target inside the window.
PAIR_LIMIT = 6
PAIR_WINDOW_SECS = 600

#: How much of a Bot's description ``bot_list`` shows.
DESCRIPTION_CHARS = 160

#: The slot attribute that holds the hop of the message that started the thread's
#: running turn. Set when the wake starts the turn and cleared when any turn ends,
#: so a send made in that turn is one hop further (:func:`hop_of`).
HOP_ATTR = "_bot_message_hop"

#: The one clock, so a test can freeze it.
_now = time.monotonic


def enabled(cfg: Any) -> bool:
    """Whether Bot messaging is on in *cfg* (``agent.bot_messaging``).

    A config whose ``agent`` section failed to load reads as off, the direction
    that cannot send what the operator meant to stop (as ``members.crew_panel_enabled``).
    """
    from kiro_crew.config.loader import DEGRADED_WHOLE_CONFIG

    if cfg.degraded_sections & {DEGRADED_WHOLE_CONFIG, "agent"}:
        return False
    return bool(cfg.agent.bot_messaging)


def reachable(cfg: Any) -> dict[str, dict[str, str]]:
    """Every Bot a message can reach, as ``{name: {"slot_key", "description"}}``.

    A Bot is a configured crew member whose pinned thread is bound. Blocking file
    reads (one binding per name): call it off the event loop.
    """
    found: dict[str, dict[str, str]] = {}
    for name, entry in cfg.agents.items():
        if not members_mod.is_dispatchable_member_name(name):
            continue
        try:
            binding = members_mod.read_dm_binding(members_mod.member_slug(name, cfg))
        except members_mod.MemberSlugError:
            continue
        if not binding or binding.get("member") != name or not binding.get("slot_key"):
            continue
        description = " ".join(str(getattr(entry, "description", "") or "").split())
        found[name] = {"slot_key": binding["slot_key"], "description": description}
    return found


def resolve(name: Any, names: list[str]) -> str | None:
    """The Bot *name* means among *names*: exact, else the one case-insensitive match."""
    if not isinstance(name, str):
        return None
    if name in names:
        return name
    same = [n for n in names if n.casefold() == name.strip().casefold()]
    return same[0] if len(same) == 1 else None


def hop_of(slot: Any) -> int:
    """The hop of the Bot message that started *slot*'s running turn; 0 for any other turn."""
    hop = getattr(slot, HOP_ATTR, 0)
    return hop if isinstance(hop, int) else 0


def _queues(state: Any) -> dict[str, dict[str, dict[str, Any]]]:
    queues = getattr(state, "_bot_messages", None)
    if not isinstance(queues, dict):
        queues = {}
        state._bot_messages = queues
    return queues


def has_pending(state: Any, key: str) -> bool:
    """Whether thread *key* holds a message not yet delivered."""
    return bool(_queues(state).get(key))


def enqueue(state: Any, key: str, sender: str, text: str, hop: int) -> bool:
    """Hold *text* from *sender* for thread *key*; True when it replaced an earlier one.

    One entry per sender: a newer message replaces the sender's undelivered one,
    and the count of replaced ones travels with it so the target is told.
    """
    queue = _queues(state).setdefault(key, {})
    prior = queue.pop(sender, None)
    queue[sender] = {
        "from": sender,
        "text": text,
        "hop": hop,
        "replaced": (prior["replaced"] + 1) if prior else 0,
    }
    return prior is not None


def take_next(state: Any, key: str) -> dict[str, Any] | None:
    """Remove and return the oldest message held for thread *key*, else ``None``."""
    queue = _queues(state).get(key)
    if not queue:
        return None
    entry = queue.pop(next(iter(queue)))
    if not queue:
        del _queues(state)[key]
    return entry


def take_rate_slot(state: Any, sender: str, target: str) -> bool:
    """Count one send from *sender* to *target*; False when the pair is at its limit."""
    sends = getattr(state, "_bot_message_sends", None)
    if not isinstance(sends, dict):
        sends = {}
        state._bot_message_sends = sends
    window = sends.setdefault((sender, target), deque())
    now = _now()
    while window and now - window[0] >= PAIR_WINDOW_SECS:
        window.popleft()
    if len(window) >= PAIR_LIMIT:
        return False
    window.append(now)
    return True
