"""Sharing a session into a Discord view channel: ``/sessions`` and one thread per session.

In a view channel (``discord.view_channels``) a writer runs ``/sessions``. The list of
private session titles is answered EPHEMERALLY, so only the writer sees it. Picking a
session opens a public thread under the view channel, links the session to that thread
(each finished reply is mirrored into it) and replays the last few messages there.
Everyone who can see the channel reads the whole shared session, the writer's own
messages included. Only writers can type in the thread, and typing there drives the
linked session. ``Stop sharing`` in the thread unlinks the session and archives and
locks the thread.

The pieces reuse the DM resume machinery (picker registry, bind transaction, replay
preview) through :class:`~kiro_crew.discord.session_resume.DiscordSessionResume`; what
is new is only the surface (private picker, a thread as the bound conversation) and
the thread's life cycle. Every function here fails closed: a refusal shows the writer
a short private line and changes nothing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from kiro_crew.discord.commands import parse_command
from kiro_crew.discord.session_resume import (
    _REPLAY_RESERVE,
    _REPLAY_TEXT_LIMIT,
    DiscordSessionResume,
    _DiscordResumeSurface,
    _picker_components,
    _replay_preview,
    _safe_discord_text,
)
from kiro_crew.discord.view_policy import ViewChannel
from kiro_crew.messaging.dispatch import inbound_permitted
from kiro_crew.messaging.driver import sanitize_channel_replay_text
from kiro_crew.messaging.link import ChannelLink
from kiro_crew.messaging.session_resume import ResumeReleaseError, SessionChoice
from kiro_crew.platform.context import redact_via_context
from kiro_crew.sel import sel

if TYPE_CHECKING:
    from kiro_crew.discord.client import DiscordClient, DiscordInbound, DiscordInteraction
    from kiro_crew.discord.transport import DiscordTransport
    from kiro_crew.discord.transport_dispatch import DiscordDispatcher

logger = logging.getLogger(__name__)

#: Custom id of the in-thread button that ends a share.
UNSHARE_ID = "vu:stop"
#: Longest thread name Discord accepts is 100; the title is cut well inside it.
_THREAD_NAME_MAX = 90
#: A thread with no traffic archives after this many minutes (Discord's longest, 7 days).
_THREAD_AUTO_ARCHIVE_MINUTES = 10080
_PUBLIC_THREAD = 11

_TEXT_SESSIONS_REFUSAL = (
    "🔒 `!sessions` is not available in a view channel: a text reply is public and "
    "would list your private session titles. Use the `/sessions` slash command here "
    "(only you see the list), or DM me."
)


def picker_owner(user_id: str, channel_id: str) -> str:
    """The identity a view picker is scoped to (never equal to a DM picker's)."""
    return f"view:{user_id}:{channel_id}"


class _ViewSurface(_DiscordResumeSurface):
    """The ephemeral picker and its settlements, written through the interaction token."""

    owner_refusal = (
        "🔒 `/sessions` in a view channel needs exactly one `discord.allowed_user_ids` "
        "entry, and it must be you."
    )
    choice_owner_refusal = "🔒 Only the owner can share a session."
    choice_expired = "⌛ This list expired. Run `/sessions` again."
    choice_missing = "That session is no longer available. Run `/sessions` again."
    choice_expectation_failed = (
        "⚠️ Couldn't save the share, so the session was NOT shared. Run `/sessions` to try again."
    )
    choice_claim_lost = (
        "🧵 That session is already linked to another conversation. "
        "Unlink it there, then run `/sessions` again."
    )
    choice_binding_failed = "⚠️ Couldn't share that session. Run `/sessions` to try again."

    def __init__(
        self,
        client: "DiscordClient",
        view_channel_id: str,
        token: str,
        *,
        caller_permitted: Any,
        thread_id: str = "",
    ) -> None:
        super().__init__(client, view_channel_id)
        self.token = token
        self.caller_permitted = caller_permitted
        self.expectation_id = thread_id or view_channel_id

    def no_choices(self, query: str) -> str:
        return super().no_choices(query).replace("`!sessions`", "`/sessions`")

    def picker_heading(self, query: str, total: int) -> str:
        return (
            super().picker_heading(query, total)
            + "\n⚠️ Everyone who can see this channel will read the WHOLE shared session, "
            "your own messages included, and the session's title is the thread name."
        )

    def choice_success(self, choice: SessionChoice) -> str:
        return (
            f"🧵 Shared in a new thread: {choice.title}\n"
            "Press **Stop sharing** in that thread to end it."
        )

    async def post_picker(
        self, heading: str, nonce: str, choices: tuple[SessionChoice, ...]
    ) -> str:
        message_id = await self.client.edit_interaction_original(
            self.token,
            f"{heading}\nChoose one to share in this channel.",
            components=_picker_components(nonce, choices),
            destination=self.channel_id,
            caller_permitted=self.caller_permitted,
        )
        return message_id or ""

    async def settle_picker(self, message_id: str, text: str) -> bool:
        return await self._write(text, [])

    async def say(self, text: str) -> None:
        await self._write(text, None)

    async def _write(self, text: str, components: list[dict] | None) -> bool:
        sent = await self.client.edit_interaction_original(
            self.token,
            text,
            components=components,
            destination=self.channel_id,
            caller_permitted=self.caller_permitted,
        )
        return sent is not None


def _writer_gate(d: "DiscordDispatcher", user_id: str, view: ViewChannel) -> Any:
    """Re-read before every reply: still authorized and still a writer here."""
    return lambda: d._authorized(user_id) and d.view.is_writer(view, user_id)


async def handle_view_interaction(d: "DiscordDispatcher", itx: "DiscordInteraction") -> bool:
    """Serve ``/sessions`` and the picker buttons in a view channel; ``False`` = not ours.

    Reached only after the writers gate, so the presser is a writer. Any other command
    or button in the channel itself falls through to the normal refusal.
    """
    view = d.view.channel(itx.channel_id)
    if view is None or not itx.guild_id or d.client is None:
        return False
    if itx.is_command:
        if itx.command_name != "sessions":
            return False
        await _list(d, itx, view)
        return True
    if (itx.custom_id or "").startswith("s:"):
        await _pick(d, itx, view)
        return True
    return False


async def _list(d: "DiscordDispatcher", itx: "DiscordInteraction", view: ViewChannel) -> None:
    client = d.client
    assert client is not None
    if not await inbound_permitted("discord"):
        await client.respond_interaction(
            itx.interaction_id,
            itx.interaction_token,
            "🔒 The Discord channel is currently disabled by policy.",
            ephemeral=True,
        )
        return
    await client.defer_interaction(
        itx.interaction_id, itx.interaction_token, destination=itx.channel_id
    )
    surface = _ViewSurface(
        client,
        itx.channel_id,
        itx.interaction_token,
        caller_permitted=_writer_gate(d, itx.user_id, view),
    )
    resume = d._session_resume
    await resume.controller.show_picker(
        surface,
        caller=itx.user_id,
        picker_owner=picker_owner(itx.user_id, itx.channel_id),
        is_owner=resume.is_owner(itx.user_id),
        query=" ".join(itx.options.values()).strip(),
    )


async def _pick(d: "DiscordDispatcher", itx: "DiscordInteraction", view: ViewChannel) -> None:
    client = d.client
    assert client is not None
    await client.ack_component_interaction(
        itx.interaction_id, itx.interaction_token, destination=itx.channel_id
    )
    resume = d._session_resume
    controller = resume.controller
    nonce, index = DiscordSessionResume._parse_choice(itx.custom_id)
    owner = picker_owner(itx.user_id, itx.channel_id)
    surface = _ViewSurface(
        client,
        itx.channel_id,
        itx.interaction_token,
        caller_permitted=_writer_gate(d, itx.user_id, view),
    )
    is_owner = resume.is_owner(itx.user_id)
    picked = controller.pickers.peek(nonce, index, owner, itx.message_id) if is_owner else None
    if picked is None:
        # Not the owner, or a stale, forged or foreign press: the controller words
        # the refusal and binds nothing (no link is stored on these paths).
        await controller.choose(
            surface,
            caller=itx.user_id,
            picker_owner=owner,
            is_owner=is_owner,
            message_id=itx.message_id,
            nonce=nonce,
            index=index,
            link=ChannelLink(channel_type="discord", channel_id=itx.channel_id),
        )
        return
    if not await inbound_permitted("discord"):
        await surface.settle_picker(itx.message_id, "🔒 The Discord channel is disabled by policy.")
        return
    if not d.view.allow_share(view.channel_id):
        await surface.settle_picker(
            itx.message_id, "⏳ Sharing too fast. Wait a few seconds and run `/sessions` again."
        )
        return
    thread_id = await _create_thread(client, view.channel_id, picked.title)
    if not thread_id:
        await surface.settle_picker(
            itx.message_id, "⚠️ Couldn't create the thread, so nothing was shared."
        )
        return
    surface.expectation_id = thread_id
    choice = await controller.choose(
        surface,
        caller=itx.user_id,
        picker_owner=owner,
        is_owner=True,
        message_id=itx.message_id,
        nonce=nonce,
        index=index,
        link=ChannelLink(channel_type="discord", channel_id=thread_id, principal=itx.user_id),
    )
    if choice is None:
        await _delete_thread(client, thread_id)
        return
    _admit(d, thread_id)
    await client.send_message(
        thread_id,
        f"🔗 **Shared session:** {choice.title}\n"
        "Everyone who can see this thread reads the whole session. Only writers can reply.",
        components=[
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 4, "label": "Stop sharing", "custom_id": UNSHARE_ID}
                ],
            }
        ],
    )
    await _replay_into(d, client, thread_id, choice.key, view.replay_messages)
    sel().log_api_access(
        caller=itx.user_id or "unknown",
        operation="discord_view.share",
        outcome="allowed",
        source="discord",
        resources=f"channel={view.channel_id};thread={thread_id};session={choice.title}",
    )


def _admit(d: "DiscordDispatcher", thread_id: str) -> None:
    d.register_allowed_thread(thread_id)
    if d.transport is not None:
        d.transport.admit_view_thread(thread_id)


async def _create_thread(client: "DiscordClient", channel_id: str, title: str) -> str:
    """Open a public thread under the view channel, named from the sanitized title."""
    name = _safe_discord_text(" ".join(title.split()), _THREAD_NAME_MAX) or "Shared session"
    result = await client.api_json(
        "POST",
        f"/channels/{channel_id}/threads",
        {
            "name": name,
            "type": _PUBLIC_THREAD,
            "auto_archive_duration": _THREAD_AUTO_ARCHIVE_MINUTES,
        },
        recheck_destination=False,
    )
    data = result.data
    return str(data.get("id") or "") if isinstance(data, dict) else ""


async def _delete_thread(client: "DiscordClient", thread_id: str) -> None:
    await client.api_json("DELETE", f"/channels/{thread_id}", None, recheck_destination=False)


def _replay_text(raw: str) -> str:
    return redact_via_context(sanitize_channel_replay_text(raw))


async def _replay_into(
    d: "DiscordDispatcher",
    client: "DiscordClient",
    thread_id: str,
    session_key: str,
    count: int,
) -> None:
    """Post the last *count* user and assistant messages, sanitized, into the thread."""
    conv_log = d._session_resume.conv_log
    if conv_log is None or count <= 0:
        return
    try:
        messages = await asyncio.to_thread(
            conv_log.recent, session_key, count, {"user", "assistant"}
        )
    except Exception:
        logger.exception("discord view: failed to read the transcript to replay")
        return
    for message in messages:
        role = message.get("role", "")
        if role not in {"user", "assistant"}:
            continue
        body = await _replay_preview(
            _replay_text(str(message.get("content") or "")),
            _REPLAY_TEXT_LIMIT,
            reserve=_REPLAY_RESERVE,
        )
        if not body:
            continue
        try:
            await client.send_message(thread_id, f"{'🧑' if role == 'user' else '🤖'}\n{body}")
        except Exception:
            logger.debug("discord view: replay message failed", exc_info=True)


async def unshare(d: "DiscordDispatcher", itx: "DiscordInteraction") -> None:
    """The ``Stop sharing`` button: unlink this thread's session, then archive and lock it.

    The session is the one linked to the thread the button sits in, never one a button
    names, so a replayed button can only end the share it was posted in.
    """
    client = d.client
    assert client is not None
    thread_id = itx.channel_id
    try:
        view = await d.view.view_of(client, thread_id)
    except Exception:
        return
    if view is None or view.channel_id == thread_id or not d.view.is_writer(view, itx.user_id):
        return
    try:
        await d._session_resume.leave_resumed_session(thread_id)
    except ResumeReleaseError:
        await client.edit_interaction_original(
            itx.interaction_token,
            "⚠️ Couldn't stop sharing. Try again.",
            destination=thread_id,
        )
        return
    await client.edit_interaction_original(
        itx.interaction_token,
        "🔕 Sharing stopped. This thread is now closed.",
        components=[],
        destination=thread_id,
    )
    d._allowed_threads.discard(thread_id)
    if d.transport is not None:
        d.transport.revoke_view_thread(thread_id)
    await client.api_json(
        "PATCH",
        f"/channels/{thread_id}",
        {"archived": True, "locked": True},
        recheck_destination=False,
    )
    sel().log_api_access(
        caller=itx.user_id or "unknown",
        operation="discord_view.unshare",
        outcome="allowed",
        source="discord",
        resources=f"channel={view.channel_id};thread={thread_id}",
    )


async def refuse_text_sessions(t: "DiscordTransport", inbound: "DiscordInbound") -> bool:
    """A ``!sessions`` / ``!bot`` typed in the view channel itself: refuse, list nothing.

    A text reply cannot be private, so the command is answered with where to go
    instead. Reached only after the writers gate; ``True`` means the message was
    consumed.
    """
    if t.view.channel(inbound.channel_id) is None or parse_command(inbound.text) not in (
        "sessions",
        "bot",
    ):
        return False
    await t.client.send_message(inbound.channel_id, _TEXT_SESSIONS_REFUSAL)
    return True


async def rehydrate_view_threads(client: "DiscordClient", transport: "DiscordTransport") -> int:
    """Re-admit the active threads this bot opened under a view channel; returns how many.

    The admission is held in memory, so a restart forgets it and the mirror feed to a
    shared thread would stay refused until a writer next posted in it. Discord is asked
    instead of a local file: the active threads of the guild, kept only when the bot
    owns them and their parent is a configured view channel. An archived thread is not
    listed; a writer's next message in it re-admits it through ``readmit_own_thread``.
    """
    bot_id = getattr(client, "bot_user_id", "")
    if not bot_id or not transport.view.active:
        return 0
    admitted = 0
    guilds: set[str] = set()
    for channel_id in transport.view.channel_ids():
        info = (await client.api_json("GET", f"/channels/{channel_id}", None)).data
        guild_id = str(info.get("guild_id") or "") if isinstance(info, dict) else ""
        if guild_id and guild_id not in guilds:
            guilds.add(guild_id)
            listing = (
                await client.api_json("GET", f"/guilds/{guild_id}/threads/active", None)
            ).data
            threads = listing.get("threads") if isinstance(listing, dict) else None
            for thread in threads if isinstance(threads, list) else []:
                if (
                    isinstance(thread, dict)
                    and str(thread.get("owner_id") or "") == bot_id
                    and transport.view.channel(str(thread.get("parent_id") or "")) is not None
                    and str(thread.get("id") or "")
                ):
                    transport.admit_view_thread(str(thread["id"]))
                    admitted += 1
    return admitted
