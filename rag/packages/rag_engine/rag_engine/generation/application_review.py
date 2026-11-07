"""原文の位置照合と分離した、推導結果だけの公開門。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from time import monotonic
from typing import Any

from pr_backend_core.observability.model_purpose import call_with_model_purpose

from rag_engine.generation.application_reason import explain_verdicts, propagates
from rag_engine.generation.application_sources import application_item_citations
from rag_engine.generation.applications import (
    APPLICATION_AUDIT_SYSTEM_PROMPT,
    TABLE_UNDETERMINED_REASON,
    CheckedApplication,
    _source_label,
    apply_application_audit,
    audit_view,
    withhold_undetermined_tables,
)
from rag_engine.generation.confirmed_context import confirmed_premise_records
from rag_engine.generation.document_background import background_label
from rag_engine.generation.document_inputs import document_groups, document_key
from rag_engine.generation.input_budget import ensure_model_input
from rag_engine.models.llm import ApplicationVerdictAudit, GroundedAudit
from rag_engine.resources.runtime import resolve_prompt


def application_documents(spans: Sequence[dict[str, Any]], row_conditions: Any = None) -> list[dict[str, Any]]:
    """生成へ選んだ原文全体を文書・版ごとに保持し、引用抜粋へ縮めない。

    ``row_conditions`` があれば、表の原文の根拠に、コードが決めた表の行の判定（table_row_judgments）を添える（#4349）。
    """
    judgments = row_conditions.facts_by_evidence() if row_conditions is not None and row_conditions.status == "completed" else {}
    return [
        {
            "document_scope": list(document_key(members[0])),
            "source": str(members[0].get("source") or ""),
            "evidence": [
                {
                    **{
                        key: span[key]
                        for key in (
                            "evidence_id",
                            "source_id",
                            "source",
                            "page",
                            "page_end",
                            "section_path",
                            "source_order",
                            "start",
                            "end",
                            "text",
                            "excerpt_only",
                            "limits",
                            "origin",
                            "tags",
                            "document_background_only",
                            "background_ref",
                        )
                        if key in span
                    },
                    **({"role": background_label(span)} if span.get("document_background_only") else {}),
                    **(
                        {"table_row_judgments": judgments[str(span.get("evidence_id"))]}
                        if str(span.get("evidence_id") or "") in judgments
                        else {}
                    ),
                }
                for span in members
            ],
        }
        for members in document_groups(spans)
    ]


# 判定の根拠が結論の引用元を含まない valid を未確認にしたときの理由（判定の型は変えない）。
BASIS_NOT_CITED_REASON = "判定の根拠が結論の引用した資料を含まないため、推導を確認できない"


def basis_not_cited(inputs: dict[str, Any], audit: ApplicationVerdictAudit) -> list[int]:
    """valid=true なのに basis_evidence_ids が結論の引用元の evidence_id を 1 つも含まない application の index。

    ID の集合の照合だけで、意味は判定しない。引用元の evidence_id が無い（束縛を検証できなかった）application は
    照合できないので対象にしない。index の欠落・重複は apply_application_audit が未確認にする。
    """
    blocked = []
    for application in inputs["applications"]:
        number = int(application["index"])
        rows = [row for row in audit.application_reviews if row.index == number]
        cited = {
            str(binding.get("evidence_id"))
            for position in application["item_indexes"]
            for binding in inputs["items"][position].get("citation_bindings") or ()
            if binding.get("evidence_id")
        }
        if len(rows) == 1 and rows[0].valid and cited and not cited & set(rows[0].basis_evidence_ids):
            blocked.append(number)
    return blocked


def review_applications(
    question: str,
    checked: Sequence[CheckedApplication],
    *,
    evidence_spans: Sequence[dict[str, Any]] | None = None,
    existing_audit: GroundedAudit | None,
    audit_requested: bool,
    parse_text: Callable[..., Any],
    settings: Any,
    provider_id: str | None,
    row_conditions: Any = None,
) -> dict[str, Any]:
    """既存監査を再利用し、監査なしの推導群にだけ1回まとめて判定を問い合わせる。

    ``row_conditions``（表の行の判定。#4349）に決められない行のある表があれば、推導を問い合わせずに保留する
    （``withhold_undetermined_tables``）。決まった表の行の判定は documents に添えて監査に渡す。

    独立の監査のモデルは判定と判定に使った evidence_id だけを返し、表示の理由は判定が確定した推導ごとに
    別の段（application_reason）が日本語だけの入力から作る。理由の有無・成否で判定は変えない。
    """
    if not checked:
        return {"status": "not_applicable", "calls": 0}
    if evidence_spans is None or not evidence_spans:
        apply_application_audit(checked, None, require_complete=True)
        return {
            "status": "incomplete",
            "calls": 0,
            "reason": "evidence_context_missing",
            "results": [dict(entry.assessment) for entry in checked],
        }
    if audit_requested:
        invalid_contract = existing_audit is not None and any(
            row.index not in range(len(checked)) for row in existing_audit.application_reviews
        )
        if invalid_contract:
            existing_audit = None
        apply_application_audit(checked, existing_audit, require_complete=True)
        complete = all(entry.assessment.get("status") != "unknown" for entry in checked)
        return {
            "status": "invalid_contract" if invalid_contract else "reused" if existing_audit is not None and complete else "incomplete",
            "calls": 0,
            "results": [dict(entry.assessment) for entry in checked],
        }
    if withheld := withhold_undetermined_tables(checked, row_conditions):
        for entry in checked:
            entry.assessment = entry.assessment or {"status": "unknown", "reason": TABLE_UNDETERMINED_REASON}
        return {
            "status": "withheld_table_not_determined",
            "calls": 0,
            "withheld": withheld,
            "results": [dict(entry.assessment) for entry in checked],
        }
    candidates = [entry for entry in checked if not entry.reasons]
    if not candidates:
        return {"status": "not_applicable", "calls": 0}
    items = list({id(item): item for entry in candidates for item in entry.entries}.values())
    index = {id(item): position for position, item in enumerate(items)}
    inputs = {
        "question": question,
        "documents": application_documents(evidence_spans, row_conditions),
        "items": [
            {
                "index": position,
                "text": item.item.text,
                "quote": item.item.quote or item.item.text,
                "source": _source_label(item.span),
                "citation_bindings": application_item_citations(item),
            }
            for position, item in enumerate(items)
        ],
        "applications": audit_view(candidates, lambda item: index.get(id(item))),
        "summary": "",
    }
    if premises := confirmed_premise_records(settings.confirmed_premises, settings.confirmed_premise_sources):
        inputs["confirmed_premises"] = premises
    system = resolve_prompt("application.auditSystem", APPLICATION_AUDIT_SYSTEM_PROMPT)
    prompt = json.dumps(inputs, ensure_ascii=False)
    started = monotonic()
    diagnostic: dict[str, Any] = {
        "status": "completed",
        "calls": 0,
        "prompt_chars": len(system) + len(prompt),
        "evidence_spans": len(evidence_spans),
        "evidence_scope": "generation_selected_originals",
    }
    audit = None
    try:
        ensure_model_input(settings, system, prompt, ApplicationVerdictAudit.model_json_schema(), provider_id=provider_id)
        diagnostic["calls"] = 1
        audit = call_with_model_purpose(
            "answer.audit", parse_text, system, prompt, settings, ApplicationVerdictAudit, provider_id=provider_id
        )
        if any(row.index not in range(len(candidates)) for row in audit.application_reviews):
            diagnostic["status"] = "invalid_contract"
            audit = None
    except Exception as exc:
        if propagates(exc):
            raise
        diagnostic.update(status="incomplete", error_type=type(exc).__name__)
    blocked: list[int] = []
    if audit is not None and (blocked := basis_not_cited(inputs, audit)):
        # 引用元を含まない根拠の valid は採用せず未確認にする（理由の段も送らない）。
        diagnostic["basis_not_cited"] = blocked
        audit = audit.model_copy(update={"application_reviews": [row for row in audit.application_reviews if row.index not in blocked]})
    reasons, reason_trace = explain_verdicts(inputs, audit, parse_text=parse_text, settings=settings, provider_id=provider_id)
    diagnostic["calls"] += reason_trace["calls"]
    if unknown_basis := reason_trace.pop("unknown_basis_ids", None):
        diagnostic["unknown_basis_ids"] = unknown_basis
    diagnostic["reason_step"] = reason_trace
    apply_application_audit(candidates, audit, require_complete=True, reasons=reasons)
    for position in blocked:
        candidates[position].assessment["reason"] = BASIS_NOT_CITED_REASON
    diagnostic.update(seconds=round(monotonic() - started, 3), results=[dict(entry.assessment) for entry in candidates])
    if any(entry.assessment.get("status") == "unknown" for entry in candidates) and diagnostic["status"] == "completed":
        diagnostic["status"] = "incomplete"
    return diagnostic
