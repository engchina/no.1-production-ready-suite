"""信頼した描画処理の結果をRun単位で固定し、同じ内容だけを公開する。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.features.agent.report_contract import (
    AnswerPlan,
    FinalSelection,
    ReportBudgetMetric,
    ReportOutputContract,
)

REPORT_PREVIEWS_METADATA_KEY = "_report_previews"
MAX_REPORT_PREVIEWS = 16
MAX_REPORT_BODY_CHARS = 262_144
MAX_REPORT_LEDGER_BYTES = 2 * 1024 * 1024
BudgetStatus = Literal["WITHIN_BUDGET", "OVER_TARGET", "REJECTED_HARD_MAX"]
# report_runtime の正式な表示用 citation と同じ有限の形式。根拠の実在検証は描画側で行う。
_CANONICAL_CITATION = re.compile(r"【根拠: ([^\r\n】]*)】")


class ReportPreviewError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RenderedBlockCost(_Model):
    index: int = Field(ge=1)
    kind: Literal["narrative", "table", "confirmation_section"]
    expanded_codepoints: int = Field(ge=0)


class RenderedReport(_Model):
    """全ての本文変換を済ませた、Runtimeからだけ渡す描画結果。"""

    body: str = Field(max_length=MAX_REPORT_BODY_CHARS)
    # 引用照合用はNarrativeだけ。表・確認節・coverageのliteralを混ぜない。
    evidence_text: str = Field(default="", max_length=MAX_REPORT_BODY_CHARS)
    block_costs: tuple[RenderedBlockCost, ...]
    # 分隔と全体正規化による増減。変換前の値を最終コストとは呼ばない。
    composition_delta_chars: int = 0
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewSummary(_Model):
    preview_id: str
    metric: ReportBudgetMetric = "expanded_markdown_codepoints"
    budget_codepoints: int
    citation_marker_codepoints: int
    block_costs: tuple[RenderedBlockCost, ...]
    composition_delta_chars: int
    total_codepoints: int
    remaining_target: int | None
    remaining_hard: int | None
    status: BudgetStatus
    incomplete: bool
    reused: bool = False


class PreviewCreation(_Model):
    ledger: dict[str, Any]
    result: PreviewSummary


class SelectedPreview(_Model):
    preview_id: str
    status: Literal["complete", "incomplete"]
    budget_status: BudgetStatus
    body: str | None
    evidence_text: str = ""
    reason: Literal["hard_max_exceeded"] | None
    total_codepoints: int


class _Record(_Model):
    preview_id: str
    operation_key: str = Field(min_length=1, max_length=256)
    plan: AnswerPlan
    plan_digest: str
    body: str = Field(max_length=MAX_REPORT_BODY_CHARS)
    evidence_text: str = Field(default="", max_length=MAX_REPORT_BODY_CHARS)
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    body_digest: str
    block_costs: list[RenderedBlockCost]
    composition_delta_chars: int
    total_codepoints: int
    remaining_target: int | None
    remaining_hard: int | None
    status: BudgetStatus
    incomplete: bool
    record_digest: str


class _Ledger(_Model):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    contract_digest: str
    next_number: int = Field(ge=1, le=MAX_REPORT_PREVIEWS + 1)
    records: list[_Record] = Field(max_length=MAX_REPORT_PREVIEWS)


def report_source_digest(value: Any) -> str:
    """JSON形式の取得結果・出典対応のsnapshotを内部照合用hashにする。"""
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReportPreviewError(
            "runtime.report_snapshot_invalid", "回答の出典対応を固定できません。"
        ) from exc
    return hashlib.sha256(raw.encode()).hexdigest()


def _record_digest(record: _Record) -> str:
    return report_source_digest(record.model_dump(mode="json", exclude={"record_digest"}))


def _budget_measurement(
    body: str, evidence_text: str, contract: ReportOutputContract
) -> tuple[ReportBudgetMetric, int, int]:
    """本文は変えず、Narrative が明示した検証済み citation だけの費用を求める。"""
    metric: ReportBudgetMetric = (
        contract.budget.metric if contract.budget else "expanded_markdown_codepoints"
    )
    markers = [match[0] for match in _CANONICAL_CITATION.finditer(evidence_text)]
    marker_cost = sum(len(marker) for marker in markers)
    if metric == "expanded_markdown_without_citation_markers_codepoints":
        # evidence_text は callback が検証済みの引用だけを保存する。分隔の改行は控除しない。
        if _CANONICAL_CITATION.sub("", evidence_text).strip():
            raise ReportPreviewError(
                "runtime.report_render_invalid", "回答案の引用計測用の対応が不正です。"
            )
        required = Counter(markers)
        present = Counter(match[0] for match in _CANONICAL_CITATION.finditer(body))
        if any(present[marker] < count for marker, count in required.items()):
            raise ReportPreviewError(
                "runtime.report_render_invalid", "回答案の引用と描画済み本文が一致しません。"
            )
        return metric, len(body) - marker_cost, marker_cost
    return metric, len(body), marker_cost


def _budget_values(
    total: int, contract: ReportOutputContract
) -> tuple[BudgetStatus, int | None, int | None]:
    budget = contract.budget
    target = budget.soft_target if budget else None
    hard = budget.hard_max if budget else None
    remaining_target = None if target is None else target - total
    remaining_hard = None if hard is None else hard - total
    status: BudgetStatus = "WITHIN_BUDGET"
    if remaining_hard is not None and remaining_hard < 0:
        status = "REJECTED_HARD_MAX"
    elif remaining_target is not None and remaining_target < 0:
        status = "OVER_TARGET"
    return status, remaining_target, remaining_hard


def _check_costs(
    plan: AnswerPlan,
    body: str,
    costs: list[RenderedBlockCost] | tuple[RenderedBlockCost, ...],
    delta: int,
) -> None:
    if (
        len(costs) != len(plan.blocks)
        or any(
            cost.index != index or cost.kind != block.kind
            for index, (cost, block) in enumerate(zip(costs, plan.blocks, strict=True), 1)
        )
        or sum(cost.expanded_codepoints for cost in costs) + delta != len(body)
    ):
        raise ReportPreviewError(
            "runtime.report_render_invalid", "回答案の描画結果と文字数が一致しません。"
        )


def _bounded(value: Mapping[str, Any]) -> None:
    try:
        size = len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReportPreviewError(
            "runtime.report_preview_invalid", "保存した回答案を読み取れません。"
        ) from exc
    if size > MAX_REPORT_LEDGER_BYTES:
        raise ReportPreviewError(
            "runtime.report_preview_limit", "回答案の保存量が上限に達しました。"
        )


def _load(value: Mapping[str, Any] | None, run_id: str, contract: ReportOutputContract) -> _Ledger:
    digest = report_source_digest(contract.model_dump(mode="json"))
    if value is None:
        try:
            return _Ledger(run_id=run_id, contract_digest=digest, next_number=1, records=[])
        except ValidationError as exc:
            raise ReportPreviewError(
                "runtime.report_preview_invalid", "回答案の実行識別子が不正です。"
            ) from exc
    if not isinstance(value, Mapping):
        raise ReportPreviewError(
            "runtime.report_preview_invalid", "保存した回答案がオブジェクトではありません。"
        )
    _bounded(value)
    if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise ReportPreviewError("runtime.report_preview_invalid", "保存した回答案の版が不正です。")
    try:
        ledger = _Ledger.model_validate(value)
    except (ValidationError, TypeError, ValueError, RecursionError) as exc:
        raise ReportPreviewError(
            "runtime.report_preview_invalid", "保存した回答案の形式が不正です。"
        ) from exc
    if ledger.run_id != run_id:
        raise ReportPreviewError(
            "runtime.report_preview_wrong_run", "この実行の回答案ではありません。"
        )
    if ledger.contract_digest != digest:
        raise ReportPreviewError(
            "runtime.report_contract_changed", "回答案に適用した公開版の出力設定が一致しません。"
        )
    if ledger.next_number != len(ledger.records) + 1:
        raise ReportPreviewError(
            "runtime.report_preview_invalid", "保存した回答案の番号が不正です。"
        )
    operations: set[str] = set()
    for number, record in enumerate(ledger.records, 1):
        if record.preview_id != f"A{number}" or record.operation_key in operations:
            raise ReportPreviewError(
                "runtime.report_preview_invalid", "保存した回答案の対応が不正です。"
            )
        operations.add(record.operation_key)
        if (
            report_source_digest(record.plan.model_dump(mode="json")) != record.plan_digest
            or report_source_digest(record.body) != record.body_digest
            or _record_digest(record) != record.record_digest
            or record.total_codepoints != len(record.body)
            or (record.status, record.remaining_target, record.remaining_hard)
            != _budget_values(
                _budget_measurement(record.body, record.evidence_text, contract)[1], contract
            )
            or record.incomplete != (record.status == "REJECTED_HARD_MAX")
        ):
            raise ReportPreviewError(
                "runtime.report_preview_invalid", "保存した回答案の内容が一致しません。"
            )
        _check_costs(record.plan, record.body, record.block_costs, record.composition_delta_chars)
    return ledger


def _summary(
    record: _Record, contract: ReportOutputContract, *, reused: bool = False
) -> PreviewSummary:
    # 本文と内部hash・operation keyをモデルへ返さない。
    metric, budget_cost, marker_cost = _budget_measurement(
        record.body, record.evidence_text, contract
    )
    return PreviewSummary(
        metric=metric,
        budget_codepoints=budget_cost,
        citation_marker_codepoints=marker_cost,
        preview_id=record.preview_id,
        block_costs=tuple(record.block_costs),
        composition_delta_chars=record.composition_delta_chars,
        total_codepoints=record.total_codepoints,
        remaining_target=record.remaining_target,
        remaining_hard=record.remaining_hard,
        status=record.status,
        incomplete=record.incomplete,
        reused=reused,
    )


def create_preview(
    ledger: Mapping[str, Any] | None,
    *,
    run_id: str,
    operation_key: str,
    plan: AnswerPlan,
    contract: ReportOutputContract,
    render_body: Callable[[AnswerPlan], RenderedReport],
) -> PreviewCreation:
    """呼出元の保存lock内で実行する。描画callbackは純CPUでI/Oをしない。"""
    current = _load(ledger, run_id, contract)
    if not isinstance(operation_key, str) or not operation_key.strip() or len(operation_key) > 256:
        raise ReportPreviewError(
            "runtime.report_operation_key_invalid", "回答案の操作識別子がありません。"
        )
    plan_data = plan.model_dump(mode="json")
    plan_digest = report_source_digest(plan_data)
    previous = next(
        (record for record in current.records if record.operation_key == operation_key), None
    )
    if previous is not None:
        if previous.plan_digest != plan_digest:
            raise ReportPreviewError(
                "runtime.report_operation_conflict",
                "同じ操作識別子に異なる回答案は保存できません。",
            )
        return PreviewCreation(
            ledger=current.model_dump(mode="json"), result=_summary(previous, contract, reused=True)
        )
    if len(current.records) >= MAX_REPORT_PREVIEWS:
        raise ReportPreviewError(
            "runtime.report_preview_limit", "保存できる回答案の数が上限に達しました。"
        )
    # モデルの引数を保持したままにせず、描画・保存ごとに独立の値へ復元する。
    frozen_plan = AnswerPlan.model_validate_json(json.dumps(plan_data, ensure_ascii=False))
    try:
        rendered = render_body(frozen_plan)
    except ValidationError as exc:
        raise ReportPreviewError(
            "runtime.report_render_invalid",
            "回答案の描画結果が保存できる形式・大きさではありません。",
        ) from exc
    if not isinstance(rendered, RenderedReport):
        raise ReportPreviewError("runtime.report_render_invalid", "回答案の描画結果が不正です。")
    if report_source_digest(frozen_plan.model_dump(mode="json")) != plan_digest:
        raise ReportPreviewError(
            "runtime.report_render_invalid", "描画中に回答案が変更されました。"
        )
    _check_costs(frozen_plan, rendered.body, rendered.block_costs, rendered.composition_delta_chars)
    total = len(rendered.body)
    _, budget_cost, _ = _budget_measurement(rendered.body, rendered.evidence_text, contract)
    status, target, hard = _budget_values(budget_cost, contract)
    record = _Record(
        preview_id=f"A{current.next_number}",
        operation_key=operation_key,
        plan=frozen_plan,
        plan_digest=plan_digest,
        body=rendered.body,
        evidence_text=rendered.evidence_text,
        source_digest=rendered.source_digest,
        body_digest=report_source_digest(rendered.body),
        block_costs=list(rendered.block_costs),
        composition_delta_chars=rendered.composition_delta_chars,
        total_codepoints=total,
        remaining_target=target,
        remaining_hard=hard,
        status=status,
        incomplete=status == "REJECTED_HARD_MAX",
        record_digest="",
    )
    record = record.model_copy(update={"record_digest": _record_digest(record)})
    updated = _Ledger(
        run_id=run_id,
        contract_digest=current.contract_digest,
        next_number=current.next_number + 1,
        records=[*current.records, record],
    )
    value = updated.model_dump(mode="json")
    _bounded(value)
    return PreviewCreation(ledger=value, result=_summary(record, contract))


def selectable_previews(
    ledger: Mapping[str, Any] | None,
    *,
    run_id: str,
    contract: ReportOutputContract,
    source_digest: str,
) -> tuple[str, ...]:
    """同じ根拠と出力設定に結び付き、公開できる保存済み番号だけを返す。"""
    current = _load(ledger, run_id, contract)
    return tuple(
        record.preview_id
        for record in current.records
        if not record.incomplete and record.source_digest == source_digest
    )


def select_preview(
    ledger: Mapping[str, Any] | None,
    *,
    run_id: str,
    selection: FinalSelection,
    contract: ReportOutputContract,
    source_digest: str | None = None,
) -> SelectedPreview:
    """固定済み本文をそのまま返す。描画・省略・再生成は行わない。"""
    current = _load(ledger, run_id, contract)
    record = next(
        (record for record in current.records if record.preview_id == selection.preview_id), None
    )
    if record is None:
        raise ReportPreviewError(
            "runtime.report_preview_not_found", "この実行に指定された回答案がありません。"
        )
    if source_digest is not None and source_digest != record.source_digest:
        raise ReportPreviewError(
            "report.source_changed", "回答案の作成後に出典対応が変わったため、再確認が必要です。"
        )
    return SelectedPreview(
        preview_id=record.preview_id,
        status="incomplete" if record.incomplete else "complete",
        budget_status=record.status,
        body=None if record.incomplete else record.body,
        evidence_text="" if record.incomplete else record.evidence_text,
        reason="hard_max_exceeded" if record.incomplete else None,
        total_codepoints=record.total_codepoints,
    )
