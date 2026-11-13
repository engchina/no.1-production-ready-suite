"""回答を回答フローだけにしたこと(#594)を、呼び出し口ごとに確かめる。

検索 API(検索・回答プロファイルあり・なし)・MCP・評価は、どれも ``RagPipeline.run`` を通る。
回答エンジンの選択(旧 ``RAG_ANSWER_ENGINE``・検索
・回答プロファイルの ``answer_engine``)は削除したので、

保存済みの値や環境変数が残っていても回答フローで回答する。外部 I/O はスタブ。
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest import MonkeyPatch

import app.rag.pipeline as pipeline_module
from app.api.routes import search as search_route
from app.config import Settings, get_settings
from app.main import app
from app.rag.evaluation import EvaluationRunner
from app.rag.pipeline import RagPipeline
from app.rag.search_answer_profile_config import (
    SearchAnswerProfileConfig,
)
from app.schemas.evaluation import EvaluationCase
from tests.support import AsgiTestClient
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm
from tests.test_search_search_answer_profile import FakeViewOracle

client = AsgiTestClient(app)


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: MonkeyPatch) -> None:
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)


class _ViewAndSearchOracle(FakeViewOracle, FakeOracle):
    """検索・回答プロファイルの解決(検索 API)と回答フローの検索(pipeline)の両方を受けるスタブ。"""

    def __init__(self, views: dict[str, SearchAnswerProfileConfig]) -> None:
        FakeViewOracle.__init__(self, views)
        FakeOracle.__init__(self)


def _install_real_pipeline(
    monkeypatch: MonkeyPatch, views: dict[str, SearchAnswerProfileConfig]
) -> _ViewAndSearchOracle:
    """検索 API から本物の RagPipeline を動かし、Oracle と embedding だけスタブにする。"""
    oracle = _ViewAndSearchOracle(views)
    monkeypatch.setattr(search_route, "OracleClient", lambda *_args, **_kwargs: oracle)
    monkeypatch.setattr(pipeline_module, "OracleClient", lambda *_args, **_kwargs: oracle)
    monkeypatch.setattr(pipeline_module, "OciGenAiClient", lambda *_args, **_kwargs: FakeGenAi())
    return oracle


def test_settings_ignore_removed_answer_engine_env(monkeypatch: MonkeyPatch) -> None:
    """旧 ``RAG_ANSWER_ENGINE`` は読まない(旧名との互換なし。extra=ignore で起動は続く)。"""
    monkeypatch.setenv("RAG_ANSWER_ENGINE", "standard")

    settings = Settings()

    assert not hasattr(settings, "rag_answer_engine")


def test_search_api_with_search_answer_profile_answers_with_answer_engine(
    monkeypatch: MonkeyPatch,
) -> None:
    """検索・回答プロファイルを指定した検索も回答フローで回答する。"""
    _install_real_pipeline(
        monkeypatch, {"bv-1": SearchAnswerProfileConfig(knowledge_base_ids=["kb-1"])}
    )

    response = client.post(
        "/api/search", json={"query": "受注の登録方法は？", "search_answer_profile_id": "bv-1"}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert "登録ボタン" in data["answer"]
    assert data["diagnostics"]["retrieval_strategy"] == "hybrid"
    assert data["diagnostics"]["answer"]["evidence_tree"]
    assert data["diagnostics"]["search_answer_profile_applied"] == "bv-1"


def test_search_api_without_search_answer_profile_answers_with_answer_engine(
    monkeypatch: MonkeyPatch,
) -> None:
    """検索・回答プロファイルなしの呼び出し(KB だけ)も回答フローで回答する。"""
    oracle = _install_real_pipeline(monkeypatch, {})

    response = client.post(
        "/api/search", json={"query": "受注の登録方法は？", "knowledge_base_ids": ["kb-1"]}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["diagnostics"]["retrieval_strategy"] == "hybrid"
    assert data["citations"][0]["chunk_id"] == "doc-1:c1"
    assert oracle.filters and oracle.filters[0]["knowledge_base_id"] == "kb-1"


def test_mcp_rag_search_answers_with_answer_engine(monkeypatch: MonkeyPatch) -> None:
    """MCP の rag_search(Agent からの呼び出し)も回答フローで回答する(local mode)。"""
    _install_real_pipeline(monkeypatch, {})
    assert get_settings().auth_mode == "local"

    response = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "rag_search",
                "arguments": {"query": "受注の登録方法は？", "knowledge_base_ids": ["kb-1"]},
            },
        },
    )

    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()["result"]
    assert result["isError"] is False
    assert "登録ボタン" in result["structuredContent"]["answer"]
    assert result["structuredContent"]["evidence"][0]["chunk_id"] == "doc-1:c1"


async def test_evaluation_runs_cases_with_answer_engine() -> None:
    """評価のケースも回答フローで回答する(回答のキーワードと根拠の文書で採点する)。"""
    pipeline = RagPipeline(
        settings=Settings(),
        oracle=FakeOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    async def judge(**kwargs: Any) -> dict[str, object]:
        assert "登録ボタン" in str(kwargs["evaluation_input"])
        return {"status": "completed", "response_decision_accuracy": 1.0}

    runner = EvaluationRunner(pipeline=pipeline, settings=Settings(), answer_judge=judge)

    metrics = await runner.run(
        [
            EvaluationCase(
                id="case-1",
                query="受注の登録方法は？",
                relevant_document_ids=["doc-1"],
                required_answer_points=["登録ボタン"],
                expected_response_decision="answer",
            )
        ],
        top_k=5,
    )

    assert metrics.case_count == 1
    assert metrics.error_count == 0
    assert metrics.response_decision_accuracy == 1.0
    assert metrics.mrr == 1.0
    # 回答の記録(引用・根拠・実行記録)から測る指標も求まる(#591)。
    assert metrics.required_evidence_recall is None
    assert metrics.answer_correctness is None
