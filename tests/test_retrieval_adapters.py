from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from backend import rag
from backend.retrieval_adapters import (
    GRAPH_SCHEMA_VERSION,
    HybridRetrievalAdapter,
    LegacyRetrievalAdapter,
    LocalGraphAdapter,
    LocalKeywordAdapter,
    RetrievalAdapterError,
    RetrievalRequest,
    offline_legacy_fallback,
)


def _documents(_: str = "ws-1") -> list[dict[str, Any]]:
    return [
        {
            "id": "doc-a",
            "workspace_id": "ws-1",
            "title": "Alpha revenue",
            "content": "alpha revenue evidence",
            "source_file": "corpus-a",
            "corpus_ref": "corpus-a",
            "chunk_id": "chunk-a",
        },
        {
            "id": "doc-b",
            "workspace_id": "ws-1",
            "title": "Beta costs",
            "content": "beta cost evidence",
            "source_file": "corpus-b",
            "corpus_ref": "corpus-b",
            "chunk_id": "chunk-b",
        },
        {
            "id": "doc-secret",
            "workspace_id": "ws-1",
            "title": "Alpha secret",
            "content": "alpha confidential evidence",
            "source_file": "corpus-secret",
            "corpus_ref": "corpus-secret",
            "chunk_id": "chunk-secret",
        },
    ]


def _graph() -> dict[str, Any]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "workspace_id": "ws-1",
        "corpus_version": "corpus-v1",
        "nodes": [
            {"id": "entity-alpha", "kind": "entity", "label": "Alpha", "evidence_refs": ["chunk-a"]},
            {"id": "entity-beta", "kind": "entity", "label": "Beta", "evidence_refs": ["chunk-b"]},
            {
                "id": "entity-secret",
                "kind": "entity",
                "label": "Secret",
                "evidence_refs": ["chunk-secret"],
            },
        ],
        "edges": [
            {
                "source": "entity-alpha",
                "target": "entity-beta",
                "type": "related_to",
                "weight": 0.8,
                "evidence_refs": ["chunk-a"],
            },
            {
                "source": "entity-alpha",
                "target": "entity-secret",
                "type": "restricted",
                "weight": 1.0,
                "evidence_refs": ["chunk-secret"],
            },
        ],
    }


def _write_graph(workspace_base: Path, *, jsonl: bool = False) -> Path:
    workspace = workspace_base / "ws-1"
    workspace.mkdir(parents=True)
    graph = _graph()
    if not jsonl:
        path = workspace / "corpus_graph.json"
        path.write_text(json.dumps(graph), encoding="utf-8")
        return path
    path = workspace / "corpus_graph.jsonl"
    records = [
        {
            "schema_version": graph["schema_version"],
            "workspace_id": graph["workspace_id"],
            "corpus_version": graph["corpus_version"],
        },
        *[{"record_type": "node", **node} for node in graph["nodes"]],
        *[{"record_type": "edge", **edge} for edge in graph["edges"]],
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def test_default_backend_preserves_legacy_signature_result_and_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_RETRIEVAL_BACKEND", raising=False)
    expected = [{"id": "legacy", "retrieval_mode": "hybrid", "score": 0.7}]
    calls: list[tuple[Any, ...]] = []

    def legacy(
        workspace_id: str,
        query: str,
        top_k: int,
        *,
        use_vector: bool,
        prefer_local: bool,
    ) -> list[dict[str, Any]]:
        calls.append((workspace_id, query, top_k, use_vector, prefer_local))
        return expected

    monkeypatch.setattr(rag, "_legacy_search", legacy)

    result = rag.search("ws-1", "revenue", 7, use_vector=False, prefer_local=True)

    assert result is expected
    assert calls == [("ws-1", "revenue", 7, False, True)]
    assert set(result[0]) == {"id", "retrieval_mode", "score"}


def test_default_legacy_keeps_hybrid_keyword_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_RETRIEVAL_BACKEND", raising=False)
    monkeypatch.setenv("SEARCH_ENDPOINT", "https://search.test")
    monkeypatch.setenv("SEARCH_KEY", "key")
    monkeypatch.setattr(rag, "_query_vector", lambda _: [0.25])
    payloads: list[dict[str, Any]] = []

    def remote(_: str, payload: dict[str, Any], __: str) -> dict[str, Any]:
        payloads.append(payload)
        if len(payloads) == 1:
            raise urllib.error.URLError("vector unavailable")
        return {"value": [{"id": "hit-1", "workspace_id": "ws-1", "title": "Result"}]}

    monkeypatch.setattr(rag, "_remote_search", remote)

    result = rag.search("ws-1", "revenue", 3)

    assert "vectorQueries" in payloads[0]
    assert "vectorQueries" not in payloads[1]
    assert result[0]["retrieval_mode"] == "keyword_fallback"


def test_local_keyword_is_offline_acl_filtered_and_stably_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", "local_keyword")
    equal_score_documents = [
        {**_documents()[1], "id": "z-doc", "title": "alpha", "content": ""},
        {**_documents()[0], "id": "a-doc", "title": "alpha", "content": ""},
    ]
    monkeypatch.setattr(rag, "_local_adapter_documents", lambda _: equal_score_documents)
    monkeypatch.setattr(rag, "_trusted_allowed_corpus_refs", lambda _: ("corpus-a", "corpus-b"))
    monkeypatch.setattr(rag, "_remote_search", lambda *args, **kwargs: pytest.fail("network search was called"))
    monkeypatch.setattr(rag, "_terraform_output", lambda _: pytest.fail("terraform lookup was called"))

    first = rag.search("ws-1", "alpha", 5)
    second = rag.search("ws-1", "alpha", 5)

    assert [hit["id"] for hit in first] == ["a-doc", "z-doc"]
    assert first == second
    assert all(hit["adapter"] == "local_keyword" for hit in first)
    assert all(hit["retrieval_trace"]["permission_filtered"] for hit in first)

    direct = LocalKeywordAdapter(Path.cwd() / "workspaces", _documents).search(
        RetrievalRequest(
            "ws-1",
            "alpha",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=True,
        )
    )
    assert [hit["id"] for hit in direct] == ["doc-a"]


@pytest.mark.parametrize("backend", ["local_keyword", "local_graph", "local_hybrid_graph"])
def test_public_local_backends_deny_excluded_corpus_from_trusted_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    workspace_base = tmp_path / "workspaces"
    _write_graph(workspace_base)
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", backend)
    monkeypatch.setenv("DF_LOCAL_GRAPH_PATH", "corpus_graph.json")
    monkeypatch.setattr(rag, "ROOT", tmp_path)
    monkeypatch.setattr(rag, "_local_adapter_documents", _documents)
    monkeypatch.setattr(rag, "_trusted_allowed_corpus_refs", lambda _: ("corpus-a",), raising=False)

    hits = rag.search("ws-1", "alpha", 5)

    assert [hit["id"] for hit in hits] == ["doc-a"]
    assert all(hit["retrieval_trace"]["permission_filtered"] is True for hit in hits)


def test_public_offline_fallback_denies_excluded_corpus_from_trusted_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", "local_hybrid_graph")
    monkeypatch.setattr(
        rag,
        "_local_adapter_documents",
        lambda _: (_ for _ in ()).throw(ValueError("bad graph")),
    )
    monkeypatch.setattr(rag, "_local_search", lambda *_: _documents())
    monkeypatch.setattr(rag, "_trusted_allowed_corpus_refs", lambda _: ("corpus-a",), raising=False)

    hits = rag.search("ws-1", "alpha", 5)

    assert [hit["id"] for hit in hits] == ["doc-a"]
    assert hits[0]["retrieval_trace"]["permission_filtered"] is True


@pytest.mark.parametrize("jsonl", [False, True])
def test_local_graph_reads_versioned_json_and_jsonl_with_acl_before_expansion(
    tmp_path: Path,
    jsonl: bool,
) -> None:
    graph_path = _write_graph(tmp_path, jsonl=jsonl)
    adapter = LocalGraphAdapter(tmp_path, _documents)
    request = RetrievalRequest(
        "ws-1",
        "alpha",
        top_k=5,
        allowed_corpus_refs=("corpus-a",),
        authorization_applied=True,
        graph_path=graph_path.name,
    )

    hits = adapter.search(request)

    assert [hit["id"] for hit in hits] == ["doc-a"]
    assert hits[0]["adapter"] == "local_graph"
    assert hits[0]["score_kind"] == "graph_weight"
    assert hits[0]["retrieval_trace"]["graph_succeeded"] is True
    assert "doc-secret" not in {hit["id"] for hit in hits}


def test_local_graph_rejects_path_traversal_and_workspace_traversal_before_loading_documents(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_graph()), encoding="utf-8")
    (tmp_path / "ws-1").mkdir()
    loaded: list[str] = []

    def loader(workspace_id: str) -> list[dict[str, Any]]:
        loaded.append(workspace_id)
        return _documents(workspace_id)

    adapter = LocalGraphAdapter(tmp_path, loader)

    with pytest.raises(RetrievalAdapterError, match="escapes workspace root"):
        adapter.search(
            RetrievalRequest(
                "ws-1",
                "alpha",
                allowed_corpus_refs=("corpus-a",),
                authorization_applied=True,
                graph_path="../outside.json",
            )
        )
    with pytest.raises(RetrievalAdapterError, match="workspace path escapes"):
        adapter.search(RetrievalRequest("../escape", "alpha", graph_path="corpus_graph.json"))
    assert loaded == []


def test_graph_discards_nodes_and_edges_without_authorized_evidence(tmp_path: Path) -> None:
    path = _write_graph(tmp_path)
    adapter = LocalGraphAdapter(tmp_path, _documents)

    hits = adapter.search(
        RetrievalRequest(
            "ws-1",
            "alpha",
            top_k=10,
            allowed_corpus_refs=("corpus-a", "corpus-b"),
            authorization_applied=True,
            graph_path=path.name,
        )
    )

    assert {hit["id"] for hit in hits} == {"doc-a", "doc-b"}
    assert "doc-secret" not in {hit["id"] for hit in hits}
    assert any("entity-alpha>entity-beta" in hit["graph_path_refs"] for hit in hits)


class _StaticAdapter:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self._hits = hits

    def search(self, _: RetrievalRequest) -> list[dict[str, Any]]:
        return [dict(hit) for hit in self._hits]


def test_hybrid_rrf_deduplicates_and_uses_deterministic_tie_break() -> None:
    keyword = _StaticAdapter(
        [
            {
                "id": "shared",
                "workspace_id": "ws-1",
                "source_file": "a",
                "chunk_id": "shared",
                "content": "shared",
                "rank": 1,
            },
            {
                "id": "z-only",
                "workspace_id": "ws-1",
                "source_file": "z",
                "chunk_id": "z",
                "content": "z",
                "rank": 2,
            },
            {
                "id": "shared",
                "workspace_id": "ws-1",
                "source_file": "a",
                "chunk_id": "shared",
                "content": "duplicate",
                "rank": 3,
            },
        ]
    )
    graph = _StaticAdapter(
        [
            {
                "id": "shared",
                "workspace_id": "ws-1",
                "source_file": "a",
                "chunk_id": "shared",
                "content": "shared",
                "rank": 1,
                "graph_path_refs": ["entity-a>entity-b"],
            },
            {
                "id": "a-only",
                "workspace_id": "ws-1",
                "source_file": "a",
                "chunk_id": "a",
                "content": "a",
                "rank": 2,
            },
        ]
    )
    adapter = HybridRetrievalAdapter(keyword, graph)
    request = RetrievalRequest(
        "ws-1",
        "anything",
        top_k=5,
        allowed_corpus_refs=("shared", "z", "a"),
        authorization_applied=True,
    )

    first = adapter.search(request)
    second = adapter.search(request)

    assert first == second
    assert [hit["id"] for hit in first] == ["shared", "a-only", "z-only"]
    assert first[0]["score"] == pytest.approx(2 / 61)
    assert first[0]["score_kind"] == "rrf"
    assert first[0]["adapter"] == "local_hybrid_graph"
    assert first[0]["graph_path_refs"] == ["entity-a>entity-b"]
    assert first[0]["retrieval_trace"]["candidate_sources"] == ["local_graph", "local_keyword"]
    assert [hit["rank"] for hit in first] == [1, 2, 3]


def test_local_hybrid_graph_never_networks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_graph(tmp_path)
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", "local_hybrid_graph")
    monkeypatch.setenv("DF_LOCAL_GRAPH_PATH", "corpus_graph.json")
    monkeypatch.setattr(rag, "ROOT", tmp_path.parent)
    (tmp_path.parent / "workspaces").mkdir(exist_ok=True)
    workspace_target = tmp_path.parent / "workspaces" / "ws-1"
    workspace_target.mkdir(exist_ok=True)
    (workspace_target / "corpus_graph.json").write_text(json.dumps(_graph()), encoding="utf-8")
    monkeypatch.setattr(rag, "_local_adapter_documents", _documents)
    monkeypatch.setattr(rag, "_remote_search", lambda *args, **kwargs: pytest.fail("network search was called"))
    monkeypatch.setattr(
        rag,
        "_trusted_allowed_corpus_refs",
        lambda _: ("corpus-a", "corpus-b", "corpus-secret"),
    )
    monkeypatch.setattr(rag, "_terraform_output", lambda _: pytest.fail("terraform lookup was called"))

    hits = rag.search("ws-1", "alpha", 5)

    assert hits
    assert all(hit["retrieval_mode"] == "local_hybrid_graph" for hit in hits)
    assert all(hit["retrieval_trace"]["graph_succeeded"] is True for hit in hits)


def test_adapter_failure_uses_explicit_offline_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", "local_hybrid_graph")
    monkeypatch.setattr(rag, "_local_adapter_documents", lambda _: (_ for _ in ()).throw(ValueError("bad graph")))
    monkeypatch.setattr(
        rag,
        "_local_search",
        lambda workspace_id, query, top_k: [
            {
                "id": "local-fallback",
                "workspace_id": workspace_id,
                "source_file": "raw_docs/fallback.txt",
                "chunk_id": "fallback",
                "content": query,
                "score": 1,
                "retrieval_mode": "local_keyword",
            }
        ],
    )
    monkeypatch.setattr(rag, "_remote_search", lambda *args, **kwargs: pytest.fail("network search was called"))
    monkeypatch.setattr(rag, "_trusted_allowed_corpus_refs", lambda _: ("raw_docs/fallback.txt",))

    hits = rag.search("ws-1", "alpha", 3)

    assert [hit["id"] for hit in hits] == ["local-fallback"]
    assert hits[0]["adapter"] == "legacy"
    assert hits[0]["retrieval_mode"] == "local_keyword"
    assert hits[0]["retrieval_trace"] == {
        "candidate_sources": ["legacy_local_fallback"],
        "permission_filtered": True,
        "fallback": True,
        "fallback_from": "local_hybrid_graph",
        "adapter_error_category": "ValueError",
        "graph_succeeded": False,
    }


def test_default_legacy_preserves_boundary_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DF_RETRIEVAL_BACKEND", raising=False)
    calls: list[tuple[str, str, int]] = []

    def legacy(
        workspace_id: str,
        query: str,
        top_k: int,
        *,
        use_vector: bool,
        prefer_local: bool,
    ) -> list[dict[str, Any]]:
        del use_vector, prefer_local
        calls.append((workspace_id, query, top_k))
        return []

    monkeypatch.setattr(rag, "_legacy_search", legacy)

    assert rag.search("", "", 0) == []
    assert calls == [("", "", 0)]


def test_unknown_backend_fallback_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DF_RETRIEVAL_BACKEND", "local_graph_typo")
    monkeypatch.setattr(
        rag,
        "_legacy_search",
        lambda *args, **kwargs: [
            {
                "id": "legacy-hit",
                "workspace_id": "ws-1",
                "source_file": "corpus-a",
                "chunk_id": "chunk-a",
                "content": "legacy",
                "score": 0.5,
                "retrieval_mode": "keyword",
            }
        ],
    )

    hits = rag.search("ws-1", "alpha")

    assert hits[0]["adapter"] == "legacy"
    assert hits[0]["retrieval_trace"]["fallback"] is True
    assert hits[0]["retrieval_trace"]["fallback_from"] == "local_graph_typo"
    assert hits[0]["retrieval_trace"]["graph_succeeded"] is False


def test_offline_fallback_reapplies_corpus_acl() -> None:
    def local_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        return _documents()

    hits = offline_legacy_fallback(
        local_search,
        RetrievalRequest(
            "ws-1",
            "alpha",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=True,
            mode="local_hybrid_graph",
        ),
        ValueError("graph failed"),
    )

    assert [hit["id"] for hit in hits] == ["doc-a"]
    assert hits[0]["retrieval_trace"]["permission_filtered"] is True
    assert hits[0]["retrieval_trace"]["graph_succeeded"] is False


def test_local_keyword_rejects_workspace_traversal_before_loading(tmp_path: Path) -> None:
    loaded: list[str] = []

    def loader(workspace_id: str) -> list[dict[str, Any]]:
        loaded.append(workspace_id)
        return _documents(workspace_id)

    adapter = LocalKeywordAdapter(tmp_path, loader)

    with pytest.raises(RetrievalAdapterError, match="workspace path escapes"):
        adapter.search(RetrievalRequest("../escape", "alpha"))
    assert loaded == []


@pytest.mark.parametrize(
    "retrieval_request",
    [
        RetrievalRequest(
            "ws-1",
            "alpha",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=False,
        ),
        RetrievalRequest("ws-1", "alpha", authorization_applied=True),
    ],
    ids=["authorization-not-applied", "empty-allow-list"],
)
def test_local_keyword_direct_entry_fails_closed_before_loading(
    tmp_path: Path,
    retrieval_request: RetrievalRequest,
) -> None:
    loaded: list[str] = []
    adapter = LocalKeywordAdapter(tmp_path, lambda workspace_id: loaded.append(workspace_id) or _documents())

    assert adapter.search(retrieval_request) == []
    assert loaded == []


@pytest.mark.parametrize(
    "retrieval_request",
    [
        RetrievalRequest(
            "ws-1",
            "alpha",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=False,
        ),
        RetrievalRequest("ws-1", "alpha", authorization_applied=True),
    ],
    ids=["authorization-not-applied", "empty-allow-list"],
)
def test_local_graph_direct_entry_fails_closed_before_graph_or_document_loading(
    tmp_path: Path,
    retrieval_request: RetrievalRequest,
) -> None:
    loaded: list[str] = []
    adapter = LocalGraphAdapter(tmp_path, lambda workspace_id: loaded.append(workspace_id) or _documents())

    assert adapter.search(retrieval_request) == []
    assert loaded == []


@pytest.mark.parametrize(
    "retrieval_request",
    [
        RetrievalRequest(
            "ws-1",
            "anything",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=False,
        ),
        RetrievalRequest("ws-1", "anything", authorization_applied=True),
    ],
    ids=["authorization-not-applied", "empty-allow-list"],
)
def test_hybrid_direct_entry_fails_closed_before_rrf(retrieval_request: RetrievalRequest) -> None:
    calls: list[str] = []

    class _RecordingAdapter:
        def __init__(self, name: str) -> None:
            self._name = name

        def search(self, _: RetrievalRequest) -> list[dict[str, Any]]:
            calls.append(self._name)
            return [{"id": "secret", "workspace_id": "ws-1", "corpus_ref": "corpus-secret"}]

    adapter = HybridRetrievalAdapter(_RecordingAdapter("keyword"), _RecordingAdapter("graph"))

    assert adapter.search(retrieval_request) == []
    assert calls == []


@pytest.mark.parametrize(
    "retrieval_request",
    [
        RetrievalRequest(
            "ws-1",
            "alpha",
            allowed_corpus_refs=("corpus-a",),
            authorization_applied=False,
            mode="local_hybrid_graph",
        ),
        RetrievalRequest(
            "ws-1",
            "alpha",
            authorization_applied=True,
            mode="local_hybrid_graph",
        ),
    ],
    ids=["authorization-not-applied", "empty-allow-list"],
)
def test_offline_fallback_direct_entry_fails_closed_before_search(
    retrieval_request: RetrievalRequest,
) -> None:
    calls: list[str] = []

    def local_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args, kwargs
        calls.append("search")
        return _documents()

    assert offline_legacy_fallback(local_search, retrieval_request, ValueError("graph failed")) == []
    assert calls == []


def test_hybrid_applies_workspace_and_corpus_acl_before_rrf() -> None:
    keyword = _StaticAdapter(
        [
            {
                "id": "secret",
                "workspace_id": "ws-1",
                "corpus_ref": "corpus-secret",
                "rank": 1,
            },
            {"id": "allowed", "workspace_id": "ws-1", "corpus_ref": "corpus-a", "rank": 2},
            {"id": "foreign", "workspace_id": "ws-2", "corpus_ref": "corpus-a", "rank": 3},
        ]
    )
    graph = _StaticAdapter(
        [
            {
                "id": "secret",
                "workspace_id": "ws-1",
                "corpus_ref": "corpus-secret",
                "rank": 1,
            },
            {"id": "allowed", "workspace_id": "ws-1", "corpus_ref": "corpus-a", "rank": 2},
        ]
    )
    request = RetrievalRequest(
        "ws-1",
        "anything",
        allowed_corpus_refs=("corpus-a",),
        authorization_applied=True,
    )

    hits = HybridRetrievalAdapter(keyword, graph).search(request)

    assert [hit["id"] for hit in hits] == ["allowed"]
    assert hits[0]["retrieval_trace"]["permission_filtered"] is True


def test_legacy_adapter_preserves_contract_without_local_authorization() -> None:
    expected = [{"id": "legacy"}]
    calls: list[tuple[Any, ...]] = []

    def legacy(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append((*args, kwargs))
        return expected

    request = RetrievalRequest("", "", top_k=0)

    assert LegacyRetrievalAdapter(legacy).search(request) is expected
    assert calls == [("", "", 0, {"use_vector": True, "prefer_local": False})]
