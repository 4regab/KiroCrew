"""Give a worker its own conversation on the channel its creator is mirrored to.

A session mirrored to a channel that offers it (``open_child_conversation``) gets one
new conversation per worker it creates, and the worker's replies are mirrored there
one way: nothing typed in it reaches the worker. Best effort: the worker already
exists, so a failure here only means it has no conversation of its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from kiro_crew.dashboard.chat_utils import effective_session_key
from kiro_crew.messaging.link import SLACK_NAMESPACE, ChannelLink

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kiro_crew.dashboard.state import DashboardState, _ChatSlot

logger = logging.getLogger("kiro_crew.dashboard.session_control")


async def mirror_child_session(
    state: "DashboardState", creator_key: str, child: "_ChatSlot"
) -> bool:
    """Open *child*'s conversation beside its creator's and mirror its replies there."""
    try:
        creator = state.get_slot(creator_key)
        sessions = getattr(state, "sessions", None)
        if creator is None or sessions is None:
            return False
        link = sessions.get_mirror_link(effective_session_key(creator))
        if not isinstance(link, ChannelLink) or link.channel_type == SLACK_NAMESPACE:
            return False
        opener = getattr(
            state.get_channel_transport(link.channel_type), "open_child_conversation", None
        )
        if not callable(opener):
            return False
        opened = await opener(link.channel_id, child.title or "")
        if not isinstance(opened, str) or not opened:
            return False
        session_key = effective_session_key(child)
        # No principal and no admission: the conversation is admitted on the transport
        # (``may_send_to`` reads its roster), and a link carrying a peer would have to be
        # signed, which only the two link-creation paths may do.
        child_link = ChannelLink(channel_type=link.channel_type, channel_id=opened)
        await asyncio.to_thread(sessions.set_mirror_link, session_key, child_link)
        return True
    except Exception:
        logger.warning(
            "session_control: could not open a conversation for %s", child.key, exc_info=True
        )
        return False
