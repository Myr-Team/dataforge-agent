from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import foundry_client, orchestrator  # noqa: E402
from backend.feasibility_rubric import finalize_verdict_contract  # noqa: E402


class Transient429(RuntimeError):
    status_code = 429


class Business400(RuntimeError):
    status_code = 400


class _Responses:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def create(self, **_: Any) -> Any:
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("unexpected extra responses.create call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome() if callable(outcome) else outcome


class _Client:
    def __init__(self, outcomes: list[Any]) -> None:
        self.responses = _Responses(outcomes)


class _DeltaEvent:
    type = "response.output_text.delta"

    def __init__(self, delta: str) -> None:
        self.delta = delta


def _stream(values: Iterable[Any]) -> Iterable[Any]:
    for value in values:
        if isinstance(value, BaseException):
            raise value
        yield value


def _without_sleep() -> tuple[Any, Any]:
    original_sleep = foundry_client.time.sleep
    original_jitter = foundry_client.random.uniform
    foundry_client.time.sleep = lambda _: None
    foundry_client.random.uniform = lambda *_: 0.0
    return original_sleep, original_jitter


def _restore_sleep(original_sleep: Any, original_jitter: Any) -> None:
    foundry_client.time.sleep = original_sleep
    foundry_client.random.uniform = original_jitter


def test_llm_retry_non_stream() -> None:
    original_sleep, original_jitter = _without_sleep()
    try:
        client = _Client([Transient429("rate limited"), SimpleNamespace(id="ok", usage={})])
        response = foundry_client._responses_create_with_retry(client, model="gpt-test", input="hello")
        assert client.responses.calls == 2
        assert getattr(response, "_dataforge_retry_attempts") == 1

        client = _Client([Business400("bad request")])
        try:
            foundry_client._responses_create_with_retry(client, model="gpt-test", input="hello")
        except Business400:
            pass
        else:
            raise AssertionError("business 400 must not be retried or swallowed")
        assert client.responses.calls == 1
    finally:
        _restore_sleep(original_sleep, original_jitter)


def test_llm_retry_stream_boundary() -> None:
    original_sleep, original_jitter = _without_sleep()
    try:
        client = _Client([ConnectionResetError("reset before token"), _stream([_DeltaEvent("ok")])])
        events = list(foundry_client._stream_response_events_with_retry(client, {"stream": True}))
        assert client.responses.calls == 2
        assert [event.delta for event in events] == ["ok"]

        client = _Client([_stream([_DeltaEvent("partial"), ConnectionResetError("reset after token")])])
        try:
            list(foundry_client._stream_response_events_with_retry(client, {"stream": True}))
        except ConnectionResetError:
            pass
        else:
            raise AssertionError("stream must not replay after a token has been emitted")
        assert client.responses.calls == 1
    finally:
        _restore_sleep(original_sleep, original_jitter)


def test_producer_degrades_when_image_fails() -> None:
    original_pdf = orchestrator.render_pdf_report
    original_image = orchestrator.generate_image
    original_refs = orchestrator.workspace_reference_images
    try:
        def render_pdf_mock(*_args, workspace_id: str, **_kwargs):
            assert workspace_id == "demo-corpus"
            return {
                "artifact_url": "/api/artifacts/project_proposal/mock.pdf",
                "local_path": str(ROOT / "artifacts" / "mock.pdf"),
                "bytes": 1024,
                "mode": "test_pdf",
            }

        orchestrator.render_pdf_report = render_pdf_mock
        orchestrator.generate_image = lambda *_: (_ for _ in ()).throw(TimeoutError("image timeout"))
        orchestrator.workspace_reference_images = lambda *_: []
        result = orchestrator._run_producer(
            {
                "workspace_id": "demo-corpus",
                "answer": {"text": "A bounded opportunity based on evidence."},
                "corpus": {"top_hits": []},
                "market": {},
                "audit": {},
                "feasibility": {
                    "opportunity_id": "test-opportunity",
                    "verdict": "conditional",
                    "overall_confidence": "market_inferred",
                    "dimensions": [],
                    "gap_list": ["Need more direct buyer evidence."],
                },
            },
            ["pdf", "concept_image"],
        )
        assert result["artifact_urls"]["pdf"].endswith(".pdf")
        assert "concept_image" not in result["artifact_urls"]
        assert result["degraded"] is True
        assert result["warnings"][0]["kind"] == "concept_image"
    finally:
        orchestrator.render_pdf_report = original_pdf
        orchestrator.generate_image = original_image
        orchestrator.workspace_reference_images = original_refs


def test_verdict_downgrade_is_visible() -> None:
    artifact = {
        "_blind_feasibility": {
            "verdict": "feasible",
            "overall_confidence": "data_confirmed",
            "dimensions": [{"name": "asset_data", "score": 4.6}],
        },
        "feasibility": {
            "verdict": "not_yet_feasible",
            "overall_confidence": "speculative",
            "dimensions": [{"name": "asset_data", "score": 1.7}],
            "gap_list": ["No direct customer outcome evidence."],
        },
    }
    audit = {"issues": ["Evidence catalog lacks direct customer outcome evidence."]}
    contract = finalize_verdict_contract(artifact, audit)
    assert contract["verdict_before"] == "feasible"
    assert contract["verdict_after"] == "not_yet_feasible"
    assert contract["downgrade"]["downgrade_reason"]
    assert artifact["verdict_downgrade"] == contract["downgrade"]


def main() -> None:
    tests = [
        test_llm_retry_non_stream,
        test_llm_retry_stream_boundary,
        test_producer_degrades_when_image_fails,
        test_verdict_downgrade_is_visible,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
