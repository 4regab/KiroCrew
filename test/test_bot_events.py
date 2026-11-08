"""Bot event rows: a Bot's own transcript shows what it sent, received or was refused.

The rows are system notices (``meta.kind == "bot_event"``) written with ``slot.append``, so
they must be skipped by the last-real-message scans, saved with the transcript, and drawn
again after a reload or a gateway restart. The handlers and the wakes that write them are
exercised in ``test_bot_messages.py`` and ``test_group_rooms.py`` on the same real slot.
"""

from __future__ import annotations

import json

from chat_test_helpers import _make_state

from kiro_crew.dashboard import bot_events
from kiro_crew.dashboard.chat_persistence import _save_slot_to_history
from kiro_crew.dashboard.chat_utils_display import _prepare_messages
from kiro_crew.dashboard.state import _ChatSlot
from kiro_crew.dashboard.system_notices import (
    BOT_EVENT_KIND,
    SYSTEM_NOTICE_KINDS,
    is_speech_row,
    is_system_notice,
)
from kiro_crew.history import ConversationLog

SECRET = "AKIAIOSFODNN7EXAMPLE"


def _events() -> list[dict]:
    return [
        bot_events.sent("Ace", "Four", "Can you review PR 7?", queued=False),
        bot_events.received("Ace", "Four", "Yes, after lunch.", hop=1),
        bot_events.room_post("Ace", "Office", "Standup in five."),
        bot_events.room_wake(
            "Ace",
            "Office",
            [{"from": "", "text": "hey there"}, {"from": "Four", "text": "hi"}],
            3,
        ),
        bot_events.refused("Ace", "rate_limited", peer="Four"),
    ]


def test_the_kind_is_a_system_notice_on_both_scans():
    assert BOT_EVENT_KIND in SYSTEM_NOTICE_KINDS
    slot = _ChatSlot("member-ace", agent="Ace")
    assert bot_events.record(slot, bot_events.sent("Ace", "Four", "hello", queued=True))
    row = slot.messages[-1]
    assert row["role"] == "assistant" and row["meta"]["kind"] == BOT_EVENT_KIND
    assert row["content"] == "Ace sent a message to Four"
    assert row["meta"]["bot_event"]["queued"] is True and row["meta"]["mid"]
    # not the Bot's speech, so no scan treats it as the last real message
    assert is_system_notice(row["role"], row["meta"])
    assert not is_speech_row(row["role"], row["content"], row["meta"])


def test_a_row_keeps_peer_text_clipped_and_free_of_secrets():
    long_text = f"key {SECRET} " + "x" * 5000
    event = bot_events.received("Ace", "Four", long_text, hop=2)
    assert SECRET not in event["text"] and len(event["text"]) <= bot_events.TEXT_CHARS
    many = [{"from": "Four", "text": "y" * 3000} for _ in range(30)]
    wake = bot_events.room_wake("Ace", "Office", many, 0)
    assert len(wake["messages"]) == bot_events.MAX_WAKE_MESSAGES
    assert all(len(m["text"]) <= bot_events.WAKE_TEXT_CHARS for m in wake["messages"])
    # junk in, empty out: the builders never raise on what a caller got wrong
    assert bot_events.sent(None, 7, {"a": 1}, queued=False)["text"] == ""


def test_writing_a_row_never_raises():
    class Broken:
        key = "member-ace"

        def append(self, *a, **k):
            raise RuntimeError("disk")

    assert bot_events.record(Broken(), _events()[0]) is False
    assert bot_events.record(None, _events()[0]) is False


def test_the_rows_survive_a_save_a_reload_and_a_restart(tmp_path):
    state = _make_state(tmp_path)
    key = "dashboard:chat-ace"
    slot = state.get_or_create_slot(name="chat-ace", linked_session_key=key)
    slot.append("user", "hi", "msg msg-u")
    for event in _events():
        assert bot_events.record(slot, event)
    slot.append("assistant", "Done.", "msg msg-a")
    assert _save_slot_to_history(state, slot, force=True)

    # a gateway restart: a new reader over the same files
    rows = ConversationLog(base_dir=tmp_path).read_messages(key)
    events = [
        r["meta"]["bot_event"] for r in rows if r.get("meta", {}).get("kind") == BOT_EVENT_KIND
    ]
    assert events == _events()
    assert [r["role"] for r in rows].count("assistant") == len(_events()) + 1

    # what the dashboard gets on a reload still carries the payload
    served = _prepare_messages(rows, False, live_child="")
    shown = [
        m["meta"]["bot_event"]["type"]
        for m in served
        if m.get("meta", {}).get("kind") == BOT_EVENT_KIND
    ]
    assert shown == ["sent", "received", "room_post", "room_wake", "refused"]


def test_a_secret_in_the_text_is_not_written_to_disk(tmp_path):
    state = _make_state(tmp_path)
    key = "dashboard:chat-ace"
    slot = state.get_or_create_slot(name="chat-ace", linked_session_key=key)
    # a builder scrubs; a hand-made event with a raw secret is redacted by the transcript writer
    raw = {"type": "received", "me": "Ace", "peer": "Four", "text": f"token {SECRET}", "hop": 1}
    assert bot_events.record(slot, raw)
    assert _save_slot_to_history(state, slot, force=True)
    on_disk = ConversationLog(base_dir=tmp_path)._path(key).read_text(encoding="utf-8")
    assert SECRET not in on_disk
    assert json.loads(on_disk.splitlines()[-1])["meta"]["kind"] == BOT_EVENT_KIND
