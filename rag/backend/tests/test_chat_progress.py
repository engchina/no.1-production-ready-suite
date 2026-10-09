"""チャットの処理の段階（3 製品共通の段階のイベント。#1146 / #1359）の記録のテスト。

内部の工程 → 段階の対応、段階を戻さないこと（#1358）、実行中は 1 つ、rerank 無効、補正検索の回数、
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
from app.rag.chat_progress import (
    CHAT_PROGRESS_STEP_IDS,
    ChatProgressTracker,
    chat_progress_step_id,
)
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


def _tracker(*, rerank_enabled: bool = True) -> ChatProgressTracker:
    clock = _Clock()
    return ChatProgressTracker(
        MESSAGE_ID,
        rerank_enabled=rerank_enabled,
        model_id="m1",
        clock=clock.clock,
        timer=clock.timer,
    )


def _statuses(tracker: ChatProgressTracker) -> dict[str, str]:
    fold = fold_chat_progress_events(tracker.events)
    return {step.step_id: step.status for step in fold.step_list()}


def _step(tracker: ChatProgressTracker, step_id: str) -> ChatProgressStepEvent:
    return [
        event
        for event in tracker.events
        if isinstance(event, ChatProgressStepEvent) and event.step_id == step_id
    ][-1]


def _feed(tracker: ChatProgressTracker, events: list[tuple[str, str]]) -> list[bool]:
    return [tracker.observe(_progress(stage, outcome)) for stage, outcome in events]


def test_declares_five_pending_steps_in_order_without_labels() -> None:
    """最初に 5 段階を待機中として記録する（並びを決める）。名前（文言）は記録に入れない。"""
    tracker = _tracker()
    events = tracker.events
    assert [event.seq for event in events] == [1, 2, 3, 4, 5]
    assert all(isinstance(event, ChatProgressStepEvent) for event in events)
    assert [event.step_id for event in events if isinstance(event, ChatProgressStepEvent)] == list(
        CHAT_PROGRESS_STEP_IDS
    )
    assert CHAT_PROGRESS_STEP_IDS == (
        "rewrite_query",
        "retrieve",
        "rerank",
        "generate_answer",
        "check_guardrail",
    )
    assert {event.target_id for event in events} == {MESSAGE_ID}
    assert set(_statuses(tracker).values()) == {"pending"}
    dumped = tracker.dump()
    assert all("label" not in event and "detail" not in event for event in dumped)
    # 保存の JSON から同じイベントに戻せる。
    assert parse_chat_progress_events(dumped) == events


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
    """rerank が無効なら並べ替えはスキップ。回答フローの「Rerank」の工程は検索の一部にする。"""
    assert chat_progress_step_id("answer_step:Rerank", rerank_enabled=False) == "retrieve"
    tracker = _tracker(rerank_enabled=False)
    rerank = _step(tracker, "rerank")
    assert rerank.status == "skipped"
    # 始まらなかった段階には時刻を付けない（所要時間を出さない）。
    assert rerank.started_at is None and rerank.finished_at is None
    _feed(tracker, [("answer_step:文書検索", "started"), ("answer_step:Rerank", "started")])
    assert _statuses(tracker)["retrieve"] == "running"
    assert _statuses(tracker)["rerank"] == "skipped"


def test_full_answer_flow_moves_one_running_step_at_a_time() -> None:
    """回答フローの工程の順に、実行中の段階は 1 つだけで進み、完了の終端で残りはスキップ。"""
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
    # 全体を包む工程・工程の終わり・同じ段階の 2 つ目の工程では記録しない
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
    assert list(statuses.values()).count("running") == 1

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
    assert tracker.terminal == "done"
    assert isinstance(tracker.events[-1], ChatProgressTerminalEvent)
    assert _statuses(tracker)["check_guardrail"] == "done"
    # 根拠の件数は値で付ける（文言は画面の i18n）。
    retrieve = _step(tracker, "retrieve")
    assert retrieve.params == {"citations": 3}
    assert retrieve.detail is None
    rewrite = _step(tracker, "rewrite_query")
    assert rewrite.started_at is not None and rewrite.finished_at is not None
    assert rewrite.started_at < rewrite.finished_at
    # 番号は 1 から連続する。
    assert [event.seq for event in tracker.events] == list(range(1, len(tracker.events) + 1))


def test_finish_marks_steps_that_never_ran_as_skipped() -> None:
    """会話の書き換えも回答フローも通らない（安全チェックで止めた）回答は、すべてスキップ。"""
    tracker = _tracker()
    tracker.finish(citation_count=0)
    assert set(_statuses(tracker).values()) == {"skipped"}
    # 検索をしていないので根拠の件数は付けない。
    assert _step(tracker, "retrieve").params is None
    assert tracker.terminal == "done"


def test_steps_only_move_forward_when_the_flow_returns_to_retrieval() -> None:
    """#1358 の再現: Rerank の後の文書の選択・根拠確認で、検索の段階を実行中に戻さない。

    戻すと、完了の一覧が「質問の整理・検索」→「質問の整理・並べ替え」（検索が消える）→
    「質問の整理・検索・並べ替え」と揺れていた。
    """
    tracker = _tracker()
    finished: list[list[str]] = []
    for stage in (
        "answer_step:質問の理解",
        "answer_step:文書検索（1回目）",
        "answer_step:Rerank",
        "answer_step:文書の選択",
        "answer_step:根拠確認（1回目）",
        "answer_step:回答文の生成と根拠確認（1回目）",
    ):
        _feed(tracker, [(stage, "started")])
        finished.append([step for step, status in _statuses(tracker).items() if status == "done"])
    assert finished == [
        [],
        ["rewrite_query"],
        ["rewrite_query", "retrieve"],
        ["rewrite_query", "retrieve"],
        ["rewrite_query", "retrieve"],
        ["rewrite_query", "retrieve", "rerank"],
    ]
    # 戻りの間も実行中の段階は 1 つだけで、回答の作成へ進む。
    statuses = _statuses(tracker)
    assert statuses["generate_answer"] == "running"
    assert list(statuses.values()).count("running") == 1
    # 記録したイベントのどれも、終わった段階を実行中に戻さない。
    seen_done: set[str] = set()
    for event in tracker.events:
        if not isinstance(event, ChatProgressStepEvent):
            continue
        if event.status == "done":
            seen_done.add(event.step_id)
        assert not (event.step_id in seen_done and event.status in {"running", "pending"})


def test_corrective_retrieval_keeps_steps_forward_with_attempt_param() -> None:
    """補正検索（CRAG の 2 回目の文書検索）は段階を戻さず、回数を検索の値に付ける（#1358）。"""
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
    assert _statuses(tracker)["retrieve"] == "done"
    assert _statuses(tracker)["rerank"] == "running"
    changed = _feed(
        tracker,
        [
            ("answer_step:根拠確認（1回目）", "success"),
            ("answer_step:文書検索（2回目）", "started"),
        ],
    )
    # 値が変わったので記録する（段階の状態は変えない）。
    assert changed == [False, True]
    retrieve = _step(tracker, "retrieve")
    assert retrieve.status == "done"
    assert retrieve.params == {"attempt": 2}
    assert _statuses(tracker)["rerank"] == "running"
    # 完了すると、回数に根拠の件数が加わる。
    tracker.finish(citation_count=4)
    assert _step(tracker, "retrieve").params == {"attempt": 2, "citations": 4}


def test_corrective_retrieval_without_rerank_stays_on_retrieve() -> None:
    """並べ替えを使わない設定では、補正検索の間も検索の段階が実行中のまま回数を付ける。"""
    tracker = _tracker(rerank_enabled=False)
    _feed(
        tracker,
        [
            ("answer_step:文書検索（1回目）", "started"),
            ("answer_step:Rerank", "started"),
            ("answer_step:根拠確認（1回目）", "started"),
            ("answer_step:文書検索（2回目）", "started"),
        ],
    )
    retrieve = _step(tracker, "retrieve")
    assert retrieve.status == "running"
    assert retrieve.params == {"attempt": 2}


def test_inner_step_error_does_not_fail_the_step() -> None:
    """回答フローの中の工程の失敗（続けられる失敗）では段階を失敗にしない。"""
    tracker = _tracker()
    _feed(tracker, [("answer_step:是正", "started"), ("answer_step:是正", "error")])
    assert _statuses(tracker)["generate_answer"] == "running"
    tracker.finish(citation_count=1)
    assert _statuses(tracker)["generate_answer"] == "done"


def test_fail_marks_running_step_failed_and_ends_with_failed_terminal() -> None:
    """時間切れで工程が中断された（cancelled）ときは、その段階を失敗にして失敗の終端にする。"""
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
    # 始まらなかった段階はスキップ（実行していない）。
    assert statuses["rerank"] == "skipped"
    assert _step(tracker, "retrieve").finished_at is not None
    assert tracker.terminal == "failed"


def test_fail_keeps_done_steps_and_fails_next_step_when_nothing_running() -> None:
    """失敗では終わった段階を残す。何も実行中でなければ次の段階（何も始まっていなければ先頭）。"""
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
    assert _step(untouched, "rewrite_query").started_at is None
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
    assert _statuses(tracker)["retrieve"] == running_status
    assert _statuses(tracker)["generate_answer"] == "skipped"
    last_seq = tracker.last_seq
    # 終端の後の工程・完了・もう一度の停止は記録しない。
    _feed(tracker, [("answer_step:回答文の生成", "started")])
    tracker.finish(citation_count=1)
    tracker.close("failed")
    assert tracker.last_seq == last_seq


def test_attached_sink_receives_events_after_attach() -> None:
    """記録の送り先は付けた後の記録から受け取る（最初の待機中は呼び出し元が送る）。"""
    tracker = _tracker()
    received: list[int] = []
    tracker.attach(lambda event: received.append(event.seq))
    _feed(tracker, [("answer_step:質問の理解", "started")])
    tracker.finish(citation_count=0)
    assert received == list(range(6, tracker.last_seq + 1))


def test_resumes_from_saved_events() -> None:
    """保存済みのイベントから作り直すと、続きの番号で記録する（待機中を出し直さない）。"""
    first = _tracker()
    _feed(first, [("answer_step:文書検索", "started")])
    resumed = ChatProgressTracker(MESSAGE_ID, events=first.events)
    assert resumed.last_seq == first.last_seq
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
    assert started.__dict__["message_id"] == MESSAGE_ID
    assert finished.__dict__["status"] == "done"
    assert finished.__dict__["duration_ms"] == 1000.0
