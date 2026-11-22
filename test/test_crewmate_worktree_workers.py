# SPDX-License-Identifier: Apache-2.0
"""An edition can give each Bot worker its own worktree, set it up, and tidy up after it.

``AgentRuntime.worker_worktree_subpath(repo)`` names where each worker's own
worktree goes, below ``worktrees/<slug>/workers/<worker>/``; ``prepare_worktree``
runs after each ``git worktree add`` the gateway makes and ``release_worktree``
after each ``git worktree remove``. A worker's own worktrees go when its session
is closed, and a worker reopened or a gateway restarted at any point of that
cleanup ends up back in its worktree. The shared per-Bot layout is covered in
``test_crewmate_worktree``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from chat_test_helpers import _make_state
from crewmate_worktree_helpers import (  # noqa: F401 - fixtures
    _commit_in,
    _git,
    _init,
    edition_runtime_fixture,
    git_here_fixture,
    repo_fixture,
)
from test_crewmate_worktree import _scope_env

from kiro_crew import crewmate_worktree as cw
from kiro_crew.mate_worker import MATE_WORKER_AGENT
from kiro_crew.platform.defaults import DefaultAgentRuntime

#: Ceiling that only turns a hang into a named failure; every wait here is on a signal.
_HANG_SECS = 60.0


class _PerWorker(DefaultAgentRuntime):
    """A build that needs ``<workspace root>/src/<package>``, the root holding a marker file."""

    def __init__(self, subpath=None, fail_prepare: bool = False) -> None:
        self.subpath = subpath
        self.fail_prepare = fail_prepare
        self.prepared: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []

    def worker_worktree_subpath(self, repo: str):
        return self.subpath if self.subpath is not None else ("src", os.path.basename(repo))

    def prepare_worktree(self, repo: str, path: str) -> None:
        if self.fail_prepare:
            raise RuntimeError("no build workspace")
        self.prepared.append((repo, path))
        if cw.WORKERS_DIR in path.split(os.sep):
            root = os.path.dirname(os.path.dirname(path))
            with open(os.path.join(root, "packageInfo"), "w", encoding="utf-8") as fh:
                fh.write("workspace\n")

    def release_worktree(self, repo: str, path: str) -> None:
        self.released.append((repo, path))
        root = os.path.dirname(os.path.dirname(path))
        if cw.WORKERS_DIR in path.split(os.sep) and not os.listdir(os.path.dirname(path)):
            os.remove(os.path.join(root, "packageInfo"))


def test_each_worker_gets_its_own_worktree_and_branch_at_the_editions_layout(
    repo, tmp_path, edition_runtime
):
    runtime = _PerWorker()
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    real = os.path.realpath(repo)

    first = cw.ensure("atlas", ws, os.path.join(repo, "docs"), worker="w1")
    second = cw.ensure("atlas", ws, repo, worker="w2")

    root = os.path.join(os.path.realpath(ws), "worktrees", "atlas", "workers")
    assert first.path == os.path.join(root, "w1", "src", "tasklog")
    assert first.branch == "crew/atlas/w1/tasklog"
    assert first.cwd == os.path.join(first.path, "docs")
    assert second.path == os.path.join(root, "w2", "src", "tasklog")
    assert second.branch == "crew/atlas/w2/tasklog"
    assert _git(first.path, "branch", "--show-current") == "crew/atlas/w1/tasklog"
    assert runtime.prepared == [(real, first.path), (real, second.path)]
    # Reused as the worker left it: no second add, so no second prepare.
    assert cw.ensure("atlas", ws, repo, worker="w1").path == first.path
    assert len(runtime.prepared) == 2
    # A caller that is no worker still gets the Bot's shared worktree.
    shared = cw.ensure("atlas", ws, repo)
    assert shared.path == cw.path_for(ws, "atlas", repo) and shared.branch == "crew/atlas/tasklog"
    # Both layouts map back to the checkout for the repository's AWS profile binding.
    assert cw.source_path(first.cwd) == os.path.join(real, "docs")
    assert cw.source_path(shared.path) == real


def test_the_public_build_keeps_one_shared_worktree_and_hooks_that_do_nothing(repo):
    runtime = DefaultAgentRuntime()
    assert runtime.worker_worktree_subpath(repo) is None
    assert runtime.prepare_worktree(repo, "/x") is None
    assert runtime.release_worktree(repo, "/x") is None


def test_a_workers_turn_moves_into_its_own_worktree(repo, tmp_path, monkeypatch, edition_runtime):
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    scope, config = _scope_env(monkeypatch, ws)
    monkeypatch.setattr(scope, "_worker_crewmate", lambda _c, _e: "Atlas")
    state = SimpleNamespace(sessions=SimpleNamespace(get_provider=lambda _k: None))
    slot = SimpleNamespace(project=repo, key="w1", _dirty=False)

    out = asyncio.run(scope.scope_worker_turn(state, slot, config, None, f"edit {repo}/docs"))

    expected = os.path.join(cw.worker_path_for(ws, "atlas", "w1", ("src", "tasklog")), "docs")
    assert out == expected and slot.project == expected
    assert _git(expected, "branch", "--show-current") == "crew/atlas/w1/tasklog"


@pytest.mark.parametrize(
    "answer", [(), ("..",), ("a/b",), ("a", "b", "c", "d"), "src", ("src", 3), ["x y"]]
)
def test_an_answer_that_is_not_a_layout_keeps_the_shared_worktree(
    repo, tmp_path, edition_runtime, answer, caplog
):
    edition_runtime(_PerWorker(subpath=answer))
    ws = str(tmp_path / "ws-atlas")
    with caplog.at_level(logging.WARNING, logger=cw.logger.name):
        found = cw.ensure("atlas", ws, repo, worker="w1")
    assert found.path == cw.path_for(ws, "atlas", repo)
    assert "is not a layout" in caplog.text


def test_an_edition_that_cannot_answer_keeps_the_shared_worktree(repo, tmp_path, edition_runtime):
    class _Raises(DefaultAgentRuntime):
        def worker_worktree_subpath(self, repo: str):
            raise RuntimeError("boom")

    class _Older:  # written before these methods existed
        def works_in_place(self, repo: str) -> bool:
            return False

    for slug, runtime in (("atlas", _Raises()), ("briar", _Older())):
        edition_runtime(runtime)
        ws = str(tmp_path / f"ws-{slug}")
        assert cw.ensure(slug, ws, repo, worker="w1").path == cw.path_for(ws, slug, repo)


def test_a_failed_prepare_undoes_the_add_and_a_later_turn_tries_again(
    repo, tmp_path, edition_runtime
):
    runtime = _PerWorker(fail_prepare=True)
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    dest = cw.worker_path_for(ws, "atlas", "w1", ("src", "tasklog"))

    with pytest.raises(cw.WorktreeRefused, match="could not prepare"):
        cw.ensure("atlas", ws, repo, worker="w1")
    assert not os.path.exists(dest)
    assert _git(repo, "branch", "--list", "crew/*") == ""
    assert dest not in _git(repo, "worktree", "list")

    runtime.fail_prepare = False
    assert cw.ensure("atlas", ws, repo, worker="w1").path == dest
    assert runtime.prepared == [(os.path.realpath(repo), dest)]


def test_a_workers_close_removes_only_its_own_clean_worktrees(repo, tmp_path, edition_runtime):
    runtime = _PerWorker()
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    other = _init(tmp_path / "shared" / "other")
    clean = cw.ensure("atlas", ws, repo, worker="w1")
    dirty = cw.ensure("atlas", ws, other, worker="w1")
    with open(os.path.join(dirty.path, "README.md"), "a", encoding="utf-8") as fh:
        fh.write("unsaved\n")
    sibling = cw.ensure("atlas", ws, repo, worker="w2")
    shared = cw.ensure("atlas", ws, repo)

    removed, kept = cw.remove_worker_worktrees(ws, "atlas", "w1")

    assert removed == [clean.path] and kept == [dirty.path]
    assert runtime.released == [(os.path.realpath(repo), clean.path)]
    assert not os.path.exists(clean.path)
    # The worker's folder stays only for the dirty worktree it still holds.
    assert os.path.exists(os.path.join(dirty.path, "README.md"))
    for path in (sibling.path, shared.path):
        assert os.path.isdir(path)
    assert _git(repo, "branch", "--list", "crew/atlas/w1/tasklog")  # branches stay

    os.remove(os.path.join(dirty.path, "README.md"))
    _git(dirty.path, "checkout", "--", "README.md")
    assert cw.remove_worker_worktrees(ws, "atlas", "w1") == ([dirty.path], [])
    assert not os.path.exists(os.path.dirname(os.path.dirname(dirty.path)))
    assert cw.remove_worker_worktrees(ws, "atlas", "nobody") == ([], [])


def _remove_workers_worktrees(ws: str, bot_deletion: bool) -> tuple[list[str], list[str]]:
    """Worker ``w1``'s worktrees removed by its close, or by deleting the Bot."""
    if bot_deletion:
        return cw.remove_crewmate_worktrees(ws, "atlas")
    return cw.remove_worker_worktrees(ws, "atlas", "w1")


@pytest.mark.parametrize("bot_deletion", [False, True])
def test_removal_keeps_a_detached_worktree_whose_commit_is_on_no_branch(
    repo, tmp_path, edition_runtime, bot_deletion
):
    """``worktree remove`` drops the worktree's HEAD reflog, the commit's last reference."""
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    _git(found.path, "checkout", "-q", "--detach")
    _commit_in(found.path, "orphan.txt", "a commit on no branch")
    orphan = _git(found.path, "rev-parse", "HEAD")
    assert _git(repo, "branch", "--contains", orphan) == ""
    assert _remove_workers_worktrees(ws, bot_deletion) == ([], [found.path])
    assert _git(found.path, "rev-parse", "HEAD") == orphan


@pytest.mark.parametrize("worker", ["", "w1"])
def test_bot_deletion_removes_a_clean_worktree_on_any_branch(
    repo, tmp_path, edition_runtime, worker
):
    """The Bot is gone, so no later worktree depends on the branch; the branch stays."""
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker=worker)
    _git(found.path, "switch", "-q", "-c", "someone-elses")
    assert cw.remove_crewmate_worktrees(ws, "atlas") == ([found.path], [])
    assert _git(repo, "branch", "--list", "someone-elses")


def test_a_worker_reopened_mid_cleanup_keeps_the_worktrees_not_yet_removed(
    repo, tmp_path, edition_runtime
):
    """The reopen is checked before each removal, so the turn's next worktree stays."""
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    other = _init(tmp_path / "shared" / "other")
    first, second = sorted(
        (cw.ensure("atlas", ws, repo, worker="w1"), cw.ensure("atlas", ws, other, worker="w1")),
        key=lambda found: found.path,
    )
    asked: list[bool] = []

    def still_closed() -> bool:
        asked.append(not asked)  # closed for the first removal, reopened after it
        return asked[-1]

    removed, kept = cw.remove_worker_worktrees(ws, "atlas", "w1", still_closed=still_closed)
    assert removed == [first.path] and kept == [second.path]
    assert os.path.isdir(second.path)


def test_removal_keeps_a_locked_worktree(repo, tmp_path, edition_runtime):
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    _git(repo, "worktree", "lock", found.path)
    assert cw.remove_worker_worktrees(ws, "atlas", "w1") == ([], [found.path])
    _git(repo, "worktree", "unlock", found.path)
    assert cw.remove_worker_worktrees(ws, "atlas", "w1") == ([found.path], [])


@pytest.mark.parametrize("failure", ["exit", "timeout"])
@pytest.mark.parametrize("command", ["worktree", "status", "ls-files"])
def test_removal_keeps_a_worktree_when_a_git_read_fails(
    repo, tmp_path, monkeypatch, edition_runtime, command, failure
):
    """A check git could not answer proves nothing, so the clean worktree stays."""
    from kiro_crew.dashboard.handlers import worktree as wt

    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    real_run_git = wt._run_git
    hits: list[str] = []

    def failing(args, cwd, **kw):
        if command in args and ("list" in args or command != "worktree"):
            hits.append(command)
            if failure == "timeout":
                raise subprocess.TimeoutExpired(["git", *args], 1)
            return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: broken")
        return real_run_git(args, cwd, **kw)

    monkeypatch.setattr(wt, "_run_git", failing)
    assert cw.remove_worker_worktrees(ws, "atlas", "w1") == ([], [found.path])
    assert hits  # the failing read was the one asked
    assert os.path.exists(os.path.join(found.path, "README.md"))
    monkeypatch.setattr(wt, "_run_git", real_run_git)
    assert cw.remove_worker_worktrees(ws, "atlas", "w1") == ([found.path], [])


@pytest.mark.parametrize("bot_deletion", [False, True])
def test_removal_keeps_a_tracked_edit_core_ignore_stat_hides(
    repo, tmp_path, edition_runtime, bot_deletion
):
    """``core.ignoreStat=true`` hides the edit from ``worktree remove``'s own check too."""
    edition_runtime(_PerWorker())
    _git(repo, "config", "core.ignoreStat", "true")
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    tracked = os.path.join(found.path, "docs", "a.md")
    with open(tracked, "w", encoding="utf-8") as fh:
        fh.write("unsaved work\n")
    assert _git(found.path, "status", "--porcelain") == ""  # the config hides the edit
    assert _remove_workers_worktrees(ws, bot_deletion) == ([], [found.path])
    with open(tracked, encoding="utf-8") as fh:
        assert fh.read() == "unsaved work\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows keeps no executable bit")
@pytest.mark.parametrize("bot_deletion", [False, True])
def test_removal_keeps_an_executable_bit_core_file_mode_hides(
    repo, tmp_path, edition_runtime, bot_deletion
):
    """``core.fileMode=false`` hides a chmod from ``worktree remove``'s own check too."""
    edition_runtime(_PerWorker())
    _git(repo, "config", "core.fileMode", "false")
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    tracked = os.path.join(found.path, "docs", "a.md")
    os.chmod(tracked, os.stat(tracked).st_mode | stat.S_IXUSR)
    assert _git(found.path, "status", "--porcelain") == ""  # the config hides the chmod
    assert _remove_workers_worktrees(ws, bot_deletion) == ([], [found.path])
    assert os.stat(tracked).st_mode & stat.S_IXUSR


@pytest.mark.parametrize("bot_deletion", [False, True])
def test_removal_keeps_an_untracked_file_a_status_config_hides(
    repo, tmp_path, edition_runtime, bot_deletion
):
    """``status.showUntrackedFiles=no`` hides the file from ``worktree remove``'s own check too."""
    edition_runtime(_PerWorker())
    _git(repo, "config", "status.showUntrackedFiles", "no")
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    draft = os.path.join(found.path, "docs", "draft.md")
    with open(draft, "w", encoding="utf-8") as fh:
        fh.write("unsaved work\n")
    assert _git(found.path, "status", "--porcelain") == ""  # the config hides the file
    assert _remove_workers_worktrees(ws, bot_deletion) == ([], [found.path])
    with open(draft, encoding="utf-8") as fh:
        assert fh.read() == "unsaved work\n"


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
@pytest.mark.parametrize("bot_deletion", [False, True])
def test_removal_keeps_a_worktree_holding_an_edit_hidden_by_an_index_flag(
    repo, tmp_path, edition_runtime, flag, bot_deletion
):
    """``git status`` and ``worktree remove`` both skip a flagged file's edit."""
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    found = cw.ensure("atlas", ws, repo, worker="w1")
    tracked = os.path.join(found.path, "docs", "a.md")
    _git(found.path, "update-index", flag, "docs/a.md")
    with open(tracked, "w", encoding="utf-8") as fh:
        fh.write("unsaved work\n")
    assert _git(found.path, "status", "--porcelain") == ""  # the flag hides the edit
    assert _remove_workers_worktrees(ws, bot_deletion) == ([], [found.path])
    with open(tracked, encoding="utf-8") as fh:
        assert fh.read() == "unsaved work\n"


def test_a_failed_prepare_leaves_no_checkout_where_a_tree_walk_cannot_pin(
    repo, tmp_path, monkeypatch, edition_runtime
):
    """Windows: the folder holds a checkout, so git removes it before the folder rollback."""
    from kiro_crew import pinned_fs

    monkeypatch.setattr(pinned_fs, "supports_pinned_tree_walk", lambda: False)
    runtime = _PerWorker(fail_prepare=True)
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    dest = cw.worker_path_for(ws, "atlas", "w1", ("src", "tasklog"))

    with pytest.raises(cw.WorktreeRefused, match="could not prepare"):
        cw.ensure("atlas", ws, repo, worker="w1")
    assert not os.path.exists(dest)
    assert _git(repo, "branch", "--list", "crew/*") == ""
    assert dest not in _git(repo, "worktree", "list")

    runtime.fail_prepare = False
    assert cw.ensure("atlas", ws, repo, worker="w1").path == dest
    assert runtime.prepared == [(os.path.realpath(repo), dest)]


def test_a_workers_close_keeps_a_worktree_it_moved_to_another_branch(
    repo, tmp_path, edition_runtime
):
    """A worktree made again starts on the worker's own branch, so this one is kept."""
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    own = cw.ensure("atlas", ws, repo, worker="w1")
    _git(own.path, "switch", "-q", "-c", "crew/atlas/w1/feature")
    _commit_in(own.path, "feature.txt", "feature work")

    removed, kept = cw.remove_worker_worktrees(ws, "atlas", "w1")

    assert removed == [] and kept == [own.path]
    assert os.path.exists(os.path.join(own.path, "feature.txt"))
    # Back on its own branch, the same worktree is removed as usual.
    _git(own.path, "switch", "-q", own.branch)
    assert cw.remove_worker_worktrees(ws, "atlas", "w1")[0] == [own.path]


def test_removing_the_bot_removes_its_workers_worktrees_too(repo, tmp_path, edition_runtime):
    runtime = _PerWorker()
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    own = cw.ensure("atlas", ws, repo, worker="w1")
    shared = cw.ensure("atlas", ws, repo)

    removed, kept = cw.remove_crewmate_worktrees(ws, "atlas")

    assert sorted(removed) == sorted([own.path, shared.path]) and kept == []
    assert not os.path.exists(os.path.join(os.path.realpath(ws), "worktrees", "atlas"))
    assert len(runtime.released) == 2


def test_worker_folders_never_follow_a_link(repo, tmp_path, edition_runtime):
    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    own = cw.ensure("atlas", ws, repo, worker="w1")
    outside = _init(tmp_path / "outside" / "victim")
    os.symlink(os.path.dirname(outside), os.path.join(os.path.dirname(own.path), "link"))

    removed, _kept = cw.remove_worker_worktrees(ws, "atlas", "w1")

    assert removed == [own.path]
    assert os.path.isdir(os.path.join(outside, ".git"))


def test_a_path_at_another_layout_is_not_mapped_to_the_checkout(repo, tmp_path, edition_runtime):
    runtime = _PerWorker()
    edition_runtime(runtime)
    ws = str(tmp_path / "ws-atlas")
    own = cw.ensure("atlas", ws, repo, worker="w1")
    runtime.subpath = ("pkg", "tasklog")  # the layout the edition gives now
    assert cw.source_path(own.path) == own.path


def _worker_execution():
    return SimpleNamespace(
        selection_kind="template", template_id=MATE_WORKER_AGENT, memory_mode="persistent"
    )


class _Log:
    """The conversation log's guarded metadata write, recorded."""

    def __init__(self, project: str) -> None:
        self.meta = {"project": project}

    def update_metadata_if(self, _key, fields, guard) -> bool:
        if not guard(dict(self.meta)):
            return False
        self.meta.update(fields)
        return True


def _closed_worker(key: str, project: str, **extra):
    return SimpleNamespace(**{"key": key, "agent": MATE_WORKER_AGENT, "project": project, **extra})


@pytest.mark.asyncio
async def test_closing_a_worker_removes_its_own_worktrees_and_a_resume_rebuilds_it(
    repo, tmp_path, monkeypatch, edition_runtime
):
    """A persistent worker has no in-memory carrier; its durable record is what is read."""
    from kiro_crew import execution_context
    from kiro_crew.config import loader
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    own = cw.ensure("atlas", ws, os.path.join(repo, "docs"), worker="w1")
    _commit_in(own.path, "work.txt", "the worker's commit")
    _scope, config = _scope_env(monkeypatch, ws)
    monkeypatch.setattr(loader.KiroCrewConfig, "load", classmethod(lambda _cls: config))
    assert execution_context.read_live_session_execution("dashboard:w1") is None
    read: list[str] = []

    def durable(key: str, **_kw):
        read.append(key)
        return _worker_execution()

    monkeypatch.setattr(execution_context, "read_session_execution", durable)
    monkeypatch.setattr(scope, "_worker_crewmate", lambda _c, ex: "Atlas" if ex else "")
    log = _Log(own.cwd)
    state = SimpleNamespace(get_slot=lambda _k: None, _background_tasks=set(), conversation_log=log)

    for skipped, holder in (
        (_closed_worker("w1", own.cwd, agent="kirocrew"), state),
        (_closed_worker("w1", own.cwd, _project_picked=True), state),
        (_closed_worker("w1", own.cwd), SimpleNamespace(get_slot=lambda _k: object())),
    ):
        scope.release_closed_worker(holder, skipped, "dashboard:w1")
    assert state._background_tasks == set() and os.path.isdir(own.path)

    scope.release_closed_worker(state, _closed_worker("w1", own.cwd), "dashboard:w1")
    await asyncio.gather(*state._background_tasks)

    assert read == ["dashboard:w1"]
    assert not os.path.exists(own.path)
    # The saved project is the same folder in the checkout, so a resume finds the repository.
    real = os.path.realpath(repo)
    assert log.meta["project"] == os.path.join(real, "docs")
    again = cw.ensure("atlas", ws, log.meta["project"], worker="w1")
    assert again.path == own.path and again.branch == own.branch
    assert os.path.exists(os.path.join(again.path, "work.txt"))


class _GatewayStopped(Exception):
    """Raised at a held gate by :meth:`_Lifecycle.restart`: the cleanup dies there."""


class _Lifecycle:
    """One worker with its own worktree, closed and reopened under a controllable cleanup.

    ``hold`` names a point the cleanup stops at until ``release()``: ``"read"`` (before
    the execution read, so the cleanup is queued), ``"write"`` (the saved project is
    about to move) or ``"remove"`` (the removal is about to run). ``restart()`` ends
    a held cleanup at its gate and awaits it, then drops the in-memory state the way
    a gateway restart does, keeping only the disk.
    """

    def __init__(self, repo, ws, monkeypatch, config) -> None:
        import threading

        from kiro_crew import execution_context
        from kiro_crew.dashboard import crewmate_worktree_scope as scope

        self.scope, self.repo, self.ws, self.config = scope, repo, ws, config
        self.loop = asyncio.get_running_loop()
        self.reached = asyncio.Event()
        self.go = threading.Event()
        self.go.set()
        self.hold = ""
        self.stopped = False
        self.removals = 0
        self.own = cw.ensure("atlas", ws, os.path.join(repo, "docs"), worker="w1")
        _commit_in(self.own.path, "work.txt", "the worker's commit")
        self.log = _Log(self.own.cwd)
        self._fresh_state()

        def gate(point: str) -> None:
            if self.hold == point:
                self.loop.call_soon_threadsafe(self.reached.set)
                assert self.go.wait(_HANG_SECS)
                if self.stopped:
                    raise _GatewayStopped(point)

        def read(_key, **_kw):
            gate("read")
            return _worker_execution()

        real_write, real_remove = self.log.update_metadata_if, cw.remove_for_worker

        def write(*args):
            gate("write")
            return real_write(*args)

        def remove(*args):
            gate("remove")
            self.removals += 1
            return real_remove(*args)

        monkeypatch.setattr(execution_context, "read_session_execution", read)
        monkeypatch.setattr(self.log, "update_metadata_if", write)
        monkeypatch.setattr(cw, "remove_for_worker", remove)

    def _fresh_state(self) -> None:
        self.slots: dict = {}
        self.state = SimpleNamespace(
            get_slot=self.slots.get,
            _slots=self.slots,
            _background_tasks=set(),
            conversation_log=self.log,
            sessions=SimpleNamespace(get_provider=lambda _k: None),
        )

    async def close(self, hold: str = "") -> None:
        slot = self.slots.pop("w1", None)
        project = slot.project if slot is not None else self.log.meta["project"]
        self.hold, self.reached = hold, asyncio.Event()
        if hold:
            self.go.clear()
        self.scope.release_closed_worker(self.state, _closed_worker("w1", project), "dashboard:w1")
        if hold:
            await asyncio.wait_for(self.reached.wait(), _HANG_SECS)

    def reopen(self):
        slot = SimpleNamespace(key="w1", project=self.log.meta["project"], _dirty=False)
        self.slots["w1"] = slot
        return slot

    def release(self) -> None:
        self.go.set()

    async def settle(self) -> None:
        self.release()
        await asyncio.wait_for(asyncio.gather(*self.state._background_tasks), _HANG_SECS)

    async def restart(self) -> None:
        self.stopped = True
        self.release()
        old = tuple(self.state._background_tasks)
        await asyncio.wait_for(asyncio.gather(*old, return_exceptions=True), _HANG_SECS)
        self.stopped = False
        self._fresh_state()

    async def turn(self, slot) -> None:
        execution = _worker_execution()
        await asyncio.wait_for(
            self.scope.scope_worker_turn(self.state, slot, self.config, execution, "carry on"),
            _HANG_SECS,
        )

    def assert_back_in_its_worktree(self, slot) -> None:
        assert slot.project == self.own.cwd and os.path.isdir(slot.project)
        assert _git(self.own.path, "branch", "--show-current") == self.own.branch
        assert os.path.exists(os.path.join(self.own.path, "work.txt"))


@pytest.fixture
def lifecycle_env(repo, tmp_path, monkeypatch, edition_runtime):
    """What a :class:`_Lifecycle` needs; ``async with _lifecycle(env)`` builds and settles it."""
    from kiro_crew.config import loader
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    edition_runtime(_PerWorker())
    ws = str(tmp_path / "ws-atlas")
    _scope, config = _scope_env(monkeypatch, ws)
    monkeypatch.setattr(loader.KiroCrewConfig, "load", classmethod(lambda _cls: config))
    monkeypatch.setattr(scope, "_worker_crewmate", lambda _c, ex: "Atlas" if ex else "")
    return repo, ws, monkeypatch, config


@contextlib.asynccontextmanager
async def _lifecycle(env):
    """A :class:`_Lifecycle`, its background cleanups released and awaited on exit."""
    life = _Lifecycle(*env)
    try:
        yield life
    finally:
        life.release()
        await asyncio.wait_for(asyncio.gather(*life.state._background_tasks), _HANG_SECS)


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["read", "write", "remove", "finished"])
async def test_a_worker_reopened_at_any_point_of_its_cleanup_is_back_in_its_worktree(
    lifecycle_env, point
):
    """Reopened while the cleanup is queued, about to move the project, removing, or done."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        await _reopen_at(lifecycle, point)


async def _reopen_at(lifecycle, point: str) -> None:
    await lifecycle.close(hold="" if point == "finished" else point)
    if point == "finished":
        await lifecycle.settle()
    slot = lifecycle.reopen()
    turn = asyncio.create_task(lifecycle.turn(slot))
    await lifecycle.settle()
    await turn
    lifecycle.assert_back_in_its_worktree(slot)
    # Reopened while still queued: the cleanup sees the slot and removes nothing.
    assert lifecycle.removals == (0 if point == "read" else 1)
    assert lifecycle.state._worker_cleanups == {}
    assert not getattr(lifecycle.state, "_worker_moves", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["read", "write", "remove", "finished"])
async def test_a_restart_at_any_point_of_the_cleanup_resumes_into_its_worktree(
    lifecycle_env, point
):
    """The cleanup dies with the gateway; the saved project is all the resume has."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        await _restart_at(lifecycle, point)


async def _restart_at(lifecycle, point: str) -> None:
    await lifecycle.close(hold="" if point == "finished" else point)
    if point == "finished":
        await lifecycle.settle()
    await lifecycle.restart()
    slot = lifecycle.reopen()
    await lifecycle.turn(slot)
    lifecycle.assert_back_in_its_worktree(slot)
    assert lifecycle.removals == (1 if point == "finished" else 0)


@pytest.mark.asyncio
async def test_two_quick_close_and_reopen_cycles_end_back_in_the_worktree(lifecycle_env):
    async with _lifecycle(lifecycle_env) as lifecycle:
        await _two_quick_cycles(lifecycle)


async def _two_quick_cycles(lifecycle) -> None:
    await lifecycle.close(hold="remove")
    first = lifecycle.reopen()
    await lifecycle.close()  # closed again before the first cleanup finished
    second = lifecycle.reopen()
    turn = asyncio.create_task(lifecycle.turn(second))
    await lifecycle.settle()
    await turn
    lifecycle.assert_back_in_its_worktree(second)
    assert first is not second


@pytest.mark.asyncio
async def test_a_turn_waits_for_an_older_cleanup_that_outlives_a_newer_one(lifecycle_env):
    """Closed, reopened, closed again: the second cleanup ends first, the first still removes."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        await lifecycle.close(hold="remove")  # the first cleanup, held before its removal
        first = set(lifecycle.state._background_tasks)
        lifecycle.reopen()
        await lifecycle.close()  # the second, which finishes while the first is held
        second = set(lifecycle.state._background_tasks) - first
        await asyncio.wait_for(asyncio.gather(*second), _HANG_SECS)
        slot = lifecycle.reopen()
        waiting = asyncio.create_task(lifecycle.scope._settle_cleanup(lifecycle.state, slot))
        await asyncio.sleep(0)
        assert not waiting.done()  # still held by the first cleanup
        await lifecycle.settle()
        await asyncio.wait_for(waiting, _HANG_SECS)
        await lifecycle.turn(slot)
        lifecycle.assert_back_in_its_worktree(slot)
        assert lifecycle.removals == 2  # each cleanup removed, the turn came after both


@pytest.mark.asyncio
async def test_a_project_picked_during_the_cleanup_stands_and_its_turn_waits(lifecycle_env):
    """A person picks the old worktree while it is removed: the cleanup's move does not apply."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        old = lifecycle.own.cwd
        await lifecycle.close(hold="remove")
        slot = lifecycle.reopen()
        slot.project, slot._project_picked = old, True
        turn = asyncio.create_task(lifecycle.turn(slot))
        await asyncio.sleep(0)
        assert not turn.done()  # the picked turn waits for the removal too
        await lifecycle.settle()
        await turn
        assert slot.project == old and slot._project_picked
        assert not getattr(lifecycle.state, "_worker_moves", {})


@pytest.mark.asyncio
async def test_another_workers_close_mid_cleanup_keeps_the_first_workers_move(lifecycle_env):
    async with _lifecycle(lifecycle_env) as lifecycle:
        old = lifecycle.own.cwd
        await lifecycle.close(hold="remove")
        other = _closed_worker("w2", "")
        lifecycle.scope.release_closed_worker(lifecycle.state, other, "dashboard:w2")
        await lifecycle.settle()
        # A resume that read the old project during the removal and published its
        # slot only after both cleanups finished: the kept move is still there.
        slot = SimpleNamespace(key="w1", project=old, _dirty=False)
        lifecycle.slots["w1"] = slot
        await lifecycle.turn(slot)
        lifecycle.assert_back_in_its_worktree(slot)
        assert not lifecycle.state._worker_moves


@pytest.mark.asyncio
async def test_a_saved_project_at_a_layout_the_edition_no_longer_gives_keeps_the_worktree(
    lifecycle_env, edition_runtime
):
    """No checkout to move the saved project to, so nothing it names is removed."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        edition_runtime(_PerWorker(subpath=("pkg", "tasklog")))
        await lifecycle.close()
        await lifecycle.settle()
        assert lifecycle.removals == 0 and os.path.isdir(lifecycle.own.path)
        assert lifecycle.log.meta["project"] == lifecycle.own.cwd


@pytest.mark.asyncio
async def test_a_saved_project_only_the_workers_branch_has_keeps_the_worktree(lifecycle_env):
    """A package committed on the worker's branch has no checkout folder to resume in."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        package = os.path.join(lifecycle.own.path, "newpkg")
        os.mkdir(package)
        _commit_in(package, "a.txt", "a new package")
        lifecycle.log.meta["project"] = package
        await lifecycle.close()
        await lifecycle.settle()
        assert lifecycle.removals == 0 and os.path.isdir(package)
        assert lifecycle.log.meta["project"] == package


@pytest.mark.asyncio
async def test_a_saved_project_that_moved_on_keeps_the_worktree(lifecycle_env):
    """Saved with another project since this close: that close's own cleanup owns it."""
    async with _lifecycle(lifecycle_env) as lifecycle:
        lifecycle.reopen()  # its slot still names the worktree
        lifecycle.log.meta["project"] = "/elsewhere"
        await lifecycle.close()
        await lifecycle.settle()
        assert lifecycle.removals == 0 and os.path.isdir(lifecycle.own.path)
        assert lifecycle.log.meta["project"] == "/elsewhere"


@pytest.mark.asyncio
async def test_closing_a_worker_in_the_bots_shared_worktree_leaves_its_project_alone(
    repo, tmp_path, monkeypatch, edition_runtime
):
    """The public build: nothing is removed on a close, so the saved project stays put."""
    from kiro_crew import execution_context
    from kiro_crew.config import loader
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    edition_runtime(DefaultAgentRuntime())
    ws = str(tmp_path / "ws-atlas")
    shared = cw.ensure("atlas", ws, os.path.join(repo, "docs"), worker="w1")
    assert shared.path == cw.path_for(ws, "atlas", repo)
    _scope, config = _scope_env(monkeypatch, ws)
    monkeypatch.setattr(loader.KiroCrewConfig, "load", classmethod(lambda _cls: config))
    monkeypatch.setattr(
        execution_context, "read_session_execution", lambda _k, **_kw: _worker_execution()
    )
    monkeypatch.setattr(scope, "_worker_crewmate", lambda _c, ex: "Atlas" if ex else "")
    mapped: list[str] = []
    monkeypatch.setattr(cw, "source_path", lambda p: mapped.append(p) or p)
    log = _Log(shared.cwd)
    state = SimpleNamespace(
        get_slot=lambda _k: None, _slots={}, _background_tasks=set(), conversation_log=log
    )

    scope.release_closed_worker(state, _closed_worker("w1", shared.cwd), "dashboard:w1")
    await asyncio.wait_for(asyncio.gather(*state._background_tasks), _HANG_SECS)

    assert log.meta["project"] == shared.cwd and os.path.isdir(shared.path)
    assert mapped == [] and not getattr(state, "_worker_moves", {})


def test_kept_moves_are_bounded(monkeypatch):
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    monkeypatch.setattr(scope, "_MAX_KEPT_MOVES", 3)
    state = SimpleNamespace(_slots={})
    for i in range(5):
        scope._remember_move(state, f"w{i}", (f"/old{i}", f"/new{i}"))
    assert list(state._worker_moves) == ["w2", "w3", "w4"]
    live = SimpleNamespace(project="/old9", _dirty=False)
    state._slots["w9"] = live
    scope._remember_move(state, "w9", ("/old9", "/new9"))
    assert live.project == "/new9" and live._dirty and "w9" not in state._worker_moves


def test_a_worker_turn_settles_its_close_cleanup_before_the_lookup(monkeypatch):
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    steps: list[str] = []

    async def settle(_state, _slot):
        steps.append("settle")

    def lookup(*_args):
        steps.append("lookup")

    monkeypatch.setattr(scope, "_worker_crewmate", lambda _c, _e: "Atlas")
    monkeypatch.setattr(scope, "_settle_cleanup", settle)
    monkeypatch.setattr(scope, "resolve_worker_project", lookup)
    slot = SimpleNamespace(project="/r", key="w1", _project_picked=False)
    assert asyncio.run(scope.scope_worker_turn(None, slot, None, None, "")) == ""
    assert steps == ["settle", "lookup"]


@pytest.mark.asyncio
async def test_a_close_releases_the_worker_and_an_idle_archive_does_not(tmp_path, monkeypatch):
    from kiro_crew.dashboard import chat_handlers as handlers
    from kiro_crew.dashboard import crewmate_worktree_scope as scope

    monkeypatch.setattr(handlers, "save_slot_off_loop", AsyncMock(return_value=True))
    released: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scope, "release_closed_worker", lambda _s, slot, key: released.append((slot.key, key))
    )
    state = _make_state(tmp_path)

    await handlers.close_slot(state, state.get_or_create_slot("w1"), "w1")
    await handlers.close_slot(
        state, state.get_or_create_slot("w2"), "w2", hand_back=False, release_worktrees=False
    )

    assert released == [("w1", "dashboard:w1")]
