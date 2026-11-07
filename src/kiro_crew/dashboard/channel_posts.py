"""A Bot posts to a connected channel only when the user says yes to that exact post.

``messaging.md``, "Bot channel posts". The ``channel_list`` and ``channel_send`` tools
(``kirocrew-mate``) reach the gateway through
:mod:`kiro_crew.dashboard.handlers.member_channels`; this module holds what that handler
needs:

* **Which channels a Bot can name**: the channels that are connected right now and that
  the owner-DM send already serves (:func:`channels`). A Bot says a short name such as
  ``telegram``; it never handles an id or a recipient.
* **The approval**: one blocking question card on the Bot's own tab, the same card
  ``ask_question`` shows, with the exact channel and the exact text. Only the user's
  press of :data:`APPROVE` sends. The card is opened and read by the gateway, so the
  answer never passes through the Bot, and the route that answers a card takes the
  dashboard user's login, which a Bot's tool call does not have.
* **The send**: the sender the ``send_message`` tool already uses
  (:func:`deliver`). This module adds no second one.

The post goes where ``send_message(session="<channel>")`` goes: the owner's own direct
messages on that channel, and only where the channel advertises exactly one owner. A
channel the owner-DM send refuses on purpose (``weixin`` and ``wecom``, whose recipient
lists can include people who only messaged the bot) is refused here too.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kiro_crew.constants import CHANNEL_OWNER_DM_NAMESPACES, SLACK_NAMESPACE
from kiro_crew.validation import ASK_WAIT_SLICE_SECS

logger = logging.getLogger(__name__)

#: The two answers on the approval card. Only an answer equal to :data:`APPROVE` sends;
#: the other, a typed reply, a closed card and a timeout are all "no".
APPROVE = "Send it"
REJECT = "Don't send"

#: Longest post, in characters. A longer one is refused, not cut. The text sits in the
#: card's question, which holds 500 characters, next to the Bot's name and the channel.
MAX_POST_CHARS = 400

#: Approval requests one Bot may open for one channel inside the Bot-message window.
#: A refused or declined request counts, so a Bot cannot flood the user with cards.
POST_LIMIT = 3

#: How long the gateway holds the Bot's call for the user's answer, in seconds. Past it
#: the card is withdrawn and nothing is sent. Under the 600 s the agent harness gives a
#: silent tool call. ponytail: a user slower than this asks the Bot to try again; a
#: longer window needs the deferred-tool wait the core ``ask_question`` tool uses.
APPROVAL_WAIT_SECS = 300

#: Longest Bot name the card quotes.
_CARD_NAME_CHARS = 60

#: The channels a Bot may post to: Slack, and every channel the owner-DM send serves.
POSTABLE = frozenset({SLACK_NAMESPACE, *CHANNEL_OWNER_DM_NAMESPACES})

#: Why a connected channel outside :data:`POSTABLE` is refused, for the Bot to relay.
NOT_AVAILABLE_WHY = (
    "its recipient list can include people who only messaged the bot, so the gateway "
    "cannot tell which one is the owner"
)


def channels(state: Any) -> tuple[list[str], list[str]]:
    """``(postable, blocked)``: the connected channels a Bot may post to, and those it may not.

    Read from the same status the dashboard's channel page shows. Both lists are sorted
    channel names; an unreadable status reads as no channel connected.
    """
    try:
        status = state.channel_status()
    except Exception:
        logger.warning("channel posts: channel status unavailable", exc_info=True)
        return [], []
    connected = sorted(name for name, row in status.items() if row.get("connected"))
    return (
        [n for n in connected if n in POSTABLE],
        [n for n in connected if n not in POSTABLE],
    )


def label(channel: str) -> str:
    """The channel as a person reads it."""
    return channel.title()


def questions(member: str, channel: str, text: str) -> list[dict[str, Any]]:
    """The approval card: the Bot, the channel and the exact *text*, and the two answers."""
    name = " ".join(member.split())[:_CARD_NAME_CHARS]
    where = label(channel)
    return [
        {
            "header": f"Post to {where}"[:50],
            "question": f"{name} wants to post this to {where}:\n\n{text}",
            "multiSelect": False,
            "options": [
                {
                    "label": APPROVE,
                    "description": f"Posts exactly this text to your {where} messages now, "
                    f"with a line saying it came from {name}.",
                },
                {"label": REJECT, "description": "Nothing is sent."},
            ],
        }
    ]


def governance_refusal(session_key: str, channel: str) -> str:
    """Why the active governance profile forbids this Bot posting to *channel*, else ``""``.

    The same two checks the ``send_message`` tool makes: the on/off messaging capability
    and the per-channel allowlist. Both fail closed. Blocking policy reads: call off the
    event loop.
    """
    from kiro_crew.dashboard.handlers import messaging

    try:
        from kiro_crew.platform.governance_profiles import vet_and_audit

        decision = vet_and_audit(
            "capabilities.messaging",
            "",
            session_key=session_key,
            tool_name="channel_send",
            fail_closed=True,
        )
        if not getattr(decision, "permitted", False):
            return "outbound messaging is blocked by governance policy"
    except Exception:
        logger.warning("channel_send governance check failed", exc_info=True)
        return "governance evaluation unavailable"
    return messaging._vet_channel_send(channel, session_key)


def caller_gone(request: Any) -> bool:
    """Whether the Bot's tool call has hung up (the turn was stopped), so nobody reads the answer."""
    transport = getattr(request, "transport", None)
    return transport is None or bool(transport.is_closing())


async def await_approval(state: Any, request: Any, ask_id: str, session_key: str) -> dict[str, Any]:
    """Hold for the user's answer on card *ask_id*; the outcome the card ended with.

    ``{"status": "answered", "questions": [...], "answers": {...}}`` when the user
    answered, else a status of ``dismissed``, ``composer``, ``expired`` (no answer in
    :data:`APPROVAL_WAIT_SECS`), ``withdrawn`` or ``cancelled`` (the Bot's call hung up).
    The card is always withdrawn on the way out, so it never stays on screen unread.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + APPROVAL_WAIT_SECS
    try:
        while True:
            if caller_gone(request):
                return {"status": "cancelled"}
            reply = await state.wait_agent_ask(ask_id, session_key, ASK_WAIT_SLICE_SECS)
            if reply is None:
                return {"status": "withdrawn"}
            if reply.get("status") != "pending":
                return reply
            if loop.time() >= deadline:
                return {"status": "expired"}
    finally:
        state.withdraw_agent_ask(ask_id, session_key)


def approved(reply: dict[str, Any]) -> bool:
    """Whether *reply* is the user pressing :data:`APPROVE` on the one question asked."""
    asked = reply.get("questions") or []
    answers = reply.get("answers") or {}
    return (
        reply.get("status") == "answered"
        and len(asked) == 1
        and answers.get(asked[0].get("question")) == APPROVE
        and len(answers) == 1
    )


def card_text(state: Any, ask_id: str) -> str:
    """The question text the open card is showing, as the gateway kept it (after redaction)."""
    ask = getattr(state, "_agent_asks", {}).get(ask_id) or {}
    shown = ask.get("questions") or [{}]
    return str(shown[0].get("question", ""))


async def deliver(
    state: Any, channel: str, text: str, *, member: str, session_key: str
) -> tuple[bool, str, str]:
    """Send *text* with the existing owner-DM sender; ``(sent, code, detail)``.

    An empty *code* with ``sent=False`` is the soft miss: the channel has no single
    owner to write to, or went away since the list was read. A text ends with one line
    naming the Bot, as every proactive send names its sender.
    """
    from kiro_crew.dashboard.handlers import messaging

    text = messaging._with_source_line(text, f"Sent by Bot: {' '.join(member.split())[:80]}")
    if channel != SLACK_NAMESPACE:
        return await messaging._deliver_channel_dm(state, channel, text, caller_session=session_key)
    if state.slack_client is None or not state.owner_id:
        return False, "", ""
    shown, _ = messaging.redact_for_display(text, messaging._redact_all)
    outcome = messaging._SendMessageOutcome()
    await messaging._post_send_message_to_slack(
        state,
        outcome,
        blocks=None,
        text=shown,
        options=[],
        target_channel="",
        target_user="",
        thread_ts=None,
        reply_broadcast=None,
        session_link_url="",
    )
    if outcome.sent_slack:
        return True, "", ""
    if outcome.slack_failure == "unconfirmed":
        return False, "channel_delivery_unconfirmed", outcome.slack_error
    return False, "channel_delivery_failed", outcome.slack_error or "Slack did not take the message"
