"""回答生成の時間の上限と時間切れの文言のテスト（#375）。"""

import asyncio

import pytest
from pydantic import ValidationError

from app.config import (
    OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS,
    RAG_ANSWER_TIMEOUT_CHOICES,
    RAG_ANSWER_TIMEOUT_MAX_SECONDS,
    RAG_ANSWER_TIMEOUT_MIN_SECONDS,
    Settings,
)
from app.rag.answer_timeout import (
    AnswerTimeoutError,
    StageTracker,
    answer_timeout_message,
    run_answer_with_timeout,
)
from app.rag.pipeline import SearchStageProgress
from app.rag.span_display import ANSWER_STAGE_LABELS


def _progress(stage: str, outcome: str) -> SearchStageProgress:
    return SearchStageProgress(
        trace_id="trace", stage=stage, outcome=outcome, elapsed_ms=0.0, attributes={}
    )


def test_answer_timeout_default_and_limit() -> None:
    """回答生成の通しの上限は既定 600 秒・100〜1200 秒（#5280）。LLM 1 回の上限とは分ける。

    検索だけの上限（旧 rag_search_timeout_seconds）は #383 で削除した（品質評価の 1 ケースも
    回答生成の上限で打ち切る）。
    """
    settings = Settings()
    assert settings.rag_answer_timeout_seconds == 600.0
    assert (RAG_ANSWER_TIMEOUT_MIN_SECONDS, RAG_ANSWER_TIMEOUT_MAX_SECONDS) == (100.0, 1200.0)
    # 画面の選択肢は範囲の両端と既定を含む。
    assert RAG_ANSWER_TIMEOUT_CHOICES == (100, 300, 600, 1200)
    # 通しの上限は複数回の LLM 呼び出しを含むので、1 回の上限（600 秒）とは別に持つ。
    assert OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS == 600.0
    assert settings.oci_enterprise_ai_timeout_seconds == OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS
    assert not hasattr(settings, "rag_search_timeout_seconds")
    for choice in RAG_ANSWER_TIMEOUT_CHOICES:
        assert Settings(rag_answer_timeout_seconds=choice).rag_answer_timeout_seconds == choice
    for invalid in (RAG_ANSWER_TIMEOUT_MAX_SECONDS + 1, RAG_ANSWER_TIMEOUT_MIN_SECONDS - 1, 0):
        with pytest.raises(ValidationError):
            Settings(rag_answer_timeout_seconds=invalid)
    with pytest.raises(ValidationError):
        Settings(oci_enterprise_ai_timeout_seconds=OCI_ENTERPRISE_AI_TIMEOUT_MAX_SECONDS + 1)


def test_answer_timeout_env_example_documents_setting() -> None:
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert "RAG_ANSWER_TIMEOUT_SECONDS=600" in env_example.read_text(encoding="utf-8")


@pytest.mark.parametrize("choice", RAG_ANSWER_TIMEOUT_CHOICES, ids=str)
def test_short_answer_limit_skips_reading_and_expansion(choice: int) -> None:
    """上限が生成の予約（1 回の見積もり + 確定の 30 秒）より短いと、読み足しをしない（#5280）。

    既定の見積もり 120 秒では予約は 150 秒。100 秒を選ぶと、回答の開始から参照先の読取
    （``_reference_deadline_short``）・章節の拡張・分割読取（rag_engine の同じ予約）をせず、
    入る根拠だけで 1 回生成する。300 秒以上なら開始の時点では読み足せる。
    """
    from types import SimpleNamespace

    from rag_engine.generation.section_expansion import SectionExpansionLimits
    from rag_engine.resources.deadline import Deadline

    from app.rag.answer_engine import (
        _reference_deadline_short,
        _reference_generation_reserve_seconds,
        _SearchState,
    )

    settings = Settings(rag_answer_timeout_seconds=choice)
    reserve = _reference_generation_reserve_seconds(settings)
    assert reserve == 150.0
    state = _SearchState()
    state.deadline = Deadline(float(choice))
    assert _reference_deadline_short(state, reserve) is (choice < reserve)
    # rag_engine の章節の拡張・分割読取も同じ予約（設定から渡す見積もり + 確定の分）を使う。
    limits = SectionExpansionLimits.from_settings(
        SimpleNamespace(
            answer_generation_round_estimate_seconds=(
                settings.rag_answer_generation_round_estimate_seconds
            )
        )
    )
    assert limits.generation_reserve_seconds == reserve


def test_answer_timeout_message_names_stage_and_retry() -> None:
    message = answer_timeout_message("history_rewrite", 300)
    assert "5 分" in message
    assert "質問を補い、検索が要るかを分ける" in message
    assert "もう一度送信してください" in message
    assert "90 秒" in answer_timeout_message("answer", 90)
    assert "質問の条件を整理して回答を作る" in answer_timeout_message("answer", 90)
    # 開始前・未知の工程・工程の中の時間切れ（上限の秒数は出さない）。
    assert "検索の準備" in answer_timeout_message(None, 30)
    assert "（時間切れになった工程: 処理）" in answer_timeout_message("unknown_stage", 30)
    assert "時間内に終わりませんでした" in answer_timeout_message("answer", None)
    # 回答フローの各工程（#593）は利用者向けの名前にする（#2096。画面の処理の経過と同じ名前）。
    assert (
        "（時間切れになった工程: 検索文から根拠の候補を集める（1 回目））"
        in answer_timeout_message("answer_step:文書検索（1回目）", 30)
    )
    # 知らない工程名はそのまま出す。
    assert "（時間切れになった工程: 新しい工程（2回目））" in answer_timeout_message(
        "answer_step:新しい工程（2回目）", 30
    )
    assert "（時間切れになった工程: 処理）" in answer_timeout_message("answer_step:", 30)


def test_stage_labels_cover_answer_stages() -> None:
    """進捗の工程は回答の工程・回答側の安全チェック(#1146)・保存(#2342)・会話の添付からの根拠の
    選択(#3475)・検索の要らない発話への応答(#4787)・選んだ承認済み FAQ の回答(#5107)と検索だけの
    工程。

    旧 standard の工程は #595 で削除した。
    """
    assert set(ANSWER_STAGE_LABELS) == {
        "retrieval",
        "history_rewrite",
        "small_talk_reply",
        "approved_faq_answer",
        "attachment_evidence",
        "answer",
        # 回答を作らない RAG 検索の全体（#3506）。
        "evidence",
        "query_guardrail",
        "answer_guardrail",
        "answer_record_save",
        "chat_message_save",
    }


async def test_stage_tracker_follows_nested_stages() -> None:
    forwarded: list[str] = []

    async def inner(progress: SearchStageProgress) -> None:
        forwarded.append(f"{progress.stage}:{progress.outcome}")

    tracker = StageTracker(inner)
    assert tracker.current_stage is None
    await tracker(_progress("answer", "started"))
    await tracker(_progress("answer_step:文書検索（1回目）", "started"))
    assert tracker.current_stage == "answer_step:文書検索（1回目）"
    await tracker(_progress("answer_step:文書検索（1回目）", "success"))
    # 入れ子の内側が終わったら外側の工程に戻る。
    assert tracker.current_stage == "answer"
    await tracker(_progress("answer", "success"))
    # 実行中の工程がなければ、最後に通知された工程。
    assert tracker.current_stage == "answer"
    assert forwarded[0] == "answer:started"
    assert len(forwarded) == 4


async def test_run_answer_with_timeout_reports_last_stage() -> None:
    settings = Settings().model_copy(update={"rag_answer_timeout_seconds": 0.05})

    async def slow(tracker: StageTracker) -> str:
        await tracker(_progress("history_rewrite", "started"))
        await asyncio.sleep(1)
        return "done"

    with pytest.raises(AnswerTimeoutError) as caught:
        await run_answer_with_timeout(slow, settings)

    assert caught.value.stage == "history_rewrite"
    assert caught.value.timeout_seconds == 0.05
    assert "質問を補い、検索が要るかを分ける" in caught.value.user_message
    assert isinstance(caught.value, TimeoutError)
    assert isinstance(caught.value.original_error, TimeoutError)


async def test_run_answer_with_timeout_wraps_inner_timeout_without_limit() -> None:
    """工程の中の TimeoutError（LLM 1 回の timeout など）も工程付きの時間切れにする。"""
    settings = Settings().model_copy(update={"rag_answer_timeout_seconds": 60})

    async def inner_timeout(tracker: StageTracker) -> str:
        await tracker(_progress("answer", "started"))
        raise TimeoutError

    with pytest.raises(AnswerTimeoutError) as caught:
        await run_answer_with_timeout(inner_timeout, settings)

    assert caught.value.stage == "answer"
    assert caught.value.timeout_seconds is None
    assert "時間内に終わりませんでした" in caught.value.user_message


async def test_run_answer_with_timeout_returns_result_within_limit() -> None:
    settings = Settings().model_copy(update={"rag_answer_timeout_seconds": 5})

    async def quick(tracker: StageTracker) -> str:
        await asyncio.sleep(0.05)
        return "ok"

    assert await run_answer_with_timeout(quick, settings) == "ok"


async def test_search_reports_started_stages_as_mcp_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rag_search の工程の始まりを MCP の進み具合の段階の名前にする（#1864）。"""
    from app.api.routes import search as search_route

    reported: list[str] = []
    monkeypatch.setattr(search_route, "report_tool_progress", reported.append)
    await search_route._report_tool_progress(_progress("answer", "started"))
    await search_route._report_tool_progress(_progress("answer_step:章節の拡張", "started"))
    await search_route._report_tool_progress(_progress("answer_step:章節の拡張", "success"))
    await search_route._report_tool_progress(_progress("answer_guardrail", "started"))
    # 終わった工程は知らせない。名前は時間切れの文言と同じ（answer_stage_label）。
    assert reported == [
        "質問の条件を整理して回答を作る",
        "関係する章節の原文を根拠に加える",
        "回答に安全上の問題がないか検査する",
    ]
