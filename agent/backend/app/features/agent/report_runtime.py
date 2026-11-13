"""レポート契約の内部ツールと、既取得根拠からの固定描画。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from typing import Any

from app.features.agent import source_handles
from app.features.agent.answer_data import DataReferenceError, data_index
from app.features.agent.answer_evidence import passed_evidence, selected_answer_evidence
from app.features.agent.report_contract import (
    AnswerPlan,
    NarrativeBlock,
    ReportOutputContract,
    TableBlock,
)
from app.features.agent.report_fields import REPORT_FIELDS_KEY, render_confirmation
from app.features.agent.report_preview import (
    RenderedBlockCost,
    RenderedReport,
    ReportPreviewError,
    report_source_digest,
)
from app.features.agent.tools import (
    ExternalToolError,
    ToolDefinition,
    ToolHandler,
    ToolInvocationContext,
    mcp_base_tool_name,
)

DRAFT_REPORT_TOOL = "draft_report"
REPORT_RENDERER_REVISION = 1
REPORT_DATA_INSTRUCTIONS = (
    "## Step 1: 回答の構成を選ぶ\n"
    "結論と必要な集計・行列を本文に選び、取得済みの詳しい明細は保存した結果表から確認できるようにします。"
    "表はTableBlockのtableにT番号を指定します。"
    "全表はrowsとcolsをnullにし、一部はrowsに行番号、colsにcolumn_refsの列番号を指定します。"
    "行列は1始まり、*は全件、範囲とカンマ列挙は指定順です。縦表はlayout=verticalにします。"
    "Narrativeの値はquantitiesの[[quantity:T1:R2:C3]]、その他はcellsの[[cell:T1:R2:C3]]を使います。\n"
    "## Step 2: 必要な確認状況を選ぶ\n"
    "ConfirmationSectionは任意です。回答に必要な列の確認状況を示す場合だけfield_refを選び、"
    "状態はプログラムに任せます。"
    "Narrativeに別の可用性判定を書かないでください。"
)
_CANONICAL_CITATION = re.compile(r"【根拠: ([^\r\n】]*)】")


def source_digest(run: Any) -> str:
    """自分のpreview保存で変わらない、根拠と対応表だけの版。"""
    return report_source_digest(
        {
            "renderer_revision": REPORT_RENDERER_REVISION,
            "data": run.metadata.get("_answer_data_references"),
            "source": run.metadata.get("_source_reference_handles"),
            "fields": run.metadata.get(REPORT_FIELDS_KEY),
            "steps": [
                {
                    "id": step.id,
                    "call": step.tool_call.model_dump(mode="json") if step.tool_call else None,
                    "result": step.tool_result.model_dump(mode="json"),
                }
                for step in run.steps
                if step.tool_result is not None
                and step.tool_call is not None
                and mcp_base_tool_name(step.tool_call.name)
                in (
                    source_handles.SOURCE_TOOLS
                    | {"nl2sql_query", "nl2sql_get_job", "nl2sql_get_profile_schema"}
                )
            ],
        }
    )


def render_plan(
    run: Any,
    plan: AnswerPlan,
    *,
    trusted_sources: Collection[str],
    render_sources: Callable[[str], str],
    finalize: Callable[[str, str], str],
) -> RenderedReport:
    """モデル本文と表は参照検証し、確認節は検証済み状態からだけ描画する。"""
    index = data_index(run.steps, run.metadata, trusted_sources=trusted_sources)
    parts: list[str] = []
    costs: list[RenderedBlockCost] = []
    model_parts: list[str] = []
    available_sources = {item["chunk_id"] for item in passed_evidence(run.steps, limit=None)}
    for number, block in enumerate(plan.blocks, 1):
        citation_cost = 0
        if isinstance(block, TableBlock):
            rendered = index.render(render_sources(block.marker()))
        elif isinstance(block, NarrativeBlock):
            if "[[table:" in block.text:
                raise ReportPreviewError(
                    "report.table_block_required", "表はTable blockで指定してください。"
                )
            source_text = render_sources(block.text)
            # データ値の展開前に、モデルが明示した正式な引用印だけを選ぶ。
            explicit = "\n".join(
                citation[0] for citation in _CANONICAL_CITATION.finditer(source_text)
            )
            if any(
                citation[1] not in available_sources
                for citation in _CANONICAL_CITATION.finditer(source_text)
            ):
                raise DataReferenceError("取得済みの根拠に対応しない引用です。")
            selected_answer_evidence(run.steps, explicit)
            if explicit:
                model_parts.append(explicit)
                citation_cost = sum(
                    len(match[0]) for match in _CANONICAL_CITATION.finditer(explicit)
                )
            rendered = index.render(source_text)
        else:
            # 取得件数/NULL件数などはモデルのliteralではなく、プログラムが計算した値。
            rendered = render_confirmation(run.metadata, block, data_index=index)
        parts.append(rendered)
        costs.append(
            RenderedBlockCost(
                index=number,
                kind=block.kind,
                expanded_codepoints=len(rendered),
                citation_marker_codepoints=citation_cost,
            )
        )
    evidence_text = "\n\n".join(model_parts)
    body = finalize("\n\n".join(parts), evidence_text)
    return RenderedReport(
        body=body,
        block_costs=tuple(costs),
        composition_delta_chars=len(body) - sum(c.expanded_codepoints for c in costs),
        source_digest=source_digest(run),
        evidence_text=evidence_text,
    )


def draft_report_tool(
    run_id: str,
    contract: ReportOutputContract,
    renderer: Callable[[Any, AnswerPlan], RenderedReport],
) -> tuple[ToolDefinition, ToolHandler]:
    def handler(arguments: dict[str, Any], context: ToolInvocationContext) -> dict[str, Any]:
        from app.features.agent.runtime import runtime_repository

        if context.run_id != run_id:
            raise ExternalToolError("report.run_mismatch", "別の実行に回答案を保存できません。")
        operation_key = context.operation_key or context.trace_id
        if not operation_key:
            raise ExternalToolError(
                "report.operation_missing", "回答案の呼び出しを識別できません。"
            )
        try:
            plan = AnswerPlan.model_validate(arguments)
            return runtime_repository.register_report_preview(
                run_id, operation_key=operation_key, plan=plan, contract=contract, renderer=renderer
            )
        except (ValueError, TypeError) as exc:
            code = exc.code if isinstance(exc, ReportPreviewError) else "report.draft_invalid"
            raise ExternalToolError(code, str(exc)) from exc

    return ToolDefinition(
        name=DRAFT_REPORT_TOOL,
        title="回答の表示準備",
        description=(
            "## Step 1: 回答案を確認する\n"
            "構成を渡すと、参照を展開した分量と表示の準備状態を返します。\n"
            "budget_block_costsはmetricと同じ数え方です。各budget_codepointsと"
            "composition_delta_charsの合計が全体のbudget_codepointsです。"
            "block_costsは引用印を含む展開後の文字数です。"
            "budget_block_costsがnullの既存案では全体の計数を使います。\n"
            "## Step 2: 回答案を選ぶ\n"
            "返された分量の状態を確認し、最終出力は選んだpreview_idだけを返す。\n"
            "分量設定: "
            + json.dumps(
                contract.budget.model_dump(mode="json")
                if contract.budget is not None
                else {
                    "metric": "expanded_markdown_codepoints",
                    "soft_target": None,
                    "hard_max": None,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        input_schema=AnswerPlan.model_json_schema(),
        output_schema={"type": "object"},
        side_effects=False,
        audit_tags=["output"],
    ), handler
