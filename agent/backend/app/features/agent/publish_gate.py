"""公開前の評価の門（#2519・#2569）。

画面・API の公開（`POST /api/agents/{agent_id}/publish`）と、管理の命令の公開
（`app.cli.agent_test_resources`。#3545）が同じ判定を使う。
"""

from __future__ import annotations

from app.features.agent.evaluation import (
    EvaluationJob,
    JobStatus,
    cases_fingerprint,
    draft_definition_hash,
    evaluation_store,
)
from app.features.agent.runtime import AgentProfile
from app.settings import get_settings


def _format_rate(value: float) -> str:
    return f"{round(value * 100)}%"


def _publish_baseline(agent: AgentProfile, draft_job: EvaluationJob) -> EvaluationJob | None:
    """回帰の門の基準（#2569）: 下書きの評価と同じ評価セット・同じ評価ケースで、公開中の版を
    評価した最新の評価（完了して合格率のあるもの）。"""
    fingerprint = cases_fingerprint(draft_job)
    return next(
        (
            job
            for job in evaluation_store.list()
            if job.agent_id == agent.id
            and job.agent_version == agent.published_version
            and job.status == JobStatus.COMPLETED
            and job.set_id == draft_job.set_id
            and job.summary.pass_rate is not None
            and cases_fingerprint(job) == fingerprint
        ),
        None,
    )


def publish_evaluation_refusal(agent: AgentProfile) -> str | None:
    """公開前の評価の門（#2519・#2569）。満たさなければ理由の文、満たす・門が無ければ None。

    数えるのは、下書きを最後に変えた後に始めた、この業務 Agent の下書きの評価（完了したもの）の
    最新の 1 件。公開中の版と下書きが同じ（変更の無い再公開）なら求めない。門は、合格率の閾値
    （`AGENT_PUBLISH_MIN_PASS_RATE`）と、公開中の版との回帰（`AGENT_PUBLISH_MAX_PASS_RATE_DROP`）の
    2 つで、どちらかを使うときはケースの数の下限（`AGENT_PUBLISH_MIN_CASES`）も見る。
    """
    settings = get_settings()
    threshold = settings.agent_publish_min_pass_rate
    max_drop = settings.agent_publish_max_pass_rate_drop
    if (threshold <= 0 and max_drop is None) or not agent.has_unpublished_changes():
        return None
    since = agent.draft_updated_at or agent.created_at
    current_definition = draft_definition_hash(agent)
    latest = next(
        (
            job
            for job in evaluation_store.list()
            if job.agent_id == agent.id
            and job.agent_version == "draft"
            and job.status == JobStatus.COMPLETED
            and job.created_at >= since
            and job.definition_hash == current_definition
        ),
        None,
    )
    requirement = f"、合格率 {_format_rate(threshold)} 以上にして" if threshold > 0 else "て"
    if latest is None:
        return (
            f"公開するには、今の下書きを品質評価で確かめ{requirement}ください"
            "（下書きを変えた後の評価がありません）。"
        )
    judged = latest.summary.judged_cases
    if settings.agent_publish_min_cases and judged < settings.agent_publish_min_cases:
        return (
            f"今の下書きの品質評価（{latest.set_name}）で判定できたケースが {judged} 件で、"
            f"公開の判定に必要な {settings.agent_publish_min_cases} 件に足りません。"
            "評価セットにケースを足して評価し直してください。"
        )
    rate = latest.summary.pass_rate
    if rate is None:
        return (
            f"今の下書きの品質評価（{latest.set_name}）で判定できたケースがありません。"
            "評価し直してください。"
        )
    if threshold > 0 and rate < threshold:
        return (
            f"今の下書きの品質評価（{latest.set_name}）が合格率 {_format_rate(rate)}で、"
            f"公開に必要な合格率 {_format_rate(threshold)} に届いていません。"
            "指示やスキルを直して評価し直してください。"
        )
    if max_drop is None or agent.published_version is None:
        return None
    baseline = _publish_baseline(agent, latest)
    if baseline is None:
        return (
            f"公開するには、公開中の版（v{agent.published_version}）も同じ評価セット"
            f"（{latest.set_name}）・同じ評価ケースで評価してください（下書きの合格率を、公開中の版と"
            "比べて確かめます）。"
        )
    base_rate = baseline.summary.pass_rate or 0.0
    # 浮動小数の誤差で、ちょうど許す下げ幅を断らない。
    if rate < base_rate - max_drop - 1e-9:
        return (
            f"今の下書きの合格率 {_format_rate(rate)} が、"
            f"公開中の版（v{agent.published_version}）の合格率 {_format_rate(base_rate)} から"
            "下がりすぎています（許す下げ幅は "
            f"{_format_rate(max_drop)}、評価セット {latest.set_name}）。"
            "指示やスキルを直して評価し直してください。"
        )
    return None
