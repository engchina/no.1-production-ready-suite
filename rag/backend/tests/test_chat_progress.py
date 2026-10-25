"""回答の処理の経過（trace の span のイベント。#1359 / #3226）の記録のテスト。

回答の pipeline と同じ span（実行の全体・パイプラインの段・回答フローの工程）の知らせを
`ChatProgressTracker` が段のイベントにすること（親子・表示用のデータ・状態）、回答の thread からの
知らせの順、完了・失敗・停止・中断の終端を、記録したイベントで確かめる。
"""

import asyncio
import logging
import threading

import pytest
from pr_backend_core.chat_progress import (
    ChatProgressStepEvent,
    ChatProgressTerminalEvent,
    fold_chat_progress_events,
)

from app.rag.chat_answer_runs import close_saved_chat_progress
from app.rag.chat_progress import ChatProgressTracker
from app.rag.span_display import ANSWER_STEP_KIND
from tests.span_progress_support import (
    answer_step,
    answer_trace,
    pipeline_stage,
    span_rows,
    span_steps,
)

MESSAGE_ID = "a" * 32


def _statuses(tracker: ChatProgressTracker) -> list[str]:
    fold = fold_chat_progress_events(tracker.events)
    return [step.status for step in fold.step_list()]


async def test_records_root_stage_and_answer_steps_as_a_span_tree() -> None:
    """実行の全体・パイプラインの段・回答フローの工程を、親子つきの段として記録する。"""
    tracker = ChatProgressTracker(MESSAGE_ID, model_id="m1")
    with answer_trace(tracker.span_listener()):
        await pipeline_stage("history_rewrite")

        async def answer() -> None:
            with answer_step("質問の理解"):
                pass
            with answer_step("文書検索"), answer_step("Rerank"):
                pass

        await pipeline_stage("answer", answer())
        await pipeline_stage("answer_guardrail")
    tracker.finish()

    assert span_rows(tracker.events) == [
        # 始まった順（包む段の後に中の工程）。
        ("rag.history_rewrite", "history_rewrite", None, "done"),
        ("rag.answer", "", None, "done"),
        ("rag.engine.stage", ANSWER_STEP_KIND, "質問の理解", "done"),
        ("rag.engine.stage", ANSWER_STEP_KIND, "文書検索", "done"),
        ("rag.engine.stage", ANSWER_STEP_KIND, "Rerank", "done"),
        ("rag.answer_guardrail", "answer_guardrail", None, "done"),
    ]
    steps = {event.step_id: event for event in span_steps(tracker.events)}
    root = next(event for event in steps.values() if event.root)
    assert root.name == "rag.answer"
    assert root.trace_id is not None
    by_name = {(event.params or {}).get("name") or event.name: event for event in steps.values()}
    # 入れ子は span の親子（Rerank は文書検索の子、段は実行の全体の子）。
    assert by_name["Rerank"].parent_id == by_name["文書検索"].step_id
    assert by_name["rag.history_rewrite"].parent_id == root.step_id
    # 段の値に深さ（depth）を入れない（入れ子は親子で決まる）。
    assert "depth" not in (by_name["Rerank"].params or {})
    assert isinstance(tracker.events[-1], ChatProgressTerminalEvent)
    assert tracker.terminal == "done"


async def test_span_start_is_running_until_it_finishes() -> None:
    """開始した span は実行中の段になり、終わると所要時間つきの完了になる（実行中も届く）。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    seen: list[tuple[str, str]] = []
    tracker.attach(
        lambda event: (
            seen.append(
                (str(event.params.get("name") if event.params else event.name), event.status)
            )
            if isinstance(event, ChatProgressStepEvent) and not event.root
            else None
        )
    )
    with answer_trace(tracker.span_listener()), answer_step("文書検索"):
        assert seen == [("文書検索", "running")]
    assert seen[-1] == ("文書検索", "done")
    event = span_steps(tracker.events)[-1]
    assert event.started_at is not None and event.finished_at is not None


async def test_answer_step_display_has_llm_calls_note_and_details() -> None:
    """回答フローの工程の span に、LLM の回数・結果の説明・工程の詳細を付ける。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    with answer_trace(tracker.span_listener()), answer_step("根拠確認") as step:
        step.result("該当なし")
        step.status = "該当なし"
        step.field("検索文", "合成の検索文")
    event = next(e for e in span_steps(tracker.events) if not e.root)
    assert event.kind == ANSWER_STEP_KIND
    assert event.params == {"name": "根拠確認", "note": "該当なし"}
    assert event.details is not None
    assert [(field.label, field.value) for field in event.details.fields] == [
        ("結果", "該当なし"),
        ("検索文", "合成の検索文"),
    ]


async def test_pipeline_step_details_and_failure_summary_are_attached() -> None:
    """パイプラインの段の成功の詳細と、失敗の要約・エラーの種類を span の詳細にする。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    with answer_trace(tracker.span_listener()):
        await pipeline_stage(
            "history_rewrite",
            result="書き換えた質問",
            result_details=lambda value: {"fields": [{"label": "書き換えた質問", "value": value}]},
        )
        with pytest.raises(RuntimeError):
            await pipeline_stage("answer_record_save", error=RuntimeError("保存の失敗"))
    events = [event for event in span_steps(tracker.events) if not event.root]
    rewrite, save = events
    assert rewrite.details is not None
    assert [(field.label, field.value) for field in rewrite.details.fields] == [
        ("書き換えた質問", "書き換えた質問")
    ]
    assert save.status == "failed"
    assert save.details is not None
    assert save.details.lines[0].startswith("回答の記録をデータベースに保存できませんでした")
    assert "エラーの種類: RuntimeError" in save.details.lines
    # 例外の本文は詳細に入れない。
    assert all("保存の失敗" not in line for line in save.details.lines)


async def test_failed_answer_step_is_failed_with_summary_line() -> None:
    """例外で止まった回答フローの工程は失敗の段になり、詳細の先頭に要約を足す。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    with (
        answer_trace(tracker.span_listener()),
        pytest.raises(ValueError),
        answer_step("文書検索"),
    ):
        raise ValueError("合成の失敗")
    tracker.fail()
    step = next(e for e in span_steps(tracker.events) if not e.root)
    assert step.status == "failed"
    assert step.details is not None
    assert step.details.lines[0] == "この工程でエラーが発生しました。"
    assert tracker.terminal == "failed"


async def test_thread_spans_are_recorded_in_order_on_the_loop() -> None:
    """回答の thread の span の知らせは、ループの中で届いた順に記録する。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    recorded_on: set[int] = set()
    tracker.attach(lambda _event: recorded_on.add(threading.get_ident()))
    loop_thread = threading.get_ident()

    def work() -> None:
        with answer_step("質問の理解"):
            pass
        with answer_step("文書検索"):
            pass

    with answer_trace(tracker.span_listener()):
        await asyncio.to_thread(work)
        await asyncio.sleep(0)
    assert recorded_on == {loop_thread}
    assert [row[2] for row in span_rows(tracker.events)] == ["質問の理解", "文書検索"]
    assert [event.seq for event in tracker.events] == list(range(1, tracker.last_seq + 1))


@pytest.mark.parametrize(
    ("status", "running_status"),
    [("cancelled", "skipped"), ("failed", "failed")],
    ids=["stop", "interrupt"],
)
async def test_close_records_stop_and_interrupt_terminal(status: str, running_status: str) -> None:
    """停止（cancelled）・中断（failed）の終端。実行中の段は終端に合わせ、終端の後は記録しない。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    with answer_trace(tracker.span_listener()), answer_step("文書検索"):
        tracker.close(status)  # type: ignore[arg-type]
        assert tracker.terminal == status
        last_seq = tracker.last_seq
    # 終端の後の span の終了・完了・もう一度の停止は記録しない。
    tracker.finish()
    tracker.close("failed")
    assert tracker.last_seq == last_seq
    assert set(_statuses(tracker)) == {running_status}


async def test_resumes_from_saved_events() -> None:
    """保存済みのイベントから作り直すと、続きの番号で記録する。"""
    first = ChatProgressTracker(MESSAGE_ID)
    with answer_trace(first.span_listener()), answer_step("文書検索"):
        pass
    resumed = ChatProgressTracker(MESSAGE_ID, events=first.events)
    assert resumed.last_seq == first.last_seq
    resumed.close("failed")
    assert [event.seq for event in resumed.events] == list(range(1, resumed.last_seq + 1))


async def test_close_saved_progress_continues_numbering() -> None:
    """別のプロセスの停止・中断の後始末は、保存済みのイベントの続きで終端を記録する。"""
    tracker = ChatProgressTracker(MESSAGE_ID)
    with answer_trace(tracker.span_listener()), answer_step("文書検索"):
        saved = tracker.dump()
    closed = close_saved_chat_progress(saved, message_id=MESSAGE_ID, status="cancelled")
    assert closed[: len(saved)] == saved
    assert closed[-1]["type"] == "terminal"
    assert closed[-1]["status"] == "cancelled"
    assert [event["seq"] for event in closed] == list(range(1, len(closed) + 1))
    # 既に終端なら変えない。
    assert close_saved_chat_progress(closed, message_id=MESSAGE_ID, status="failed") == closed


async def test_logs_end_of_product_steps_with_duration_and_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """工程の span の終了を INFO で残す（request_id・trace_id・回答の id・所要時間）。"""
    from pr_backend_core.observability import request_id_var

    token = request_id_var.set("req-3226")
    try:
        with caplog.at_level(logging.INFO, logger="app.rag.chat_progress"):
            tracker = ChatProgressTracker(MESSAGE_ID, model_id="m1")
            tracker.trace_id = "trace-1"
            with answer_trace(tracker.span_listener()), answer_step("文書検索"):
                pass
            tracker.finish()
    finally:
        request_id_var.reset(token)
    records = [record for record in caplog.records if record.name == "app.rag.chat_progress"]
    # root（実行の全体）は出さない。
    assert len(records) == 1
    finished = records[0]
    assert finished.__dict__["step_kind"] == ANSWER_STEP_KIND
    assert finished.__dict__["step_name"] == "文書検索"
    assert finished.__dict__["status"] == "done"
    assert finished.__dict__["request_id"] == "req-3226"
    assert finished.__dict__["trace_id"] == "trace-1"
    assert finished.__dict__["message_id"] == MESSAGE_ID
    assert isinstance(finished.__dict__["duration_ms"], float)
