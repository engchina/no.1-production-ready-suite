"""原文を関連度と入力予算で選ぶ。意味の評価やモデルによる縮約は行わない。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from rag_engine.config import Settings
from rag_engine.generation.evidence_cuts import evidence_cuts
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, model_input_measurement
from rag_engine.retrieval.rerank_scores import rerank_first_key


def select_evidence(
    spans: Sequence[dict[str, Any]],
    settings: Settings,
    *,
    render: Callable[[Sequence[dict[str, Any]]], str],
    evidence_text: Callable[[Sequence[dict[str, Any]]], str],
    tag: Callable[[Sequence[dict[str, Any]]], list[dict[str, Any]]],
    system: str,
    schema_for: Callable[[Sequence[dict[str, Any]]], Mapping[str, Any]],
    provider_id: str | None = None,
    source_scores: Mapping[str, float | None] | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """固定入力も含めて測り、入らない原文は除外する。単独でも大きい原文は位置付きで一部を渡す。"""

    def measure(values: Sequence[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        prompt = render(values)
        measured = model_input_measurement(settings, system, prompt, schema_for(values), provider_id=provider_id)
        chars = len(evidence_text(values))
        return prompt, {**measured, "rendered_chars": chars, "fits": measured["fits"] and chars <= settings.evidence_budget_chars}

    _, fixed = measure([])
    if not fixed["fits"]:
        raise GenerationInputBudgetExceeded({**fixed, "reason": "fixed_input_exceeds_limit"})
    seen: set[tuple[str, str]] = set()
    duplicates = 0
    ordered = []
    for span in sorted(spans, key=lambda value: rerank_first_key((source_scores or {}).get(str(value["source_id"])))):
        key = str(span["source_id"]), str(span["text"])
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        ordered.append(span)
    # 全件が入る場合は、候補を足すたびに全 prompt と引用ビューを作り直さない。
    complete = tag(ordered)
    prompt, measurement = measure(complete)
    if measurement["fits"]:
        return (
            complete,
            prompt,
            {
                **measurement,
                "selection": "relevance_within_budget",
                "batch_count": 0,
                "input_span_count": len(spans),
                "selected_span_count": len(complete),
                "excluded_span_count": 0,
                "partial_span_count": 0,
                "duplicate_span_count": duplicates,
            },
        )
    selected: list[dict[str, Any]] = []
    omitted = 0
    partial = 0
    for span in ordered:
        if measure([*selected, span])[1]["fits"]:
            selected.append(span)
            continue
        # 原文を入れる余地がある場合だけ、絶対位置を保った前半を選ぶ。未投入の範囲は診断へ残す。
        cuts = evidence_cuts(span, {})
        ends = sorted(
            {
                end
                for level in cuts.levels
                for end in level
                if 0 < end < len(span["text"]) and not any(table.start < end < table.end for table in cuts.tables)
            }
        )
        low, high = 0, len(ends)
        best: dict[str, Any] | None = None
        while low < high:
            middle = (low + high + 1) // 2
            end = ends[middle - 1]
            text = span["text"][:end]
            start = int(span.get("start", 0))
            identity = hashlib.sha256(f"{span['source_id']}:{start}:{start + end}:{text}".encode()).hexdigest()[:20]
            piece = {**span, "text": text, "start": start, "end": start + end, "evidence_id": "E" + identity, "excerpt_only": True}
            if measure([*selected, piece])[1]["fits"]:
                low, best = middle, piece
            else:
                high = middle - 1
        if best is not None:
            selected.append(best)
            partial += 1
        else:
            omitted += 1
    selected = tag(selected)
    prompt, measurement = measure(selected)
    if not measurement["fits"]:
        raise GenerationInputBudgetExceeded({**measurement, "reason": "final_model_input_exceeds_limit"})
    if not selected and spans:
        raise GenerationInputBudgetExceeded({**measurement, "fits": False, "reason": "no_evidence_fits"})
    return (
        selected,
        prompt,
        {
            **measurement,
            "selection": "relevance_within_budget",
            "batch_count": 0,
            "input_span_count": len(spans),
            "selected_span_count": len(selected),
            "excluded_span_count": omitted + partial,
            "partial_span_count": partial,
            "duplicate_span_count": duplicates,
        },
    )
