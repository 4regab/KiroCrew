"""Behaviour of the server-side memory graph layout and its sidecar file."""

from __future__ import annotations

import json
import sqlite3
import stat
import sys
from pathlib import Path

import numpy as np
import pytest

from kiro_crew import memory_graph_layout as gl
from kiro_crew.memory_graph_sidecar import PAGE_CAP, LayoutSidecar, PlaceItem

DIM = 32
EVERYWHERE: tuple[float, float, float, float] = (-1e9, -1e9, 1e9, 1e9)


def _unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v, axis=-1, keepdims=True)).astype(np.float32)


def _topic(rng: np.random.Generator, axis: int, n: int) -> np.ndarray:
    centre = np.zeros(DIM, np.float32)
    centre[axis] = 1.0
    return _unit(centre + 0.05 * rng.standard_normal((n, DIM)).astype(np.float32))


def _make_store(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE episodic_memories (
            id TEXT PRIMARY KEY, embedding BLOB, tags TEXT DEFAULT '[]', is_deleted INTEGER DEFAULT 0
        );
        CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, embedding BLOB, is_deleted INTEGER DEFAULT 0);
        """)
    return db


def _episode(db: sqlite3.Connection, rid: str, vec: np.ndarray | None, tags=(), deleted=0) -> None:
    db.execute(
        "INSERT INTO episodic_memories VALUES (?, ?, ?, ?)",
        (rid, None if vec is None else vec.tobytes(), json.dumps(list(tags)), deleted),
    )


def _fact(db: sqlite3.Connection, key: str, vec: np.ndarray | None, deleted=0) -> None:
    db.execute(
        "INSERT INTO semantic_memory VALUES (?, ?, ?)",
        (key, None if vec is None else vec.tobytes(), deleted),
    )


@pytest.fixture
def two_topics(tmp_path: Path):
    """60 experiences about topic A, 60 about B. Topic A rows carry tags
    ``alpha0``..``alpha11`` (5 rows each, rare enough to place by), B ``beta*``."""
    rng = np.random.default_rng(3)
    store = tmp_path / "memory.db"
    db = _make_store(store)
    for i, v in enumerate(_topic(rng, 0, 60)):
        _episode(db, f"a{i}", v, tags=[f"alpha{i % 12}"])
    for i, v in enumerate(_topic(rng, 1, 60)):
        _episode(db, f"b{i}", v, tags=[f"beta{i % 12}"])
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    gl.build_sidecar(store, side.path)
    return side, rng, store


def _centre_of(side: LayoutSidecar, prefix: str) -> np.ndarray:
    ids = [i for i in side.ids_in_box(EVERYWHERE) if i.startswith(prefix) and i[1:].isdigit()]
    return np.array(list(side.positions(ids).values())).mean(0)


def _placed_by(side: LayoutSidecar, rid: str) -> str:
    db = sqlite3.connect(side.path)
    try:
        return db.execute("SELECT placed_by FROM nodes WHERE id = ?", (rid,)).fetchone()[0]
    finally:
        db.close()


def _nearer(xy, a, b) -> bool:
    xy = np.asarray(xy)
    return bool(np.linalg.norm(xy - a) < np.linalg.norm(xy - b))


# --- full build -----------------------------------------------------------


def test_build_lays_out_every_live_row(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    store = tmp_path / "memory.db"
    db = _make_store(store)
    vecs = _unit(rng.standard_normal((40, DIM)).astype(np.float32))
    for i, v in enumerate(vecs):
        _episode(db, f"e{i}", v)
    _fact(db, "lesson.x", vecs[0])
    _fact(db, "project.acme.owner", None)
    _fact(db, "fact.gone", vecs[1], deleted=1)
    _episode(db, "old_model", np.zeros(DIM * 2, np.float32))
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    stats = gl.build_sidecar(store, side.path)

    ids = set(side.ids_in_box(EVERYWHERE))
    assert "fact.gone" not in ids
    assert {"lesson.x", "project.acme.owner", "old_model"} <= ids
    assert len(ids) == stats["n"] == 43
    assert stats["placed_by"][gl.PLACED_VECTOR] == 41
    assert sum(c["count"] for c in side.clusters()) == 43


def test_similar_rows_are_laid_out_together(two_topics) -> None:
    side, *_ = two_topics
    a, b = _centre_of(side, "a"), _centre_of(side, "b")
    pos = side.positions(side.ids_in_box(EVERYWHERE))
    a_rows = [pos[f"a{i}"] for i in range(60)]
    assert sum(_nearer(xy, a, b) for xy in a_rows) >= 57


def test_rows_without_a_vector_are_placed_by_their_tags(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    store = tmp_path / "memory.db"
    db = _make_store(store)
    for i, v in enumerate(_topic(rng, 0, 40)):
        _episode(db, f"a{i}", v, tags=[f"alpha{i % 20}"])
    for i, v in enumerate(_topic(rng, 1, 40)):
        _episode(db, f"b{i}", v, tags=[f"beta{i % 20}"])
    _episode(db, "tagged-a", None, tags=["alpha3"])
    _episode(db, "tagged-b", None, tags=["beta3"])
    # Only link-holder shares a tag with a vector row; via-tagged-a shares one
    # with link-holder alone, so a second round places it.
    _episode(db, "link-holder", None, tags=["alpha5", "rare-link"])
    _episode(db, "via-tagged-a", None, tags=["rare-link"])
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    stats = gl.build_sidecar(store, side.path)

    a, b = _centre_of(side, "a"), _centre_of(side, "b")
    pos = side.positions(["tagged-a", "tagged-b", "via-tagged-a"])
    assert _nearer(pos["tagged-a"], a, b)
    assert _nearer(pos["tagged-b"], b, a)
    assert _nearer(pos["via-tagged-a"], a, b)
    assert stats["placed_by"][gl.PLACED_TOKENS] == 4


def test_facts_are_placed_by_their_key_namespace(tmp_path: Path) -> None:
    rng = np.random.default_rng(6)
    store = tmp_path / "memory.db"
    db = _make_store(store)
    for i, v in enumerate(_topic(rng, 0, 30)):
        _fact(db, f"project.acme.f{i}", v)
    for i, v in enumerate(_topic(rng, 1, 30)):
        _fact(db, f"project.zeta.f{i}", v)
    _fact(db, "project.acme.no_vector", None)
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    gl.build_sidecar(store, side.path)

    pos = side.positions(side.ids_in_box(EVERYWHERE))
    acme = np.mean([pos[f"project.acme.f{i}"] for i in range(30)], axis=0)
    zeta = np.mean([pos[f"project.zeta.f{i}"] for i in range(30)], axis=0)
    assert _nearer(pos["project.acme.no_vector"], acme, zeta)
    assert _placed_by(side, "project.acme.no_vector") == gl.PLACED_TOKENS


def test_rows_with_nothing_to_place_them_by_go_on_the_band(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    store = tmp_path / "memory.db"
    db = _make_store(store)
    for i, v in enumerate(_topic(rng, 0, 30)):
        _episode(db, f"a{i}", v)
    for i in range(3):
        _fact(db, f"orphan_ns.k{i}", None)
    _fact(db, "lesson.deadbeef", None)
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    stats = gl.build_sidecar(store, side.path)
    assert stats["placed_by"][gl.PLACED_BAND] == 4

    pos = side.positions(side.ids_in_box(EVERYWHERE))
    inner = max(np.hypot(*pos[f"a{i}"]) for i in range(30))
    for rid in ("orphan_ns.k0", "orphan_ns.k1", "orphan_ns.k2", "lesson.deadbeef"):
        assert np.hypot(*pos[rid]) > inner
    # One arc per fact namespace: its three facts sit closer to each other than
    # to the lesson's arc.
    ns = np.array([pos[f"orphan_ns.k{i}"] for i in range(3)])
    spread = max(np.linalg.norm(p - ns.mean(0)) for p in ns)
    assert spread < np.linalg.norm(np.array(pos["lesson.deadbeef"]) - ns.mean(0))


def test_bot_store_views_are_read(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    store = tmp_path / "memory.db"
    db = sqlite3.connect(store)
    db.executescript("""
        CREATE TABLE memory_items (
            id TEXT PRIMARY KEY, kind TEXT, key TEXT, embedding BLOB,
            tags TEXT DEFAULT '[]', is_deleted INTEGER DEFAULT 0
        );
        CREATE VIEW episodic_memories AS
            SELECT id, embedding, tags, is_deleted FROM memory_items WHERE kind = 'episode';
        CREATE VIEW semantic_memory AS
            SELECT key, embedding, is_deleted FROM memory_items WHERE kind IN ('fact', 'directive');
        """)
    for i, v in enumerate(_topic(rng, 0, 20)):
        db.execute(
            "INSERT INTO memory_items VALUES (?, 'episode', NULL, ?, '[]', 0)",
            (f"e{i}", v.tobytes()),
        )
    db.execute("INSERT INTO memory_items VALUES ('x', 'directive', 'lesson.1', NULL, '[]', 0)")
    db.commit()
    db.close()
    side = LayoutSidecar(gl.sidecar_path(store))
    stats = gl.build_sidecar(store, side.path)
    assert stats["n"] == 21
    assert "lesson.1" in side.ids_in_box(EVERYWHERE, kinds=["lesson"])


# --- incremental placement --------------------------------------------------


def test_new_row_with_a_vector_lands_with_its_topic_and_nothing_else_moves(two_topics) -> None:
    side, rng, _ = two_topics
    before = side.positions(side.ids_in_box(EVERYWHERE))
    vec = _topic(rng, 0, 1)[0]
    placed = side.place([PlaceItem("new-a", "episode", vec.tobytes())])

    assert _nearer(placed["new-a"], _centre_of(side, "a"), _centre_of(side, "b"))
    assert side.positions(list(before)) == before
    assert side.meta()["incr_count"] == 1


def test_new_row_lands_where_a_full_build_puts_it(two_topics) -> None:
    """The cluster map reproduces the build: a row placed again with its own
    vector lands next to where the build put it."""
    side, _, store = two_topics
    centres = {c["id"]: np.array([c["x"], c["y"]]) for c in side.clusters()}
    sdb = sqlite3.connect(side.path)
    rows = sdb.execute("SELECT id, x, y, cluster FROM nodes WHERE id LIKE 'a%'").fetchall()
    sdb.close()
    # The row farthest from its cluster's centre: a map that ignored the row's
    # own offset would put it at the centre instead.
    rid, x, y, cid = max(rows, key=lambda r: np.linalg.norm(np.array(r[1:3]) - centres[r[3]]))
    built = np.array([x, y])
    offset = np.linalg.norm(built - centres[cid])
    assert offset > 0

    db = sqlite3.connect(store)
    blob = db.execute("SELECT embedding FROM episodic_memories WHERE id = ?", (rid,)).fetchone()[0]
    db.close()
    placed = np.array(side.place([PlaceItem(rid, "episode", blob)])[rid])
    assert np.linalg.norm(placed - built) < 0.25 * offset


def test_new_row_without_a_vector_is_placed_by_its_tags(two_topics) -> None:
    side, *_ = two_topics
    placed = side.place([PlaceItem("tag-only", "episode", None, tags=["beta4"])])
    assert _nearer(placed["tag-only"], _centre_of(side, "b"), _centre_of(side, "a"))
    assert _placed_by(side, "tag-only") == gl.PLACED_TOKENS


def test_backfilled_vector_moves_a_tag_placed_row_to_its_topic(two_topics) -> None:
    side, rng, _ = two_topics
    side.place([PlaceItem("late", "episode", None, tags=["beta4"])])
    assert _placed_by(side, "late") == gl.PLACED_TOKENS
    moved = side.place(
        [PlaceItem("late", "episode", _topic(rng, 0, 1)[0].tobytes(), tags=["beta4"])]
    )
    assert side.ids_in_box(EVERYWHERE).count("late") == 1
    assert _nearer(moved["late"], _centre_of(side, "a"), _centre_of(side, "b"))
    assert _placed_by(side, "late") == gl.PLACED_VECTOR


def test_row_with_nothing_to_place_it_by_goes_outside_the_layout(two_topics) -> None:
    side, *_ = two_topics
    pos = side.positions(side.ids_in_box(EVERYWHERE))
    inner = max(np.hypot(*xy) for xy in pos.values())
    placed = side.place([PlaceItem("lesson.abc", "lesson", None)])
    assert np.hypot(*placed["lesson.abc"]) > inner
    assert _placed_by(side, "lesson.abc") == gl.PLACED_BAND


def test_row_of_another_width_falls_back_to_its_tags(two_topics) -> None:
    side, *_ = two_topics
    wide = np.zeros(DIM * 2, np.float32).tobytes()
    placed = side.place([PlaceItem("wide", "episode", wide, tags=["alpha2"])])
    assert _nearer(placed["wide"], _centre_of(side, "a"), _centre_of(side, "b"))
    assert _placed_by(side, "wide") == gl.PLACED_TOKENS


def test_removed_rows_leave_the_layout_and_their_cluster_count(two_topics) -> None:
    side, *_ = two_topics
    total = sum(c["count"] for c in side.clusters())
    side.remove(["a1", "b2", "not-there"])
    assert not {"a1", "b2"} & set(side.ids_in_box(EVERYWHERE))
    assert sum(c["count"] for c in side.clusters()) == total - 2


# --- box queries ------------------------------------------------------------


def test_box_pages_cover_every_node_once_heaviest_first(two_topics) -> None:
    side, *_ = two_topics
    ids = side.ids_in_box(EVERYWHERE)
    side.set_weights({i: (n % 7) / 7 for n, i in enumerate(ids)})

    seen: list[str] = []
    weights: list[float] = []
    cursor = None
    for _ in range(len(ids)):  # one page per node at most, so a stuck cursor fails
        page, cursor = side.nodes_in_box(EVERYWHERE, limit=13, cursor=cursor)
        assert len(page) <= 13
        seen += [n["id"] for n in page]
        weights += [n["weight"] for n in page]
        if cursor is None:
            break
    assert cursor is None
    assert sorted(seen) == sorted(ids)
    assert weights == sorted(weights, reverse=True)


def test_box_page_is_capped(two_topics) -> None:
    side, *_ = two_topics
    page, _ = side.nodes_in_box(EVERYWHERE, limit=10**9)
    assert len(page) == min(PAGE_CAP, len(side.ids_in_box(EVERYWHERE)))


def test_box_select_returns_nodes_a_client_never_loaded(two_topics) -> None:
    side, *_ = two_topics
    first_page, _ = side.nodes_in_box(EVERYWHERE, limit=5)
    every = side.ids_in_box(EVERYWHERE)
    assert len(every) == 120 > len(first_page)

    pos = side.positions(every)
    a_xy = np.array([pos[f"a{i}"] for i in range(60)])
    box = (*a_xy.min(0), *a_xy.max(0))
    assert {f"a{i}" for i in range(60)} <= set(side.ids_in_box(box))


def test_kind_filter_applies_to_box_queries(two_topics) -> None:
    side, rng, _ = two_topics
    side.place([PlaceItem("lesson.n", "lesson", _topic(rng, 0, 1)[0].tobytes())])
    assert side.ids_in_box(EVERYWHERE, kinds=["lesson"]) == ["lesson.n"]
    page, _ = side.nodes_in_box(EVERYWHERE, kinds=["lesson"])
    assert [n["id"] for n in page] == ["lesson.n"]


def test_rows_missing_from_the_file_are_reported(two_topics) -> None:
    side, *_ = two_topics
    assert side.missing(["a0", "written-during-rebuild"]) == ["written-during-rebuild"]


# --- rebuild triggers and file trust ----------------------------------------


def test_drift_past_threshold_asks_for_a_rebuild(two_topics) -> None:
    side, rng, _ = two_topics
    assert not side.needs_rebuild(DIM)
    batch = int(120 * gl.REBUILD_DRIFT) + 1
    vecs = _topic(rng, 0, batch)
    side.place([PlaceItem(f"n{i}", "episode", v.tobytes()) for i, v in enumerate(vecs)])
    assert side.needs_rebuild(DIM)


def test_width_change_asks_for_a_rebuild(two_topics) -> None:
    side, *_ = two_topics
    assert side.needs_rebuild(DIM * 2)


def test_missing_corrupt_or_old_file_is_not_trusted(tmp_path: Path, two_topics) -> None:
    assert LayoutSidecar(tmp_path / "absent.sqlite").needs_rebuild()
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database")
    assert LayoutSidecar(bad).meta() is None

    side, *_ = two_topics
    db = sqlite3.connect(side.path)
    db.execute("UPDATE meta SET value = '1' WHERE key = 'version'")
    db.commit()
    db.close()
    assert side.needs_rebuild()


def test_adopt_swaps_in_a_finished_build(tmp_path: Path, two_topics) -> None:
    side, rng, store = two_topics
    side.place([PlaceItem("new", "episode", _topic(rng, 0, 1)[0].tobytes())])
    assert side.meta()["incr_count"] == 1

    built = tmp_path / "rebuild.sqlite"
    gl.build_sidecar(store, built)
    side.adopt(built)
    assert side.meta()["incr_count"] == 0
    assert not built.exists()
    # Placement after the swap uses the new file's maps.
    assert side.place([PlaceItem("after", "episode", _topic(rng, 1, 1)[0].tobytes())])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_layout_file_is_owner_only(two_topics) -> None:
    side, *_ = two_topics
    assert stat.S_IMODE(side.path.stat().st_mode) == 0o600


def test_build_in_a_child_process_writes_the_file(tmp_path: Path, two_topics) -> None:
    _, _, store = two_topics
    out = tmp_path / "child.sqlite"
    proc = gl.spawn_rebuild(store, out)
    assert proc.wait(timeout=120) == 0
    assert LayoutSidecar(out).meta()["n"] == 120
