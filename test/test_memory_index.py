"""The memory index contract, its registry, and the conformance kit."""

from __future__ import annotations

import struct

import pytest

from kiro_crew.memory_index import (
    EmbeddingSpace,
    IndexDoc,
    IndexQuery,
    MemoryIndexError,
    StoreRef,
    backend_names,
    create_index_backend,
    register_index_backend,
)
from kiro_crew.memory_index.conformance import run_conformance
from kiro_crew.memory_index.contract import CAP_VECTOR
from kiro_crew.memory_index.exact import ExactIndex

SPACE = EmbeddingSpace(signature="test", dim=2)


def _open_exact() -> ExactIndex:
    index = ExactIndex()
    index.open(StoreRef("s"), SPACE, {})
    return index


def _doc(ident: str, text: str, vector: bytes | None = None, **filters: str) -> IndexDoc:
    return IndexDoc(
        ("items", ident), "fact", vector, text, " ".join(sorted(set(text.split()))), filters
    )


def _failed(results) -> dict[str, str]:
    return {result.name: result.detail for result in results if result.status == "fail"}


@pytest.mark.parametrize("name", backend_names())
def test_every_built_in_backend_passes_the_conformance_kit(name):
    results = run_conformance(lambda: create_index_backend(name))
    assert _failed(results) == {}
    # Only a capability the backend does not declare is skipped.
    declared = create_index_backend(name).capabilities
    skipped = {result.name for result in results if result.status == "skip"}
    assert skipped == ({"substring_matches_exact"} if "substring" not in declared else set())


def test_the_kit_fails_a_backend_that_still_returns_removed_documents():
    class KeepsRemoved(ExactIndex):
        name = "keeps-removed"

        def remove(self, idents, *, seq):
            self._applied_seq = max(self._applied_seq, seq)

    assert (
        "returned a removed document"
        in _failed(run_conformance(KeepsRemoved))["upsert_then_remove"]
    )


def test_the_kit_fails_a_backend_that_shows_a_build_before_swap():
    class SwapsEarly(ExactIndex):
        name = "swaps-early"

        def rebuild(self, source, *, build_seq):
            handle = super().rebuild(source, build_seq=build_seq)
            super().swap(handle)
            return handle

    assert "before swap" in _failed(run_conformance(SwapsEarly))["build_then_swap"]


def test_the_kit_skips_checks_for_capabilities_a_backend_does_not_declare():
    class VectorOnly(ExactIndex):
        name = "vector-only"
        capabilities = frozenset({CAP_VECTOR})

    statuses = {result.name: result.status for result in run_conformance(VectorOnly)}
    assert statuses["substring_matches_exact"] == "skip"
    assert statuses["filters_applied"] == "skip"
    assert statuses["vector_top10_overlap"] == "pass"
    assert statuses["upsert_then_remove"] == "pass"


def test_a_registered_backend_is_created_by_name_and_removed_again():
    class Hosted(ExactIndex):
        name = "hosted"

    register_index_backend("hosted", Hosted)
    assert "hosted" in backend_names()
    assert isinstance(create_index_backend("hosted"), Hosted)
    register_index_backend("hosted", None)
    assert "hosted" not in backend_names()
    with pytest.raises(MemoryIndexError, match="no memory index backend"):
        create_index_backend("hosted")


def test_a_backend_name_cannot_shadow_a_built_in():
    with pytest.raises(MemoryIndexError, match="built-in"):
        register_index_backend("exact", ExactIndex)
    assert type(create_index_backend("exact")) is ExactIndex


def test_a_factory_that_is_not_a_memory_index_is_refused():
    register_index_backend("broken", object)
    with pytest.raises(MemoryIndexError, match="does not implement"):
        create_index_backend("broken")


def test_unknown_capabilities_are_refused():
    class Odd(ExactIndex):
        capabilities = frozenset({"telepathy"})

    register_index_backend("odd", Odd)
    with pytest.raises(MemoryIndexError, match="unknown capabilities"):
        create_index_backend("odd")


def test_substring_search_needs_every_word_and_reads_chinese():
    index = _open_exact()
    index.upsert(
        [
            _doc("en", "rebase the branch onto main"),
            _doc("zh", "合并之前先跑测试"),
            _doc("mix", "kas 回归测试"),
        ],
        seq=1,
    )

    def found(text):
        return sorted(hit.ident[1] for hit in index.search(IndexQuery("substring", 10, text=text)))

    assert found("测试") == ["mix", "zh"]
    assert found("kas 测试") == ["mix"]
    assert found("base main") == ["en"]
    assert found("rebase 测试") == []


def test_terms_search_ranks_more_matched_terms_first():
    index = _open_exact()
    index.upsert(
        [_doc("one", "alpha gamma"), _doc("two", "alpha beta"), _doc("none", "delta")], seq=1
    )
    hits = index.search(IndexQuery("terms", 10, text="alpha beta"))
    assert [(hit.ident[1], hit.score) for hit in hits] == [("two", 2.0), ("one", 1.0)]


def test_a_vector_of_the_wrong_dimension_is_refused():
    index = _open_exact()
    with pytest.raises(MemoryIndexError, match="wrong dimension"):
        index.upsert([_doc("a", "x", vector=b"\0" * 12)], seq=1)
    with pytest.raises(MemoryIndexError, match="wrong dimension"):
        index.search(IndexQuery("vector", 3, vector=b"\0" * 12))


def test_a_backend_refuses_use_before_open():
    with pytest.raises(MemoryIndexError, match="open the index"):
        ExactIndex().search(IndexQuery("terms", 3, text="x"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "fuzzy", "k": 1, "text": "x"},
        {"mode": "terms", "k": 0, "text": "x"},
        {"mode": "terms", "k": 1, "text": "   "},
        {"mode": "vector", "k": 1, "vector": b"abc"},
    ],
)
def test_a_malformed_query_is_refused(kwargs):
    with pytest.raises(MemoryIndexError):
        IndexQuery(**kwargs)


def test_swap_resets_applied_seq_to_the_build():
    index = _open_exact()
    handle = index.rebuild([_doc("a", "alpha")], build_seq=4)
    index.upsert([_doc("b", "beta")], seq=6)
    index.swap(handle)
    status = index.status()
    assert (status.built_seq, status.applied_seq, status.rows) == (4, 4, 1)


def test_a_non_finite_vector_is_refused_and_search_keeps_working():
    index = _open_exact()
    with pytest.raises(MemoryIndexError, match="non-finite"):
        index.upsert([_doc("nan", "x", vector=struct.pack("2f", float("nan"), 1.0))], seq=1)
    index.upsert([_doc("ok", "x", vector=struct.pack("2f", 1.0, 0.0))], seq=2)
    hits = index.search(IndexQuery("vector", 3, vector=struct.pack("2f", 1.0, 0.0)))
    assert [hit.ident[1] for hit in hits] == ["ok"]


def test_the_kit_reports_a_backend_that_cannot_open_as_failed():
    class WontOpen(ExactIndex):
        name = "wont-open"

        def open(self, store, space, config):
            raise RuntimeError("no connection")

    results = run_conformance(WontOpen)
    assert results and all(result.status == "fail" for result in results)
    assert all("no connection" in result.detail for result in results)
