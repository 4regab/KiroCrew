"""The board relay decides off the writer and catches up from a durable cursor.

Each test runs the real crew-log writer and the real relay consumer on a temp data home,
with a real binding file, so the binding read, the header read and the board's newest
unit are the ones a gateway uses. A "restart" is :func:`work_relay.forget_for_tests`:
it drops every in-memory relay state and keeps what is on disk, like a new process.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import sys
import threading
from typing import Any

import pytest

from kiro_crew import crew_log as lg
from kiro_crew import session_ledger
from kiro_crew.crew_log import CrewLog
from kiro_crew.crew_log import eager as crew_log_eager
from kiro_crew.crew_log import emit as crew_log_emit
from kiro_crew.crew_log import projection as crew_log
from kiro_crew.crew_log import work_relay, work_relay_feed
from kiro_crew.crew_log.emit_runtime import _write as emit_write
from kiro_crew.testing.wait import FALLBACK_TIMEOUT_SECS, default_timeout, wait_until
from kiro_crew.work_ledger_writes import _write_binding

LEAD = "chat-relay-feed-lead"
WORKER = "chat-relay-feed-worker"
LEAD_UNIT = "u-feed-lead"
WORKER_UNIT = "u-feed-worker"


@pytest.fixture(autouse=True)
def _live_crew_log(tmp_path):
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
        patch.setenv("KIROCREW_CREW_LOG", "1")
        crew_log_emit.reset_caches()
        crew_log_eager.stop_for_tests()
        crew_log.forget_slot_folds()
        work_relay.forget_for_tests()
        yield patch
        crew_log_emit.flush()
        crew_log_eager.stop_for_tests()
        crew_log.forget_slot_folds()
        work_relay.forget_for_tests()
        crew_log_emit.reset_caches()


def _unit(unit: str, slot: str) -> None:
    CrewLog.create(lg.KIND_SESSION, unit, owner="owner", agent="lead", slot=slot)


def _settle() -> None:
    assert crew_log_emit.flush()
    assert crew_log_eager.drain(timeout=default_timeout())


def _board() -> None:
    _unit(LEAD_UNIT, LEAD)
    data = {"slot": LEAD, "actor": "conductor", "by": LEAD, "action": "goal", "goal": "g"}
    assert crew_log_emit.on_work_recorded(
        LEAD_UNIT, {**data, "round": 1}, timeout=default_timeout()
    )
    _settle()


def _bind() -> None:
    _write_binding(WORKER, LEAD, "it_0000000a")


def _turn(turn: int, credits: float) -> None:
    emit_write(
        WORKER_UNIT,
        "turn/completed",
        {
            "turn": turn,
            "stop_reason": "end_turn",
            "depth": 0,
            "duration_ms": 10,
            "model": "opus",
            "provider": "kiro",
            "credits": credits,
            "tokens": {"input": 5, "output": 1, "cache_read": 0, "cache_write": 0},
        },
    )
    _settle()


def _charges() -> "list[float]":
    log = CrewLog.open(lg.KIND_SESSION, LEAD_UNIT)
    return [e.data["credits"] for e in log.iter_from(1) if e.type == "work/charged"]


def _restart() -> None:
    """Forget every in-memory relay state, as a new process would; disk stays."""
    _settle()
    work_relay.forget_for_tests()


def test_the_writer_lane_never_resolves_a_relay(_live_crew_log):
    """The header and newest-unit reads run on the relay's own thread; the lane only
    looks at the binding, which is what makes the board the one in force at commit."""
    _board()
    _noted_unit(WORKER_UNIT, WORKER)
    _bind()
    threads: "list[str]" = []
    for name in ("_unit_slot",):
        real = getattr(work_relay, name)

        def spy(*args: Any, _real: Any = real) -> Any:
            threads.append(threading.current_thread().name)
            return _real(*args)

        _live_crew_log.setattr(work_relay, name, spy)
    real_newest = session_ledger.newest_work_unit

    def newest(board: str) -> str:
        threads.append(threading.current_thread().name)
        return real_newest(board)

    _live_crew_log.setattr(session_ledger, "newest_work_unit", newest)
    _turn(1, 2.5)
    assert _charges() == [2.5]
    assert threads and set(threads) == {"crew-log-relay"}, threads


def test_a_closer_the_crash_cut_off_is_relayed_after_the_restart(_live_crew_log):
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    with pytest.MonkeyPatch.context() as crash:
        # The closer commits, and the process dies before the relay decides it.
        crash.setattr(work_relay_feed, "note", lambda *a, **k: None)
        _turn(2, 2.0)
    assert _charges() == [1.0]
    _restart()
    _turn(3, 3.0)
    assert _charges() == [1.0, 2.0, 3.0]


def test_a_copy_that_landed_before_its_cursor_was_written_is_not_copied_again(
    _live_crew_log,
):
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    cursor = work_relay_feed._cursor_path(work_relay_feed._state_home)
    assert json.loads(cursor.read_text())["units"][WORKER_UNIT] > 0
    # A crash after the copy landed and before the cursor moved: no cursor on disk.
    cursor.unlink()
    _restart()
    _turn(2, 2.0)
    assert _charges() == [1.0, 2.0]


class _Clock:
    """The crew log's entry clock, set by the test: no entry time comes from the wall."""

    def __init__(self, patch: pytest.MonkeyPatch, start_ms: int = 1_000_000) -> None:
        self.ms = start_ms
        patch.setattr("kiro_crew.crew_log.store.now_ms", self.read)

    def read(self) -> int:
        return self.ms

    def at(self, ms: int) -> "_Clock":
        self.ms = ms
        return self


def _bind_to(board: str, at_ms: int, item: str = "it_0000000a", worker: str = WORKER) -> None:
    """Bind *worker* to *board*, with the binding file stamped exactly *at_ms*."""
    from kiro_crew import work_ledger

    _write_binding(worker, board, item)
    stamp = at_ms * 1_000_000
    os.utime(work_ledger.binding_path(worker), ns=(stamp, stamp))


def test_a_catch_up_does_not_relay_what_was_written_before_the_bind(_live_crew_log):
    clock = _Clock(_live_crew_log)
    _board()
    _unit(WORKER_UNIT, WORKER)
    clock.at(1_000_000)
    _turn(1, 1.0)
    _bind_to(LEAD, 2_000_000)
    _restart()
    clock.at(3_000_000)
    _turn(2, 2.0)
    assert _charges() == [2.0]


def test_a_full_queue_is_caught_up_from_the_cursor_in_order(_live_crew_log):
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    _live_crew_log.setattr(work_relay_feed, "QUEUE_LIMIT", 0)
    for turn in (2, 3, 4):
        _turn(turn, float(turn))
    assert work_relay_feed.overflowed() >= 3
    assert _charges() == [1.0, 2.0, 3.0, 4.0]


def test_the_cursor_only_moves_forward_and_survives_a_restart(_live_crew_log):
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    _turn(2, 2.0)
    work_relay_feed.persist()
    path = work_relay_feed._cursor_path(work_relay_feed._state_home)
    first = json.loads(path.read_text())["units"][WORKER_UNIT]
    _restart()
    _turn(3, 3.0)
    work_relay_feed.persist()
    later = json.loads(path.read_text())["units"][WORKER_UNIT]
    assert later > first
    assert _charges() == [1.0, 2.0, 3.0]


def _refuse_charges(patch: pytest.MonkeyPatch, credits: float) -> "dict[str, int]":
    """Make the store refuse every ``work/charged`` copy carrying *credits*."""
    from kiro_crew.crew_log.errors import CODE_BAD_DATA, CrewLogError

    calls = {"n": 0}
    real_many, real_one = CrewLog.append_many, CrewLog.append

    def many(log: CrewLog, items: Any, **kwargs: Any) -> Any:
        if any(i["type"] == "work/charged" and i["data"].get("credits") == credits for i in items):
            calls["n"] += 1
            raise CrewLogError("refused for the test", code=CODE_BAD_DATA)
        return real_many(log, items, **kwargs)

    def one(log: CrewLog, entry_type: str, data: Any, **kwargs: Any) -> Any:
        if entry_type == "work/charged" and data.get("credits") == credits:
            calls["n"] += 1
            raise CrewLogError("refused for the test", code=CODE_BAD_DATA)
        return real_one(log, entry_type, data, **kwargs)

    patch.setattr(CrewLog, "append_many", many)
    patch.setattr(CrewLog, "append", one)
    return calls


def test_a_refused_copy_is_decided_once_and_never_retried(_live_crew_log):
    """A refusal settles the entry: the cursor passes it and no retry marks it again."""
    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    calls = _refuse_charges(_live_crew_log, 2.0)
    _turn(2, 2.0)
    _turn(3, 3.0)
    assert _charges() == [1.0, 3.0]
    refused_attempts = calls["n"]
    _turn(4, 4.0)
    assert _charges() == [1.0, 3.0, 4.0]
    with work_relay_feed._cond:
        assert WORKER_UNIT not in work_relay_feed._held
        assert not work_relay_feed._retry_at
    assert calls["n"] == refused_attempts, "a refused copy was tried again"


def test_a_header_that_cannot_be_read_is_retried_not_passed(_live_crew_log):
    """An unprovable header holds the cursor; the charge lands once the read recovers."""
    from kiro_crew.crew_log import store

    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay._UNIT_SLOTS.clear()
    real = store._unit_header_object
    calls = {"n": 0}

    def once(kind: str, unit: str) -> Any:
        # Only the relay's own header read fails, once; every other reader is served.
        if unit == WORKER_UNIT and sys._getframe(1).f_code.co_name == "_unit_slot":
            calls["n"] += 1
            if calls["n"] == 1:
                return None
        return real(kind, unit)

    _live_crew_log.setattr(store, "_unit_header_object", once)
    _turn(2, 2.0)
    _turn(3, 3.0)
    _wait_for([1.0, 2.0, 3.0])
    assert calls["n"] >= 2, "the relay never read the header again"


class _Gate:
    """Park the relay consumer inside its next decision until the test opens the gate."""

    def __init__(self, patch: pytest.MonkeyPatch) -> None:
        self.entered = threading.Event()
        self.opened = threading.Event()
        real = work_relay.resolve

        def parked(*args: Any, **kwargs: Any) -> bool:
            if not self.opened.is_set():
                self.entered.set()
                assert self.opened.wait(default_timeout()), "the gate was never opened"
            return real(*args, **kwargs)

        patch.setattr(work_relay, "resolve", parked)

    def wait_parked(self) -> None:
        assert self.entered.wait(default_timeout()), "the relay never reached its decision"

    def open(self) -> None:
        self.opened.set()


def _noted_unit(unit: str, slot: str) -> None:
    """A unit the writer knows the slot of, as a unit opened through the emitter is."""
    from kiro_crew.crew_log import writer_groups

    _unit(unit, slot)
    writer_groups.note_unit_slot(unit, slot)


def _emit_closer(turn: int, credits: float, unit: str = WORKER_UNIT) -> None:
    emit_write(
        unit,
        "turn/completed",
        {
            "turn": turn,
            "stop_reason": "end_turn",
            "depth": 0,
            "duration_ms": 10,
            "model": "opus",
            "provider": "kiro",
            "credits": credits,
            "tokens": {"input": 5, "output": 1, "cache_read": 0, "cache_write": 0},
        },
    )


def _board_charges(unit: str) -> "list[float]":
    log = CrewLog.open(lg.KIND_SESSION, unit)
    return [e.data["credits"] for e in log.iter_from(1) if e.type == "work/charged"]


def test_a_closer_decided_after_its_worker_is_bound_is_judged_by_its_commit(_live_crew_log):
    """Unbound when it committed: a bind before the relay decides does not charge it."""
    clock = _Clock(_live_crew_log)
    _board()
    _noted_unit(WORKER_UNIT, WORKER)
    gate = _Gate(_live_crew_log)
    clock.at(1_000_000)
    _emit_closer(1, 1.0)
    gate.wait_parked()
    _bind_to(LEAD, 2_000_000)
    gate.open()
    _settle()
    assert _charges() == []


def test_a_rebind_before_the_relay_decides_keeps_the_closer_on_its_own_board(_live_crew_log):
    """Bound to the lead when it committed: rebinding first does not move or drop it."""
    clock = _Clock(_live_crew_log)
    other, other_unit = "chat-relay-feed-other", "u-feed-other"
    _board()
    _unit(other_unit, other)
    data = {"slot": other, "actor": "conductor", "by": other, "action": "goal", "goal": "o"}
    assert crew_log_emit.on_work_recorded(other_unit, {**data, "round": 1})
    _noted_unit(WORKER_UNIT, WORKER)
    _bind_to(LEAD, 1_000_000)
    clock.at(2_000_000)
    gate = _Gate(_live_crew_log)
    _emit_closer(1, 1.0)
    gate.wait_parked()
    _bind_to(other, 3_000_000, item="it_0000000b")
    gate.open()
    _settle()
    assert _board_charges(LEAD_UNIT) == [1.0]
    assert _board_charges(other_unit) == []


def test_a_binding_file_unreadable_at_startup_is_scanned_again(_live_crew_log):
    """The startup catch-up retries; a later live closer cannot pass the missed one."""
    import pathlib

    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    _crash_cut_closer(2, 2.0)
    _settle()
    real = pathlib.Path.read_text
    fails = {"n": 2}

    def flaky(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if fails["n"] and sys._getframe(1).f_code.co_name == "bound_workers":
            fails["n"] -= 1
            raise OSError("binding file busy")
        return real(path, *args, **kwargs)

    _live_crew_log.setattr(pathlib.Path, "read_text", flaky)
    _restart()
    _turn(3, 3.0)
    _wait_for([1.0, 2.0, 3.0])
    assert fails["n"] == 0


def test_flush_waits_for_a_copy_the_relay_hands_over_after_the_writer_drained(
    _live_crew_log,
):
    """The relay hands its copy to the writer just after the writer drained: still waited."""
    from kiro_crew.crew_log import emit_runtime

    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    real_hand_over = work_relay.hand_over
    writer_drained = threading.Event()
    handed = threading.Event()

    def late_hand_over(copies: Any) -> None:
        assert writer_drained.wait(default_timeout())
        real_hand_over(copies)
        handed.set()

    _live_crew_log.setattr(work_relay, "hand_over", late_hand_over)
    writer = emit_runtime._writer()
    real_flush = writer.flush
    first = {"done": False}

    def flush_then_let_the_relay_finish(timeout: Any = 5.0) -> bool:
        drained = real_flush(timeout=timeout)
        if not first["done"]:
            first["done"] = True
            writer_drained.set()
            assert handed.wait(default_timeout())
            assert work_relay_feed.drain(default_timeout())
        return drained

    _live_crew_log.setattr(writer, "flush", flush_then_let_the_relay_finish)
    _emit_closer(2, 2.0)
    assert crew_log_emit.flush(timeout=default_timeout())
    assert _charges() == [1.0, 2.0]


def test_flush_accepts_no_timeout():
    assert crew_log_emit.flush(timeout=None)


#: Every order of the four live disturbances, plus a fixed sample of orders that also
#: hold an entry for a retry (``resolve_fail``) and restart over a unit listing that
#: fails once (``restart``). Sampled with a fixed seed, so the case list never changes.
_ORDERS = sorted(set(itertools.permutations(["rebind", "read_fail", "overflow", "flush"])))
_ORDERS += sorted(
    random.Random(31502).sample(
        sorted(
            itertools.permutations(
                ["rebind", "read_fail", "overflow", "flush", "resolve_fail", "restart"]
            )
        ),
        48,
    )
)


@pytest.mark.parametrize("order", _ORDERS, ids=["-".join(o) for o in _ORDERS])
def test_no_charge_is_lost_or_doubled_in_any_order_of_disturbances(_live_crew_log, order):
    """Each closer lands once, on the board its worker was bound to when it committed."""
    from kiro_crew import work_ledger
    from kiro_crew.crew_log import emit_runtime
    from kiro_crew.crew_log.errors import CODE_BAD_HEADER, CrewLogError

    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    clock = _Clock(_live_crew_log)
    other, other_unit = "chat-relay-feed-other", "u-feed-other"
    _board()
    _unit(other_unit, other)
    data = {"slot": other, "actor": "conductor", "by": other, "action": "goal", "goal": "o"}
    assert crew_log_emit.on_work_recorded(other_unit, {**data, "round": 1})
    _noted_unit(WORKER_UNIT, WORKER)
    _bind_to(LEAD, 1_000_000)
    bound = LEAD
    expected: "dict[str, list[float]]" = {LEAD_UNIT: [], other_unit: []}
    real_read = work_ledger.read_binding
    fail_reads = {"n": 0}

    def flaky_read(worker: str, **kwargs: Any) -> Any:
        if fail_reads["n"]:
            fail_reads["n"] -= 1
            raise OSError("binding read failed")
        return real_read(worker, **kwargs)

    _live_crew_log.setattr(work_ledger, "read_binding", flaky_read)
    from kiro_crew.crew_log import store

    real_resolve, fail_resolve = work_relay.resolve, {"n": 0}

    def flaky_resolve(*args: Any, **kwargs: Any) -> bool:
        if fail_resolve["n"]:
            fail_resolve["n"] -= 1
            raise OSError("decision read failed")
        return real_resolve(*args, **kwargs)

    _live_crew_log.setattr(work_relay, "resolve", flaky_resolve)
    real_listing, fail_listing = store.session_units_by_slot, {"n": 0}

    def flaky_listing(*, strict: bool = False) -> Any:
        if fail_listing["n"] and sys._getframe(1).f_code.co_name == "_bound_units":
            fail_listing["n"] -= 1
            if strict:
                raise CrewLogError("a unit header will not read", code=CODE_BAD_HEADER)
            return {}
        return real_listing(strict=strict)

    _live_crew_log.setattr(store, "session_units_by_slot", flaky_listing)
    credits = 0.0
    now = 1_000_000
    for step in order:
        now += 1_000
        if step == "rebind":
            bound = other if bound == LEAD else LEAD
            _bind_to(bound, now, item=f"it_{int(now):08x}"[:11])
        elif step == "read_fail":
            fail_reads["n"] = 1
            with work_relay._recent_guard:
                work_relay._BINDINGS.clear()
        elif step == "overflow":
            _live_crew_log.setattr(work_relay_feed, "QUEUE_LIMIT", 0)
        elif step == "flush":
            assert crew_log_emit.flush(timeout=default_timeout())
        elif step == "resolve_fail":
            fail_resolve["n"] = 1
        elif step == "restart":
            assert crew_log_emit.flush(timeout=default_timeout())
            work_relay_feed.persist()
            fail_listing["n"] = 1
            work_relay.forget_for_tests()
        now += 1_000
        clock.at(now)
        credits += 1.0
        _emit_closer(int(credits), credits)
        # Committed (appended) before the next disturbance; the relay may still lag.
        assert emit_runtime._writer().flush(timeout=default_timeout())
        expected[LEAD_UNIT if bound == LEAD else other_unit].append(credits)
        _live_crew_log.setattr(work_relay_feed, "QUEUE_LIMIT", 4096)

    def landed() -> bool:
        crew_log_emit.flush()
        return all(_board_charges(u) == v for u, v in expected.items())

    wait_until(landed, describe=lambda: {u: _board_charges(u) for u in expected})


def _wait_for(charges: "list[float]") -> None:
    def landed() -> bool:
        crew_log_emit.flush()
        return _charges() == charges

    wait_until(landed, describe=lambda: _charges())
    assert _charges() == charges


def test_a_failed_binding_read_is_retried_and_its_copies_keep_their_order(_live_crew_log):
    """A read that fails holds the cursor; the unit's later closers wait for the retry."""
    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    real = work_relay._bound_board
    calls = {"n": 0}

    def once(worker: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real(worker)

    _live_crew_log.setattr(work_relay, "_bound_board", once)
    _turn(2, 2.0)
    _turn(3, 3.0)
    _wait_for([1.0, 2.0, 3.0])
    work_relay_feed.persist()
    path = work_relay_feed._cursor_path(work_relay_feed._state_home)
    assert json.loads(path.read_text())["units"][WORKER_UNIT] > 0


def test_a_copy_dropped_and_requeued_by_the_writer_settles_once_it_lands(_live_crew_log):
    """The requeued follow-on carries the ``after``, so the cursor moves past it."""
    from kiro_crew.crew_log.writer import WriterLimits

    crew_log_emit.reset_caches(limits=WriterLimits(max_write_attempts=1, retry_backoff_seconds=0.0))
    # Long, so only the requeued copy's own ``after`` can release the hold here.
    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", FALLBACK_TIMEOUT_SECS)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    real = work_relay._append_run
    calls = {"n": 0}

    def fail_once(*args: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        return real(*args)

    _live_crew_log.setattr(work_relay, "_append_run", fail_once)
    _turn(2, 2.0)
    _wait_for([1.0, 2.0])
    _turn(3, 3.0)
    _wait_for([1.0, 2.0, 3.0])
    with work_relay_feed._cond:
        assert WORKER_UNIT not in work_relay_feed._held


# --------------------------------------------------------------------------- #
# The cursor file is advisory, atomic, idempotent to replay, and has one writer
# --------------------------------------------------------------------------- #


def _cursor_file() -> Any:
    return work_relay_feed._cursor_path(work_relay_feed._state_home)


def _crash_cut_closer(turn: int, credits: float) -> None:
    """Commit a closer the relay never hears about, as a crash before its wake would."""
    with pytest.MonkeyPatch.context() as crash:
        crash.setattr(work_relay_feed, "note", lambda *a, **k: None)
        _turn(turn, credits)


@pytest.mark.parametrize("damage", ["garbage", "stale"])
def test_an_unreadable_or_stale_cursor_falls_back_to_a_rescan(_live_crew_log, damage):
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    path = _cursor_file()
    _crash_cut_closer(2, 2.0)
    _settle()
    if damage == "garbage":
        path.write_text("{not json", encoding="utf-8")
    else:
        path.write_text(json.dumps({"schema": 1, "units": {WORKER_UNIT: 10_000}}))
    _restart()
    _turn(3, 3.0)
    assert _charges() == [1.0, 2.0, 3.0]


def test_the_cursor_is_replaced_whole_and_a_failed_replace_keeps_the_old_one(
    _live_crew_log,
):
    from kiro_crew import atomic_write as aw

    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    path = _cursor_file()
    before = path.read_text(encoding="utf-8")
    seen: "dict[str, Any]" = {}
    real = aw.atomic_write

    def spy(target: Any, content: Any, **kwargs: Any) -> None:
        seen.update(kwargs)
        real(target, content, **kwargs)

    def no_rename(*_a: Any, **_k: Any) -> None:
        raise OSError("crash before the rename")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(aw, "atomic_write", spy)
        patch.setattr(aw, "publish_replacement", no_rename)
        _turn(2, 2.0)
        work_relay_feed.persist()
    assert seen.get("fsync") is True, seen
    assert path.read_text(encoding="utf-8") == before, "a torn or partial cursor was left"
    assert not [p for p in path.parent.iterdir() if p.name not in ("cursor.json", "relay.lock")]


def test_replaying_from_an_old_cursor_bills_each_closer_once(_live_crew_log):
    """A cursor behind what landed replays those entries; nothing is copied or billed twice."""
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    for turn in (1, 2, 3):
        _turn(turn, float(turn))
    _settle()
    _cursor_file().write_text(json.dumps({"schema": 1, "units": {WORKER_UNIT: 1}}))
    _restart()
    _turn(4, 4.0)
    assert _charges() == [1.0, 2.0, 3.0, 4.0]
    from kiro_crew.crew_log.projection_readers import _slot_units_for_fold

    units = _slot_units_for_fold(LEAD, "workstreams")
    state = crew_log.fold_slot_warm("workstreams", units, slot=LEAD).state
    assert state["spend"][WORKER]["credits"] == 10.0
    assert state["spend"][WORKER]["reported"] == 4


def test_a_second_relayer_is_detected_and_never_writes_the_cursor(_live_crew_log, caplog):
    import logging
    import os

    from kiro_crew.platform_lock_compat import release_lock, try_acquire_lock_or_raise

    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    path = _cursor_file()
    before = path.read_text(encoding="utf-8")
    _restart()
    # Another process holds the relay lock for this data home.
    fd = os.open(path.with_name("relay.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert try_acquire_lock_or_raise(fd, exclusive=True)
        with caplog.at_level(logging.WARNING, logger="kiro_crew.crew_log.work_relay_feed"):
            _turn(2, 2.0)
            work_relay_feed.persist()
        assert "another process holds" in caplog.text
        assert path.read_text(encoding="utf-8") == before
    finally:
        release_lock(fd)
        os.close(fd)


def test_an_entry_older_than_every_binding_seen_is_logged_not_silent(_live_crew_log, caplog):
    """No interval covers the entry's time while the worker is bound: warned, not copied."""
    import logging

    _bind_to(LEAD, 2_000_000)
    with caplog.at_level(logging.WARNING, logger="kiro_crew.crew_log.work_relay"):
        assert work_relay._bound_at(WORKER, 1_000_000, unit=WORKER_UNIT) == ""
    assert "predates every binding" in caplog.text
    assert WORKER_UNIT in caplog.text and "1000000" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="kiro_crew.crew_log.work_relay"):
        assert work_relay._bound_at(WORKER, 3_000_000, unit=WORKER_UNIT) == LEAD
    assert "predates" not in caplog.text


# --------------------------------------------------------------------------- #
# Retry order, no-cursor catch-up after a rebind, an incomplete startup listing
# --------------------------------------------------------------------------- #


def test_a_due_retry_is_read_before_a_newer_wake_of_its_unit(_live_crew_log):
    """The held closer and the ones after it land in order, ahead of a newer wake."""
    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", FALLBACK_TIMEOUT_SECS)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    real = work_relay.resolve
    calls = {"n": 0}

    def fail_first(*args: Any, **kwargs: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("binding read failed")
        return real(*args, **kwargs)

    _live_crew_log.setattr(work_relay, "resolve", fail_first)
    _turn(1, 1.0)  # fails: held
    _turn(2, 2.0)  # its wake waits for the retry
    captured: "list[Any]" = []
    real_note = work_relay_feed.note

    def capture(*args: Any, **kwargs: Any) -> None:
        captured.append((args, kwargs))

    _live_crew_log.setattr(work_relay_feed, "note", capture)
    _turn(3, 3.0)  # committed; its wake is handed to the pass below by hand
    _live_crew_log.setattr(work_relay_feed, "note", real_note)
    [(args, kwargs)] = captured
    unit, entry_type, seq, data, time_ms = args
    wake = work_relay_feed._Wake(
        work_relay_feed._state_home, unit, entry_type, seq, data, time_ms, **kwargs
    )
    with work_relay_feed._cond:
        work_relay_feed._retry_at[WORKER_UNIT] = 0.0
        retries = work_relay_feed._take_retries_locked()
    assert WORKER_UNIT in retries
    work_relay_feed._pass([wake], {}, retries, False)
    _settle()
    assert _charges() == [1.0, 2.0, 3.0]


def test_a_no_cursor_unit_caught_up_after_a_rebind_charges_its_first_board(_live_crew_log):
    """Overflow on a unit's first closer, then a rebind: the closer keeps its board."""
    clock = _Clock(_live_crew_log)
    other, other_unit = "chat-relay-feed-other", "u-feed-other"
    second, second_unit = "chat-relay-feed-second", "u-feed-second"
    _board()
    _unit(other_unit, other)
    data = {"slot": other, "actor": "conductor", "by": other, "action": "goal", "goal": "o"}
    assert crew_log_emit.on_work_recorded(other_unit, {**data, "round": 1})
    _unit(WORKER_UNIT, WORKER)
    _unit(second_unit, second)
    _bind_to(LEAD, 1_000_000)
    _bind_to(LEAD, 1_000_000, item="it_0000000c", worker=second)
    gate = _Gate(_live_crew_log)
    clock.at(2_000_000)
    _emit_closer(1, 9.0, unit=second_unit)
    gate.wait_parked()  # the consumer is busy with another unit
    _live_crew_log.setattr(work_relay_feed, "QUEUE_LIMIT", 0)
    _emit_closer(1, 1.0)  # WORKER's first closer, no cursor: falls behind
    from kiro_crew.crew_log import emit_runtime

    assert emit_runtime._writer().flush(timeout=default_timeout())
    _live_crew_log.setattr(work_relay_feed, "QUEUE_LIMIT", 4096)
    _bind_to(other, 3_000_000, item="it_0000000b")
    gate.open()
    _settle()
    assert _board_charges(LEAD_UNIT) == [9.0, 1.0]
    assert _board_charges(other_unit) == []


def test_an_incomplete_unit_listing_at_startup_is_scanned_again(_live_crew_log):
    """A listing that fails strict is incomplete; a live closer cannot pass the missed one."""
    from kiro_crew.crew_log import store

    _live_crew_log.setattr(work_relay_feed, "_RETRY_SECONDS", 0)
    _board()
    _unit(WORKER_UNIT, WORKER)
    _bind()
    _turn(1, 1.0)
    work_relay_feed.persist()
    _crash_cut_closer(2, 2.0)
    _settle()
    real = store.session_units_by_slot
    fails = {"n": 1}

    def flaky(*, strict: bool = False) -> Any:
        if fails["n"] and sys._getframe(1).f_code.co_name == "_bound_units":
            fails["n"] -= 1
            if strict:
                from kiro_crew.crew_log.errors import CODE_BAD_HEADER, CrewLogError

                raise CrewLogError("a unit header will not read", code=CODE_BAD_HEADER)
            return {}  # the plain listing quietly leaves the unit out
        return real(strict=strict)

    _live_crew_log.setattr(store, "session_units_by_slot", flaky)
    _restart()
    _turn(3, 3.0)
    _wait_for([1.0, 2.0, 3.0])
    assert fails["n"] == 0
