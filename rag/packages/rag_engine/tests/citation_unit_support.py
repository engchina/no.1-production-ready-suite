"""合成テストの応答は、実際の入力に表示された単位 ID から選ぶ。"""

import json
import re
from typing import Any

from pydantic import BaseModel


def repair_output(schema: type[BaseModel], prompt: str, *ranges: tuple[int, int, str | None, int, int]) -> Any:
    """テストが指定した位置の、入力に存在する ID を返却契約で検証する。"""
    payload = json.loads(prompt.split("\n", 1)[1])
    sources = {row["snapshot_id"]: re.findall(r"\[(U[0-9a-f]{24})\]", row["text"]) for row in payload["citation_sources"]}
    targets = {(row["item_index"], row["citation_index"]): row for row in payload["targets"]}
    updates = []
    for item_index, citation_index, label, first, last in ranges:
        target = targets[(item_index, citation_index)]
        if label is not None:
            assert target["evidence_id"] == label
        ids = sources[target["snapshot_id"]]
        assert 1 <= first <= last <= len(ids)
        updates.append({"item_index": item_index, "citation_index": citation_index, "unit_ids": ids[first - 1 : last]})
    return schema.model_validate({"ranges": updates})


def selected_answer_payload(
    unit_ids: list[str], *, text: str = "合成の回答です。", evidence_ids: tuple[str, ...] = ("E1",)
) -> dict[str, Any]:
    """新規の送達ID契約を直接使う、1要点の合成応答。"""
    return {
        "response_kind": "answered",
        "request_coverage": [
            {
                "request_id": "Q1",
                "evidence_scope": [
                    {"evidence_id": label, "target": "合成の対象", "relation": "direct", "link": None} for label in evidence_ids
                ],
            }
        ],
        "answer_parts": [
            {
                "request_id": "Q1",
                "requirement": "合成の要求",
                "answer": {
                    "kind": "answer",
                    "basis": {"unit_ids": unit_ids},
                    "reasoning": "選択した原文の記載を示す。",
                    "result": text,
                },
            }
        ],
        "summary_part_indexes": [],
        "confidence": "low",
    }


def generated_output(schema: type[BaseModel], prompt: str, *, text: str, first: int = 1, last: int = 1) -> Any:
    """単一根拠の合成応答。本文の unit ID をそのまま使い、自由な原文位置を返さない。"""
    units = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
    assert 1 <= first <= last <= len(units)
    if getattr(schema, "_uses_full_answer", False):
        return schema.model_validate(
            {
                "answer_body": text + "[C1]",
                "response_kind": "answered",
                "confidence": "medium",
                "citations": [{"unit_ids": units[first - 1 : last]}],
            }
        )
    if getattr(schema, "_uses_typed_answer_parts", False):
        return schema.model_validate(selected_answer_payload(units[first - 1 : last], text=text))
    return schema.model_validate(
        {
            "items": [{"kind": "operation", "request_id": "Q1", "text": text, "citations": [{"unit_ids": units[first - 1 : last]}]}],
            "summary": "合成の要約",
            "confidence": "medium",
        }
    )


def without_unit_annotations(prompt: str) -> str:
    """原文の分割・空白の既存回帰は ID 注記だけを外して比較する。"""
    return re.sub(r"(?m)^(\d+\| )\[U[0-9a-f]{24}\] ", r"\1", prompt)


def generated_fixture(schema: type[BaseModel], prompt: str, payload: dict[str, Any]) -> Any:
    """既存の合成 fixture の範囲・引用文を、実提示 ID を選ぶ合成応答へ移す。"""
    from copy import deepcopy

    if getattr(schema, "_uses_full_answer", False):
        # 旧 fixture は履歴の形式で再生する。新規 wire の検証は test_full_answer_wire で行う。
        from rag_engine.generation import grounded
        from rag_engine.generation.citation_wire import canonical_generation_draft

        spans = [pair[0] for pair in (grounded._CITATION_VIEWS.get() or {}).values()]
        catalog = grounded.generation_unit_catalog(spans, sent_only=True)
        old = legacy_generation_output_model("", unit_catalog=catalog)
        output = old.model_validate(payload) if "answer_parts" in payload else generated_fixture(old, prompt, payload)
        return canonical_generation_draft(output, unit_catalog=catalog)

    blocks = {}
    for label, body in re.findall(r"(?ms)^\[(E\d+)\][^\n]*\n(.*?)^\[/\1\]", prompt):
        units = re.findall(r"(?ms)^\d+\| \[(U[0-9a-f]{24})\] (.*?)(?=^\d+\| \[U[0-9a-f]{24}\]|\Z)", body)
        blocks[label] = [(uid, text.rstrip("\n")) for uid, text in units]
    result = deepcopy(payload)
    for item in result.get("items", []):
        selected = []
        for citation in item.get("citations", []):
            if "unit_ids" in citation:
                selected.append(citation)
                continue
            if citation.get("quote"):
                candidates = [uid for units in blocks.values() for uid, text in units if citation["quote"] in text]
                assert len(candidates) == 1, "合成 fixture の引用は入力の一単位に一意に対応する必要があります。"
            else:
                units = blocks[citation["evidence_id"]]
                first, last = citation["line_start"], citation["line_end"]
                assert 1 <= first <= last <= len(units)
                candidates = [uid for uid, text in units[first - 1 : last]]
            selected.append({"unit_ids": candidates})
        if "citations" in item:
            item["citations"] = selected
    if getattr(schema, "_uses_typed_answer_parts", False):
        result.setdefault("response_kind", "answered")
        parts = []
        original_items = result.pop("items", [])
        for position, item in enumerate(original_items):
            if item["kind"] == "gap":
                answer = {"kind": "gap", "text": item["text"]}
            else:
                basis = item.get("derivation") or {}
                sources = [original_items[index] for index in basis.get("item_indexes", [])] if basis else [item]
                ids = list(
                    dict.fromkeys(uid for source in sources for citation in source.get("citations", []) for uid in citation["unit_ids"])
                )
                assert ids, "回答可能な要点のfixtureには明示した出典が必要です。"
                answer = {
                    "kind": "answer",
                    "basis": {"unit_ids": ids},
                    "reasoning": basis.get("reasoning") or "選択した原文の記載を示す。",
                    "result": item["text"],
                }
            parts.append({"request_id": item.get("request_id", "Q1"), "requirement": f"合成の要点{position + 1}", "answer": answer})
        if not parts:
            parts = [{"request_id": "Q1", "requirement": "合成の要求", "answer": {"kind": "gap", "text": "資料が不足しています。"}}]
        selected = result.pop("summary_item_indexes", [])
        summary = result.pop("summary", "")
        result.pop("applications", None)
        result.pop("summary_application_indexes", None)
        result["answer_parts"] = parts
        result["summary_part_indexes"] = selected[:2] if selected else [0] if summary else []
    return schema.model_validate(result)


def canonical_fixture(payload: dict[str, Any]) -> Any:
    """意味判定の既存 fixture を、指名した根拠内の明示的な単位選択へ移す。

    本番の引用解決では使わない。存在しない原文を指定した fixture は不正範囲を返すモデル桩として再生する。
    """
    from rag_engine.generation import grounded
    from rag_engine.generation.citation_unit_ids import BoundUnitRange, EvidenceUnitSnapshot
    from rag_engine.models.llm import GroundedDraft

    result = GroundedDraft.model_validate(payload)
    spans = [span for span, _view in (grounded._CITATION_VIEWS.get() or {}).values()]
    for item in result.items:
        for citation in item.citations:
            if not citation.quote or citation.lines:
                continue
            candidates = grounded._line_citation_spans(item.model_copy(update={"citations": [citation]}), spans)
            if len(candidates) != 1:
                continue
            span = candidates[0]
            view = grounded._sent_view(span)
            needle, _ = grounded._canonical(citation.quote)
            haystack, positions = grounded._canonical(view.text)
            start = haystack.find(needle) if needle else -1
            if start < 0:
                citation.lines = "999999"
                citation._wire_range = (999999, 999999)
                continue
            begin, end = positions[start], positions[start + len(needle) - 1] + 1
            selected = [i for i, (a, b) in enumerate(view.units) if a < end and begin < b]
            assert selected
            snapshot = EvidenceUnitSnapshot.create(span, view.units, view.contract_version)
            citation._unit_binding = BoundUnitRange(snapshot, selected[0], selected[-1])
            citation._wire_range = (selected[0] + 1, selected[-1] + 1)
            citation.lines = str(selected[0] + 1) if selected[0] == selected[-1] else f"{selected[0] + 1}-{selected[-1] + 1}"
    return result


def legacy_generation_output_model(question, *, unit_catalog):
    """旧形式の保存記録の再生専用。現在のオンライン生成の契約とは区別する。"""
    from rag_engine.generation.answer_parts_wire import answer_parts_model
    from rag_engine.generation.citation_wire import generation_draft_wire_model
    from rag_engine.generation.grounded import generation_request_ids, generation_subquestions

    return answer_parts_model(
        generation_draft_wire_model(),
        unit_catalog,
        request_ids=generation_request_ids(question),
        subquestions=generation_subquestions(question),
    )
