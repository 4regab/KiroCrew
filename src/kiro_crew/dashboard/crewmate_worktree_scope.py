"""Point a crewmate's worker at the crewmate's own worktree, at the start of its turn.

A worker session a crewmate opened (a ``template`` record on
``kirocrew-mate-worker`` carrying the crewmate's ``member_id``) gets its
``slot.project`` -- its cwd and project scope -- set to the crewmate's own git
worktree of the repository the turn is about (:mod:`kiro_crew.crewmate_worktree`).
The repository is read from the paths this turn's message names, then from the
slot's current project; the first one inside a git repository wins. A path in
the worktree the worker already runs in (or in that repository's own checkout)
leaves it where it is, so its session is not reset for nothing. A project a person
picked for the session (``slot._project_picked``, held on the transcript binding
and set by ``api_chat_slot_project``) is kept as it is.

Called by turn admission (``chat_turn.turn_admission``) right after the
crewmate's Profile Permissions are seeded and before the provider is claimed, so
a first turn, a restored slot and a resumed one alike start in the worktree. A
change of project on a session that already runs arms the same deferred reset
``set_project`` arms, so the next spawn takes the new cwd. Git failing leaves the
turn on its current project.

A worker whose session is closed loses the worktrees that were its own (a
repository the edition lays out per worker): :func:`release_closed_worker`,
called by ``chat_api.slot_lifecycle.close_slot`` once the close commits. The
cleanup removes nothing once a slot holds the worker's key again, and a turn of a
reopened worker first waits for a cleanup still running and takes the project it
moved (:func:`_settle_cleanup`), so it never runs in a folder being removed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Longest a reopened worker's turn waits for its own close cleanup to finish.
_CLEANUP_WAIT_SECS = 120.0
#: Most project moves kept for workers whose next turn has not come yet.
_MAX_KEPT_MOVES = 256


def _worker_crewmate(config: Any, execution: Any) -> str:
    """The crewmate a WORKER session runs for, else ``""`` (a conductor answers ``""``)."""
    from kiro_crew.dashboard.crewmate_approval import crewmate_name_for
    from kiro_crew.mate_worker import MATE_WORKER_AGENT

    if (
        getattr(execution, "selection_kind", "") != "template"
        or getattr(execution, "template_id", "") != MATE_WORKER_AGENT
    ):
        return ""
    return crewmate_name_for(config, execution)


def resolve_worker_project(
    config: Any, name: str, project: str, message: str, worker: str = ""
) -> "Any | None":
    """The crewmate worktree this worker turn should run in, else ``None``. Blocking.

    *worker* is the worker's slot key: a repository the edition lays out per
    worker gets this worker's own worktree.
    """
    from kiro_crew import crewmate_worktree as cw
    from kiro_crew.config.loader import workspace_dir_for
    from kiro_crew.members import member_slug

    record = config.agents.get(name)
    if record is None:
        return None
    slug = member_slug(name, config)
    ws_dir = str(workspace_dir_for(getattr(record, "workspace", "") or None))
    prefix = config.agent.member_branch_prefix
    candidates = [*cw.paths_in(message), *([project] if project else [])]
    for candidate in candidates:
        try:
            found = cw.ensure(slug, ws_dir, candidate, prefix, worker)
        except cw.WorktreeRefused as exc:
            logger.warning("Bot %s: no worktree for %s: %s", name, candidate, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - this candidate only; try the next
            if type(exc).__name__ == "SandboxUnavailable":
                # No sandbox for git at all: no candidate can have a worktree.
                logger.warning("Bot %s: worktree lookup failed", name, exc_info=True)
                return None
            logger.warning("Bot %s: no worktree for %s", name, candidate, exc_info=True)
            continue
        if found is None:
            continue
        real_project = os.path.realpath(project) if project else ""
        if real_project and cw._contains(found.path, real_project):
            # Already in this worktree: a path of the same repository moves nothing.
            return cw.CrewmateWorktree(found.repo, found.path, found.branch, project)
        return found
    return None


async def scope_worker_turn(
    state: Any, slot: Any, config: Any, execution: Any, message: str
) -> str:
    """Move *slot*'s project to its crewmate's worktree; return the new project or ``""``."""
    name = _worker_crewmate(config, execution)
    if not name:
        return ""
    # Even a picked project waits: a cleanup still removing must not run under the turn.
    await _settle_cleanup(state, slot)
    if getattr(slot, "_project_picked", False):
        # A project a person picked for this session is kept as it is.
        return ""
    project = getattr(slot, "project", "") or ""
    found = await asyncio.to_thread(
        resolve_worker_project, config, name, project, message or "", slot.key
    )
    if found is None or found.cwd == project:
        return ""
    if getattr(slot, "_project_picked", False):
        # A pick landed during the lookup: it stands.
        return ""
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot.project = found.cwd
    key = effective_session_key(slot)
    sessions = getattr(state, "sessions", None)
    if sessions is not None and sessions.get_provider(key) is not None:
        from kiro_crew.dashboard.transcript_binding import arm_project_reset

        arm_project_reset(state, slot, key)
    slot._dirty = True
    push = getattr(state, "push_slots_update", None)
    if push is not None:
        try:
            push()
        except Exception:  # noqa: BLE001 - a missed push is repaired by the next one
            logger.debug("push_slots_update failed", exc_info=True)
    logger.info("Bot %s worker %s runs in %s (%s)", name, slot.key, found.cwd, found.branch)
    return found.cwd


async def _settle_cleanup(state: Any, slot: Any) -> None:
    """Wait for every close cleanup of *slot*'s worker still running, then take the project moved.

    Two quick closes leave two cleanups; the turn waits for both, so an older one
    cannot remove a worktree after this turn starts in it. The wait is bounded by
    :data:`_CLEANUP_WAIT_SECS`; a cleanup still running past
    it leaves the turn to the lookup that follows, as for any project that moved.
    A finished cleanup points a live slot at the moved project itself, or leaves
    the move in ``state._worker_moves`` (:func:`_remember_move`) for this turn to
    take, so a worker resumed while the removal ran still gets the checkout path
    however late its first turn comes.
    """
    cleanups = getattr(state, "_worker_cleanups", None)
    pending = tuple(cleanups.get(slot.key, ())) if cleanups else ()
    if pending:
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.gather(*pending, return_exceptions=True)),
                _CLEANUP_WAIT_SECS,
            )
        except Exception:  # noqa: BLE001 - a slow or failed cleanup moves nothing here
            logger.warning("worker %s: its close cleanup did not finish", slot.key, exc_info=True)
            return
    moves = getattr(state, "_worker_moves", None)
    moved = moves.pop(slot.key, None) if moves else None
    if moved and _on_moved_project(slot, moved):
        slot.project = moved[1]
        slot._dirty = True


def _release_worker(
    log: Any,
    worker: str,
    session_key: str,
    history_key: str,
    saved: str,
    still_closed: Callable[[], bool] = lambda: True,
) -> tuple[str, str] | None:
    """Remove *worker*'s own worktrees, when its session is a crewmate's worker. Blocking.

    *saved* is the project the closed session was saved with. The session's
    execution record is read here, durable record included, so a worker whose
    carrier is not in memory (a persistent session, or one restored after a restart
    that has run no turn) is recognised too.

    When the project sits in one of the worker's worktrees, the saved project is
    rewritten to the same folder in the repository's checkout BEFORE anything is
    removed. A restart at any point after that resumes on the checkout path, where
    the next turn's lookup reuses the worktree if it is still there and rebuilds it
    on the worker's own branch if it is gone. The move is returned as
    ``(saved, checkout)``. Nothing is touched once *still_closed* answers false (a
    slot holds the worker's key again).
    """
    from kiro_crew import crewmate_worktree as cw
    from kiro_crew.config.loader import KiroCrewConfig
    from kiro_crew.execution_context import read_session_execution

    try:
        execution = read_session_execution(session_key)
        config = KiroCrewConfig.load()
        name = _worker_crewmate(config, execution)
        own = cw.worker_folder_for(name, config, worker) if name else None
        in_own = bool(saved and own and cw._contains(own, os.path.realpath(saved)))
        # Only a project in the worker's own folder is moved: the Bot's shared
        # worktree is never removed on a close, so its path stays valid.
        source = cw.source_path(saved) if in_own else ""
    except Exception:  # noqa: BLE001 - a close never fails on this
        logger.warning("worker %s: its worktrees were not looked at", worker, exc_info=True)
        return None
    if not name or not still_closed():
        return None
    move = (saved, source) if source and source != saved else None
    if move is not None and not os.path.isdir(source):
        # A folder only the worker's branch has: the checkout path would resume one
        # level up, so the worktree holding it stays.
        logger.warning(
            "worker %s: %s is not in the checkout; its worktrees are kept", worker, saved
        )
        return None
    if in_own and move is None:
        # The saved project names a worktree of this worker that maps to no
        # checkout (a layout other than the one the edition gives now): removing it would
        # leave the saved project on a deleted folder with nothing to rebuild from.
        logger.warning("worker %s: %s maps to no checkout; its worktrees are kept", worker, saved)
        return None
    if move is not None:
        try:
            written = log.update_metadata_if(
                history_key, {"project": source}, lambda meta: meta.get("project") == saved
            )
        except Exception:  # noqa: BLE001 - keep the worktree the saved project names
            logger.warning("worker %s: its saved project was not moved", worker, exc_info=True)
            return None
        if not written:
            # A later save names another project; that close's own cleanup handles it.
            return None
    cw.remove_for_worker(name, config, worker, still_closed)
    return move


def _on_moved_project(slot: Any, move: tuple[str, str]) -> bool:
    """Whether *slot* still runs in the project *move* came from, and no person picked it."""
    return not getattr(slot, "_project_picked", False) and getattr(slot, "project", "") == move[0]


def _table(state: Any, name: str) -> dict:
    """The per-worker table *name* on *state*, made once; never replaced, even when empty."""
    table = getattr(state, name, None)
    if table is None:
        table = {}
        setattr(state, name, table)
    return table


def _remember_move(state: Any, worker: str, move: tuple[str, str]) -> None:
    """Point a live slot of *worker* still on the moved project at its new path, else keep it.

    A kept move waits in ``state._worker_moves`` for the worker's next turn (a resume
    still building its slot is not in the map yet). The table keeps at most
    :data:`_MAX_KEPT_MOVES` entries, dropping the oldest first; one dropped only
    loses the in-memory shortcut, since the saved project was rewritten already.
    """
    live = getattr(state, "_slots", {}).get(worker)
    if live is not None and getattr(live, "_project_picked", False):
        return  # a person's pick stands over any cleanup's move
    if live is not None and _on_moved_project(live, move):
        live.project = move[1]
        live._dirty = True
        return
    moves: dict[str, tuple[str, str]] = _table(state, "_worker_moves")
    moves.pop(worker, None)
    moves[worker] = move
    while len(moves) > _MAX_KEPT_MOVES:
        moves.pop(next(iter(moves)))


def release_closed_worker(state: Any, slot: Any, session_key: str) -> None:
    """Remove a closed worker's own worktrees in the background; a crewmate's workers only.

    Called once *slot*'s close has committed, with the session key it ran under.
    Nothing runs when a slot holds its key again (the session was reopened), or
    when a person picked its project, which no worktree is ever applied over.
    Two closes of one worker in quick succession may run their cleanups at once:
    the removal of each worktree holds that worktree's lookup lock, the saved
    project is rewritten only while it still names what this close saved, and the
    worker's next turn waits for all of them (:func:`_settle_cleanup`). The
    Bot's shared worktrees are left alone: they go with the Bot.
    """
    from kiro_crew.dashboard.chat_utils import slot_history_key
    from kiro_crew.mate_worker import MATE_WORKER_AGENT

    worker = slot.key
    if (
        str(getattr(slot, "agent", "") or "") != MATE_WORKER_AGENT
        or state.get_slot(worker) is not None
        or getattr(slot, "_project_picked", False)
    ):
        return
    cleanups: dict[str, set[asyncio.Task]] = _table(state, "_worker_cleanups")
    saved = getattr(slot, "project", "") or ""
    history_key = slot_history_key(slot)

    def still_closed() -> bool:
        # Read from the worker thread: a dict membership test and a lookup, no
        # await. The raw map also sees a resume still constructing its slot.
        return state.get_slot(worker) is None and worker not in getattr(state, "_slots", {})

    task = asyncio.create_task(
        asyncio.to_thread(
            _release_worker,
            state.conversation_log,
            worker,
            session_key,
            history_key,
            saved,
            still_closed,
        )
    )
    cleanups.setdefault(worker, set()).add(task)

    def finished(done: asyncio.Task) -> None:
        outstanding = cleanups.get(worker)
        if outstanding is not None:
            outstanding.discard(done)
            if not outstanding:
                del cleanups[worker]
        if not done.cancelled() and done.exception() is None and done.result():
            _remember_move(state, worker, done.result())

    task.add_done_callback(finished)
    background = getattr(state, "_background_tasks", None)
    if background is not None:
        background.add(task)
        task.add_done_callback(background.discard)
