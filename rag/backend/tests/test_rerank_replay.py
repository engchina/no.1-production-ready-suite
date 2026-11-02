"""再ランク・埋め込みの SDK の呼び出しの記録と再生（#4089）。

実の ``OciGenAiClient`` に偽の SDK を渡す。モデルの API・鍵には接続しない。
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch
from rag_engine.resources.external_calls import (
    ExternalCallMismatch,
    ExternalCallSession,
    use_external_calls,
)
from rag_engine.resources.rerank_diagnostics import bind_rerank_context

from app import config as config_module
from app.clients.oci_genai import OciGenAiClient
from app.config import Settings, get_settings
from app.rag import evaluation_run_cli
from app.schemas.evaluation import EvaluationMetrics, EvaluationRunRequest

ServiceError = importlib.import_module("oci.exceptions").ServiceError
QUERY = "合成の質問"
DOCUMENTS = ["合成の候補 A", "合成の候補 B", "合成の候補 C"]
RANKS = [(2, 0.91), (0, 0.42), (1, 0.05)]


class Sdk:
    """応答か例外を順に返す偽の SDK。"""

    def __init__(self, *rerank_steps: object, embed_steps: tuple[object, ...] = ()) -> None:
        self.rerank_steps = list(rerank_steps)
        self.embed_steps = list(embed_steps)
        self.rerank_calls = 0
        self.embed_calls = 0

    def rerank_text(self, rerank_text_details: object) -> object:
        self.rerank_calls += 1
        return _step(self.rerank_steps)

    def embed_text(self, embed_text_details: object) -> object:
        self.embed_calls += 1
        return _step(self.embed_steps)


def _step(steps: list[object]) -> object:
    step = steps.pop(0)
    if isinstance(step, BaseException):
        raise step
    return step


def _rerank_response(ranks: list[tuple[int, float]] = RANKS) -> object:
    document_ranks = [SimpleNamespace(index=i, relevance_score=s) for i, s in ranks]
    return SimpleNamespace(data=SimpleNamespace(document_ranks=document_ranks))


def _client(sdk: object | None = None, *, cache: bool = True) -> OciGenAiClient:
    """``sdk`` が無ければ、SDK の client を作ろうとした時点で失敗する（鍵なしの再生）。"""

    async def inline(operation: Callable[[], Any]) -> Any:
        return operation()

    client = OciGenAiClient(
        settings=Settings.model_construct(
            oci_compartment_id="synthetic",
            oci_genai_rerank_model="synthetic-rerank",
            oci_genai_embedding_model="synthetic-embedding",
            oci_genai_embedding_dim=2,
            rag_rerank_cache_enabled=cache,
            rag_embedding_cache_enabled=cache,
        ),
        inference_client=sdk,  # type: ignore[arg-type]
        sdk_call_runner=inline,
    )
    if sdk is None:

        def no_client() -> Any:
            raise AssertionError("再生で SDK の client を作りました")

        client._client = no_client  # type: ignore[method-assign]
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: MonkeyPatch) -> None:
    async def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("app.clients.ai_calls._sleep", no_sleep)


@pytest.fixture
def spans(monkeypatch: MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    @contextmanager
    def observe(name: str, **kwargs: Any) -> Iterator[Any]:
        data: dict[str, Any] = {"name": name, "outputs": 0, **kwargs}
        captured.append(data)

        def set_output(*args: Any, **output_kwargs: Any) -> None:
            data["outputs"] += 1

        yield SimpleNamespace(
            set_call_input=lambda *args, **input_kwargs: None,
            set_call_output=set_output,
            set_attributes=lambda values: data["attributes"].update(values),
            set_status=lambda value: data.update(status=value),
            set_display=lambda **values: data.update(values),
        )

    monkeypatch.setattr("app.clients.oci_genai.observed_span", observe)
    return captured


def _recorded(session: ExternalCallSession) -> ExternalCallSession:
    """保存と読み込みを通した再生の session（記録は JSON にできる必要がある）。"""
    return ExternalCallSession.from_recording(
        json.loads(json.dumps(session.to_recording({}), ensure_ascii=False))
    )


async def test_record_keeps_flat_ranks_and_replay_returns_same_without_sdk() -> None:
    sdk = Sdk(_rerank_response())
    session = ExternalCallSession("record")
    with use_external_calls(session):
        recorded = await _client(sdk).rerank(QUERY, DOCUMENTS, 3)
    assert sdk.rerank_calls == 1
    assert recorded == RANKS

    assert [entry["kind"] for entry in session.entries] == ["rerank"]
    assert session.entries[0]["result"] == [
        {"index": 2, "relevance_score": 0.91},
        {"index": 0, "relevance_score": 0.42},
        {"index": 1, "relevance_score": 0.05},
    ]
    # 記録に質問・候補の原文を残さない。
    text = json.dumps(session.to_recording({}), ensure_ascii=False)
    assert QUERY not in text and DOCUMENTS[0] not in text

    replay = _recorded(session)
    with use_external_calls(replay):
        replayed = await _client().rerank(QUERY, DOCUMENTS, 3)
    assert replayed == recorded
    assert replay.unused() == [] and replay.mismatches == []


@pytest.mark.parametrize(
    ("query", "documents", "top_n"),
    [
        ("別の合成の質問", DOCUMENTS, 3),
        (QUERY, DOCUMENTS[:2], 2),
        (QUERY, DOCUMENTS, 2),
    ],
    ids=["query", "documents", "top_n"],
)
async def test_replay_of_unrecorded_input_fails_without_sdk(
    query: str, documents: list[str], top_n: int
) -> None:
    session = ExternalCallSession("record")
    with use_external_calls(session):
        await _client(Sdk(_rerank_response())).rerank(QUERY, DOCUMENTS, 3)

    replay = _recorded(session)
    with use_external_calls(replay), pytest.raises(ExternalCallMismatch):
        await _client().rerank(query, documents, top_n)
    assert replay.mismatches


async def test_replay_does_not_create_sdk_attempt(spans: list[dict[str, Any]]) -> None:
    session = ExternalCallSession("record")
    with use_external_calls(session):
        await _client(Sdk(_rerank_response())).rerank(QUERY, DOCUMENTS, 3)
    recorded_call = next(span for span in spans if span["name"] == "gen_ai.rerank")
    assert recorded_call["attributes"]["rerank.status"] == "completed"
    assert recorded_call["outputs"] == 1
    spans.clear()

    with (
        use_external_calls(_recorded(session)),
        bind_rerank_context(
            phase="final", batch_id="b", query_index=0, query_role="original"
        ) as receipt,
    ):
        assert await _client().rerank(QUERY, DOCUMENTS, 3) == RANKS

    replayed_call = next(span for span in spans if span["name"] == "gen_ai.rerank")
    assert replayed_call["attributes"]["rerank.status"] == "not_run"
    assert replayed_call["attributes"]["rerank.status_scope"] == "sdk_attempt"
    assert replayed_call["outputs"] == 0
    summary = receipt.to_dict()
    assert summary["status"] == "completed"
    assert summary["cache_state"] == "miss"
    assert summary["sdk_attempt_count"] == 0 and summary["sdk_attempts"] == []


async def test_without_session_calls_sdk_and_keeps_sdk_attempt(
    spans: list[dict[str, Any]],
) -> None:
    sdk = Sdk(_rerank_response())
    with bind_rerank_context(
        phase="final", batch_id="b", query_index=0, query_role="original"
    ) as receipt:
        assert await _client(sdk).rerank(QUERY, DOCUMENTS, 3) == RANKS
    assert sdk.rerank_calls == 1
    assert receipt.to_dict()["sdk_attempt_count"] == 1
    call = next(span for span in spans if span["name"] == "gen_ai.rerank")
    assert call["attributes"]["rerank.status"] == "completed"
    assert call["outputs"] == 1


async def test_cache_hit_in_record_and_replay_makes_one_call() -> None:
    sdk = Sdk(_rerank_response())
    session = ExternalCallSession("record")
    client = _client(sdk)
    with use_external_calls(session):
        await client.rerank(QUERY, DOCUMENTS, 3)
        await client.rerank(QUERY, DOCUMENTS, 3)
    assert sdk.rerank_calls == 1 and len(session.entries) == 1

    replay = _recorded(session)
    client = _client()
    with use_external_calls(replay):
        assert await client.rerank(QUERY, DOCUMENTS, 3) == RANKS
        assert await client.rerank(QUERY, DOCUMENTS, 3) == RANKS
    assert replay.unused() == [] and replay.mismatches == []


@pytest.mark.parametrize(
    "failure",
    [
        ServiceError(429, "TooManyRequests", {}, "synthetic throttle"),
        ServiceError(503, "ServiceUnavailable", {}, "synthetic outage"),
        TimeoutError("synthetic timeout"),
    ],
    ids=["http-429", "http-503", "timeout"],
)
async def test_replay_repeats_retry_of_recorded_failure(failure: Exception) -> None:
    """失敗の記録は再試行の判定まで同じになり、成功の記録まで使い切る。"""
    sdk = Sdk(failure, _rerank_response())
    session = ExternalCallSession("record")
    with use_external_calls(session):
        recorded = await _client(sdk).rerank(QUERY, DOCUMENTS, 3)
    assert sdk.rerank_calls == 2 and len(session.entries) == 2
    assert "error" in session.entries[0]

    replay = _recorded(session)
    with use_external_calls(replay):
        assert await _client().rerank(QUERY, DOCUMENTS, 3) == recorded
    assert replay.unused() == [] and replay.mismatches == []


async def test_replay_stops_at_recorded_permanent_failure_like_record() -> None:
    failure = ServiceError(404, "NotAuthorizedOrNotFound", {}, "synthetic")
    sdk = Sdk(failure)
    session = ExternalCallSession("record")
    with use_external_calls(session), pytest.raises(Exception) as recorded:
        await _client(sdk).rerank(QUERY, DOCUMENTS, 3)
    assert sdk.rerank_calls == 1

    replay = _recorded(session)
    with use_external_calls(replay), pytest.raises(Exception) as replayed:
        await _client().rerank(QUERY, DOCUMENTS, 3)
    assert type(replayed.value) is type(recorded.value)
    assert replay.unused() == [] and replay.mismatches == []


async def test_malformed_response_is_recorded_as_failure_and_replayed() -> None:
    malformed = SimpleNamespace(data=SimpleNamespace(document_ranks=None))
    sdk = Sdk(malformed, _rerank_response())
    session = ExternalCallSession("record")
    with use_external_calls(session):
        recorded = await _client(sdk).rerank(QUERY, DOCUMENTS, 3)
    assert "document_ranks がありません" in session.entries[0]["error"]["message"]

    replay = _recorded(session)
    with use_external_calls(replay):
        assert await _client().rerank(QUERY, DOCUMENTS, 3) == recorded
    assert replay.unused() == [] and replay.mismatches == []


async def test_embedding_replay_repeats_retry_of_recorded_service_error() -> None:
    vectors = SimpleNamespace(data=SimpleNamespace(embeddings=[[0.1, 0.2]]))
    failure = ServiceError(429, "TooManyRequests", {}, "synthetic throttle")
    sdk = Sdk(embed_steps=(failure, vectors))
    session = ExternalCallSession("record")
    with use_external_calls(session):
        recorded = await _client(sdk).embed(["合成の本文"])
    assert sdk.embed_calls == 2

    replay = _recorded(session)
    with use_external_calls(replay):
        assert await _client().embed(["合成の本文"]) == recorded
    assert replay.unused() == [] and replay.mismatches == []


@pytest.fixture
def _restore_cli_settings(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    names = ["rag_evaluation_concurrency"] + [
        f"oci_enterprise_ai_{slot}{field}"
        for slot in ("", "secondary_", "tertiary_")
        for field in ("api_key", "endpoint")
    ]
    for name in names:
        monkeypatch.setattr(settings, name, getattr(settings, name))


@pytest.mark.usefixtures("_restore_cli_settings")
def test_cli_replays_rerank_from_recording_file(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """``evaluation_run_cli`` の記録のファイルから、SDK の client なしで再ランクを再生する。"""
    from app.api.routes import evaluation as evaluation_route

    golden = tmp_path / "golden.json"
    golden.write_text(json.dumps({"cases": [{"id": "c1", "query": "q"}]}), encoding="utf-8")
    monkeypatch.setattr(config_module, "local_today", lambda settings: date(2026, 10, 31))
    seen: list[list[tuple[int, float]]] = []
    client = {"current": _client(Sdk(_rerank_response()))}

    async def execute_run(
        request: EvaluationRunRequest, *, time_budget_seconds: float
    ) -> tuple[EvaluationMetrics, None]:
        del request, time_budget_seconds
        seen.append(await client["current"].rerank(QUERY, DOCUMENTS, 3))
        manifest = {"as_of": "2026-10-31", "code_version": "abc", "comparable": True}
        return EvaluationMetrics(case_count=1, passed=True, run_manifest=manifest), None

    monkeypatch.setattr(evaluation_route, "_execute_run", execute_run)
    recording = tmp_path / "recording.json"
    live = tmp_path / "live.json"
    assert (
        evaluation_run_cli.main(
            ["run", str(golden), "--record", str(recording), "--output", str(live)]
        )
        == 0
    )
    assert json.loads(recording.read_text(encoding="utf-8"))["entries"][0]["kind"] == "rerank"

    client["current"] = _client()
    output = tmp_path / "replay.json"
    assert (
        evaluation_run_cli.main(
            ["run", str(golden), "--replay", str(recording), "--output", str(output)]
        )
        == 0
    )
    assert seen == [RANKS, RANKS]
    external = json.loads(output.read_text(encoding="utf-8"))["data"]["run_manifest"][
        "external_calls"
    ]
    assert external["mode"] == "replay" and external["calls"] == 1
    assert external["mismatches"] == 0 and external["unused"] == 0
