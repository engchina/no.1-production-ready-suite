"""最終生成へ実際に渡した原文の範囲を、検索候補と分けて保存する。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def scope_digest(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def capture_provided_scope(citation_trace: Mapping[str, Any]) -> dict[str, Any]:
    captured = citation_trace.get("input")
    if not isinstance(captured, Mapping):
        return {"status": "unknown", "reason": "generation_input_not_recorded", "evidence_items": []}
    rows = captured.get("evidence")
    if not isinstance(rows, list):
        return {"status": "unknown", "reason": "generation_evidence_not_recorded", "evidence_items": []}
    items = []
    prompt = captured.get("prompt")
    complete = (
        isinstance(prompt, str)
        and hashlib.sha256(prompt.encode()).hexdigest() == captured.get("prompt_sha256")
        and captured.get("snapshot_status") == "recorded"
        and captured.get("image_payload_capture") == "not_applicable"
        and not captured.get("unrecorded_evidence_ids")
        and type(captured.get("expected_evidence_count")) is int
        and captured.get("expected_evidence_count") == len(rows)
        and isinstance(captured.get("prompt_sha256"), str)
        and bool(captured["prompt_sha256"])
        and any(
            isinstance(output, Mapping)
            and output.get("purpose") == "answer.generate"
            and output.get("input_prompt_sha256") == captured.get("prompt_sha256")
            for output in citation_trace.get("outputs", [])
        )
    )
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or not row.get("present_in_prompt"):
            complete = False
            continue
        text = row.get("text")
        units = row.get("citation_units")
        if (
            not isinstance(text, str)
            or not isinstance(units, list)
            or type(row.get("numbered")) is not bool
            or row["numbered"]
            and not units
        ):
            complete = False
            continue
        if any(
            not isinstance(unit, list) or len(unit) != 2 or any(type(n) is not int for n in unit) or not 0 <= unit[0] < unit[1] <= len(text)
            for unit in units
        ):
            complete = False
            continue
        block = row.get("prompt_block")
        heading = row.get("group_heading")
        if (
            not isinstance(block, str)
            or not block
            or not isinstance(heading, str)
            or not heading
            or not isinstance(prompt, str)
            or block not in prompt
            or heading not in prompt
            or hashlib.sha256(block.encode()).hexdigest() != row.get("prompt_block_sha256")
        ):
            complete = False
            continue
        # 引用可能な本文だけでは、見出しや補助情報の意味が欠ける。実際の入力ブロックをそのまま残す。
        items.append(
            {
                "id": str(row.get("view_sha256") or f"input-{index}"),
                "source": str(row.get("source") or ""),
                "text": heading + "\n" + block,
            }
        )
    return {
        "status": "complete" if complete else "partial",
        "provenance": "captured_generation_input",
        "scope": "final_generation_evidence",
        "evidence_items": items,
        "sha256": scope_digest(items),
    }
