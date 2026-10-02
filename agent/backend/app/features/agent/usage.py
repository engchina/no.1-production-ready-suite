"""業務 Agent の Run のモデル利用量の集計（#772）。

Run に記録した `RunUsage`（組み込み Runtime が SDK の `Usage` から書く）を、期間・業務 Agent・
利用者・モデル・日ごとに合計する。対象の Run（利用できる業務 Agent の Run だけ）は呼び出し側が渡す。
期間は Run の作成日時で決め、日は利用者のタイムゾーン（画面のブラウザの IANA 名）で区切る。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.features.agent.runtime import RunState

# 画面で選べる期間（日）。
USAGE_PERIOD_DAYS: tuple[int, ...] = (7, 30, 90)
DEFAULT_TIMEZONE = "Asia/Tokyo"


class UsageTotals(BaseModel):
    runs: int = 0
    # 利用量を記録した Run（#772 より前の Run・モデルを呼ぶ前に止まった Run は数えない）。
    runs_with_usage: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, run: RunState) -> None:
        self.runs += 1
        usage = run.usage
        if usage is None:
            return
        self.runs_with_usage += 1
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.total_tokens += usage.total_tokens


class AgentUsage(UsageTotals):
    agent_id: str
    agent_name: str


class UserUsage(UsageTotals):
    # 作成者のない Run（#233 より前）は None。
    user_uuid: str | None
    display_name: str


class ModelUsage(UsageTotals):
    # 利用量の無い Run は空文字（画面では「記録なし」）。
    model: str


class DailyUsage(UsageTotals):
    day: date


class UsageReport(BaseModel):
    days: int
    timezone: str
    since: datetime
    until: datetime
    totals: UsageTotals
    # 直前の同じ長さの期間（増減の比較に使う）。
    previous: UsageTotals
    by_agent: list[AgentUsage] = Field(default_factory=list)
    by_user: list[UserUsage] = Field(default_factory=list)
    by_model: list[ModelUsage] = Field(default_factory=list)
    by_day: list[DailyUsage] = Field(default_factory=list)


def resolve_timezone(name: str | None) -> ZoneInfo:
    """IANA のタイムゾーン名を解決する。知らない名前は ValueError（API は 422 にする）。"""
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("タイムゾーンが正しくありません。") from exc


def build_usage_report(
    runs: Iterable[RunState],
    *,
    days: int,
    now: datetime,
    tz: ZoneInfo,
    agent_names: Mapping[str, str],
    user_names: Callable[[list[str]], Mapping[str, str]],
) -> UsageReport:
    """期間（今日を含む `days` 日）と直前の同じ長さの期間の利用量を集計する。

    `user_names` は作成者の user_uuid の一覧を受け取り、表示名を返す（見つからない人は省く）。
    """
    today = now.astimezone(tz).date()
    first_day = today - timedelta(days=days - 1)
    since = datetime.combine(first_day, datetime.min.time(), tzinfo=tz)
    previous_since = since - timedelta(days=days)

    totals = UsageTotals()
    previous = UsageTotals()
    by_agent: dict[str, AgentUsage] = {}
    by_user: dict[str | None, UserUsage] = {}
    by_model: dict[str, ModelUsage] = {}
    by_day = {
        first_day + timedelta(days=offset): DailyUsage(day=first_day + timedelta(days=offset))
        for offset in range(days)
    }

    for run in runs:
        created = run.created_at
        if created > now:
            continue
        if created < since:
            if created >= previous_since:
                previous.add(run)
            continue
        totals.add(run)
        by_agent.setdefault(
            run.agent_id,
            AgentUsage(agent_id=run.agent_id, agent_name=agent_names.get(run.agent_id, "")),
        ).add(run)
        by_user.setdefault(
            run.created_by_user_uuid,
            UserUsage(user_uuid=run.created_by_user_uuid, display_name=""),
        ).add(run)
        model = run.usage.model if run.usage is not None else ""
        by_model.setdefault(model, ModelUsage(model=model)).add(run)
        day = created.astimezone(tz).date()
        if day in by_day:
            by_day[day].add(run)

    names = user_names([uuid for uuid in by_user if uuid])
    for item in by_user.values():
        if item.user_uuid:
            item.display_name = names.get(item.user_uuid, "")

    return UsageReport(
        days=days,
        timezone=tz.key,
        since=since,
        until=now,
        totals=totals,
        previous=previous,
        by_agent=_ranked(by_agent.values()),
        by_user=_ranked(by_user.values()),
        by_model=_ranked(by_model.values()),
        by_day=[by_day[day] for day in sorted(by_day)],
    )


def _ranked[T: UsageTotals](items: Iterable[T]) -> list[T]:
    """token の多い順（同じなら Run の多い順）。"""
    return sorted(items, key=lambda item: (-item.total_tokens, -item.runs))
