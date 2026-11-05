"""最終選択後の先頭Chunkと完全先頭頁を、版付きの生成入力へ追加する。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from rag_engine.dependencies import injected_document_background
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answer_records import _stored_chunk_answer_record
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextEvidence, ContextParentEvidence
from rag_engine.retrieval.evidence_selection import evidence_spans
from rag_engine.retrieval.metadata_context import document_context_key

BACKGROUND_ONLY = "document_background_only"


def background_label(span: Mapping[str, Any]) -> str:
    """生成と校正で背景の役割・保存時の完全性を同じ表記にする。"""
    truncated = (span.get("background_ref") or {}).get("truncated")
    completeness = (
        "保存時に欠落のある旧記録"
        if truncated is True
        else "完全性が記録されていない旧記録"
        if truncated is not False
        else "原文の補助情報"
    )
    return f"文書の先頭ページ背景（{completeness}・Chunkの引用対象外、参照 {span['source_id']}）"


def without_embedded_background(context: AnswerContext) -> AnswerContext:
    """文字予算で切られる補助metadataから先頭頁を外し、生成専用入力に集約する。"""
    copies: dict[int, AnswerRecord] = {}

    def clean(record: AnswerRecord) -> AnswerRecord:
        if id(record) in copies:
            return copies[id(record)]
        document = record.metadata.get("document")
        result = record
        if isinstance(document, dict) and "first_page_context" in document:
            result = replace(
                record,
                metadata={**record.metadata, "document": {key: value for key, value in document.items() if key != "first_page_context"}},
            )
        copies[id(record)] = result
        return result

    return replace(
        context,
        records=[clean(record) for record in context.records],
        evidence=tuple(replace(item, record=clean(item.record)) for item in context.evidence),
        evidence_tree=tuple(
            replace(
                parent, record=clean(parent.record), children=tuple(replace(child, record=clean(child.record)) for child in parent.children)
            )
            for parent in context.evidence_tree
        ),
    )


def _page_spans(template: dict[str, Any], page: Mapping[str, Any], selected: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    text = str(page.get("text") or "")
    if not text:
        return [], 0
    own = [value for value in selected if value.get("document_scope") == template.get("document_scope")]
    excluded: list[tuple[int, int]] = []
    for unit in page.get("source_units") or ():
        if not isinstance(unit, Mapping):
            continue
        start, end, key = unit.get("start"), unit.get("end"), unit.get("record_id")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            continue
        if key and any(key in value.get("source_record_ids", ()) and text[start:end] in value["text"] for value in own):
            excluded.append((start, end))
    # 原文位置と同一要素の照合がない文字の一致だけでは、繰り返された別の記載を消さない。
    covered = sorted(excluded)
    ranges, cursor = [], 0
    for start, end in covered:
        if cursor < start:
            ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < len(text):
        ranges.append((cursor, len(text)))
    digest = hashlib.sha256(text.encode()).hexdigest()
    source_id = "first-page:" + hashlib.sha256((repr(template["document_scope"]) + digest).encode()).hexdigest()
    values = []
    for start, end in ranges:
        if not text[start:end].strip():
            continue
        uid = "E" + hashlib.sha256(f"{source_id}:{start}:{end}".encode()).hexdigest()[:20]
        values.append(
            {
                "evidence_id": uid,
                "label": uid,
                "source_id": source_id,
                "source": template.get("source", ""),
                "document_scope": list(template["document_scope"]),
                "source_order": (-1, 0, start),
                "text": text[start:end],
                "start": start,
                "end": end,
                "page": 1,
                "page_end": 1,
                "tags": [],
                "pinned": False,
                "section_path": [],
                "source_context": {},
                "origin": "document_background",
                BACKGROUND_ONLY: True,
                "background_ref": {
                    "content_sha256": digest,
                    "record_ids": list(page.get("record_ids") or ()),
                    "source_units": list(page.get("source_units") or ()),
                    "truncated": page.get("truncated"),
                },
            }
        )
    return values, len(excluded)


class DocumentBackgroundReader:
    def __init__(self) -> None:
        self.pages: dict[tuple[str, ...], Mapping[str, Any]] = {}
        self.rounds: list[dict[str, Any]] = []

    def remember(self, context: AnswerContext) -> None:
        for record in [*context.records, *(child.record for parent in context.evidence_tree for child in parent.children)]:
            document = record.metadata.get("document")
            if isinstance(document, Mapping) and isinstance(page := document.get("first_page_context"), Mapping):
                self.pages.setdefault(document_context_key(record), page)

    def append(
        self, question: str, context: AnswerContext, spans: list[dict[str, Any]]
    ) -> tuple[AnswerContext, list[dict[str, Any]], dict[str, Any]]:
        from rag_engine.generation.grounded import tag_spans

        rows = injected_document_background(spans=spans)
        provided = rows is not None
        if rows is None:
            rows = [
                {
                    "document_scope": list(key),
                    "first_page": value,
                    "first_chunk_status": "reader_not_available",
                    "first_page_status": (
                        "legacy_incomplete"
                        if value.get("truncated") is True
                        else "completeness_not_recorded"
                        if value.get("status") == "available" and value.get("truncated") is not False
                        else value.get("status", "not_available")
                    ),
                }
                for key, value in self.pages.items()
            ]
        originals = list(spans)
        wanted = {tuple(value["document_scope"]): value for value in originals}
        returned = {tuple(row.get("document_scope") or ()) for row in rows}
        rows.extend(
            {
                "document_scope": list(key),
                "first_chunk_status": "reader_not_available" if not provided else "scope_identity_not_available",
                "first_page_status": "not_recorded",
            }
            for key in wanted
            if key not in returned
        )
        records, trees, evidence = list(context.records), list(context.evidence_tree), list(context.evidence)
        if not trees:
            trees = [
                ContextParentEvidence(
                    record, "child_fallback", "selected_context", (ContextChildEvidence(record, "retrieved_anchor", "selected_context"),)
                )
                for record in records
            ]
        outcomes = []
        for row in rows:
            key = tuple(row.get("document_scope") or ())
            template = wanted.get(key)
            if template is None:
                continue
            detail = {
                field: row[field]
                for field in (
                    "document_scope",
                    "page_scope",
                    "first_chunk_status",
                    "first_page_status",
                    "first_page_reconstructed_from",
                    "reused",
                )
                if field in row
            }
            first = row.get("first_chunk")
            if first is not None:
                record = _stored_chunk_answer_record(first)
                record = replace(record, text=record.body_text or record.text, metadata={**record.metadata, "document_first_chunk": True})
                uid = record.chunk_uid or record.id
                included = any(
                    uid == value.get("source_id")
                    and value["text"] == record.text
                    or any(child.get("id") == uid for child in value.get("child_ranges", ()))
                    and record.text in value["text"]
                    for value in spans
                    if tuple(value.get("document_scope") or ()) == key
                )
                if document_context_key(record) != key:
                    detail["first_chunk_status"] = "version_mismatch"
                elif included:
                    detail["first_chunk_status"] = "already_included"
                else:
                    added = evidence_spans(question, [record], preserve_records=True)
                    spans.extend(added)
                    if not any((item.chunk_uid or item.id) == uid for item in records):
                        records.append(record)
                        evidence.append(ContextEvidence(record, "supporting", "document_first_chunk"))
                        trees.append(
                            ContextParentEvidence(
                                record,
                                "child_fallback",
                                "document_first_chunk",
                                (ContextChildEvidence(record, "same_page_context", "document_first_chunk"),),
                            )
                        )
                    detail.update(first_chunk_id=uid, first_chunk_added_spans=len(added))
            page = row.get("first_page")
            if isinstance(page, Mapping):
                from rag_engine.generation.answer_images import prompt_injection_warnings_for_records

                warning_record = AnswerRecord(
                    id="first-page",
                    engine="background",
                    engine_label="Background",
                    page=1,
                    seq_no=0,
                    category="Text",
                    text=str(page.get("text") or ""),
                    source=str(template.get("source") or ""),
                )
                detail["first_page_warnings"] = prompt_injection_warnings_for_records([warning_record])
                values, excluded = _page_spans(template, page, spans)
                spans.extend(values)
                detail.update(
                    first_page_chars=len(str(page.get("text") or "")),
                    first_page_added_spans=len(values),
                    first_page_deduplicated_units=excluded,
                    first_page_source_ids=list(page.get("record_ids") or ()),
                    first_page_input_ranges=[
                        {"source_id": value["source_id"], "start": value["start"], "end": value["end"]} for value in values
                    ],
                    first_page_content_sha256=hashlib.sha256(str(page.get("text") or "").encode()).hexdigest(),
                )
            outcomes.append(detail)
        diagnostic = {"reader_available": provided, "selected_documents": len(wanted), "documents": outcomes}
        self.rounds.append(diagnostic)
        return (
            replace(context, records=records, evidence=tuple(evidence), evidence_tree=tuple(trees)),
            tag_spans(question, spans),
            diagnostic,
        )
