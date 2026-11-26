"""Per-channel writers gate for Discord view channels.

A *view channel* is a server channel the operator shows to people who may read it
but must never drive the agent from. ``discord.view_channels`` lists each one with
the user ids allowed to write there (``writers``). In a view channel, and in every
thread whose parent is a view channel, only a writer can send a message, run a
command (slash or ``!``) or press a button. Everyone else is ignored and audited as
denied, EVEN when the id is on the global ``discord.allowed_user_ids``: that list
says who may use the bot, and this one narrows it per channel.

The gate only ever narrows. A writer who is not on ``allowed_user_ids`` is still
denied by the normal authorization.

Fails closed:

* ``writers`` omitted: the owner only, where the owner is the single id in
  ``allowed_user_ids``. With none or several ids and no ``writers`` nobody writes.
* ``writers`` present but not a list: nobody writes.
* A bare id (not an object) or a duplicated id: the channel is listed, nobody writes.
* An entry with no usable id cannot name a channel and is dropped with a warning.
* A reload whose ``view_channels`` is not a list keeps the last good set.

The parent of a thread is read from Discord (``thread_origin``) and cached, because a
thread's parent never changes. A thread whose parent cannot be read is treated as a
view thread for a sender who is not a writer everywhere.

The gate is one object shared by the transport (message receive) and the dispatcher
(commands and buttons); both call :meth:`ViewPolicy.denies`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kiro_crew.sel import sel

if TYPE_CHECKING:
    from kiro_crew.discord.client import DiscordClient

logger = logging.getLogger(__name__)

#: How often one (user, channel) pair is written to the audit log.
_AUDIT_WINDOW_SECS = 60.0
#: The audit throttle's clock, a module binding so a test sets it.
_now = time.monotonic
#: Cap on remembered audit pairs and thread parents; both reset when full.
_MEMO_MAX = 2048
#: Fewest seconds between two shares into one view channel.
SHARE_MIN_INTERVAL_SECS = 15.0
REPLAY_DEFAULT = 5
REPLAY_MAX = 10


@dataclass(frozen=True)
class ViewChannel:
    """One configured view channel."""

    channel_id: str
    #: ``None`` means the writers were omitted: the owner only.
    writers: frozenset[str] | None
    replay_messages: int = REPLAY_DEFAULT


def _snowflake(value: object) -> str:
    """A Discord id as a string, or ``""`` when it is not all digits."""
    if isinstance(value, bool):
        return ""
    text = str(value).strip() if isinstance(value, (str, int)) else ""
    return text if text.isdigit() else ""


def _writer_ids(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        return frozenset()
    return frozenset(s for s in (_snowflake(v) for v in value) if s)


def _replay_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return REPLAY_DEFAULT
    return max(0, min(REPLAY_MAX, value))


def parse_view_channels(raw: object) -> dict[str, ViewChannel] | None:
    """Read ``discord.view_channels``; ``None`` when it is not a list at all."""
    if not isinstance(raw, list):
        return None
    out: dict[str, ViewChannel] = {}
    dropped = 0
    for entry in raw:
        if isinstance(entry, dict):
            channel_id = _snowflake(entry.get("id"))
            writers = _writer_ids(entry["writers"]) if "writers" in entry else None
            replay = _replay_count(entry.get("replay_messages", REPLAY_DEFAULT))
        else:
            channel_id, writers, replay = _snowflake(entry), frozenset(), REPLAY_DEFAULT
        if not channel_id:
            dropped += 1
        elif channel_id in out:
            logger.warning("discord: a view channel is listed twice; it is read-only for all")
            out[channel_id] = ViewChannel(channel_id, frozenset(), replay)
        else:
            out[channel_id] = ViewChannel(channel_id, writers, replay)
    if dropped:
        logger.warning(
            "discord: %d view_channels entrie(s) have no usable id and were ignored", dropped
        )
    return out


class ViewPolicy:
    """The live set of view channels and who may write in them."""

    def __init__(self) -> None:
        self._channels: dict[str, ViewChannel] = {}
        self._owner = ""
        self._thread_parent: dict[str, str] = {}
        self._audited: dict[tuple[str, str], float] = {}
        self._last_share: dict[str, float] = {}

    @property
    def active(self) -> bool:
        return bool(self._channels)

    def reconfigure(self, section: Any) -> None:
        """Adopt a reloaded ``discord`` section; keep the last good set on a bad one."""
        self.apply(
            getattr(section, "view_channels", None), getattr(section, "allowed_user_ids", None)
        )

    def apply(self, view_channels: object, allowed_user_ids: object) -> None:
        if isinstance(allowed_user_ids, list):
            ids = {s for s in (_snowflake(u) for u in allowed_user_ids) if s}
            self._owner = next(iter(ids)) if len(ids) == 1 else ""
        parsed = parse_view_channels(view_channels)
        if parsed is None:
            logger.warning(
                "discord: view_channels is not a list in the config; keeping the previous "
                "%d view channel(s)",
                len(self._channels),
            )
            return
        self._channels = parsed

    def channel_ids(self) -> list[str]:
        return list(self._channels)

    def allow_share(self, channel_id: str) -> bool:
        """True, and starts the wait, unless a share into this channel was too recent."""
        now = _now()
        last = self._last_share.get(channel_id)
        if last is not None and now - last < SHARE_MIN_INTERVAL_SECS:
            return False
        self._last_share[channel_id] = now
        return True

    def channel(self, channel_id: str) -> ViewChannel | None:
        """The view channel configured under *channel_id* itself (not a thread of it)."""
        return self._channels.get(channel_id)

    def writers_of(self, view: ViewChannel) -> frozenset[str]:
        if view.writers is not None:
            return view.writers
        return frozenset({self._owner}) if self._owner else frozenset()

    def is_writer(self, view: ViewChannel, user_id: str) -> bool:
        return bool(user_id) and user_id in self.writers_of(view)

    async def view_of(self, client: "DiscordClient | None", channel_id: str) -> ViewChannel | None:
        """The view channel *channel_id* is, or sits under; ``None`` when it is neither.

        Raises ``LookupError`` when it is not a configured channel and its parent
        cannot be read, so the caller decides how to fail.
        """
        direct = self._channels.get(channel_id)
        if direct is not None:
            return direct
        parent = self._thread_parent.get(channel_id)
        if parent is None:
            origin = await client.thread_origin(channel_id) if client is not None else None
            if origin is None:
                raise LookupError(channel_id)
            if len(self._thread_parent) >= _MEMO_MAX:
                self._thread_parent.clear()
            parent = self._thread_parent[channel_id] = origin.parent_id
        return self._channels.get(parent)

    async def denies(
        self,
        client: "DiscordClient | None",
        channel_id: str,
        user_id: str,
        *,
        surface: str,
        normal_channel: bool = False,
    ) -> bool:
        """True when *user_id* must be ignored in *channel_id*; audited when so.

        ``normal_channel`` says the caller already knows the id is a plain server
        channel that is not a view channel, so no parent lookup is made for it.
        """
        if not self._channels or not channel_id:
            return False
        if channel_id not in self._channels and normal_channel:
            return False
        if all(self.is_writer(v, user_id) for v in self._channels.values()):
            return False
        try:
            view = await self.view_of(client, channel_id)
        except Exception:
            # Cannot place the channel: for someone who is no writer everywhere, no.
            view = ViewChannel(channel_id, frozenset())
        if view is None or self.is_writer(view, user_id):
            return False
        self._audit(user_id, channel_id, surface)
        return True

    def _audit(self, user_id: str, channel_id: str, surface: str) -> None:
        now = _now()
        key = (user_id, channel_id)
        last = self._audited.get(key)
        if last is not None and now - last < _AUDIT_WINDOW_SECS:
            return
        if len(self._audited) >= _MEMO_MAX:
            self._audited.clear()
        self._audited[key] = now
        sel().log_api_access(
            caller=user_id or "unknown",
            operation="discord_view.denied",
            outcome="denied",
            source="discord",
            resources=f"channel={channel_id};surface={surface}",
        )
