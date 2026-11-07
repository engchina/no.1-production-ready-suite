"""公開した推導結果だけを、元の摘要の選択と不変の引用へ結んで複写する。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.citation_unit_ids import BoundUnitRange

if TYPE_CHECKING:
    from rag_engine.generation.applications import CheckedApplication
    from rag_engine.generation.grounded import CheckedItem, Round

APPLICATION_SUMMARY_WITHHELD = "推導の条件と結論を確認できないため摘要を保留"
PROJECTION_INVALID = "摘要が選択した出力の依存を最終本文で確認できない"


@dataclass(frozen=True)
class SummaryProjection:
    """原草稿を上書きせず、選択した出力と原文の状態を最終段まで保持する。"""

    text: str
    original_summary: str
    original_indexes: tuple[int, ...]
    indexes: tuple[int, ...]
    applications: tuple[tuple[CheckedApplication, str], ...]
    basis: tuple[tuple[CheckedItem, str], ...]
    context: str


def _context_state(current: Round) -> str:
    """意味判定は追加せず、公開までに要求との対応が変わっていないかを確かめる。"""
    return json.dumps(
        {
            "coverage": [row.model_dump() for row in current.draft.request_coverage],
            "answer_parts": [part.model_dump() for part in current.draft.answer_parts],
            "requests": {
                key: getattr(current, key)
                for key in (
                    "expected_request_ids",
                    "returned_request_ids",
                    "missing_request_ids",
                    "invalid_request_ids",
                    "invalid_request_result_ids",
                    "duplicate_request_ids",
                )
            },
            "semantic_audit_policy": current.timing.get("semantic_audit_policy"),
            "audit_cut": current.audit_cut,
            "review_execution": current.review_execution,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _basis_state(entry: CheckedItem) -> str:
    from rag_engine.generation.grounded import item_citations, summary_basis_snapshot

    return json.dumps(
        [
            summary_basis_snapshot([entry])[0][1],
            [
                {
                    "scope": {
                        key: (cited.span or {}).get(key)
                        for key in ("document_scope", "start", "end", "document_id", "recipe_id", "chunk_set_id")
                    },
                    "binding": [binding.snapshot.snapshot_id, binding.snapshot.span_json, binding.first, binding.last]
                    if isinstance(binding := cited.item.citations[0]._unit_binding if cited.item.citations else None, BoundUnitRange)
                    else None,
                }
                for cited in item_citations(entry)
            ],
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _application_state(application: CheckedApplication) -> str:
    from rag_engine.generation.grounded import application_summary_snapshot

    return json.dumps(
        [application_summary_snapshot([application])[0][1], [(id(entry), entry.draft_index) for entry in application.entries]],
        ensure_ascii=False,
    )


def _eligible(current: Round, index: int, application: CheckedApplication) -> bool:
    from rag_engine.generation.grounded import item_citations

    if (
        application.source != "draft"
        or not application.published
        or application.marks
        or application.assessment.get("status") != "valid"
        or not 0 <= index < len(current.draft.applications)
        or application.application != current.draft.applications[index]
        or not any(application is final for final in current.applications)
        or [entry.draft_index for entry in application.entries] != list(dict.fromkeys(application.application.item_indexes))
    ):
        return False
    parts = [part for part in current.draft.answer_parts if index in part.application_indexes]
    if (
        len(parts) != 1
        or parts[0].result_kind != "derived"
        or parts[0].application_indexes != [index]
        # 導出の要点が持つ item は、この導出の basis の原文の引用だけ。
        or not set(parts[0].item_indexes) <= set(application.application.item_indexes)
    ):
        return False
    for entry in application.entries:
        if (
            not any(entry is final for final in current.checked)
            or not entry.confirmed
            or entry.carried
            or entry.withdrawn
            or entry.unmatched
            or entry.draft_index is None
            or not 0 <= entry.draft_index < len(current.draft.items)
            or any(
                getattr(entry.item, key) != getattr(current.draft.items[entry.draft_index], key)
                for key in ("text", "kind", "request_id", "applies", "condition")
            )
        ):
            return False
        for cited in item_citations(entry):
            if not cited.span or not cited.confirmed or cited.unmatched or len(cited.item.citations) != 1:
                return False
            binding = cited.item.citations[0]._unit_binding
            if not isinstance(binding, BoundUnitRange) or cited.item.quote != binding.text:
                return False
            original = binding.snapshot.span()
            if any(
                original.get(key) != cited.span.get(key)
                for key in (
                    "evidence_id",
                    "source_id",
                    "text",
                    "document_scope",
                    "document_id",
                    "recipe_id",
                    "chunk_set_id",
                    "start",
                    "end",
                    "version",
                    "document_version",
                    "source_run_id",
                    "chunk_uid",
                )
            ):
                return False
    return True


def projection_valid(current: Round) -> bool:
    projection = current.summary_projection
    if (
        projection is None
        or current.summary_reason
        or current.summary != projection.text
        or current.draft.summary != projection.original_summary
    ):
        return False
    if (
        _context_state(current) != projection.context
        or not current.draft._composed_summary
        or tuple(current.draft.summary_application_indexes) != projection.original_indexes
        or current.draft.summary_item_indexes
    ):
        return False
    return all(
        _eligible(current, index, application) and _application_state(application) == state
        for index, (application, state) in zip(projection.indexes, projection.applications, strict=True)
    ) and all(_basis_state(entry) == state for entry, state in projection.basis)


def invalidate_projection(current: Round) -> Round:
    return replace(
        current,
        summary=answer_labels().neutral_summary,
        summary_reason=PROJECTION_INVALID,
        summary_warning="",
        summary_projection=None,
        timing={**current.timing, "summary_projection": {"status": "invalidated"}},
    )


def recover_partial_summary(current: Round) -> Round:
    if current.summary_projection is not None:
        return current if projection_valid(current) else invalidate_projection(current)
    draft = current.draft
    indexes = tuple(draft.summary_application_indexes)
    if (
        current.summary != answer_labels().neutral_summary
        or current.summary_reason != APPLICATION_SUMMARY_WITHHELD
        or not draft._composed_summary
        or draft.summary_item_indexes
        or not indexes
        or len(indexes) != len(set(indexes))
        or any(not 0 <= index < len(draft.applications) for index in indexes)
        or draft.summary != "\n".join(draft.applications[index].result for index in indexes)
    ):
        return current
    selected = []
    for index in indexes:
        matches = [
            application
            for application in current.applications
            if application.source == "draft" and application.application == draft.applications[index]
        ]
        if len(matches) == 1 and _eligible(current, index, matches[0]):
            selected.append((index, matches[0]))
    if not selected or len(selected) == len(indexes):
        return current
    applications = [application for _, application in selected]
    basis = list({id(entry): entry for application in applications for entry in application.entries}.values())
    text = "\n".join(application.application.result for application in applications)
    projection = SummaryProjection(
        text,
        draft.summary,
        indexes,
        tuple(index for index, _ in selected),
        tuple((application, _application_state(application)) for application in applications),
        tuple((entry, _basis_state(entry)) for entry in basis),
        _context_state(current),
    )
    return replace(
        current,
        summary=text,
        summary_reason="",
        summary_warning="",
        summary_projection=projection,
        timing={
            **current.timing,
            "summary_projection": {
                "status": "reconstructed",
                "application_indexes": list(projection.indexes),
                "excluded_application_indexes": [index for index in indexes if index not in projection.indexes],
            },
        },
    )
