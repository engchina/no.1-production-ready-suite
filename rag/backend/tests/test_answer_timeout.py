"""回答生成の時間の上限と時間切れの文言のテスト（#375）。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.config import OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS, Settings
from app.rag.answer_timeout import (
    ANSWER_STAGE_LABELS,
    AnswerTimeoutError,
    StageTracker,
    answer_timeout_message,
    run_answer_with_timeout,
)
from app.rag.pipeline import SearchStageProgress


def _progress(stage: str, outcome: str) -> SearchStageProgress:
    return SearchStageProgress(
        trace_id="trace", stage=stage, outcome=outcome, elapsed_ms=0.0, attributes={}
    )


def test_answer_timeout_default_and_limit() -> None:
    """回答生成は既定 300 秒・上限は LLM 1 回の timeout の上限。

    検索だけの上限（旧 rag_search_timeout_seconds）は #383 で削除した（品質評価の 1 ケースも
    回答生成の上限で打ち切る）。
    """
    settings = Settings()
    assert settings.rag_answer_timeout_seconds == 300.0
    assert not hasattr(settings, "rag_search_timeout_seconds")
    assert Settings(rag_answer_timeout_seconds=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS)
    with pytest.raises(ValidationError):
        Settings(rag_answer_timeout_seconds=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS + 1)
    with pytest.raises(ValidationError):
        Settings(rag_answer_timeout_seconds=0)


def test_answer_timeout_env_example_documents_setting() -> None:
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert "RAG_ANSWER_TIMEOUT_SECONDS=300" in env_example.read_text(encoding="utf-8")


def test_answer_timeout_message_names_stage_and_retry() -> None:
    message = answer_timeout_message("agentic_multi_hop", 300)
    assert "5 分" in message
    assert "追加の検索の計画" in message
    assert "もう一度送信してください" in message
    assert "90 秒" in answer_timeout_message("generation", 90)
    assert "回答の生成" in answer_timeout_message("generation", 90)
    # 開始前・未知の工程・工程の中の時間切れ（上限の秒数は出さない）。
    assert "検索の準備" in answer_timeout_message(None, 30)
    assert "（時間切れになった工程: 処理）" in answer_timeout_message("unknown_stage", 30)
    assert "時間内に終わりませんでした" in answer_timeout_message("generation", None)


def test_stage_labels_cover_llm_stages() -> None:
    for stage in (
        "agentic_planning",
        "agentic_multi_hop",
        "agentic_multi_hop_retrieval",
        "crag_rewrite",
        "crag_retrieval",
        "corrective_retrieval",
        "query_expansion",
        "docrag_history_rewrite",
        "docrag_answer",
        "generation",
    ):
        assert stage in ANSWER_STAGE_LABELS


async def test_stage_tracker_follows_nested_stages() -> None:
    forwarded: list[str] = []

    async def inner(progress: SearchStageProgress) -> None:
        forwarded.append(f"{progress.stage}:{progress.outcome}")

    tracker = StageTracker(inner)
    assert tracker.current_stage is None
    await tracker(_progress("crag_retrieval", "started"))
    await tracker(_progress("context_expansion", "started"))
    assert tracker.current_stage == "context_expansion"
    await tracker(_progress("context_expansion", "success"))
    # 入れ子の内側が終わったら外側の工程に戻る。
    assert tracker.current_stage == "crag_retrieval"
    await tracker(_progress("crag_retrieval", "success"))
    # 実行中の工程がなければ、最後に通知された工程。
    assert tracker.current_stage == "crag_retrieval"
    assert forwarded[0] == "crag_retrieval:started"
    assert len(forwarded) == 4


async def test_run_answer_with_timeout_reports_last_stage() -> None:
    settings = Settings(rag_answer_timeout_seconds=0.05)

    async def slow(tracker: StageTracker) -> str:
        await tracker(_progress("agentic_planning", "started"))
        await asyncio.sleep(1)
        return "done"

    with pytest.raises(AnswerTimeoutError) as caught:
        await run_answer_with_timeout(slow, settings)

    assert caught.value.stage == "agentic_planning"
    assert caught.value.timeout_seconds == 0.05
    assert "検索の計画" in caught.value.user_message
    assert isinstance(caught.value, TimeoutError)
    assert isinstance(caught.value.original_error, TimeoutError)


async def test_run_answer_with_timeout_wraps_inner_timeout_without_limit() -> None:
    """工程の中の TimeoutError（LLM 1 回の timeout など）も工程付きの時間切れにする。"""
    settings = Settings(rag_answer_timeout_seconds=60)

    async def inner_timeout(tracker: StageTracker) -> str:
        await tracker(_progress("generation", "started"))
        raise TimeoutError

    with pytest.raises(AnswerTimeoutError) as caught:
        await run_answer_with_timeout(inner_timeout, settings)

    assert caught.value.stage == "generation"
    assert caught.value.timeout_seconds is None
    assert "時間内に終わりませんでした" in caught.value.user_message


async def test_run_answer_with_timeout_returns_result_within_limit() -> None:
    settings = Settings(rag_answer_timeout_seconds=5)

    async def quick(tracker: StageTracker) -> str:
        await asyncio.sleep(0.05)
        return "ok"

    assert await run_answer_with_timeout(quick, settings) == "ok"
