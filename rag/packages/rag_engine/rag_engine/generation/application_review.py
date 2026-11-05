"""原文の位置照合と分離した、推導結果だけの公開門。"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError
from time import monotonic
from typing import Any

from pr_backend_core.observability.model_purpose import call_with_model_purpose

from rag_engine.generation.applications import (
    APPLICATION_AUDIT_SYSTEM_PROMPT,
    CheckedApplication,
    _source_label,
    apply_application_audit,
    audit_view,
)
from rag_engine.generation.confirmed_context import confirmed_premise_records
from rag_engine.generation.document_inputs import document_groups, document_key
from rag_engine.generation.input_budget import ensure_model_input
from rag_engine.models.llm import ApplicationAudit, GroundedAudit
from rag_engine.resources.deadline import DeadlineExceeded, current_deadline, deadline_cut_short
from rag_engine.resources.runtime import resolve_prompt


def application_documents(spans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """生成へ選んだ原文全体を文書・版ごとに保持し、引用抜粋へ縮めない。"""
    return [
        {
            "document_scope": list(document_key(members[0])),
            "source": str(members[0].get("source") or ""),
            "evidence": [
                {
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
                    )
                    if key in span
                }
                for span in members
            ],
        }
        for members in document_groups(spans)
    ]


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
) -> dict[str, Any]:
    """既存監査を再利用し、監査なしの推導群にだけ1回まとめて問い合わせる。"""
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
    candidates = [entry for entry in checked if not entry.reasons]
    if not candidates:
        return {"status": "not_applicable", "calls": 0}
    items = list({id(item): item for entry in candidates for item in entry.entries}.values())
    index = {id(item): position for position, item in enumerate(items)}
    inputs = {
        "question": question,
        "documents": application_documents(evidence_spans),
        "items": [
            {"index": position, "text": item.item.text, "quote": item.item.quote or item.item.text, "source": _source_label(item.span)}
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
        ensure_model_input(settings, system, prompt, ApplicationAudit.model_json_schema(), provider_id=provider_id)
        diagnostic["calls"] = 1
        audit = call_with_model_purpose("answer.audit", parse_text, system, prompt, settings, ApplicationAudit, provider_id=provider_id)
        if any(row.index not in range(len(candidates)) for row in audit.application_reviews):
            diagnostic["status"] = "invalid_contract"
            audit = None
    except Exception as exc:
        deadline = current_deadline()
        status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
        if (
            isinstance(exc, (PermissionError, CancelledError))
            or status in {401, 403}
            or (deadline is not None and (deadline.cancelled or (deadline.expired and not deadline_cut_short())))
            or (isinstance(exc, DeadlineExceeded) and not deadline_cut_short())
        ):
            raise
        diagnostic.update(status="incomplete", error_type=type(exc).__name__)
    apply_application_audit(candidates, audit, require_complete=True)
    diagnostic.update(seconds=round(monotonic() - started, 3), results=[dict(entry.assessment) for entry in candidates])
    if any(entry.assessment.get("status") == "unknown" for entry in candidates) and diagnostic["status"] == "completed":
        diagnostic["status"] = "incomplete"
    return diagnostic
