"""送信の前に済ませた履歴による補完も、処理の経過に同じ段として残す（#4794）。"""

import json
from time import perf_counter
from typing import Any, cast

import pytest
from pr_backend_core.config.tracing import TracingSettings
from pr_backend_core.observability.call_policy import call_content_policy
from pr_backend_core.observability.tracing import (
    TraceSummary,
    configure_tracing,
    operation_trace,
    shutdown_tracing,
)

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerOutcome
from app.rag.guardrails import GuardrailResult
from app.rag.history_question import HistoryConfirmation
from app.rag.pipeline import ChatTurn, PreparedHistoryRewrite, RagPipeline
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi, SavingOracle


@pytest.fixture(autouse=True)
def tracing() -> Any:
    configure_tracing(TracingSettings(), service_name="synthetic")
    try:
        yield
    finally:
        shutdown_tracing()


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    request: SearchRequest,
    prepared: PreparedHistoryRewrite | None,
    *,
    capture: bool = True,
) -> tuple[TraceSummary, list[Any], list[str]]:
    engine_queries: list[str] = []

    async def forbidden(self: Any, *args: Any, **kwargs: Any) -> str:
        raise AssertionError("準備で済ませた補完を送信の側で呼び直さない")

    async def answer(self: Any, request: Any, **kwargs: Any) -> AnswerOutcome:
        engine_queries.append(request.query)
        return AnswerOutcome(answer="合成の回答", citations=[], context_text="", diagnostics={})

    monkeypatch.setattr(RagPipeline, "_rewrite_query_with_history", forbidden)
    monkeypatch.setattr(AnswerEngine, "run", answer)
    progress: list[Any] = []

    async def collect(item: Any) -> None:
        progress.append(item)

    pipeline = RagPipeline(
        settings=Settings(_env_file=None, rag_history_rewrite_enabled=True),
        oracle=cast(Any, SavingOracle()),
        genai=cast(Any, FakeGenAi()),
        history_question_prepared=True,
        prepared_history_rewrite=prepared,
    )
    with (
        call_content_policy(lambda: capture),
        operation_trace("rag.search", operation_id="synthetic") as summary,
    ):
        await pipeline._run_engine(
            request,
            trace_id="synthetic",
            started_at=perf_counter(),
            query_guardrail=GuardrailResult(
                allowed=True, sanitized_text=request.query, findings=[]
            ),
            token_callback=None,
            history=[
                ChatTurn(role="USER", content="合成の対象 A について。"),
                ChatTurn(role="ASSISTANT", content="合成の回答 A。"),
            ],
            progress_callback=collect,
        )
    saved = TraceSummary.model_validate_json(summary.model_dump_json())
    return saved, progress, engine_queries


def _rewrite_span(summary: TraceSummary) -> Any:
    return next(
        item for item in summary.spans if item.attributes.get("pr.stage") == "history_rewrite"
    )


@pytest.mark.parametrize("capture", [True, False], ids=["body-on", "body-off"])
async def test_prepared_rewrite_is_recorded_with_its_duration_and_details(
    monkeypatch: pytest.MonkeyPatch, capture: bool
) -> None:
    fact = HistoryConfirmation(id="fact-a", question="対象は？", answer="対象 A")
    request = SearchRequest(
        query="合成の対象 A の手順は？",
        original_question="その手順は？",
        search_answer_profile_id="profile-a",
    )
    summary, progress, engine_queries = await _run(
        monkeypatch,
        request,
        PreparedHistoryRewrite(elapsed_seconds=1.5, confirmed_facts=(fact,)),
        capture=capture,
    )
    assert engine_queries == ["合成の対象 A の手順は？"]
    span = _rewrite_span(summary)
    assert span.status == "done"
    assert span.duration_ms >= 1500
    assert span.display is not None and span.display.kind == "history_rewrite"
    finished = [item for item in progress if item.stage == "history_rewrite"]
    assert [item.outcome for item in finished] == ["started", "success"]
    assert finished[-1].elapsed_ms >= 1500
    details = span.display.details
    if not capture:
        # 本文の記録がオフなら、送信の側で補ったときと同じく詳細の本文を残さない（#3901）。
        assert details is not None and details.call is not None
        assert details.call.capture_enabled is False
        assert not details.fields
        return
    assert details is not None
    fields = {field.label: field.value for field in details.fields}
    assert fields == {"参照した会話": "2 件の発言", "書き換えた質問": "合成の対象 A の手順は？"}
    assert details.call is not None
    assert json.loads(details.call.input.text)["質問"] == "その手順は？"
    output = json.loads(details.call.output.text)
    assert output["返却された補完質問"] == "合成の対象 A の手順は？"
    assert output["採用した確認回答"][0]["id"] == "fact-a"


async def test_prepared_rewrite_without_change_shows_original_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SearchRequest(query="合成の単独の質問は？", search_answer_profile_id="profile-a")
    summary, _progress, _queries = await _run(
        monkeypatch, request, PreparedHistoryRewrite(elapsed_seconds=0.2)
    )
    span = _rewrite_span(summary)
    assert span.display is not None and span.display.details is not None
    fields = {field.label: field.value for field in span.display.details.fields}
    assert fields["結果"] == "書き換えなし（元の質問のまま）"


async def test_prepared_question_without_rewrite_has_no_rewrite_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 準備で補完を実行しなかった（確認に新しく答えて元の質問で送るなど）なら段を作らない。
    request = SearchRequest(query="合成の質問は？", search_answer_profile_id="profile-a")
    summary, progress, _queries = await _run(monkeypatch, request, None)
    assert not [
        item for item in summary.spans if item.attributes.get("pr.stage") == "history_rewrite"
    ]
    assert not [item for item in progress if item.stage == "history_rewrite"]
