"""チャットの処理の段階（3 製品共通の段階のイベント。#1146 / #1359 / #2096）の記録のテスト。

内部の工程 → 段階（1 工程 = 1 段階・入れ子の深さ）、回答の記録の値の追加、
完了・失敗・停止・中断の終端を、`ChatProgressTracker` の記録したイベントで確かめる。
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from pr_backend_core.chat_progress import (
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
    parse_chat_progress_events,
)

from app.rag.chat_answer_runs import close_saved_chat_progress
from app.rag.chat_progress import ANSWER_STEP_KIND, ChatProgressTracker, chat_progress_step_kind
from app.rag.pipeline import SearchStageProgress

MESSAGE_ID = "a" * 32


def _progress(stage: str, outcome: str) -> SearchStageProgress:
    return SearchStageProgress(
        trace_id="trace-1", stage=stage, outcome=outcome, elapsed_ms=0.0, attributes={}
    )


class _Clock:
    """1 回呼ぶごとに 1 秒進む時計（started_at / finished_at と所要時間を決定的にする）。"""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
        self.seconds = 0.0

    def clock(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now

    def timer(self) -> float:
        self.seconds += 1.0
        return self.seconds


def _tracker() -> ChatProgressTracker:
    clock = _Clock()
    return ChatProgressTracker(MESSAGE_ID, model_id="m1", clock=clock.clock, timer=clock.timer)


def _statuses(tracker: ChatProgressTracker) -> dict[str, str]:
    fold = fold_chat_progress_events(tracker.events)
    return {step.step_id: step.status for step in fold.step_list()}


def _rows(tracker: ChatProgressTracker) -> list[tuple[str, str, str, object, str]]:
    """段階の一覧（id・種類・工程名・深さ・状態）。"""
    fold = fold_chat_progress_events(tracker.events)
    return [
        (
            step.step_id,
            step.kind,
            str((step.params or {}).get("name", "")),
            (step.params or {}).get("depth"),
            step.status,
        )
        for step in fold.step_list()
    ]


def _step(tracker: ChatProgressTracker, step_id: str) -> ChatProgressStepEvent:
    return [
        event
        for event in tracker.events
        if isinstance(event, ChatProgressStepEvent) and event.step_id == step_id
    ][-1]


def _feed(tracker: ChatProgressTracker, events: list[tuple[str, str]]) -> list[bool]:
    return [tracker.observe(_progress(stage, outcome)) for stage, outcome in events]


def test_records_no_steps_until_a_stage_starts() -> None:
    """段階は工程が始まってから記録する（前もって待機中の段階を並べない）。"""
    tracker = _tracker()
    assert tracker.events == []
    assert tracker.dump() == []


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("history_rewrite", "history_rewrite"),
        ("field_filter", "field_filter"),
        ("answer_guardrail", "answer_guardrail"),
        ("answer_step:質問の理解", ANSWER_STEP_KIND),
        ("answer_step:回答生成フロー", ANSWER_STEP_KIND),
        ("answer_step:文書検索（2回目）", ANSWER_STEP_KIND),
        # 全体を包む工程・未知の工程・名前の無い工程は段階にしない。
        ("answer", None),
        ("retrieval", None),
        ("answer_step:", None),
        ("unknown", None),
    ],
    ids=[
        "history_rewrite",
        "field_filter",
        "answer_guardrail",
        "answer_step",
        "answer_step_flow",
        "answer_step_attempt",
        "answer",
        "retrieval",
        "answer_step_empty",
        "unknown",
    ],
)
def test_stage_kind(stage: str, expected: str | None) -> None:
    assert chat_progress_step_kind(stage) == expected


def test_full_answer_flow_records_each_step_with_depth() -> None:
    """回答フローの工程を 1 つずつ段階にし、入れ子の深さを値に入れる。名前（文言）は入れない。"""
    tracker = _tracker()
    changed = _feed(
        tracker,
        [
            ("history_rewrite", "started"),
            ("history_rewrite", "success"),
            ("answer", "started"),
            ("answer_step:質問の理解", "started"),
            ("answer_step:質問の理解", "success"),
            ("answer_step:回答生成フロー", "started"),
            ("answer_step:文書検索", "started"),
            ("answer_step:Rerank", "started"),
            ("answer_step:Rerank", "success"),
            ("answer_step:文書検索", "success"),
            ("answer_step:回答文の生成と根拠確認（1回目）", "started"),
        ],
    )
    # 全体を包む工程（answer）は記録しない。
    assert changed[2] is False
    assert _rows(tracker) == [
        ("s1", "history_rewrite", "", None, "done"),
        ("s2", ANSWER_STEP_KIND, "質問の理解", 0, "done"),
        ("s3", ANSWER_STEP_KIND, "回答生成フロー", 0, "running"),
        ("s4", ANSWER_STEP_KIND, "文書検索", 1, "done"),
        ("s5", ANSWER_STEP_KIND, "Rerank", 2, "done"),
        ("s6", ANSWER_STEP_KIND, "回答文の生成と根拠確認（1回目）", 1, "running"),
    ]
    _feed(
        tracker,
        [
            ("answer_step:回答文の生成と根拠確認（1回目）", "success"),
            ("answer_step:回答生成フロー", "success"),
            ("answer", "success"),
            ("answer_guardrail", "started"),
        ],
    )
    tracker.finish()
    assert tracker.terminal == "done"
    assert isinstance(tracker.events[-1], ChatProgressTerminalEvent)
    assert set(_statuses(tracker).values()) == {"done"}
    dumped = tracker.dump()
    assert all("label" not in event and "detail" not in event for event in dumped)
    # 保存の JSON から同じイベントに戻せる。
    assert parse_chat_progress_events(dumped) == tracker.events
    # 段階の時刻は工程の開始と終了。
    understand = _step(tracker, "s2")
    assert understand.started_at is not None and understand.finished_at is not None


def test_same_step_name_twice_becomes_two_steps() -> None:
    """同じ工程名の 2 回目（補正の検索など）は別の段階にし、完了した段階を実行中に戻さない。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("answer_step:文書の選択", "started"),
            ("answer_step:文書の選択", "success"),
            ("answer_step:文書の選択", "started"),
        ],
    )
    assert _statuses(tracker) == {"s1": "done", "s2": "running"}


def test_finish_adds_llm_calls_and_note_from_execution_steps() -> None:
    """回答の記録の工程を名前の順に対応させ、LLM の回数と結果の説明（complete 以外）を値に足す。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("answer_step:質問の理解", "started"),
            ("answer_step:質問の理解", "success"),
            ("answer_step:用語・ルールの確認", "started"),
            ("answer_step:用語・ルールの確認", "success"),
            ("answer_step:文書検索", "started"),
            ("answer_step:文書検索", "success"),
            ("answer_step:文書検索", "started"),
            ("answer_step:文書検索", "success"),
        ],
    )
    tracker.finish(
        execution_steps=[
            {"name": "質問の理解", "status": "complete", "llm_calls": 1, "depth": 0},
            {"name": "用語・ルールの確認", "status": "該当なし", "llm_calls": 0, "depth": 0},
            {"name": "文書検索", "status": "complete", "llm_calls": 0, "depth": 0},
            {"name": "文書検索", "status": "complete", "llm_calls": 2, "depth": 0},
            "壊れた記録",
        ]
    )
    assert _step(tracker, "s1").params == {"name": "質問の理解", "depth": 0, "llm_calls": 1}
    assert _step(tracker, "s2").params == {
        "name": "用語・ルールの確認",
        "depth": 0,
        "note": "該当なし",
    }
    assert _step(tracker, "s3").params == {"name": "文書検索", "depth": 0}
    assert _step(tracker, "s4").params == {"name": "文書検索", "depth": 0, "llm_calls": 2}
    assert tracker.terminal == "done"


def test_inner_step_error_marks_only_that_step_failed() -> None:
    """工程の失敗はその段階だけを失敗にする（回答フローは続けることがある）。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("answer_step:回答生成フロー", "started"),
            ("answer_step:是正", "started"),
            ("answer_step:是正", "error"),
        ],
    )
    assert _statuses(tracker) == {"s1": "running", "s2": "failed"}
    tracker.finish()
    assert _statuses(tracker) == {"s1": "done", "s2": "failed"}


def test_fail_marks_running_steps_failed_and_ends_with_failed_terminal() -> None:
    """時間切れで工程が中断された（cancelled）ときは、終端でその段階を失敗にする。"""
    tracker = _tracker()
    _feed(
        tracker,
        [
            ("history_rewrite", "started"),
            ("history_rewrite", "success"),
            ("answer_step:文書検索", "started"),
            ("answer_step:文書検索", "cancelled"),
        ],
    )
    assert _statuses(tracker) == {"s1": "done", "s2": "running"}
    tracker.fail()
    assert _statuses(tracker) == {"s1": "done", "s2": "failed"}
    assert _step(tracker, "s2").finished_at is not None
    assert tracker.terminal == "failed"

    untouched = _tracker()
    untouched.fail()
    assert _statuses(untouched) == {}
    assert untouched.terminal == "failed"


@pytest.mark.parametrize(
    ("status", "running_status"),
    [("cancelled", "skipped"), ("failed", "failed")],
    ids=["stop", "interrupt"],
)
def test_close_records_stop_and_interrupt_terminal(status: str, running_status: str) -> None:
    """停止（cancelled）・中断（failed）の終端。終端の後は何も記録しない。"""
    tracker = _tracker()
    _feed(tracker, [("answer_step:文書検索", "started")])
    tracker.close(status)  # type: ignore[arg-type]
    assert tracker.terminal == status
    assert _statuses(tracker) == {"s1": running_status}
    last_seq = tracker.last_seq
    # 終端の後の工程・完了・もう一度の停止は記録しない。
    _feed(tracker, [("answer_step:回答文の生成", "started")])
    tracker.finish()
    tracker.close("failed")
    assert tracker.last_seq == last_seq


def test_attached_sink_receives_events_after_attach() -> None:
    """記録の送り先は付けた後の記録から受け取る。"""
    tracker = _tracker()
    _feed(tracker, [("answer_step:質問の理解", "started")])
    received: list[int] = []
    tracker.attach(lambda event: received.append(event.seq))
    _feed(tracker, [("answer_step:質問の理解", "success")])
    tracker.finish()
    assert received == list(range(2, tracker.last_seq + 1))


def test_resumes_from_saved_events() -> None:
    """保存済みのイベントから作り直すと、続きの番号と続きの段階の id で記録する。"""
    first = _tracker()
    _feed(first, [("answer_step:文書検索", "started"), ("answer_step:文書検索", "success")])
    resumed = ChatProgressTracker(MESSAGE_ID, events=first.events)
    assert resumed.last_seq == first.last_seq
    resumed.observe(_progress("answer_guardrail", "started"))
    assert list(_statuses(resumed)) == ["s1", "s2"]
    resumed.close("failed")
    assert [event.seq for event in resumed.events] == list(range(1, resumed.last_seq + 1))


def test_close_saved_progress_continues_numbering_and_drops_snapshot() -> None:
    """別のプロセスの停止・中断の後始末は、保存済みのイベントの続きで終端を記録する。"""
    tracker = _tracker()
    _feed(tracker, [("answer_step:文書検索", "started")])
    saved = tracker.dump()
    closed = close_saved_chat_progress(saved, message_id=MESSAGE_ID, status="cancelled")
    assert closed[: len(saved)] == saved
    assert closed[-1]["type"] == "terminal"
    assert closed[-1]["status"] == "cancelled"
    assert [event["seq"] for event in closed] == list(range(1, len(closed) + 1))
    # 既に終端なら変えない。
    assert close_saved_chat_progress(closed, message_id=MESSAGE_ID, status="failed") == closed
    # 旧形式（段階の snapshot）は捨てて終端だけにする。
    legacy = close_saved_chat_progress(
        [{"id": "retrieve", "label": "文書を探しています", "status": "running"}],
        message_id=MESSAGE_ID,
        status="failed",
    )
    assert [(event["type"], event["seq"]) for event in legacy] == [("terminal", 1)]


def test_logs_step_start_and_end_with_duration_and_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """段階の開始・終了を INFO で残す（request_id・trace_id・回答の id・所要時間）。"""
    from pr_backend_core.observability import request_id_var

    token = request_id_var.set("req-1146")
    try:
        with caplog.at_level(logging.INFO, logger="app.rag.chat_progress"):
            tracker = _tracker()
            _feed(
                tracker,
                [("answer_step:文書検索", "started"), ("answer_step:文書検索", "success")],
            )
            tracker.finish()
    finally:
        request_id_var.reset(token)
    records = [record for record in caplog.records if record.name == "app.rag.chat_progress"]
    assert [record.levelno for record in records] == [logging.INFO, logging.INFO]
    started, finished = records
    assert started.__dict__["step_id"] == "s1"
    assert started.__dict__["step_kind"] == ANSWER_STEP_KIND
    assert started.__dict__["step_name"] == "文書検索"
    assert started.__dict__["status"] == "running"
    assert started.__dict__["request_id"] == "req-1146"
    assert started.__dict__["trace_id"] == "trace-1"
    assert started.__dict__["message_id"] == MESSAGE_ID
    assert finished.__dict__["status"] == "done"
    assert finished.__dict__["duration_ms"] == 1000.0
