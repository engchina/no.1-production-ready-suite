"""既存の合成応答を、実提示IDを使う要点単位の返却契約へ移す。"""

import re
from copy import deepcopy
from typing import Any

from pydantic import BaseModel


def generation_result(schema: type[BaseModel], prompt: str, payload: dict[str, Any]) -> BaseModel:
    if not getattr(schema, "_uses_typed_answer_parts", False) or "answer_parts" in payload:
        return schema.model_validate(payload)
    result = deepcopy(payload)
    items = result.pop("items", [])
    parts: list[dict[str, Any]] = []
    for position, item in enumerate(items):
        request_id = item.get("request_id") or "Q1"
        if item["kind"] == "gap":
            answer = {"kind": "gap", "text": item["text"]}
        else:
            ids = list(
                dict.fromkeys(uid for citation in item["citations"] for uid in citation["unit_ids"])
            )
            assert ids, "合成の回答には提示済み原文IDが必要です。"
            answer = {
                "kind": "answer",
                "basis": {"unit_ids": ids},
                "reasoning": "選択した原文の記載を示す。",
                "result": item["text"],
            }
        parts.append(
            {"request_id": request_id, "requirement": f"合成の要点{position + 1}", "answer": answer}
        )
    assert parts, "空の成功ではなく、合成の不足要点を明記してください。"
    if "request_coverage" not in result:
        blocks = re.findall(r"(?ms)^\[(E\d+)\][^\n]*\n(.*?)^\[/\1\]", prompt)
        result["request_coverage"] = [
            {
                "request_id": request_id,
                "evidence_scope": [
                    {
                        "evidence_id": label,
                        "target": "合成の対象",
                        "relation": "direct",
                        "link": None,
                    }
                    for label, block in blocks
                    if any(
                        uid in block
                        for part in parts
                        if part["request_id"] == request_id and part["answer"]["kind"] == "answer"
                        for uid in part["answer"]["basis"]["unit_ids"]
                    )
                ],
            }
            for request_id in dict.fromkeys(part["request_id"] for part in parts)
        ]
    selected = result.pop("summary_item_indexes", [])
    summary = result.pop("summary", "")
    result.pop("applications", None)
    result.pop("summary_application_indexes", None)
    result["answer_parts"] = parts
    result["summary_part_indexes"] = selected[:2] if selected else [0] if summary else []
    return schema.model_validate(result)
