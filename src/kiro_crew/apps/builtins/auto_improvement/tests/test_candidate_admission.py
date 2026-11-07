"""The opt-in evidence checker is consulted on both keep paths before anything is committed."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ..spine import candidate_admission as A
from ..spine import driver as D
from ..spine import ledger as L
from ..spine.contracts import BugGateResult, Candidate, Proposal
from ..spine.driver_git import _git
from ..spine.keeper import KEPT

_CHECKER = """\
import hashlib, json, os, sys
if "GITHUB_TOKEN" in os.environ:
    sys.exit(9)
raw = sys.stdin.buffer.read()
mode = sys.argv[1]
answer = {"request_sha256": hashlib.sha256(raw).hexdigest(), "status": "admitted"}
if mode == "rejected":
    answer["status"] = "rejected"
elif mode == "cached":
    answer["request_sha256"] = "0" * 64
elif mode == "bad_json":
    answer = "not an object"
elif mode == "oversize":
    answer["padding"] = "x" * 70000
elif mode == "nonzero":
    sys.exit(3)
elif mode == "deep":
    sys.stdout.write("[" * 60000)
    sys.exit(0)
elif mode == "writes":
    open(os.path.join(os.path.dirname(sys.argv[0]), "report.txt"), "w").close()
print(json.dumps(answer))
"""


class ReachedCommit(Exception):
    """The candidate got as far as the provisional commit."""


@pytest.fixture
def spawns(tmp_path, monkeypatch) -> list[dict]:
    """Record each sandbox request and run the checker unwrapped (the sandbox is not the
    subject here; `test/test_spawn_audit.py` pins that the spawn is routed)."""
    calls: list[dict] = []

    def _passthrough(argv, mode="standard", **kwargs):
        calls.append({"mode": mode, **kwargs})
        cleanup = tmp_path / "launcher-cleanup"
        cleanup.touch()
        return list(argv), {"PATH": "/usr/bin:/bin", "GITHUB_TOKEN": "t"}, str(cleanup)

    monkeypatch.setattr(A, "sandboxed_spawn_argv", _passthrough)
    monkeypatch.setattr(A, "write_protected_targets", lambda: ("/masked",))
    return calls


def _driver(tmp_path: Path, monkeypatch, mode: str | None) -> tuple[D.Driver, Proposal]:
    _git(["init", "-q"], tmp_path)
    script = tmp_path / "checker.py"
    script.write_text(_CHECKER, encoding="utf-8")
    driver = D.Driver.__new__(D.Driver)
    driver.clone = tmp_path
    driver.branch = "feature/x"
    driver.profile = SimpleNamespace(
        id="p", ruler=SimpleNamespace(primary_name="m", direction="minimize")
    )
    driver.candidate_admission = (
        None if mode is None else A.parse_command([sys.executable, "-I", str(script), mode])
    )
    driver.head_sha = Mock(return_value="b" * 40)
    driver.stats = D.Stats()
    driver.ledger = L.Ledger(tmp_path / "ledger.jsonl")
    driver.log = logging.getLogger(__name__)
    driver.archive = Mock()
    driver.measurer = SimpleNamespace(reps=1)
    driver._publishing_is_halted = Mock(return_value=False)
    driver._commit_winner_provisional = Mock(side_effect=ReachedCommit)
    driver._commit_bug_winner_provisional = Mock(side_effect=ReachedCommit)
    monkeypatch.setattr(D, "_git", Mock(return_value=SimpleNamespace(stdout="b" * 40)))
    winner = Proposal("c1", Candidate("perf", "mod::fn"), tmp_path, "b", "d", "diff")
    return driver, winner


def _keep(driver: D.Driver, winner: Proposal, track: str) -> None:
    winner.candidate.kind = track
    if track == "perf":
        verdict = SimpleNamespace(keep=True, winner=winner, reason="win", measurement=None)
        driver._apply_verdict(1, "b" * 40, verdict, [(winner, KEPT, None)], 1, {})
    else:
        driver._apply_bug_winner(1, winner, BugGateResult(passed=True, reason="bug_filed"))


def _note(driver: D.Driver, winner: Proposal) -> tuple[str, str]:
    row = driver.ledger._seen[L.fingerprint(kind=winner.candidate.kind, target="mod::fn")]
    return row.status, row.note


@pytest.mark.parametrize("track", ["perf", "bug"])
def test_unset_keeps_both_tracks_as_today(tmp_path, monkeypatch, spawns, track):
    driver, winner = _driver(tmp_path, monkeypatch, None)
    with pytest.raises(ReachedCommit):
        _keep(driver, winner, track)
    assert spawns == []


@pytest.mark.parametrize("track", ["perf", "bug"])
def test_an_admitted_candidate_reaches_the_commit_through_the_strict_sandbox(
    tmp_path, monkeypatch, spawns, track
):
    driver, winner = _driver(tmp_path, monkeypatch, "admitted")
    with pytest.raises(ReachedCommit):
        _keep(driver, winner, track)
    assert spawns == [{"mode": "strict", "extra_hidden_dirs": ("/masked",)}]
    assert not (tmp_path / "launcher-cleanup").exists()


@pytest.mark.parametrize("track", ["perf", "bug"])
@pytest.mark.parametrize(
    ("mode", "why"),
    [
        ("rejected", "rejected"),
        ("cached", "invalid response"),
        ("bad_json", "invalid response"),
        ("oversize", "invalid response"),
        ("nonzero", "exit 3"),
        ("deep", "invalid response"),
    ],
)
def test_a_refusal_never_reaches_the_commit(tmp_path, monkeypatch, spawns, track, mode, why):
    driver, winner = _driver(tmp_path, monkeypatch, mode)
    _keep(driver, winner, track)
    driver._commit_winner_provisional.assert_not_called()
    driver._commit_bug_winner_provisional.assert_not_called()
    assert (driver.stats.kept, driver.stats.not_kept) == (0, 1)
    assert _note(driver, winner) == (L.STATUS_FAILED_VERIFY, f"candidate admission refused: {why}")


def test_head_moving_during_the_check_refuses(tmp_path, monkeypatch, spawns):
    driver, winner = _driver(tmp_path, monkeypatch, "admitted")
    driver.head_sha = Mock(side_effect=["b" * 40, "c" * 40])
    assert A.admit_candidate(driver, winner) is False
    assert (
        _note(driver, winner)[1]
        == "candidate admission refused: the clone changed during the check"
    )


def test_a_file_written_into_the_clone_during_the_check_refuses(tmp_path, monkeypatch, spawns):
    driver, winner = _driver(tmp_path, monkeypatch, "writes")
    assert A.admit_candidate(driver, winner) is False
    assert (
        _note(driver, winner)[1]
        == "candidate admission refused: the clone changed during the check"
    )


@pytest.mark.parametrize(
    ("error", "why"),
    [
        (subprocess.TimeoutExpired("checker", A.TIMEOUT_S), "timed out"),
        (RuntimeError("no sandbox backend"), "could not run"),
    ],
)
def test_a_checker_that_cannot_answer_refuses(tmp_path, monkeypatch, spawns, error, why):
    driver, winner = _driver(tmp_path, monkeypatch, "admitted")
    monkeypatch.setattr(A, "run_limited", Mock(side_effect=error))
    assert A.admit_candidate(driver, winner) is False
    assert _note(driver, winner)[1] == f"candidate admission refused: {why}"


@pytest.mark.parametrize("value", ["/bin/check", [], ["relative/check"], ["/bin/check", 1]])
def test_a_malformed_command_refuses_to_start(value):
    with pytest.raises(ValueError, match="candidateAdmissionCommand"):
        A.parse_command(value)
