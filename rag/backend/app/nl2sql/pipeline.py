"""NL2SQL オーケストレーション(Router → 生成 → Guardrail → 実行)。

2 段ゲートの実体:
- ``generate``: Router で profile/backend を決め、Select AI で SQL を生成(showsql 相当)し、
  Guardrail で **実行前に静的検査**する。**ここでは実行しない**(人手プレビュー確認待ち)。
- ``execute`` : 人手承認済み(必要なら編集済み)SQL を再度 Guardrail にかけ、read-only を満たす
  もののみ実行する。

Select AI 呼び出しは ``SelectAiExecutor`` プロトコル越しに注入する(本番は ``SelectAiClient``、
CI は決定論スタブ)。確定スタックは不変。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.clients.select_ai import (
    SelectAiAgentResult,
    SelectAiGenerateAction,
    SelectAiGenerateResult,
    SqlExecutionResult,
)
from app.config import Settings, get_settings
from app.nl2sql.guardrail import GuardrailVerdict, enforce, resolve_guardrail_adapter
from app.nl2sql.router import QuestionClassifier, route

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)
_SQL_LEAD_RE = re.compile(r"((?:WITH|SELECT)\b.+)", re.IGNORECASE | re.DOTALL)


class SelectAiExecutor(Protocol):
    """Select AI 実行面(``SelectAiClient`` が構造的に満たす)。"""

    async def generate(
        self,
        prompt: str,
        *,
        profile_name: str,
        action: SelectAiGenerateAction = ...,
        set_profile: bool = ...,
        max_result_chars: int | None = ...,
    ) -> SelectAiGenerateResult: ...

    async def run_team(
        self,
        prompt: str,
        *,
        team_name: str,
        conversation_id: str | None = ...,
    ) -> SelectAiAgentResult: ...

    async def run_select(
        self,
        sql: str,
        *,
        binds: Mapping[str, object] | None = ...,
        max_rows: int = ...,
    ) -> SqlExecutionResult: ...


@dataclass(frozen=True)
class RouterSummary:
    """ルーティング判断の非機密サマリ。"""

    profile_selected: str | None
    generation_backend: str
    complexity_score: int
    matched_signals: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Nl2SqlGenerationOutcome:
    """生成フェーズ(showsql)の結果。**未実行**。"""

    question: str
    profile_name: str
    generation_backend: str
    router: RouterSummary
    generated_sql: str
    narration: str | None
    guardrail: GuardrailVerdict
    raw: str


@dataclass(frozen=True)
class Nl2SqlExecutionOutcome:
    """実行フェーズ(runsql)の結果。"""

    sql: str
    guardrail: GuardrailVerdict
    executed: bool
    blocked_reason: str | None
    result: SqlExecutionResult | None


def _extract_sql(text: str) -> str:
    """エージェント応答から SQL を抽出する(```sql``` ブロック → 先頭 SELECT/WITH)。"""
    source = text or ""
    fence = _SQL_FENCE_RE.search(source)
    if fence:
        return fence.group(1).strip().rstrip(";").strip()
    lead = _SQL_LEAD_RE.search(source)
    if lead:
        return lead.group(1).strip().rstrip(";").strip()
    return ""


class Nl2SqlPipeline:
    """NL2SQL の生成/実行を束ねるオーケストレータ。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        select_ai_client: SelectAiExecutor,
        classifier: QuestionClassifier | None = None,
        domain_to_profile: Mapping[str, str] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = select_ai_client
        self._classifier = classifier
        self._domain_to_profile = domain_to_profile

    async def generate(
        self,
        question: str,
        *,
        profile_name: str | None = None,
        team_name: str | None = None,
        allowed_objects: Sequence[str] = (),
    ) -> Nl2SqlGenerationOutcome:
        """NL から SQL を生成し Guardrail 検査する(未実行)。"""
        decision = route(
            self._settings,
            question,
            classifier=self._classifier,
            domain_to_profile=self._domain_to_profile,
        )
        summary = RouterSummary(
            profile_selected=decision.profile_selected,
            generation_backend=decision.generation_backend,
            complexity_score=decision.complexity_score,
            matched_signals=decision.matched_signals,
            reason=decision.reason,
        )
        profile = (
            profile_name
            or decision.profile_selected
            or getattr(self._settings, "oracle_select_ai_profile", "")
            or ""
        ).strip()
        team = (team_name or "").strip()
        use_agent = decision.generation_backend == "select_ai_agent" and bool(team)

        if use_agent:
            agent = await self._client.run_team(question, team_name=team)
            generated_sql = _extract_sql(agent.reply)
            narration: str | None = agent.reply
            raw = agent.raw
            effective_backend = "select_ai_agent"
        else:
            gen = await self._client.generate(
                question, profile_name=profile, action=SelectAiGenerateAction.SHOWSQL
            )
            generated_sql = (gen.text or "").strip().rstrip(";").strip()
            narration = None
            raw = gen.text
            effective_backend = "select_ai"

        params = resolve_guardrail_adapter(self._settings)
        verdict = enforce(generated_sql, params, allowed_objects=tuple(allowed_objects))
        return Nl2SqlGenerationOutcome(
            question=question,
            profile_name=profile,
            generation_backend=effective_backend,
            router=summary,
            generated_sql=generated_sql,
            narration=narration,
            guardrail=verdict,
            raw=raw,
        )

    async def execute(
        self,
        sql: str,
        *,
        allowed_objects: Sequence[str] = (),
    ) -> Nl2SqlExecutionOutcome:
        """人手承認済み SQL を Guardrail 再検査し、許可されたら read-only 実行する。"""
        params = resolve_guardrail_adapter(self._settings)
        verdict = enforce(sql, params, allowed_objects=tuple(allowed_objects))
        if not verdict.allowed:
            return Nl2SqlExecutionOutcome(
                sql=sql,
                guardrail=verdict,
                executed=False,
                blocked_reason=";".join(verdict.violations) or "blocked",
                result=None,
            )
        max_rows = verdict.max_rows or int(
            getattr(self._settings, "nl2sql_guardrail_max_rows", 1000)
        )
        exec_sql = verdict.normalized_sql or sql
        result = await self._client.run_select(exec_sql, max_rows=max_rows)
        return Nl2SqlExecutionOutcome(
            sql=exec_sql,
            guardrail=verdict,
            executed=True,
            blocked_reason=None,
            result=result,
        )
