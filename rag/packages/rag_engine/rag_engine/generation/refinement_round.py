"""既存の回数・期限内で、選別した原文による任意の再回答を試す。"""

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, cast

from rag_engine.generation.evidence_refinement import RefinementStrategy, evidence_source_keys, select_refinement_evidence


@dataclass(frozen=True)
class RefinementResult:
    candidate: Any | None
    spans: tuple[dict[str, Any], ...]
    adopted: bool
    diagnostics: dict[str, Any]


def citation_observations(current: Any) -> list[dict[str, Any]]:
    """実際の引用位置の検査だけを選別へ渡し、意味支持と混同しない。"""
    from rag_engine.generation import grounded

    result = []
    for entry in current.checked:
        for own in grounded.item_citations(entry):
            proof = own.citation_resolution
            result.append(
                {
                    "evidence_id": own.span["evidence_id"] if own.span else proof.get("resolved_evidence_id", ""),
                    "citation_validity": "valid"
                    if proof.get("authenticity") == "verified" and proof.get("status") == "extracted"
                    else "unknown",
                    "content_usability": "unusable"
                    if proof.get("reason_code") in {"citation_heading_only", "citation_content_unusable"}
                    else proof.get("content_usability", "unknown"),
                    "claim_support": "unknown",
                    "applicability": "unknown",
                }
            )
    return result


def protected_sources(
    current: Any, knowledge: Any, known_gaps: Sequence[str]
) -> tuple[list[tuple[str, ...]], list[tuple[str, ...]], list[str]]:
    """明示された必須・不明の対応を保持する。未引用だから不要とは推定しない。"""
    required: list[tuple[str, ...]] = []
    uncertain: list[tuple[str, ...]] = []
    unresolved: list[str] = []
    optional = set(knowledge.guide_optional_steps) if knowledge else set()
    for step_id, _title, sources in knowledge.guide_steps if knowledge else ():
        (uncertain if step_id in optional else required).append(tuple(sources))
        if not sources:
            unresolved.append(step_id)
    coverage = {row.request_id: row for row in current.draft.request_coverage}
    for request in current.requests:
        if request.get("kind") == "context":
            continue
        row = coverage.get(request["id"])
        sources = tuple(
            dict.fromkeys(
                [
                    *(row.evidence_ids if row else ()),
                    *((entry.evidence_id for entry in row.evidence_scope if entry.relation in {"direct", "linked"}) if row else ()),
                ]
            )
        )
        if sources:
            required.append(sources)
        else:
            unresolved.append(request["id"])
    if known_gaps:
        unresolved.extend(str(gap) for gap in known_gaps)
    return required, uncertain, unresolved


def review_feedback(current: Any) -> dict[str, Any]:
    """前回答は検査対象としてのみ渡す。削除した根拠のID・原文を混ぜない。"""
    return {
        "unverified_previous_draft": {
            "source_kind": "generated_draft_not_evidence",
            "items": [{"request_id": entry.item.request_id, "text": entry.item.text} for entry in current.checked if entry.published],
        }
    }


def retention_check(candidate: Any, current: Any, selection: Mapping[str, Any]) -> tuple[bool, str]:
    """件数や短さだけで改善としない。対応を確定できない書換えは前回答へ戻す。"""
    from rag_engine.generation import grounded

    if (
        selection.get("candidate_missing_required_groups")
        or selection.get("candidate_missing_uncertain_groups")
        or selection.get("unresolved_requirement_ids")
    ):
        return False, "required_evidence_not_preserved"
    if candidate.audit_cut:
        return False, "review_incomplete"

    def normalized(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))

    published = [entry for entry in candidate.checked if entry.published and entry.item.kind != "gap"]
    body = normalized("\n".join(entry.item.text for entry in published))
    if not body:
        return False, "no_answer_content"
    cursor = 0
    previous_body = normalized("\n".join(entry.item.text for entry in current.checked if entry.published and entry.item.kind != "gap"))
    conditions = normalized("\n".join(entry.item.condition + entry.item.text for entry in published))
    previous_conditions = normalized("\n".join(entry.item.condition + entry.item.text for entry in current.checked if entry.published))
    if normalized(candidate.summary) != normalized(current.summary):
        return False, "summary_change_support_unverified"
    if any(entry.item.condition and normalized(entry.item.condition) not in previous_conditions for entry in published):
        return False, "new_condition_support_unverified"
    for entry in current.checked:
        if entry.confirmed and entry.item.kind != "gap":
            position = body.find(normalized(entry.item.text), cursor)
            if position < 0:
                return False, "previous_content_retention_unverified"
            cursor = position + len(normalized(entry.item.text))
            if entry.item.condition and normalized(entry.item.condition) not in conditions:
                return False, "previous_condition_retention_unverified"
    if any(normalized(entry.item.text) not in previous_body for entry in published):
        return False, "new_claim_support_unverified"
    gaps = {normalized(entry.item.text) for entry in candidate.checked if entry.item.kind == "gap"}
    if any(normalized(entry.item.text) not in gaps for entry in current.checked if entry.item.kind == "gap"):
        return False, "previous_gap_resolution_unverified"
    return (True, "basic_checks_improved") if grounded.better(candidate, current) else (False, "no_verified_improvement")


def refine_answer(
    current: Any,
    spans: Sequence[dict[str, Any]],
    *,
    strategy: str,
    knowledge: Any,
    known_gaps: Sequence[str],
    next_round: int,
    max_rounds: int,
    budget: Any,
    generate: Callable[[Sequence[dict[str, Any]], dict[str, Any]], Any],
    images_present: bool = False,
) -> RefinementResult:
    """追加の選別用モデルを呼ばず、任意の再生成だけを既存予算内で試す。"""
    original = tuple(spans)
    trace: dict[str, Any] = {
        "strategy": strategy,
        "execution": "not_run",
        "additional_generation_calls": 0,
        "semantic_verification": "not_performed_by_selection",
        "context_scope": "original_retrieval_history",
        "final_generation_evidence_scope": "initial_spans",
    }

    def skipped(reason: str) -> RefinementResult:
        return RefinementResult(None, original, False, {**trace, "stop_reason": reason})

    if strategy == "legacy":
        return skipped("disabled")
    if next_round >= max_rounds:
        return skipped("correction_limit")
    if images_present:
        return skipped("image_selection_unverified")
    required, uncertain, unresolved = protected_sources(current, knowledge, known_gaps)
    selection = select_refinement_evidence(
        spans,
        citation_observations(current),
        strategy=cast(RefinementStrategy, strategy),
        required_source_groups=required,
        uncertain_source_groups=uncertain,
        unresolved_requirement_ids=unresolved,
    )
    trace["selection"] = selection.diagnostics
    if selection.diagnostics.get("status") != "selected":
        return skipped(str(selection.diagnostics.get("reason") or "no_reduction"))
    if not budget.allows_round(next_round):
        return skipped("deadline")
    trace["additional_generation_calls"] = None
    try:
        with budget.round():
            candidate = generate(selection.spans, review_feedback(current))
    except PermissionError:
        raise
    except Exception as exc:
        return RefinementResult(
            None, original, False, {**trace, "execution": "error", "error_type": type(exc).__name__, "initial_answer_retained": True}
        )
    adopted, reason = retention_check(candidate, current, selection.diagnostics)
    trace.update(
        execution="completed",
        additional_generation_calls=candidate.timing.get("generation_calls", 1),
        adoption=reason,
        adopted=adopted,
        initial_answer_retained=not adopted,
        final_generation_evidence_scope="selected_spans" if adopted else "initial_spans",
    )
    budget.note(candidate, {}, accepted=adopted)
    return RefinementResult(candidate, selection.spans, adopted, trace)


def prompt_context_for(context: Any, spans: Sequence[dict[str, Any]]) -> Any:
    """原文をコピーせず、選択範囲だけの安全確認用レコードを作る。"""
    from rag_engine.generation.answer_models import AnswerRecord

    records = [
        AnswerRecord(
            id=str(span["source_id"]),
            engine="",
            engine_label="",
            page=int(span.get("page") or 1),
            seq_no=index,
            category="Text",
            text=span["text"],
            source=span.get("source", ""),
        )
        for index, span in enumerate(spans)
    ]
    return replace(
        context, records=records, text="", evidence=(), evidence_tree=(), expansion_records=(), ranked_candidates=(), deferred_records=()
    )


def project_knowledge(knowledge: Any, spans: Sequence[dict[str, Any]]) -> Any:
    """業務の指示は保持し、根拠対応だけを今回提示する集合へ束縛する。"""
    if knowledge is None:
        return None
    keys = set().union(*(evidence_source_keys(span) for span in spans))
    return replace(
        knowledge,
        guide_steps=tuple(
            (step_id, title, tuple(source for source in sources if source in keys)) for step_id, title, sources in knowledge.guide_steps
        ),
    )


def project_crag_view(view: dict[str, Any] | None) -> dict[str, Any] | None:
    """再束縛できない評価原文を再回答へ混ぜず、未確認の観点は残す。"""
    if view is None:
        return None
    result = deepcopy(view)
    for key in ("aspect_checks", "answerable_parts"):
        for row in result.get(key, ()):
            row["evidence"] = [value for value in row.get("evidence", ()) if value.get("evidence_ids")]
            if row.get("status") in {"supported", "data_confirmation"} and not row["evidence"]:
                row["status"] = "unknown"
    result["generation_usable"] = any(row.get("status") == "supported" for row in result.get("answerable_parts", ()))
    return result
