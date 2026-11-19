"""回答の生成へ渡す原文を決める。上限内なら全部を 1 回の生成へ、上限を超えれば原文を外さずに分割読取へ渡す（#5098）。"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any

from rag_engine.config import Settings
from rag_engine.generation.batched_evidence import BatchReading, prepare_batched_evidence
from rag_engine.generation.document_inputs import FIRST_CHUNK, document_key, with_first_chunks
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
    image_count: int = 0,
    evidence_size_lower_bound: Callable[[Sequence[dict[str, Any]], int], int] | None = None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """固定入力も含めて測り、重複を除いた全原文が入れば関連度の順で 1 回の生成の入力にする。

    入らなければ原文を除外せず、``prepare_batched_evidence`` で容量内の組に分けて全件を読み、選ばれた範囲と回答候補を
    最終生成の 1 回へまとめる。protected_sources（検索拡張の印を持つ根拠の出典。#5162）は分割読取の組分けと補う順で先にする。
    Wiki の関係（``wiki_relations``。#5500）で結ばれた根拠は、分割読取の組分けと補う順で 1 つの単位にする（#5512）。

    文書の本文の先頭の chunk（``FIRST_CHUNK`` の印。#5240）は外さない。1 回の生成でも分割読取の各組・最終生成でも、その文書の
    根拠と一緒に入れ、根拠がすでにその原文を含めば足さない（``with_first_chunks``）。

    ``image_count`` は回答の生成に添付する画像の枚数（#5316）。1 回の生成と分割読取の最終生成の測定に画像の分を含める
    （分割読取の各組は画像を送らないので含めない）。

    ``evidence_size_lower_bound`` は ``evidence_text`` の字数の下限（#4993）。下限が文字予算を超えれば、全体を描画・測定せずに
    分割読取へ渡す（入ることは実際の描画で確かめる）。
    """

    def measure(values: Sequence[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        prompt = render(values)
        measured = model_input_measurement(settings, system, prompt, schema_for(values), provider_id=provider_id, image_count=image_count)
        chars = len(evidence_text(values))
        return prompt, {**measured, "rendered_chars": chars, "fits": measured["fits"] and chars <= settings.evidence_budget_chars}

    _, fixed = measure([])
    if not fixed["fits"]:
        raise GenerationInputBudgetExceeded({**fixed, "reason": "fixed_input_exceeds_limit"})

    def relevance(value: dict[str, Any]) -> tuple[int, float]:
        return rerank_first_key((source_scores or {}).get(str(value["source_id"])))

    firsts = [span for span in spans if span.get(FIRST_CHUNK)]
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for span in sorted((span for span in spans if not span.get(FIRST_CHUNK)), key=relevance):
        key = str(span["source_id"]), str(span["text"])
        if key not in seen:
            seen.add(key)
            unique.append(span)
    whole = with_first_chunks(unique, firsts)
    first_documents = {document_key(span) for span in firsts}
    counts: dict[str, Any] = {
        "input_span_count": len(spans),
        "duplicate_span_count": len(spans) - len(firsts) - len(unique),
        "excluded_span_count": 0,
    }
    if first_documents:
        # 先頭の chunk の文書の数と、根拠がすでにその原文を含んでいて足さなかった文書の数。
        counts["first_chunk_document_count"] = len(first_documents)
        counts["first_chunk_covered_document_count"] = len(
            first_documents - {document_key(span) for span in whole if span.get(FIRST_CHUNK)}
        )
    if (
        evidence_size_lower_bound is None
        or evidence_size_lower_bound(whole, settings.evidence_budget_chars) <= settings.evidence_budget_chars
    ):
        selected = tag(whole)
        prompt, measurement = measure(selected)
        if measurement["fits"]:
            return selected, prompt, {**measurement, **counts, "selection": "relevance_within_budget", "batch_count": 0}
    selected, prompt, measurement = prepare_batched_evidence(
        [*unique, *firsts],
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
        image_count=image_count,
        evidence_size_lower_bound=evidence_size_lower_bound,
    )
    return selected, prompt, {**measurement, **counts, "selection": "batched_reading"}
