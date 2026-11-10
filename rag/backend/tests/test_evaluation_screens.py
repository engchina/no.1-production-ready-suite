"""評価の案内すべき画面の入力と、ナレッジベースとプロファイルの比較のテスト（#2402）。

画面の名前・回答・根拠は合成の内容（業務の内容は書かない）。
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from pytest import MonkeyPatch

from app.main import app
from app.rag.evaluation import EvaluationRunner, score_case_answers
from app.rag.pipeline import SearchStageProgressCallback
from app.schemas.evaluation import (
    EvaluationCase,
    EvaluationMetrics,
    EvaluationRunRequest,
    EvaluationScreen,
)
from app.schemas.search import RetrievedChunk, SearchDiagnostics, SearchRequest, SearchResponse
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

SCREEN_A = EvaluationScreen(name="画面A", aliases=["エー画面"])
SCREEN_B = EvaluationScreen(name="画面B")


def _chunk(text: str, chunk_id: str = "doc-1:0") -> RetrievedChunk:
    return RetrievedChunk(document_id="doc-1", chunk_id=chunk_id, text=text, score=1.0)


def _response(
    answer: str,
    *,
    citations: Sequence[RetrievedChunk] = (),
    answer_details: Mapping[str, Any] | None = None,
    trace_id: str = "trace-1",
) -> SearchResponse:
    return SearchResponse(
        answer=answer,
        citations=list(citations) or [_chunk("本文")],
        trace_id=trace_id,
        guardrail_warnings=[],
        elapsed_ms=1.0,
        diagnostics=SearchDiagnostics(answer=dict(answer_details or {"outcome": "answered"})),
    )


def _case(*screens: EvaluationScreen, **kwargs: Any) -> EvaluationCase:
    return EvaluationCase(
        id=kwargs.pop("id", "case-1"),
        query="設定を変えるには？",
        relevant_document_ids=["doc-1"],
        required_answer_points=[screen.name for screen in screens],
        **kwargs,
    )


# ---- ケースの採点と集計 --------------------------------------------------------------------


def test_case_scoring_refusal_is_not_matched_and_clarification_is_not_measured() -> None:
    refused = score_case_answers(
        _case(SCREEN_A, expected_response_decision="answer"),
        [(_response("確認できません", answer_details={"outcome": "insufficient_evidence"}), {})],
    )
    clarification = SearchResponse(
        answer="どちらの場合ですか？",
        citations=[],
        trace_id="trace-2",
        elapsed_ms=1,
        diagnostics=SearchDiagnostics(answer={"outcome": "needs_clarification"}),
    )
    asked = score_case_answers(
        _case(SCREEN_A, expected_response_decision="clarify"), [(clarification, {})]
    )
    assert refused.response_decision_accuracy == 0
    assert "unexpected_handling" in refused.failure_reasons
    assert asked.response_decision_accuracy == 1
    assert asked.answer_correctness is None


class _ScreenPipeline:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def run(
        self,
        request: SearchRequest,
        trace_id: str | None = None,
        progress_callback: SearchStageProgressCallback | None = None,
    ) -> SearchResponse:
        del request, progress_callback
        return _response(self.answer, trace_id=trace_id or "trace")


async def test_runner_keeps_case_when_screen_judge_fails() -> None:
    async def judge(**_: object) -> Mapping[str, Any]:
        raise RuntimeError("judge down")

    metrics = await EvaluationRunner(
        pipeline=_ScreenPipeline("設定を開いて変更します。"), answer_judge=judge
    ).run(cases=[_case(SCREEN_A)], top_k=5)
    result = metrics.case_results[0]
    assert result.status == "success"
    assert result.answer_correctness is None
    assert result.answer_evaluation is not None and result.answer_evaluation.status == "error"
    assert result.failure_reasons == ["answer_evaluation_error"]
    assert metrics.passed is False


# ---- 案内すべき画面のスキーマ ----------------------------------------------------------------


def test_screen_schema_cleans_aliases_and_rejects_blank_name() -> None:
    screen = EvaluationScreen(name=" 画面A ", aliases=[" エー画面 ", "", "エー画面"])
    assert screen.name == "画面A" and screen.aliases == ["エー画面"]
    with pytest.raises(ValueError):
        EvaluationScreen(name="  ")


def test_run_request_compares_distinct_profiles_and_rejects_same_profile() -> None:
    request = EvaluationRunRequest(
        cases=[_case()],
        search_answer_profile_id=" profile-1 ",
        compare_search_answer_profile_id=" profile-2 ",
    )
    assert request.search_answer_profile_id == "profile-1"
    assert request.compare_search_answer_profile_id == "profile-2"
    with pytest.raises(ValueError, match="別にしてください"):
        EvaluationRunRequest(
            cases=[_case()],
            search_answer_profile_id="profile-1",
            compare_search_answer_profile_id=" profile-1 ",
        )


# ---- プロファイル同士の比較 --------------------------------------------------------


def test_compare_api_runs_both_profiles(monkeypatch: MonkeyPatch) -> None:
    """比べるプロファイルを指定すると、それぞれのプロファイルで 2 回流して並べる。"""
    runs: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    class FakeRunner:
        async def run(self, **kwargs: Any) -> EvaluationMetrics:
            runs.append(kwargs)
            rate = 0.5 if kwargs["search_answer_profile_id"] == "profile-1" else 0.25
            return EvaluationMetrics(case_count=1, response_decision_accuracy=rate)

    class FakeOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            artifacts.append(artifact)
            return "eval-1"

    monkeypatch.setattr("app.api.routes.evaluation.EvaluationRunner", FakeRunner)
    monkeypatch.setattr("app.api.routes.evaluation.OracleClient", FakeOracleClient)

    response = client.post(
        "/api/evaluation/run",
        json={
            "cases": [{"id": "case-1", "query": "設定を変えるには？"}],
            "search_answer_profile_id": "profile-base",
            "compare_search_answer_profile_id": "profile-1",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["response_decision_accuracy"] == 0.25
    assert data["comparison"]["search_answer_profile_id"] == "profile-1"
    assert data["comparison"]["metrics"]["response_decision_accuracy"] == 0.5
    assert [run["search_answer_profile_id"] for run in runs] == ["profile-base", "profile-1"]
    assert runs[0]["knowledge_base_ids"] == []
    # プロファイルの範囲は、プロファイルの参照 KB で決める（KB の指定は渡さない）。
    assert runs[1]["knowledge_base_ids"] == []
    assert "knowledge_base_id" not in runs[1]["filters"]
    assert artifacts[0]["request_summary"]["compare_search_answer_profile_id"] == "profile-1"


async def test_compare_progress_counts_both_passes(monkeypatch: MonkeyPatch) -> None:
    """2 回流す評価の進捗は、job 全体（ケース × 2）の数で通知する。"""
    from app.api.routes import evaluation as evaluation_route

    seen: list[tuple[int, str | None]] = []

    async def progress(*, completed_cases: int, current_case_id: str | None) -> None:
        seen.append((completed_cases, current_case_id))

    class FakeRunner:
        async def run(self, **kwargs: Any) -> EvaluationMetrics:
            report = kwargs["progress"]
            await report(completed_cases=0, current_case_id="case-1")
            await report(completed_cases=1, current_case_id=None)
            return EvaluationMetrics(case_count=1)

    class FakeOracleClient:
        async def save_evaluation_artifact(self, artifact: dict[str, Any]) -> str:
            del artifact
            return "eval-1"

    monkeypatch.setattr(evaluation_route, "EvaluationRunner", FakeRunner)
    monkeypatch.setattr(evaluation_route, "OracleClient", FakeOracleClient)
    request = EvaluationRunRequest(
        cases=[_case()], knowledge_base_ids=["kb-1"], compare_search_answer_profile_id="p-1"
    )

    await evaluation_route._execute_run(request, time_budget_seconds=60.0, progress=progress)

    assert seen == [(0, "case-1"), (1, "case-1"), (2, None)]
