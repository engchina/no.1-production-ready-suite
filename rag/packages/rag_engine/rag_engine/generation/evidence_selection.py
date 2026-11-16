"""回答の生成へ渡す原文を決める。上限内なら全部を 1 回の生成へ、上限を超えれば原文を外さずに分割読取へ渡す（#5098）。"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from rag_engine.config import Settings
from rag_engine.generation.batched_evidence import BatchReading, prepare_batched_evidence
from rag_engine.generation.input_budget import GenerationInputBudgetExceeded, model_input_measurement
from rag_engine.retrieval.rerank_scores import rerank_first_key


def select_evidence(
    spans: Sequence[dict[str, Any]],
    settings: Settings,
    *,
    question: str,
    render: Callable[[Sequence[dict[str, Any]]], str],
    evidence_text: Callable[[Sequence[dict[str, Any]]], str],
    tag: Callable[[Sequence[dict[str, Any]]], list[dict[str, Any]]],
    system: str,
    schema_for: Callable[[Sequence[dict[str, Any]]], Mapping[str, Any]],
    read: Callable[[str, str, type[BatchReading]], BatchReading],
    unusable_output: Callable[[Exception], bool],
    provider_id: str | None = None,
    source_scores: Mapping[str, float | None] | None = None,
    protected_sources: Collection[str] = (),
    failed_batches: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """固定入力も含めて測り、重複を除いた全原文が入れば関連度の順で 1 回の生成の入力にする。

    入らなければ原文を除外せず、``prepare_batched_evidence`` で容量内の組に分けて全件を読み、選ばれた範囲と回答候補を
    最終生成の 1 回へまとめる。protected_sources（検索拡張の印を持つ根拠の出典。#5162）は分割読取の組分けと補う順で先にする。
    """

    def measure(values: Sequence[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        prompt = render(values)
        measured = model_input_measurement(settings, system, prompt, schema_for(values), provider_id=provider_id)
        chars = len(evidence_text(values))
        return prompt, {**measured, "rendered_chars": chars, "fits": measured["fits"] and chars <= settings.evidence_budget_chars}

    _, fixed = measure([])
    if not fixed["fits"]:
        raise GenerationInputBudgetExceeded({**fixed, "reason": "fixed_input_exceeds_limit"})

    def relevance(value: dict[str, Any]) -> tuple[int, float]:
        return rerank_first_key((source_scores or {}).get(str(value["source_id"])))

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for span in sorted(spans, key=relevance):
        key = str(span["source_id"]), str(span["text"])
        if key not in seen:
            seen.add(key)
            unique.append(span)
    counts = {"input_span_count": len(spans), "duplicate_span_count": len(spans) - len(unique), "excluded_span_count": 0}
    selected = tag(unique)
    prompt, measurement = measure(selected)
    if measurement["fits"]:
        return selected, prompt, {**measurement, **counts, "selection": "relevance_within_budget", "batch_count": 0}
    selected, prompt, measurement = prepare_batched_evidence(
        unique,
        settings,
        question=question,
        render=render,
        evidence_text=evidence_text,
        system=system,
        schema=schema_for([]),
        schema_for=schema_for,
        read=read,
        unusable_output=unusable_output,
        provider_id=provider_id,
        tag=tag,
        failed_batches=failed_batches,
        source_scores=source_scores,
        protected_sources=protected_sources,
    )
    return selected, prompt, {**measurement, **counts, "selection": "batched_reading"}
