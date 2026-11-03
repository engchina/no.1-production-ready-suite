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


def generated_output(schema: type[BaseModel], prompt: str, *, text: str, first: int = 1, last: int = 1) -> Any:
    """単一根拠の合成応答。本文の unit ID をそのまま使い、自由な原文位置を返さない。"""
    units = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)
    assert 1 <= first <= last <= len(units)
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
    return schema.model_validate(result)
