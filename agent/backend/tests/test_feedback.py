"""チャットの回答へのフィードバックと、その集計（#774）。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pytest import MonkeyPatch
from security_support import ProductionAuth, client, enable_production_auth, login

from app.features.agent.feedback import build_feedback_report
from app.features.agent.runtime import (
    Artifact,
    FeedbackRating,
    FeedbackReason,
    RunFeedback,
    RunState,
    RunStatus,
    runtime_repository,
)
from app.security.domain import LOCAL_DEBUG_USER_UUID
from app.security.permissions import MENU_FEEDBACK, MENU_RUNS, RUNS_OPERATE
from app.security.service import set_security_service

NOW = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)
ALICE = "aaaaaaaa-0000-0000-0000-000000000001"


def _run(
    run_id: str,
    *,
    agent_id: str = "default",
    status: RunStatus = RunStatus.COMPLETED,
    user: str | None = LOCAL_DEBUG_USER_UUID,
    rated_at: datetime | None = None,
    rating: FeedbackRating = FeedbackRating.HELPFUL,
    reason: FeedbackReason | None = None,
) -> RunState:
    return RunState(
        id=run_id,
        goal=f"{run_id} の質問",
        agent_id=agent_id,
        runtime_id="builtin",
        status=status,
        created_by_user_uuid=user,
        artifacts=(
            [Artifact(name="回答", kind="answer", content={"text": f"{run_id} の回答"})]
            if status == RunStatus.COMPLETED
            else []
        ),
        feedback=(
            None
            if rated_at is None
            else RunFeedback(rating=rating, reason=reason, user_uuid=user, updated_at=rated_at)
        ),
    )


def _report(runs: list[RunState], **filters: Any) -> Any:
    return build_feedback_report(
        runs,
        days=7,
        now=NOW,
        agent_id=filters.get("agent_id"),
        rating=filters.get("rating"),
        reason=filters.get("reason"),
        agent_names={"default": "汎用業務 Agent", "sales": "営業の Agent"},
        user_names=lambda uuids: {uuid: "Alice" for uuid in uuids if uuid == ALICE},
    )


def test_report_summarizes_the_period_and_lists_newest_first() -> None:
    bad = FeedbackRating.NOT_HELPFUL
    runs = [
        _run("r1", user=ALICE, rated_at=NOW - timedelta(hours=1)),
        _run("r2", rated_at=NOW - timedelta(days=1), rating=bad, reason=FeedbackReason.INCOMPLETE),
        _run("r3", rated_at=NOW - timedelta(days=2), rating=bad, reason=FeedbackReason.INCORRECT),
        _run(
            "r4",
            agent_id="sales",
            rated_at=NOW - timedelta(days=3),
            rating=bad,
            reason=FeedbackReason.INCORRECT,
        ),
        # 前の 7 日（比較用）と、評価していない Run・2 期間より前の評価。
        _run("r5", rated_at=NOW - timedelta(days=10)),
        _run("r6"),
        _run("r7", rated_at=NOW - timedelta(days=30)),
    ]

    report = _report(runs)

    summary = report.summary
    assert (summary.total, summary.helpful, summary.not_helpful) == (4, 1, 3)
    assert summary.helpful_rate == pytest.approx(0.25)
    # 理由は件数の多い順。
    assert [(item.reason, item.count) for item in summary.reason_counts] == [
        (FeedbackReason.INCORRECT, 2),
        (FeedbackReason.INCOMPLETE, 1),
    ]
    assert (report.previous.total, report.previous.helpful_rate) == (1, 1.0)
    assert [item.run_id for item in report.items] == ["r1", "r2", "r3", "r4"]
    first = report.items[0]
    assert (first.display_name, first.question, first.answer) == ("Alice", "r1 の質問", "r1 の回答")
    assert report.items[3].agent_name == "営業の Agent"


def test_report_filters_the_list_by_agent_rating_and_reason() -> None:
    bad = FeedbackRating.NOT_HELPFUL
    runs = [
        _run("r1", rated_at=NOW - timedelta(hours=1)),
        _run("r2", rated_at=NOW - timedelta(hours=2), rating=bad, reason=FeedbackReason.INCORRECT),
        _run(
            "r3",
            agent_id="sales",
            rated_at=NOW - timedelta(hours=3),
            rating=bad,
            reason=FeedbackReason.WRONG_ACTION,
        ),
    ]

    by_agent = _report(runs, agent_id="sales")
    # 業務 Agent の絞り込みは集計にも効く。評価・理由の絞り込みは一覧だけ。
    assert (by_agent.summary.total, [item.run_id for item in by_agent.items]) == (1, ["r3"])
    by_rating = _report(runs, rating=bad)
    assert by_rating.summary.total == 3
    assert ([item.run_id for item in by_rating.items], by_rating.matched) == (["r2", "r3"], 2)
    by_reason = _report(runs, reason=FeedbackReason.WRONG_ACTION)
    assert [item.run_id for item in by_reason.items] == ["r3"]
    empty = _report([])
    assert (empty.summary.total, empty.summary.helpful_rate, empty.items) == (0, None, [])


@pytest.fixture
def seeded_runs() -> Iterator[None]:
    repository: Any = runtime_repository
    runs = [
        _run("feedback-774-done"),
        _run("feedback-774-running", status=RunStatus.RUNNING),
        _run("feedback-774-other", user=ALICE),
    ]
    with repository._lock:  # noqa: SLF001 - テスト用に Run を直接置く
        for run in runs:
            repository._runs[run.id] = run
    try:
        yield
    finally:
        with repository._lock:  # noqa: SLF001
            for run in runs:
                repository._runs.pop(run.id, None)


def test_user_rates_own_answer_and_can_change_it(seeded_runs: None) -> None:
    del seeded_runs
    url = "/api/runs/feedback-774-done/feedback"

    helpful = client.put(url, json={"rating": "helpful", "reason": "incorrect", "comment": "x"})
    assert helpful.status_code == 200, helpful.text
    feedback = helpful.json()["data"]["feedback"]
    # 役に立った評価には理由・コメントを残さない。
    assert (feedback["rating"], feedback["reason"], feedback["comment"]) == ("helpful", None, "")
    assert feedback["user_uuid"] == LOCAL_DEBUG_USER_UUID

    missing_reason = client.put(url, json={"rating": "not_helpful"})
    assert missing_reason.status_code == 422
    assert "理由を選んでください" in missing_reason.text
    too_long = client.put(
        url, json={"rating": "not_helpful", "reason": "incomplete", "comment": "あ" * 1001}
    )
    assert too_long.status_code == 422

    changed = client.put(
        url, json={"rating": "not_helpful", "reason": "wrong_action", "comment": " 別の表を見た "}
    )
    assert changed.status_code == 200, changed.text
    stored = runtime_repository.get_run("feedback-774-done").feedback
    assert stored is not None
    assert (stored.rating, stored.reason, stored.comment) == (
        FeedbackRating.NOT_HELPFUL,
        FeedbackReason.WRONG_ACTION,
        "別の表を見た",
    )


def test_only_answered_runs_of_the_same_user_can_be_rated(seeded_runs: None) -> None:
    del seeded_runs
    running = client.put("/api/runs/feedback-774-running/feedback", json={"rating": "helpful"})
    assert running.status_code == 409
    other = client.put("/api/runs/feedback-774-other/feedback", json={"rating": "helpful"})
    assert other.status_code == 403
    assert "この会話をした利用者だけ" in other.text
    missing = client.put("/api/runs/run-missing-774/feedback", json={"rating": "helpful"})
    assert missing.status_code == 404


def test_feedback_api_returns_the_report(seeded_runs: None) -> None:
    del seeded_runs
    client.put(
        "/api/runs/feedback-774-done/feedback",
        json={"rating": "not_helpful", "reason": "incomplete", "comment": "件数が足りない"},
    )

    response = client.get("/api/feedback", params={"days": 7, "rating": "not_helpful"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    item = next(item for item in data["items"] if item["run_id"] == "feedback-774-done")
    assert (item["reason"], item["comment"], item["display_name"]) == (
        "incomplete",
        "件数が足りない",
        "ローカル利用者",
    )
    assert client.get("/api/feedback", params={"days": 14}).status_code == 422
    assert client.get("/api/feedback", params={"reason": "unknown"}).status_code == 422


@pytest.fixture
def auth(monkeypatch: MonkeyPatch) -> Iterator[ProductionAuth]:
    yield enable_production_auth(monkeypatch)
    set_security_service(None)


def test_feedback_permissions(auth: ProductionAuth, seeded_runs: None) -> None:
    del seeded_runs
    # 画面の閲覧だけでは評価を付けられない（チャットの利用者 = Run の実行権限）。
    auth.user_with_permissions("viewer-774", [MENU_RUNS], agent_ids=["default"])
    viewer = login("viewer-774")
    rated = client.put(
        "/api/runs/feedback-774-done/feedback", json={"rating": "helpful"}, headers=viewer
    )
    assert rated.status_code == 403
    assert client.get("/api/feedback", headers=viewer).status_code == 403

    operator = auth.user_with_permissions("operator-774", [RUNS_OPERATE], agent_ids=["default"])
    repository: Any = runtime_repository
    with repository._lock:  # noqa: SLF001 - 作成者を作った利用者に合わせる
        repository._runs["feedback-774-done"].created_by_user_uuid = operator.user_uuid  # noqa: SLF001
    own = client.put(
        "/api/runs/feedback-774-done/feedback",
        json={"rating": "helpful"},
        headers=login("operator-774"),
    )
    assert own.status_code == 200, own.text

    auth.user_with_permissions("manager-774", [MENU_FEEDBACK], agent_ids=["sales"])
    scoped = client.get("/api/feedback", headers=login("manager-774"))
    assert scoped.status_code == 200, scoped.text
    # 利用できる業務 Agent（sales）の評価だけ。
    assert scoped.json()["data"]["items"] == []
