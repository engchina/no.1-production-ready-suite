"""完成した本文の文字破損を、正常な段落と引用の結び付きを保って復旧する。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, cast

from pydantic import BaseModel

from rag_engine.generation.full_answer_wire import CITATION_MARKER
from rag_engine.knowledge.prompt_files import ANSWER_INPUT_TEMPLATE, continue_prompt_steps
from rag_engine.resources.deadline import check_deadline
from rag_engine.resources.llm_retry_budget import LlmRetryBudget


def body_blocks(body: str) -> list[str]:
    """空行で表示単位を分ける。コードの fence 内の空行と元の区切りは保持する。"""
    blocks: list[str] = []
    current: list[str] = []
    fence = ""
    for line in body.splitlines(keepends=True):
        marker = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*", line)
        if marker:
            token, tail = marker.groups()
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = ""
        current.append(line)
        if not fence and not line.strip():
            blocks.append("".join(current))
            current = []
    if current:
        blocks.append("".join(current))
    return blocks


def repair_full_answer(
    value: BaseModel,
    *,
    original_prompt: str,
    original_system: str,
    budget: LlmRetryBudget,
    read: Callable[[str, type[BaseModel]], BaseModel],
    record: Callable[[BaseModel, str], None],
) -> tuple[BaseModel, dict[str, Any]]:
    from rag_engine.generation.answer_text_quality import REPAIR_PROMPT, AllAnswerPartsFailed, _supporting_schema

    parts = body_blocks(cast(Any, value).answer_body)
    pending = {i for i, text in enumerate(parts) if "\ufffd" in text}
    if not pending or "\ufffd" in original_system + original_prompt:
        return value, {}
    initial = sorted(pending)
    attempts: list[dict[str, Any]] = []
    failure = "retry_budget_exhausted"
    while pending:
        try:
            check_deadline()
            if not budget.take():
                break
            targets = [{"field": "answer_body", "index": i, "part": parts[i]} for i in sorted(pending)]
            schema = _supporting_schema(targets)
            prompt = original_prompt + "\n\n" + continue_prompt_steps(ANSWER_INPUT_TEMPLATE, REPAIR_PROMPT)
            prompt += "\n" + json.dumps({"targets": targets}, ensure_ascii=False)
            receipt: dict[str, Any] = {"units": [["answer_body", i] for i in sorted(pending)], "status": "started"}
            attempts.append(receipt)
            output = read(prompt, schema)
            record(output, prompt)
            failure = "replacement_character"
            for item in cast(Any, output).items:
                text = item.part
                old = parts[item.index]
                # 修復は同じ原文引用の表示単位に限る。新しい引用や別の段落へ結び直さない。
                if not text.strip() or CITATION_MARKER.findall(text) != CITATION_MARKER.findall(old):
                    failure = "citation_binding_changed"
                    continue
                if "\ufffd" not in text:
                    suffix = re.search(r"[ \t\r\n]*$", old)
                    assert suffix is not None
                    parts[item.index] = text.rstrip() + suffix.group()
                    pending.remove(item.index)
            receipt.update(status="completed", remaining_units=[["answer_body", i] for i in sorted(pending)])
        except Exception as error:
            failure = type(error).__name__
            if attempts:
                attempts[-1].update(status="failed", error_type=failure)
            break
    diagnostic = {
        "reason": "replacement_character",
        "affected_units": [["answer_body", i] for i in initial],
        "repaired_units": [["answer_body", i] for i in initial if i not in pending],
        "failed_units": [["answer_body", i] for i in sorted(pending)],
        "attempts": attempts,
        "status": "partial_failure" if pending else "recovered",
        "failure_reason": failure if pending else "",
    }
    retained = "".join(text for i, text in enumerate(parts) if i not in pending)
    if not retained.strip():
        raise AllAnswerPartsFailed(diagnostic)
    payload = value.model_dump(mode="json", exclude_unset=True)
    payload["answer_body"] = retained
    if pending:
        payload["response_kind"] = "conditional"
    return type(value).model_validate(payload), diagnostic
