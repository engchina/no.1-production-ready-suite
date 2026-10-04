"""チャットの処理の段階（`ChatProgressStep`。#1146）の組み立てのテスト。"""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.rag.chat_progress import (
    CHAT_PROGRESS_STEPS,
    ChatProgressTracker,
    chat_progress_step_id,
)
from app.rag.pipeline import SearchStageProgress


def _progress(stage: str, outcome: str) -> SearchStageProgress:
    return SearchStageProgress(
        trace_id="trace-1", stage=stage, outcome=outcome, elapsed_ms=0.0, attributes={}
    )


class _Clock:
    """1 回呼ぶごとに 1 秒進む時計（startedAt / finishedAt と所要時間を決定的にする）。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        self.seconds = 0.0

    def clock(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now

    def timer(self) -> float:
        self.seconds += 1.0
        return self.seconds


def _tracker(*, rerank_enabled: bool = True) -> ChatProgressTracker:
    clock = _Clock()
    return ChatProgressTracker(
        rerank_enabled=rerank_enabled, model_id="m1", clock=clock.clock, timer=clock.timer
    )


def _statuses(tracker: ChatProgressTracker) -> dict[str, str]:
    return {step["id"]: step["status"] for step in tracker.snapshot()}


def _feed(tracker: ChatProgressTracker, events: list[tuple[str, str]]) -> list[bool]:
    return [tracker.observe(_progress(stage, outcome)) for stage, outcome in events]


def test_steps_follow_the_contract_and_start_pending() -> None:
    """段階は契約の 5 つ（id / label / status）で、最初はすべて未開始。"""
    snapshot = _tracker().snapshot()
    assert [step["id"] for step in snapshot] == [
        "rewrite_query",
        "retrieve",
        "rerank",
        "generate_answer",
        "check_guardrail",
    ]
    assert [step["label"] for step in snapshot] == [label for _, label in CHAT_PROGRESS_STEPS]
    assert all(step["status"] == "pending" for step in snapshot)
    assert all(set(step) == {"id", "label", "status"} for step in snapshot)


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("history_rewrite", "rewrite_query"),
        ("field_filter", "rewrite_query"),
        ("answer_step:質問の理解", "rewrite_query"),
        ("answer_step:質問拡張戦略", "rewrite_query"),
        ("answer_step:検索文と検索語の確定", "rewrite_query"),
        ("answer_step:文書検索", "retrieve"),
        ("answer_step:文書検索（2回目）", "retrieve"),
        ("answer_step:根拠確認（1回目）", "retrieve"),
        ("answer_step:根拠確認の結論", "retrieve"),
        ("answer_step:文書の選択の解除", "retrieve"),
        ("answer_step:検索語の再確定（書き換え後）", "retrieve"),
        ("answer_step:Rerank", "rerank"),
        ("answer_step:回答に使う画像の確認", "generate_answer"),
        ("answer_step:回答文の生成と根拠確認（1回目）", "generate_answer"),
        ("answer_step:是正", "generate_answer"),
        ("answer_guardrail", "check_guardrail"),
        # 全体を包む工程・未知の工程は段階にしない。
        ("answer", None),
        ("answer_step:回答生成フロー", None),
        ("answer_step:未知の工程", None),
        ("unknown", None),
    ],
)
def test_stage_mapping(stage: str, expected: str | None) -> None:
    assert chat_progress_step_id(stage) == expected


def test_rerank_disabled_is_skipped_and_rerank_step_counts_as_retrieval() -> None:
    """rerank が無効なら並べ替えは skipped。回答フローの「Rerank」の工程は検索の一部にする。"""
    assert chat_progress_step_id("answer_step:Rerank", rerank_enabled=False) == "retrieve"
    tracker = _tracker(rerank_enabled=False)
    assert _statuses(tracker)["rerank"] == "skipped"
    _feed(tracker, [("answer_step:文書検索", "started"), ("answer_step:Rerank", "started")])
    assert _statuses(tracker)["retrieve"] == "running"
    assert _statuses(tracker)["rerank"] == "skipped"


def test_full_answer_flow_moves_one_running_step_at_a_time() -> None:
    """回答フローの工程の順に、実行中の段階は 1 つだけで進み、完了で残りは skipped。"""
    tracker = _tracker()
    changed = _feed(
        tracker,
        [
            ("answer", "started"),
            ("answer_step:質問の理解", "started"),
            ("answer_step:質問の理解", "success"),
            ("answer_step:質問拡張戦略", "started"),
            ("answer_step:質問拡張戦略", "success"),
        ],
    )
    # 全体を包む工程・工程の終わり・同じ段階の 2 つ目の工程では変わらない
    # （完了と実行中を行き来しない）。
    assert changed == [False, True, False, False, False]
    assert _statuses(tracker)["rewrite_query"] == "running"

    _feed(tracker, [("answer_step:文書検索", "started")])
    assert _statuses(tracker)["rewrite_query"] == "done"
    assert _statuses(tracker)["retrieve"] == "running"
    _feed(tracker, [("answer_step:Rerank", "started")])
    statuses = _statuses(tracker)
    # 文書検索の中の Rerank が始まったら、検索は終わったものとして並べ替えを実行中にする。
    assert statuses["retrieve"] == "done"
    assert statuses["rerank"] == "running"
    assert [status for status in statuses.values()].count("running") == 1

    _feed(
        tracker,
        [
            ("answer_step:Rerank", "success"),
            ("answer_step:文書検索", "success"),
            ("answer_step:回答文の生成と根拠確認（1回目）", "started"),
            ("answer_step:回答文の生成と根拠確認（1回目）", "success"),
            ("answer", "success"),
            ("answer_guardrail", "started"),
        ],
    )
    assert _statuses(tracker) == {
        "rewrite_query": "done",
        "retrieve": "done",
        "rerank": "done",
        "generate_answer": "done",
        "check_guardrail": "running",
    }
    tracker.finish(citation_count=3)
    snapshot = {step["id"]: step for step in tracker.snapshot()}
    assert snapshot["check_guardrail"]["status"] == "done"
    assert snapshot["retrieve"]["detail"] == "根拠 3 件"
    # 時刻は ISO 8601（UTC）。開始は終了より前。
    assert snapshot["rewrite_query"]["startedAt"].endswith("Z")
    assert snapshot["rewrite_query"]["startedAt"] < snapshot["rewrite_query"]["finishedAt"]


def test_finish_marks_steps_that_never_ran_as_skipped() -> None:
    """会話の書き換えも回答フローも通らない（安全チェックで止めた）回答は、すべて skipped。"""
    tracker = _tracker()
    tracker.finish(citation_count=0)
    assert set(_statuses(tracker).values()) == {"skipped"}
    assert all("detail" not in step for step in tracker.snapshot())


def test_corrective_retrieval_returns_to_retrieve_with_attempt_detail() -> None:
    """補正検索（CRAG の 2 回目の文書検索）は検索の段階を再び実行中にし、回数を補足に出す。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("answer_step:文書検索（1回目）", "started"),
            ("answer_step:Rerank", "started"),
            ("answer_step:Rerank", "success"),
            ("answer_step:文書検索（1回目）", "success"),
            ("answer_step:根拠確認（1回目）", "started"),
        ],
    )
    assert _statuses(tracker)["retrieve"] == "running"
    assert _statuses(tracker)["rerank"] == "done"
    _feed(
        tracker,
        [
            ("answer_step:根拠確認（1回目）", "success"),
            ("answer_step:文書検索（2回目）", "started"),
        ],
    )
    retrieve = next(step for step in tracker.snapshot() if step["id"] == "retrieve")
    assert retrieve["status"] == "running"
    assert retrieve["detail"] == "2 回目"


def test_inner_step_error_does_not_fail_the_step() -> None:
    """回答フローの中の工程の失敗（続けられる失敗）では段階を失敗にしない。"""
    tracker = _tracker()
    _feed(tracker, [("answer_step:是正", "started"), ("answer_step:是正", "error")])
    assert _statuses(tracker)["generate_answer"] == "running"
    tracker.finish(citation_count=1)
    assert _statuses(tracker)["generate_answer"] == "done"


def test_fail_marks_running_step_failed_even_after_cancel_event() -> None:
    """時間切れで工程が中断された（cancelled）ときは、その段階を failed にする。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("answer_step:文書検索", "started"),
            ("answer_step:文書検索", "cancelled"),
        ],
    )
    assert _statuses(tracker)["retrieve"] == "running"
    tracker.fail()
    statuses = _statuses(tracker)
    assert statuses["retrieve"] == "failed"
    # 始まらなかった段階は未開始のまま（実行していない）。
    assert statuses["rerank"] == "pending"
    retrieve = next(step for step in tracker.snapshot() if step["id"] == "retrieve")
    assert "finishedAt" in retrieve


def test_fail_keeps_done_steps_and_fails_first_step_when_nothing_started() -> None:
    """失敗では終わった段階を残し、実行中の段階だけを failed にする。何も始まっていなければ先頭。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("history_rewrite", "started"),
            ("history_rewrite", "success"),
            ("answer_step:文書検索", "started"),
        ],
    )
    tracker.fail()
    assert _statuses(tracker)["rewrite_query"] == "done"
    assert _statuses(tracker)["retrieve"] == "failed"

    untouched = _tracker()
    untouched.fail()
    assert _statuses(untouched)["rewrite_query"] == "failed"
    assert "startedAt" not in untouched.snapshot()[0]


def test_logs_step_start_and_end_with_duration_and_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """段階の開始・終了を INFO で残す（request_id・trace_id・所要時間）。"""
    from pr_backend_core.observability import request_id_var

    token = request_id_var.set("req-1146")
    try:
        with caplog.at_level(logging.INFO, logger="app.rag.chat_progress"):
            tracker = _tracker()
            _feed(
                tracker,
                [("answer_step:文書検索", "started"), ("answer_step:文書検索", "success")],
            )
            tracker.finish(citation_count=2)
    finally:
        request_id_var.reset(token)
    records = [record for record in caplog.records if record.name == "app.rag.chat_progress"]
    assert [record.levelno for record in records] == [logging.INFO, logging.INFO]
    started, finished = records
    assert started.__dict__["step_id"] == "retrieve"
    assert started.__dict__["status"] == "running"
    assert started.__dict__["request_id"] == "req-1146"
    assert started.__dict__["trace_id"] == "trace-1"
    assert finished.__dict__["status"] == "done"
    assert finished.__dict__["duration_ms"] == 1000.0
