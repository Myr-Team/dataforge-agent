from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, TypedDict

GRAPH_SCHEMA_VERSION = "dataforge.local-corpus-graph.v1"
DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class RetrievalRequest:
    workspace_id: str
    query: str
    top_k: int = 5
    allowed_corpus_refs: tuple[str, ...] = ()
    authorization_applied: bool = False
    mode: str = "legacy"
    graph_path: str | None = None
    use_vector: bool = True
    prefer_local: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_corpus_refs", tuple(self.allowed_corpus_refs))


class RetrievalHit(TypedDict, total=False):
    id: str
    workspace_id: str
    title: str
    raw_title: str
    content: str
    source_file: str
    chunk_id: str
    document_type: str
    language: str
    sheet: str | None
    row: str | None
    score: float
    score_kind: str
    rank: int
    adapter: str
    retrieval_mode: str
    graph_path_refs: list[str]
    retrieval_trace: dict[str, Any]


class SearchAdapter(Protocol):
    def search(self, request: RetrievalRequest) -> list[RetrievalHit]: ...


SearchImplementation = Callable[..., list[dict[str, Any]]]
DocumentLoader = Callable[[str], list[dict[str, Any]]]


class RetrievalAdapterError(RuntimeError):
    """A safe, local retrieval contract failure."""


class LegacyRetrievalAdapter:
    def __init__(self, search_impl: SearchImplementation) -> None:
        self._search_impl = search_impl

    def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        return self._search_impl(
            request.workspace_id,
            request.query,
            request.top_k,
            use_vector=request.use_vector,
            prefer_local=request.prefer_local,
        )


class LocalKeywordAdapter:
    def __init__(self, workspace_base: Path, document_loader: DocumentLoader) -> None:
        self._workspace_base = workspace_base
        self._document_loader = document_loader

    def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        _validate_local_request(self._workspace_base, request)
        if not _local_authorization_ready(request):
            return []
        documents = _authorized_documents(
            self._document_loader(request.workspace_id),
            request,
            require_workspace_identity=True,
        )
        terms = _query_terms(request.query)
        scored: list[tuple[float, str, dict[str, Any]]] = []
        for document in documents:
            haystack = f"{document.get('title', '')} {document.get('content', '')}".lower()
            score = float(sum(haystack.count(term) for term in terms))
            if score > 0:
                scored.append((score, _hit_key(document), document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            _decorate_hit(
                document,
                score=score,
                rank=rank,
                adapter="local_keyword",
                retrieval_mode="local_keyword",
                score_kind="keyword_frequency",
                graph_path_refs=[],
                candidate_sources=["local_keyword"],
                permission_filtered=True,
            )
            for rank, (score, _, document) in enumerate(scored[: request.top_k], start=1)
        ]


class LocalGraphAdapter:
    def __init__(self, workspace_base: Path, document_loader: DocumentLoader) -> None:
        self._workspace_base = workspace_base
        self._document_loader = document_loader

    def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        _validate_local_request(self._workspace_base, request)
        if not _local_authorization_ready(request):
            return []
        graph_path = _resolve_graph_path(self._workspace_base, request)
        documents = _authorized_documents(
            self._document_loader(request.workspace_id),
            request,
            require_workspace_identity=True,
        )
        evidence = _evidence_index(documents)
        graph = _load_graph(graph_path, request.workspace_id)
        nodes, edges = _authorized_graph(graph, evidence)
        terms = _query_terms(request.query)

        seed_scores: dict[str, float] = {}
        for node_id, node in nodes.items():
            label = f"{node_id} {node.get('kind', '')} {node.get('label', '')}".lower()
            score = float(sum(label.count(term) for term in terms))
            if score > 0:
                seed_scores[node_id] = score

        evidence_scores: dict[str, float] = {}
        evidence_paths: dict[str, set[str]] = {}
        for node_id in sorted(seed_scores):
            node = nodes[node_id]
            score = seed_scores[node_id]
            for ref in node["evidence_refs"]:
                evidence_scores[ref] = evidence_scores.get(ref, 0.0) + score
                evidence_paths.setdefault(ref, set()).add(node_id)
            for edge in edges:
                if edge["source"] == node_id:
                    neighbour = edge["target"]
                elif edge["target"] == node_id:
                    neighbour = edge["source"]
                else:
                    continue
                path_ref = f"{node_id}>{neighbour}"
                expanded_score = score * edge["weight"]
                refs = list(nodes[neighbour]["evidence_refs"]) + list(edge["evidence_refs"])
                for ref in refs:
                    evidence_scores[ref] = evidence_scores.get(ref, 0.0) + expanded_score
                    evidence_paths.setdefault(ref, set()).add(path_ref)

        candidates: dict[str, tuple[dict[str, Any], float, set[str]]] = {}
        for ref, score in evidence_scores.items():
            document = evidence[ref]
            key = _hit_key(document)
            if key not in candidates:
                candidates[key] = (document, 0.0, set())
            current_document, current_score, paths = candidates[key]
            paths.update(evidence_paths.get(ref, set()))
            candidates[key] = (current_document, current_score + score, paths)

        ranked = sorted(candidates.items(), key=lambda item: (-item[1][1], item[0]))
        return [
            _decorate_hit(
                document,
                score=score,
                rank=rank,
                adapter="local_graph",
                retrieval_mode="local_graph",
                score_kind="graph_weight",
                graph_path_refs=sorted(paths),
                candidate_sources=["local_graph"],
                extra_trace={"graph_file": graph_path.name, "graph_succeeded": True},
                permission_filtered=True,
            )
            for rank, (_, (document, score, paths)) in enumerate(ranked[: request.top_k], start=1)
        ]


class HybridRetrievalAdapter:
    def __init__(
        self,
        keyword_adapter: SearchAdapter,
        graph_adapter: SearchAdapter,
        *,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self._keyword_adapter = keyword_adapter
        self._graph_adapter = graph_adapter
        self._rrf_k = rrf_k

    def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        if not _local_authorization_ready(request):
            return []
        keyword_hits = _authorized_documents(
            self._keyword_adapter.search(request),
            request,
            require_workspace_identity=True,
        )
        graph_hits = _authorized_documents(
            self._graph_adapter.search(request),
            request,
            require_workspace_identity=True,
        )
        fused: dict[str, dict[str, Any]] = {}
        for source, hits in (("local_keyword", keyword_hits), ("local_graph", graph_hits)):
            seen: set[str] = set()
            for fallback_rank, raw_hit in enumerate(hits, start=1):
                key = _hit_key(raw_hit)
                if key in seen:
                    continue
                seen.add(key)
                rank = int(raw_hit.get("rank") or fallback_rank)
                item = fused.setdefault(
                    key,
                    {
                        "hit": dict(raw_hit),
                        "score": 0.0,
                        "sources": set(),
                        "paths": set(),
                    },
                )
                item["score"] += 1.0 / (self._rrf_k + rank)
                item["sources"].add(source)
                item["paths"].update(raw_hit.get("graph_path_refs") or [])
                if source == "local_keyword":
                    item["hit"] = dict(raw_hit)

        ranked = sorted(fused.items(), key=lambda item: (-item[1]["score"], item[0]))
        results: list[RetrievalHit] = []
        for rank, (_, item) in enumerate(ranked[: request.top_k], start=1):
            results.append(
                _decorate_hit(
                    item["hit"],
                    score=item["score"],
                    rank=rank,
                    adapter="local_hybrid_graph",
                    retrieval_mode="local_hybrid_graph",
                    score_kind="rrf",
                    graph_path_refs=sorted(item["paths"]),
                    candidate_sources=sorted(item["sources"]),
                    extra_trace={"graph_succeeded": True, "rrf_k": self._rrf_k},
                    permission_filtered=True,
                )
            )
        return results


def create_search_adapter(
    backend: str,
    *,
    legacy_search: SearchImplementation,
    document_loader: DocumentLoader,
    workspace_base: Path,
) -> SearchAdapter:
    normalized = (backend or "legacy").strip().lower()
    if normalized == "legacy":
        return LegacyRetrievalAdapter(legacy_search)
    if normalized == "local_keyword":
        return LocalKeywordAdapter(workspace_base, document_loader)
    if normalized == "local_graph":
        return LocalGraphAdapter(workspace_base, document_loader)
    if normalized == "local_hybrid_graph":
        return HybridRetrievalAdapter(
            LocalKeywordAdapter(workspace_base, document_loader),
            LocalGraphAdapter(workspace_base, document_loader),
        )
    raise RetrievalAdapterError(f"unsupported retrieval backend: {normalized}")


def legacy_fallback(
    search_impl: SearchImplementation,
    request: RetrievalRequest,
    error: Exception,
    *,
    require_workspace_identity: bool = False,
) -> list[RetrievalHit]:
    hits = search_impl(
        request.workspace_id,
        request.query,
        request.top_k,
        use_vector=request.use_vector,
        prefer_local=request.prefer_local,
    )
    authorized_hits = _authorized_documents(
        hits,
        request,
        require_workspace_identity=require_workspace_identity,
    )
    error_category = type(error).__name__
    return [
        _decorate_hit(
            hit,
            score=float(hit.get("score") or 0.0),
            rank=rank,
            adapter="legacy",
            retrieval_mode=str(hit.get("retrieval_mode") or "local_keyword"),
            score_kind="legacy_score",
            graph_path_refs=[],
            candidate_sources=["legacy_fallback"],
            permission_filtered=request.authorization_applied,
            extra_trace={
                "fallback": True,
                "fallback_from": request.mode,
                "adapter_error_category": error_category,
                "graph_succeeded": False,
            },
        )
        for rank, hit in enumerate(authorized_hits[: request.top_k], start=1)
    ]


def offline_legacy_fallback(
    local_search: SearchImplementation,
    request: RetrievalRequest,
    error: Exception,
) -> list[RetrievalHit]:
    if not _local_authorization_ready(request):
        return []
    hits = legacy_fallback(
        local_search,
        request,
        error,
        require_workspace_identity=True,
    )
    for hit in hits:
        hit["retrieval_trace"]["candidate_sources"] = ["legacy_local_fallback"]
    return hits


def _authorized_documents(
    documents: list[dict[str, Any]],
    request: RetrievalRequest,
    *,
    require_workspace_identity: bool = False,
) -> list[dict[str, Any]]:
    allowed = _allowed_corpus_refs(request)
    authorized: list[dict[str, Any]] = []
    for document in documents:
        workspace_id = document.get("workspace_id")
        if require_workspace_identity and str(workspace_id or "") != request.workspace_id:
            continue
        if not require_workspace_identity and str(workspace_id or request.workspace_id) != request.workspace_id:
            continue
        if request.authorization_applied and not (_document_refs(document) & allowed):
            continue
        authorized.append(document)
    return authorized


def _allowed_corpus_refs(request: RetrievalRequest) -> set[str]:
    return {str(ref).strip() for ref in request.allowed_corpus_refs if str(ref).strip()}


def _local_authorization_ready(request: RetrievalRequest) -> bool:
    """Return true only when an upstream decision supplied a non-empty local ACL.

    Local hits marked ``permission_filtered`` have passed both this explicit
    authorization gate and the workspace/corpus filters before ranking.
    """

    return request.authorization_applied and bool(_allowed_corpus_refs(request))


def _document_refs(document: dict[str, Any]) -> set[str]:
    fields = ("id", "chunk_id", "source_file", "corpus_ref")
    return {str(document[field]) for field in fields if document.get(field) not in (None, "")}


def _evidence_index(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for document in documents:
        for ref in _document_refs(document):
            existing = evidence.get(ref)
            if existing is not None and _hit_key(existing) != _hit_key(document):
                raise RetrievalAdapterError("ambiguous authorized evidence reference")
            evidence[ref] = document
    return evidence


def validate_workspace_root(workspace_base: Path, workspace_id: str) -> Path:
    base = workspace_base.resolve()
    workspace_root = (base / workspace_id).resolve()
    if not workspace_id.strip() or workspace_root == base or not _is_within(workspace_root, base):
        raise RetrievalAdapterError("workspace path escapes workspace root")
    return workspace_root


def _validate_local_request(workspace_base: Path, request: RetrievalRequest) -> None:
    if request.top_k < 1:
        raise RetrievalAdapterError("top_k must be positive for local retrieval")
    validate_workspace_root(workspace_base, request.workspace_id)


def _resolve_graph_path(workspace_base: Path, request: RetrievalRequest) -> Path:
    workspace_root = validate_workspace_root(workspace_base, request.workspace_id)

    if request.graph_path:
        configured = Path(request.graph_path)
        candidate = configured if configured.is_absolute() else workspace_root / configured
        candidates = [candidate]
    else:
        candidates = [workspace_root / "corpus_graph.json", workspace_root / "corpus_graph.jsonl"]

    for candidate in candidates:
        resolved = candidate.resolve()
        if not _is_within(resolved, workspace_root):
            raise RetrievalAdapterError("graph path escapes workspace root")
        if resolved.suffix.lower() not in {".json", ".jsonl"}:
            raise RetrievalAdapterError("graph file must be JSON or JSONL")
        if resolved.is_file():
            return resolved
    raise RetrievalAdapterError("workspace graph file is unavailable")


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_graph(path: Path, workspace_id: str) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".jsonl":
            graph = _load_jsonl_graph(path)
        else:
            graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RetrievalAdapterError("invalid workspace graph file") from exc
    if not isinstance(graph, dict):
        raise RetrievalAdapterError("workspace graph must be an object")
    if graph.get("schema_version") != GRAPH_SCHEMA_VERSION:
        raise RetrievalAdapterError("unsupported workspace graph schema")
    if graph.get("workspace_id") != workspace_id:
        raise RetrievalAdapterError("workspace graph identity mismatch")
    if not isinstance(graph.get("corpus_version"), str) or not graph["corpus_version"].strip():
        raise RetrievalAdapterError("workspace graph corpus_version is required")
    if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise RetrievalAdapterError("workspace graph nodes and edges must be lists")
    return graph


def _load_jsonl_graph(path: Path) -> dict[str, Any]:
    graph: dict[str, Any] = {"nodes": [], "edges": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise RetrievalAdapterError("workspace graph JSONL entries must be objects")
        if "schema_version" in item:
            for field in ("schema_version", "workspace_id", "corpus_version"):
                if field in item:
                    graph[field] = item[field]
            graph["nodes"].extend(item.get("nodes") or [])
            graph["edges"].extend(item.get("edges") or [])
        elif item.get("record_type") == "node":
            graph["nodes"].append({key: value for key, value in item.items() if key != "record_type"})
        elif item.get("record_type") == "edge":
            graph["edges"].append({key: value for key, value in item.items() if key != "record_type"})
        else:
            raise RetrievalAdapterError("workspace graph JSONL record_type is invalid")
    return graph


def _authorized_graph(
    graph: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {}
    for raw_node in graph["nodes"]:
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node.get("id") or "")
        refs = _valid_evidence_refs(raw_node.get("evidence_refs"), evidence)
        if not node_id or refs is None:
            continue
        nodes[node_id] = {**raw_node, "id": node_id, "evidence_refs": refs}

    edges: list[dict[str, Any]] = []
    for raw_edge in graph["edges"]:
        if not isinstance(raw_edge, dict):
            continue
        source = str(raw_edge.get("source") or "")
        target = str(raw_edge.get("target") or "")
        refs = _valid_evidence_refs(raw_edge.get("evidence_refs"), evidence)
        try:
            weight = float(raw_edge.get("weight", 1.0))
        except (TypeError, ValueError):
            continue
        if source not in nodes or target not in nodes or refs is None or not math.isfinite(weight) or weight < 0:
            continue
        edges.append({**raw_edge, "source": source, "target": target, "weight": weight, "evidence_refs": refs})
    edges.sort(key=lambda edge: (edge["source"], edge["target"], str(edge.get("type") or "")))
    return nodes, edges


def _valid_evidence_refs(value: Any, evidence: dict[str, dict[str, Any]]) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    refs = [str(ref) for ref in value if str(ref)]
    if not refs or any(ref not in evidence for ref in refs):
        return None
    return sorted(set(refs))


def _query_terms(query: str) -> list[str]:
    lowered = query.lower()
    terms = [term for term in re.split(r"[^0-9a-zA-Z]+", lowered) if len(term) > 2]
    for run in re.findall(r"[\u4e00-\u9fff]+", query):
        terms.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        terms.extend(run[index : index + 3] for index in range(max(0, len(run) - 2)))
    return list(dict.fromkeys(term for term in terms if term))


def _hit_key(hit: dict[str, Any]) -> str:
    for field in ("id", "chunk_id"):
        value = hit.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return f"source:{hit.get('source_file', '')}|content:{hit.get('content', '')}"


def _decorate_hit(
    hit: dict[str, Any],
    *,
    score: float,
    rank: int,
    adapter: str,
    retrieval_mode: str,
    score_kind: str,
    graph_path_refs: list[str],
    candidate_sources: list[str],
    extra_trace: dict[str, Any] | None = None,
    permission_filtered: bool = False,
) -> RetrievalHit:
    item: RetrievalHit = dict(hit)
    item["score"] = score
    item["rank"] = rank
    item["adapter"] = adapter
    item["retrieval_mode"] = retrieval_mode
    item["score_kind"] = score_kind
    item["graph_path_refs"] = graph_path_refs
    trace = {
        "candidate_sources": candidate_sources,
        "permission_filtered": permission_filtered,
    }
    if extra_trace:
        trace.update(extra_trace)
    item["retrieval_trace"] = trace
    return item
