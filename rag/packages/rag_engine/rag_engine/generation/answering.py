"""RAG 検索、回答生成、回答根拠 payload を組み立てる。"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import uuid4

from pr_backend_core.model_cascade import SIGNAL_STRUCTURED_OUTPUT_INVALID, decide_escalation, escalation_reasons
from pr_backend_core.model_cascade import cascade_record as model_cascade_record
from pr_backend_core.observability.model_purpose import call_with_model_purpose
from rag_parser_core.language import current_pack
from rag_parser_core.language.text import alternation

from rag_engine.chunking import (
    CHILD_CHUNK_LEVEL,
    DEFAULT_NEIGHBOR_CHILD_COUNT,
    DEFAULT_RETRIEVAL_TOP_K,
    load_chunk_run_by_id,
    load_latest_or_source_chunk_run,
)
from rag_engine.config import (
    LLM_PURPOSE_GENERATION,
    Settings,
    answer_model_reads_images,
    llm_reasoning_effort_note,
    settings_for_llm_purpose,
)
from rag_engine.dependencies import (
    check_adb_hybrid_search_ready,
    injected_context_chunks,
    injected_embedder,
    parse_multimodal_response,
    parse_text_response,
    record_retrieval_stage,
    report_degraded,
    rerank_text_with_scores,
    search_adb_hybrid_chunks,
)
from rag_engine.generation import applications as answer_applications
from rag_engine.generation import form_field_kinds, model_cascade
from rag_engine.generation.answer_envelope import (
    REFERENCE_CONFIRMATION_PREFIX,
    build_envelope,
    envelope_item,
    fallback_envelope,
    requires_environment_data,
)
from rag_engine.generation.answer_images import (
    _answer_image_metadata,
    _image_prompt_mode,
    _prompt_safe_warnings,
    _trim_generated_text,
    _visual_evidence_anchors,
    answer_image_evidence,
    prompt_injection_warnings_for_records,
)
from rag_engine.generation.answer_labels import answer_labels
from rag_engine.generation.answer_models import (
    QUERY_EXPANSION_SEPARATOR,
    QUESTION_DISPLAY_METADATA_SEPARATOR,
    QUESTION_PLAN_SEPARATOR,
    QUESTION_TEXT_SEARCH_SEPARATOR,
    RUNTIME_KNOWLEDGE_SEPARATOR,
    STANDARD_ANSWER_FLOW,
    AnswerContext,
    AnswerQuestionResult,
    AnswerRecord,
    AnswerResponse,
    GroundedAnswer,
    QueryExpansionResult,
    QuestionTextSearchInfo,
    RetrievalQueryPlan,
    _dedupe_queries,
    _dedupe_query_key,
    _normalize_text,
    extract_original_question,
    merge_answer_use,
)
from rag_engine.generation.answer_payload import (
    _answer_record_identity_keys,
    _cited_evidence_entries,
    _cited_evidence_ids,
    _cited_evidence_uses,
    _evidence_id_key,
    _format_answer_response,
    _metadata_image_evidence,
    _normalize_answer_response,
    _primary_source_run_id,
    _published_reference_records,
    _records_for_prompt_injection_scan,
    _rerank_scores_from_evidence,
    _string_list,
    answer_evidence_items,
    answer_result_payload,
    format_answer_response,
    format_references,
    parse_answer_response,
)
from rag_engine.generation.answer_records import (
    MAX_CONTEXT_RECORDS,
    MAX_RECORD_TEXT_CHARS,
    _bool_metadata_value,
    _chunk_answer_record,
    _context_neighbors,
    _keep_entity_expansion_positions,
    _merge_text_rerank_candidates,
    _preferred_chunk_records,
    _records_by_rerank_ranks,
    _rerank_configured,
    _rerank_document,
    _retrieval_candidate_limit,
    _search_terms_for_queries,
    _split_text_rerank_candidates,
    _stored_chunk_answer_record,
    is_reserved_entity_expansion,
    load_answer_records,
    preferred_records,
    rank_records,
    rank_records_with_rrf,
    validate_run_id,
)
from rag_engine.generation.candidate_trace import candidate_snapshot, context_snapshot
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.confirmed_context import confirmed_premise_records
from rag_engine.generation.evidence_merge import merge_evidence
from rag_engine.generation.execution_record import (
    AnswerExecutionError,
    _execution_lines,
    _execution_step,
    _ExecutionStep,
    _record_answer_execution,
    format_question_display,
)
from rag_engine.generation.guide_acquisition import prompt_acquisition_summary
from rag_engine.generation.query_prompts import (
    MAX_GENERATED_QUERY_CHARS,
    _limited_text_search_query_variants,
    _merge_retrieval_queries,
    _retrieval_metadata_filter,
    _retrieval_queries,
    _text_search_query_source,
)
from rag_engine.generation.step_details import (
    add_evidence_lines,
    evidence_summary,
    join_values,
    numbered,
    record_label,
    record_rerank_top,
    record_search_candidates,
)
from rag_engine.generation.zero_evidence import zero_evidence_stage
from rag_engine.knowledge.classification import ClassificationFilter, classification_filter_from_values
from rag_engine.knowledge.domain_keywords import load_domain_keywords
from rag_engine.knowledge.prompt_files import (
    ANSWER_INPUT_TEMPLATE,
    neutralize_boundary_markers,
    render_prompt_template,
)
from rag_engine.knowledge.runtime_knowledge import (
    RuntimeKnowledgeContext,
    build_runtime_knowledge_context,
    runtime_rules_status,
    runtime_terms_status,
)
from rag_engine.models.llm import RerankTextRank
from rag_engine.models.storage import AdbHybridSearchUnavailable
from rag_engine.resources.context_expansion_trace import bind_context_read_receipt
from rag_engine.resources.deadline import deadline_cut_short
from rag_engine.resources.rerank_diagnostics import bind_rerank_context
from rag_engine.resources.runtime import resolve_prompt
from rag_engine.resources.structured_failures import structured_failure_details
from rag_engine.resources.tracing import observe_call
from rag_engine.retrieval.answer_requirements import answer_requirements
from rag_engine.retrieval.article_catalog import is_added_catalog_article
from rag_engine.retrieval.attachment_evidence import is_attachment_evidence
from rag_engine.retrieval.candidate_budget import artifact_key as retrieval_artifact_key
from rag_engine.retrieval.candidate_budget import record_id as retrieval_record_id
from rag_engine.retrieval.candidate_budget import select_candidates, stage_limits
from rag_engine.retrieval.character_forms import fold_model_view, fold_width
from rag_engine.retrieval.context_builder import (
    MAX_ANCHORS_PER_PARENT,
    ContextBuildRequest,
    ContextParentEvidence,
    build_chunk_context_bundle,
    context_bundle_from_parent_evidence,
    context_bundle_from_records,
    record_has_image_evidence,
)
from rag_engine.retrieval.context_recovery import ContextRecovery, recover_context_records
from rag_engine.retrieval.evidence_selection import EVIDENCE_BUDGET_CHARS, evidence_spans
from rag_engine.retrieval.evidence_selection import new_evidence_count as _new_evidence_count
from rag_engine.retrieval.extra_retrieval import GUIDE_STEPS_KIND, REQUEST_COVERAGE_KIND, ExtraRetrievalBudget
from rag_engine.retrieval.guide_steps import (
    GUIDE_STEP_TRACE_SCHEMA_VERSION,
    GuidePlan,
    reference_step_sources,
    required_guide_targets,
    run_required_guide_searches,
    run_step_searches,
    select_step_parents,
    target_records,
    unsearched_steps,
)
from rag_engine.retrieval.query_identity import normalized_query_key
from rag_engine.retrieval.query_overflow import search_overflow_requests
from rag_engine.retrieval.question_planning import QuestionPlan, plan_question
from rag_engine.retrieval.reference_follow import follow_reference_sources, is_followed_reference
from rag_engine.retrieval.request_coverage import (
    CoverageBudget,
    CoverageUsage,
    TargetedRetrieval,
    select_new_parents,
)
from rag_engine.retrieval.rerank_order import COLUMN_RERANK, COLUMN_SEARCH, DEFAULT_SLOT_RATIO, order_reranked_candidates
from rag_engine.retrieval.rerank_scores import best_rerank_score, question_rerank_score, rerank_first_key, rerank_score
from rag_engine.retrieval.rerank_selection import select_reranked_candidates
from rag_engine.retrieval.scope import (
    RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
    AdbSearchReadiness,
    normalize_retrieval_scope,
)
from rag_engine.retrieval.search_stages import ORIGINAL_QUESTION_SEARCH_STAGE
from rag_engine.retrieval.task_contract import (
    REQUIRED_ASPECTS,
    filter_queries,
    query_rejection_reason,
    task_contract,
)
from rag_engine.retrieval.text_search_tokenizer import (
    MAX_TEXT_SEARCH_TOKENS,
    TEXT_SEARCH_TOKENIZER_HYBRID,
    TextSearchTokenizerConfig,
    build_oracle_text_query,
    normalize_text_search_tokenizer_config,
    sudachi_unavailable,
    text_search_tokenizer_name,
    tokenize_text_search_query,
    tokenize_text_search_query_with_trace,
    tokenizer_fingerprint,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 24000
MAX_CONTEXT_NEIGHBORS_PER_RECORD = 5
UNTRUSTED_CONTEXT_BEGIN = "BEGIN_UNTRUSTED_RETRIEVED_CONTEXT"
UNTRUSTED_CONTEXT_END = "END_UNTRUSTED_RETRIEVED_CONTEXT"


def rerank_records(
    question: str,
    records: Sequence[AnswerRecord],
    settings: Settings | None,
    *,
    enabled: bool = False,
    candidate_limit: int | None = None,
    extra_queries: Sequence[str] = (),
    rerank_phase: str = "other",
) -> list[AnswerRecord]:
    """候補 record を外部 rerank model で再順位付けします。

    candidate_limit は並べ替える候補数。既定の None は取得できた候補すべてで、取得幅（`_retrieval_candidate_limit`）が
    top_k に応じて広がれば rerank 幅も追従する。定数で固定すると、取得した候補のうち後ろの方は一段目の順位のまま
    残り、context の起点が先頭 top_k 件で埋まるため質問に合致していても context に入らない (#1084)。
    """
    with _execution_step("Rerank", "検索の候補を、質問との関連度（並べ替えのモデル）の高い順に並べ替えます。") as step:
        step.input(
            {
                "原質問": question,
                "追加候補検索文": list(extra_queries),
                "有効": enabled,
                "採点候補数の指定": candidate_limit,
                "用途": rerank_phase,
                "関連度スコアの下限": getattr(settings, "rerank_min_relevance_score", None),
                "採用件数の上限": getattr(settings, "rerank_top_n", 0),
                "候補": candidate_snapshot(records),
            }
        )
        receipt: dict[str, Any] = {}
        ranked = _rerank_records(
            question,
            records,
            settings,
            step,
            enabled=enabled,
            candidate_limit=candidate_limit,
            extra_queries=extra_queries,
            rerank_phase=rerank_phase,
            receipt=receipt,
        )
        record_retrieval_stage(
            {
                "stage": "rerank_output",
                "selected_ids": [retrieval_record_id(record) for record in ranked],
                "attachment_candidate_count": sum(is_attachment_evidence(record) for record in records),
                "attachment_selected_count": sum(is_attachment_evidence(record) for record in ranked),
                "attachment_preserved_below_min_score_count": sum(
                    is_attachment_evidence(record)
                    and isinstance(record.metadata.get("rerank"), dict)
                    and record.metadata["rerank"].get("attachment_preserved_below_min_score") is True
                    for record in ranked
                ),
            }
        )
        # 交差参照で足した参照先は、参照元の直後へ置く（質問で並べると橋渡しの参照先が下がる。#1549）。
        ranked = follow_reference_sources(ranked, step)
        # 並べ替えた後の上位と関連度（処理の経過の段を開いて見る。#2341）。
        record_rerank_top(step, ranked)
        step.output({"判定": receipt, "処理後の候補": candidate_snapshot(ranked)})
        return ranked


# rerank に使う検索文の上限（原質問のほか。LLM ではなく rerank の呼び出しの数を抑える。#2161）。
MAX_EXTRA_RERANK_QUERIES = 6
# 全候補が合格点未満のとき、合格点に掛けてやり直す倍率（#4664）。
RERANK_THRESHOLD_RELAXATION = 0.7


def _best_rerank_ranks(
    question: str,
    extra_queries: Sequence[str],
    documents: list[str],
    settings: Settings | None,
    step: Any,
    *,
    rerank_phase: str = "other",
    candidate_ids: Sequence[str] = (),
    receipt: dict[str, Any] | None = None,
) -> list[RerankTextRank]:
    """原質問と検索文ごとに rerank し、候補ごとの関連度の最高値で並べた順位（#2161）。

    原質問の rerank の失敗は呼び出し元へ伝える（取得順に戻す）。追加の検索文の失敗はその検索文だけ飛ばす。
    """
    batch_id = uuid4().hex
    query_receipts: list[dict[str, Any]] = []
    if receipt is not None:
        receipt["実際の採点呼出"] = query_receipts

    def run_query(query: str, index: int) -> list[RerankTextRank]:
        started = monotonic()
        status = "error"
        try:
            with bind_rerank_context(
                phase=rerank_phase, batch_id=batch_id, query_index=index, query_role="original" if index == 0 else "supplement"
            ) as receipt:
                receipt.describe_inputs(query, documents, model=str(getattr(settings, "rerank_model", "") or ""), top_n=len(documents))
                result = rerank_text_with_scores(query, documents, settings, top_n=len(documents))
                status = "completed"
                return result
        finally:
            row = {
                **receipt.to_dict(),
                "logical_status": "cancelled" if receipt.status == "cancelled" else status,
                "logical_elapsed_seconds": monotonic() - started,
            }
            query_receipts.append(row)
            record_retrieval_stage({"stage": "rerank_query", **row})

    ranks = run_query(question, 0)
    # 表記だけ異なる検索文は一度だけ比べ、追加条件を含む別の質問は保持する。
    seen = {normalized_query_key(question)}
    queries: list[str] = []
    for value in extra_queries:
        query = str(value).strip()
        key = normalized_query_key(query)
        if not key or key in seen:
            continue
        seen.add(key)
        queries.append(query)
    queries = queries[:MAX_EXTRA_RERANK_QUERIES]
    best = {rank.index: rank.relevance_score for rank in ranks}
    used = 0
    for query_index, query in enumerate(queries, 1):
        try:
            extra = run_query(query, query_index)
        except Exception as exc:  # noqa: BLE001 - 追加の検索文の失敗は原質問の順位で補う
            # 飛ばした検索文を、呼び出し元の診断に残す（「検索文 N 本」の数だけでは分からない。#3286）。
            report_degraded("rerank_extra_query", exc)
            continue
        used += 1
        for rank in extra:
            score = rank.relevance_score
            if score is not None and (best.get(rank.index) is None or score > (best[rank.index] or 0.0)):
                best[rank.index] = score
    if queries:
        step.add(f"関連度は原質問と検索文 {used} 本の最高値で比べます。")
    # 原質問だけの関連度も残す（文書の絞り込みが原質問の点数でも比べる。#3504）。
    question_scores = {rank.index: rank.relevance_score for rank in ranks}
    merged = [
        RerankTextRank(
            index=index,
            relevance_score=score,
            question_relevance_score=question_scores.get(index),
            question_score_explicit=True,
        )
        for index, score in sorted(best.items(), key=lambda item: -(item[1] if item[1] is not None else -1.0))
    ]
    if not queries:
        merged = list(ranks)
    if receipt is not None:
        receipt["原質問の順位"] = [{"index": rank.index, "score": rank.relevance_score} for rank in ranks]
        receipt["最高値による順位"] = [{"index": rank.index, "score": rank.relevance_score} for rank in merged]
    record_retrieval_stage(
        {
            "stage": "rerank_score_comparison",
            "batch_id": batch_id,
            "phase": rerank_phase,
            "candidate_ids": list(candidate_ids),
            "candidate_count": len(documents),
            "aggregation": "max",
            "queries": query_receipts,
            "original_ranks": [{"index": rank.index, "score": rank.relevance_score} for rank in ranks],
            "merged_ranks": [{"index": rank.index, "score": rank.relevance_score} for rank in merged],
        }
    )
    return merged


def _rerank_skip_reason(records: Sequence[AnswerRecord], settings: Settings | None, *, enabled: bool) -> str:
    """rerank を実行しない理由。実行するなら空文字。"""
    if not enabled:
        return "無効の設定"
    if settings is None or not _rerank_configured(settings):
        return "モデルまたはコンパートメントが未設定"
    if not records:
        return "対象の根拠なし"
    return ""


def _order_by_slots(
    records: Sequence[AnswerRecord], settings: Settings, step: Any, receipt: dict[str, Any], *, rerank_phase: str
) -> list[AnswerRecord]:
    """合格した候補を、rerank の順（MMR）と検索の順の枠で 1 本に並べ、工程の記録に残す（#4664）。"""
    ratio = cast(tuple[int, int], getattr(settings, "rerank_slot_ratio", DEFAULT_SLOT_RATIO))
    tokenizer_config = TextSearchTokenizerConfig(
        sudachi_dict_type=getattr(settings, "text_search_tokenizer_sudachi_dict", ""),
        sudachi_config_path=getattr(settings, "text_search_tokenizer_sudachi_config", ""),
        latin_stemmer=getattr(settings, "text_search_tokenizer_latin_stemmer", ""),
    )
    ordered, columns = order_reranked_candidates(records, ratio, tokenizer_config=tokenizer_config)
    counts = Counter(column for column in columns if column is not None)
    trace = {
        "ratio": list(ratio),
        "rerank_slot_count": counts[COLUMN_RERANK],
        "search_slot_count": counts[COLUMN_SEARCH],
        "kept_position_count": len(records) - sum(counts.values()),
    }
    receipt["並びの枠"] = trace
    step.field("並びの枠（rerank の順 : 検索の順）", f"{ratio[0]} : {ratio[1]}")
    breakdown = (
        ("rerank の順", trace["rerank_slot_count"]),
        ("検索の順", trace["search_slot_count"]),
        ("位置を保つ", trace["kept_position_count"]),
    )
    step.field("枠の内訳", " / ".join(f"{label} {count} 件" for label, count in breakdown))
    record_retrieval_stage(
        {
            "stage": "rerank_slot_order",
            "phase": rerank_phase,
            **trace,
            "order": [{"id": retrieval_record_id(record), "column": column} for record, column in zip(ordered, columns, strict=True)],
        }
    )
    return ordered


def _rerank_records(
    question: str,
    records: Sequence[AnswerRecord],
    settings: Settings | None,
    step: Any,
    *,
    enabled: bool,
    candidate_limit: int | None,
    extra_queries: Sequence[str] = (),
    rerank_phase: str = "other",
    receipt: dict[str, Any] | None = None,
) -> list[AnswerRecord]:
    """rerank 本体。無効・未設定・失敗時は取得順を保持する（並びの枠と MMR も行わない。#4664）。

    extra_queries（呼び出し元の検索文、質問に用語・同義語を足した検索文など）があれば、候補ごとに原質問と各検索文の関連度の最高値で
    並べる（#2161）。長い事例の質問の全体と比べると、要求の一部にだけ答える根拠（条文など）の関連度が薄まるため。

    実行しなかったときは、実行記録の状態に理由を付ける（「未実行: 無効の設定」など）。画面は状態をそのまま出すため、
    実行した（complete）と実行しなかった理由を見分けられる。
    """
    receipt = receipt if receipt is not None else {}
    skip_reason = _rerank_skip_reason(records, settings, enabled=enabled)
    if skip_reason:
        receipt.update({"状態": "未実行", "理由": skip_reason})
        step.status = f"未実行: {skip_reason}"
        step.add(f"rerank を実行せず、取得順を保持します（{skip_reason}）。")
        return list(records)

    candidate_count = len(records) if candidate_limit is None else min(len(records), max(1, int(candidate_limit)))
    candidates = list(records[:candidate_count])
    remainder = list(records[candidate_count:])
    protected_candidates, rerankable_candidates, rerankable_indices = _split_text_rerank_candidates(candidates)
    if not rerankable_candidates:
        receipt.update({"状態": "未実行", "理由": "並べ替える本文なし"})
        step.status = "未実行: 並べ替える本文なし"
        step.add("並べ替え対象のテキストがありません。")
        return [protected_candidates[index] for index in sorted(protected_candidates)] + remainder
    step.field("並べ替える候補", f"{len(rerankable_candidates)} 件" + (f"（ほか {len(remainder)} 件は取得順のまま）" if remainder else ""))
    documents = [_rerank_document(record) for record in rerankable_candidates]
    receipt.update(
        {
            "状態": "採点完了",
            "採点候補ID": [retrieval_record_id(record) for record in rerankable_candidates],
        }
    )
    try:
        reranked = _best_rerank_ranks(
            question,
            extra_queries,
            documents,
            settings,
            step,
            rerank_phase=rerank_phase,
            candidate_ids=[retrieval_record_id(record) for record in rerankable_candidates],
            receipt=receipt,
        )
    except Exception as exc:
        # 取得の順で続けたことを、呼び出し元の診断に残す（工程の文言だけでは縮退が分からない。#3286）。
        report_degraded("rerank", exc)
        receipt.update({"状態": "取得順を保持", "理由": "採点失敗", "エラーの種類": type(exc).__name__})
        step.status = "失敗・取得順を保持"
        step.add("並べ替えに失敗したため、取得順の根拠を使います。")
        return list(records)
    threshold_rows: list[dict[str, object]] = []
    reranked_candidates = _records_by_rerank_ranks(
        rerankable_candidates,
        reranked,
        cast(Settings, settings),
        candidate_indices=rerankable_indices,
        decisions=threshold_rows,
    )
    # 全部が不合格のときだけ、合格点を下げて 1 回だけやり直す（#4664）。それでも 0 件なら根拠 0 件で終える。
    relaxed_from: float | None = None
    if all(row["status"] == "excluded" for row in threshold_rows):
        relaxed_from = cast(float, threshold_rows[0]["minimum"])
        threshold_rows.clear()
        reranked_candidates = _records_by_rerank_ranks(
            rerankable_candidates,
            reranked,
            cast(Settings, settings),
            candidate_indices=rerankable_indices,
            decisions=threshold_rows,
            min_score_override=round(relaxed_from * RERANK_THRESHOLD_RELAXATION, 6),
        )
        receipt["関連度下限のやり直し"] = {"元の下限": relaxed_from, "下げた下限": threshold_rows[0]["minimum"]}
    receipt["関連度下限の判断"] = threshold_rows
    excluded = sum(row["status"] == "excluded" for row in threshold_rows)
    unscored = sum(row["status"] == "kept_unscored" for row in threshold_rows)
    attachments = sum(row["status"] == "kept_attachment" for row in threshold_rows)
    if relaxed_from is None:
        step.field("関連度スコアの下限", str(threshold_rows[0]["minimum"]))
    else:
        step.field("関連度スコアの下限", f"{threshold_rows[0]['minimum']}（全候補が {relaxed_from} 未満のため下げて 1 回だけやり直し）")
    step.field(
        "関連度による選別",
        f"採用 {len(reranked_candidates)} 件 / 下限未満で除外 {excluded} 件 / "
        f"未採点で保持 {unscored} 件 / 明示添付で保持 {attachments} 件 / "
        f"採点対象外 {len(protected_candidates) + len(remainder)} 件",
    )
    status_labels = {"excluded": "下限未満で除外", "kept_unscored": "未採点で保持", "kept_attachment": "明示添付で保持", "kept": "採用"}
    for row in threshold_rows:
        question_score = row["question_score"] if row["question_score"] is not None else "未採点"
        adopted_score = row["adopted_score"] if row["adopted_score"] is not None else "未採点"
        step.add(
            f"{row['source'] or '文書名の記録なし'} / 原質問の関連度: {question_score} / "
            f"判定に使った関連度: {adopted_score} / {status_labels[str(row['status'])]}"
        )
    record_retrieval_stage(
        {
            "stage": "rerank_threshold",
            "phase": rerank_phase,
            "minimum": threshold_rows[0]["minimum"],
            "relaxed_from": relaxed_from,
            "decisions": threshold_rows,
            "excluded_count": excluded,
            "unscored_kept_count": unscored,
            "attachment_kept_count": attachments,
            "outside_scoring_count": len(protected_candidates) + len(remainder),
        }
    )
    # 関連度が下限以上の実体の拡張の根拠は、backend が置いた候補の位置へ戻す（#1362・#1390）。
    protected_candidates, reranked_candidates = _keep_entity_expansion_positions(protected_candidates, reranked_candidates)
    has_scores = any(row["adopted_score"] is not None for row in threshold_rows)
    if has_scores:
        reranked_candidates = _order_by_slots(reranked_candidates, cast(Settings, settings), step, receipt, rerank_phase=rerank_phase)
    merged = _merge_text_rerank_candidates(candidates, protected_candidates, reranked_candidates) + remainder
    if not has_scores:
        receipt["採用件数の判断"] = {"applied": False, "stop_reason": "no_scores"}
        if int(getattr(settings, "rerank_top_n", 0) or 0) > 0:
            step.add("有効な採点がないため、採用件数の上限を適用せず候補を保持します。")
            record_retrieval_stage({"stage": "rerank_selection", "phase": rerank_phase, "applied": False, "stop_reason": "no_scores"})
        return merged
    selected, selection = select_reranked_candidates(merged, int(getattr(settings, "rerank_top_n", 0) or 0))
    receipt["採用件数の判断"] = selection
    if selection["applied"]:
        step.field("再ランキング後の採用上限", str(selection["limit"]))
        step.field(
            "件数による選別",
            f"普通候補 {selection['ordinary_selected_count']} 件 / "
            f"明示添付と参照先 {selection['attachment_related_count']} 件 / "
            f"条目録と参照先 {selection['catalog_related_count']} 件 / 除外 {selection['excluded_count']} 件",
        )
        step.add("引用元と参照先は組で選び、上限に収まらない組は採用しません。")
        record_retrieval_stage(
            {
                "stage": "rerank_selection",
                "phase": rerank_phase,
                **selection,
                "selected_ids": [retrieval_record_id(record) for record in selected],
            }
        )
    return selected


def build_answer_context(
    question: str,
    records: Sequence[AnswerRecord],
    *,
    max_records: int = MAX_CONTEXT_RECORDS,
    max_chars: int = MAX_CONTEXT_CHARS,
    retrieval_queries: Sequence[str] | None = None,
    rerank_enabled: bool = True,
    settings: Settings | None = None,
) -> AnswerContext:
    """record 候補から LLM に渡す context テキストと evidence を構築します。"""
    ranked = _ranked_records_with_neighbors(
        question,
        records,
        max_records,
        retrieval_queries=retrieval_queries,
        rerank_enabled=rerank_enabled,
        settings=settings,
    )
    bundle = context_bundle_from_records(
        ranked,
        max_chars=max_chars,
        max_record_text_chars=MAX_RECORD_TEXT_CHARS,
    )
    return AnswerContext(
        records=list(bundle.records),
        text=bundle.text,
        evidence=bundle.evidence,
        evidence_tree=bundle.evidence_tree,
        status=bundle.status,
        insufficient_reason=bundle.insufficient_reason,
    )


def _max_context_records(settings: Settings | None) -> int:
    """context に入れる parent 数（`RAG_ENGINE_MAX_CONTEXT_RECORDS`）。settings が無い呼び出しは既定値 (#1054)。"""
    return max(1, int(getattr(settings, "max_context_records", MAX_CONTEXT_RECORDS) or MAX_CONTEXT_RECORDS))


def _max_anchors_per_parent(settings: Settings | None) -> int:
    """同じ親から起点に採る child の上限。settings が無い呼び出し（テスト・SDK）は既定値 (#889)。"""
    return max(1, int(getattr(settings, "max_anchors_per_parent", MAX_ANCHORS_PER_PARENT) or MAX_ANCHORS_PER_PARENT))


def build_chunk_answer_context(
    question: str,
    chunk_records: Sequence[AnswerRecord],
    *,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    neighbor_child_count: int = DEFAULT_NEIGHBOR_CHILD_COUNT,
    max_records: int = MAX_CONTEXT_RECORDS,
    max_chars: int = MAX_CONTEXT_CHARS,
    retrieval_queries: Sequence[str] | None = None,
    rerank_enabled: bool = True,
    settings: Settings | None = None,
) -> AnswerContext:
    """保存済みチャンクを展開して回答用 context を構築します。"""
    active_records = [record for record in chunk_records if _answer_record_active(record)]
    children = [record for record in active_records if record.chunk_level == CHILD_CHUNK_LEVEL]
    if not children:
        return AnswerContext(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="no active child chunks are available",
        )

    queries = _retrieval_queries(question, retrieval_queries)
    ranked_children = (
        rank_records_with_rrf(queries, children, len(children)) if len(queries) > 1 else rank_records(question, children, len(children))
    )
    ranked_children = rerank_records(question, ranked_children, settings, enabled=rerank_enabled)
    bundle = build_chunk_context_bundle(
        ContextBuildRequest(
            question=question,
            ranked_children=ranked_children,
            active_records=active_records,
            top_k=max(1, int(top_k or DEFAULT_RETRIEVAL_TOP_K)),
            neighbor_child_count=neighbor_child_count,
            support_record_limit=3,
            max_anchors_per_parent=_max_anchors_per_parent(settings),
            max_records=max_records,
            max_chars=max_chars,
        )
    )
    return AnswerContext(
        records=list(bundle.records),
        text=bundle.text,
        evidence=bundle.evidence,
        evidence_tree=bundle.evidence_tree,
        status=bundle.status,
        insufficient_reason=bundle.insufficient_reason,
    )


def select_documents(
    ranked_children: Sequence[AnswerRecord], settings: Settings | None, step: Any | None = None, *, top_k: int
) -> tuple[list[AnswerRecord], list[AnswerRecord], dict[str, Any]]:
    """rerank 後の child を文書ごとに集約し、rerank の点数が明らかに低い文書の child を後回し（deferred）にする (#1028)。

    粗→細の検索の「粗」の側。文書級の要約や索引は作らず、文書の点数は文書の chunk の rerank の点数の最高値
    （MaxP。#3504）。後回しにするのは、並べ替えの点数（原質問と検索文の最高値。#2161）でも原質問だけの点数でも
    最上位の文書の `document_selection_score_ratio` 倍未満で、かつ rerank の後の並び（枠の並び。#4664）の上位
    `top_k` 件（起点）に chunk を持たない文書だけ。命中の件数は判定に使わない（件数は長い文書・繰り返しの多い文書に偏り、点数の高い 1 件の
    文書を、点数の低い候補を多く持つ文書より後ろにしていた。#3504）。集約は rerank が見た範囲、すなわち候補
    全件で行う (#1084)。後回しにした child は落とさず `AnswerContext.deferred_records` に残し、不足時に戻す。
    rerank の点数が無い（rerank が動かなかった）ときは落差を判断できないので後回しにしない。無効なら何もしない。
    """
    from rag_engine.retrieval.metadata_context import document_context_key  # 循環 import を避けて関数内で読む

    enabled = bool(getattr(settings, "document_selection_enabled", False))
    trace: dict[str, Any] = {"enabled": enabled, "selected": [], "deferred": [], "restored": False}
    if not enabled or not ranked_children:
        return list(ranked_children), [], trace
    ratio = float(getattr(settings, "document_selection_score_ratio", 0.5))
    head = max(0, int(top_k or 0))
    scores: dict[tuple[str, ...], float | None] = {}
    question_scores: dict[tuple[str, ...], float | None] = {}
    best_ranks: dict[tuple[str, ...], int] = {}
    hits: dict[tuple[str, ...], int] = {}
    sources: dict[tuple[str, ...], str] = {}

    def higher(current: float | None, value: float | None) -> float | None:
        return value if current is None or (value is not None and value > current) else current

    for position, record in enumerate(ranked_children, start=1):
        if is_added_catalog_article(record) or is_attachment_evidence(record):
            # 会話の添付は利用者が明示した入力なので、文書間の関連度の比較に数えない。
            # 目録で選んで加えた条（#2254）は文書の分数に数えない（加えた条の関連度で他の文書を後回しにしない）。
            continue
        key = document_context_key(record)
        scores[key] = higher(scores.get(key), rerank_score(record))
        question_scores[key] = higher(question_scores.get(key), question_rerank_score(record))
        best_ranks.setdefault(key, position)
        hits[key] = hits.get(key, 0) + 1
        sources.setdefault(key, str(record.source or ""))
    if not scores:
        return list(ranked_children), [], trace
    scored = any(score is not None for score in scores.values())
    top = max((score for score in scores.values() if score is not None), default=0.0)
    top_question = max((score for score in question_scores.values() if score is not None), default=0.0)

    def low(score: float | None, best: float) -> bool:
        return score is None or score < best * ratio

    excluded = (
        {key for key in scores if best_ranks[key] > head and low(scores[key], top) and low(question_scores[key], top_question)}
        if scored
        else set()
    )
    # 表示の順: 原質問との点数、並べ替えの点数、最上位の候補の順位。命中の件数は同点の順だけ。
    order = sorted(
        scores,
        key=lambda k: (*rerank_first_key(question_scores[k]), *rerank_first_key(scores[k]), best_ranks[k], -hits[k]),
    )
    for key in order:
        entry = {
            "source": sources[key],
            "score": None if scores[key] is None else round(float(scores[key] or 0.0), 4),
            "question_score": None if question_scores[key] is None else round(float(question_scores[key] or 0.0), 4),
            "best_rank": best_ranks[key],
            "hits": hits[key],
        }
        trace["deferred" if key in excluded else "selected"].append(entry)
    # 実体の 1 段の拡張で足した根拠（#1362）は、文書の分数が低くても後回しにしない（橋渡しの行）。関連度が
    # 下限未満の拡張の根拠は、印の無い候補と同じに扱う（#1390）。
    # 参照元の直後へ置いた交差参照の参照先（#1549）も、参照元に従うので後回しにしない。
    # 目録で選んで加えた条（#2254）も、事例の言葉の質問では rerank の関連度が低いので後回しにしない。
    # 会話の添付（#3100）も、利用者が明示した入力なので後回しにしない。
    reserved = [
        is_reserved_entity_expansion(r) or is_followed_reference(r) or is_added_catalog_article(r) or is_attachment_evidence(r)
        for r in ranked_children
    ]
    kept = [r for r, keep in zip(ranked_children, reserved, strict=False) if document_context_key(r) not in excluded or keep]
    deferred = [r for r, keep in zip(ranked_children, reserved, strict=False) if document_context_key(r) in excluded and not keep]
    if step is not None:
        # 文書ごとの点数と出所、残す / 後回しを示す（処理の経過の段を開いて見る。#2341・#3504）。
        step.field("判定の基準", _document_selection_rule(ratio, head, scored=scored))
        step.field("文書", f"{len(scores)} 文書（残す {len(scores) - len(excluded)}・後回し {len(excluded)}）")
        step.add(
            *numbered(
                [
                    f"{'後回し' if index >= len(trace['selected']) else '残す'}: {Path(entry['source']).name or entry['source']}"
                    f"（{_document_score_label(entry)}）"
                    for index, entry in enumerate([*trace["selected"], *trace["deferred"]])
                ],
                limit=8,
            )
        )
    return kept, deferred, trace


def _document_selection_rule(ratio: float, top_k: int, *, scored: bool) -> str:
    """文書の絞り込みの判定の基準の文（処理の経過に出す。#3504）。"""
    if not scored:
        return "rerank の点数が無いため、どの文書も後回しにしません（取得の順位で並べます）"
    return (
        "文書の点数は、文書の候補の rerank の点数（元の質問との点数と、検索文を含む並べ替えの点数）の最高値。"
        f"どちらも最上位の文書の {ratio:g} 倍未満で、上位 {top_k} 件の起点に候補が無い文書を後回し（命中の件数は判定に使いません）"
    )


def _document_score_label(entry: Mapping[str, Any]) -> str:
    """文書の行の点数の表示（元の質問との点数・並べ替えの点数・最上位の順位・命中の件数）。"""
    score, question = entry.get("score"), entry.get("question_score")
    tail = f"最上位 {entry['best_rank']} 位・命中 {entry['hits']} 件"
    if score is None:
        return f"rerank の点数なし・{tail}"
    if question is None or abs(float(question) - float(score)) < 0.0005:
        return f"元の質問との並べ替えの点数 {float(score):.3f}・{tail}"
    return f"元の質問との点数 {float(question):.3f}・検索文を含む並べ替えの点数 {float(score):.3f}・{tail}"


def cap_anchors_per_document(
    ranked_children: Sequence[AnswerRecord], settings: Settings | None, *, top_k: int, step: Any | None = None
) -> list[AnswerRecord]:
    """同じ文書の child を、文書ごとに `max_anchors_per_document` 件までにして先に並べ、超えた child を後ろへ回す（#2167）。

    rerank の上位は、章の多い 1 つの文書の似た節で占められやすく、分数の近い別の文書の候補が起点（上位 top_k）から
    押し出される（検索エンジンの host crowding と同じ）。超えた child は落とさず順位を保って後ろへ置く（起点が
    足りなければ使われる）。実体の拡張・交差参照で足した child は位置を保ち、数えない（参照元の直後に置くため）。
    目録で選んで加えた条（#2254）も位置を保ち、数えない。0 は上限なし。
    """
    from rag_engine.retrieval.metadata_context import document_context_key  # 循環 import を避けて関数内で読む

    limit = int(getattr(settings, "max_anchors_per_document", 0) or 0)
    if limit <= 0:
        return list(ranked_children)
    counts: dict[tuple[str, ...], int] = {}
    kept: list[AnswerRecord] = []
    later: list[AnswerRecord] = []
    for record in ranked_children:
        # 目録で選んで加えた条（#2254）は起点・親の枠の外に足すので数えない。会話の添付（#3100）も数えない。
        if (
            is_reserved_entity_expansion(record)
            or is_followed_reference(record)
            or is_added_catalog_article(record)
            or is_attachment_evidence(record)
        ):
            kept.append(record)
            continue
        key = document_context_key(record)
        if counts.get(key, 0) < limit:
            counts[key] = counts.get(key, 0) + 1
            kept.append(record)
        else:
            later.append(record)
    capped = kept + later
    if step is not None:
        head = {id(record) for record in capped[:top_k]}
        moved_records = [record for record in ranked_children[:top_k] if id(record) not in head]
        moved = len(moved_records)
        step.result(f"文書ごとの起点の上限 {limit} 件: 上位 {top_k} 件のうち {moved} 件を後ろへ回します")
        # 後ろへ回した候補と、代わりに起点に入った候補（#2341）。
        if moved_records:
            step.add("後ろへ回した候補:", *numbered([record_label(record) for record in moved_records]))
            original = {id(record) for record in ranked_children[:top_k]}
            raised = [record for record in capped[:top_k] if id(record) not in original]
            if raised:
                step.add("代わりに起点に入った候補:", *numbered([record_label(record) for record in raised]))
    return capped


def _bundle_context(
    question: str,
    ranked_children: Sequence[AnswerRecord],
    active_records: Sequence[AnswerRecord],
    settings: Settings,
    *,
    top_k: int,
    neighbor_child_count: int,
    max_records: int,
    max_chars: int,
):
    """rerank 済みの child を起点に parent・隣接 child を展開して context bundle を作る（検索と復元で共用）。"""
    request = ContextBuildRequest(
        question=question,
        ranked_children=list(ranked_children),
        active_records=list(active_records),
        top_k=max(1, int(top_k or DEFAULT_RETRIEVAL_TOP_K)),
        neighbor_child_count=neighbor_child_count,
        support_record_limit=3,
        max_anchors_per_parent=_max_anchors_per_parent(settings),
        max_records=max_records,
        max_chars=max_chars,
        catalog_records=int(getattr(settings, "article_catalog_context_records", 0) or 0),
    )
    # 同じ起点選択を先に行い、未採用の候補の親本文・近傍を読まない。文字予算は原文の取得後に適用する。
    provisional = build_chunk_context_bundle(replace(request, max_chars=2**31 - 1, select_anchors_only=True))
    anchor_ids = {
        retrieval_record_id(child.record)
        for parent in provisional.evidence_tree
        for child in parent.children
        if child.role == "retrieved_anchor"
    }
    selected = [record for record in ranked_children if retrieval_record_id(record) in anchor_ids]
    with (
        _execution_step("前後の文脈の読み込み", "選択した起点の前後から文脈を読み、回答の材料に加えます。") as step,
        bind_context_read_receipt() as receipt,
    ):
        step.input(
            {
                "前後それぞれの最大 Chunk 数": neighbor_child_count,
                "起点根拠数": len(selected),
                "起点根拠 ID": [retrieval_record_id(record) for record in selected],
                "文脈の最大レコード数": max_records,
            }
        )
        loaded = injected_context_chunks(anchors=selected, settings=settings)
        if loaded is not None:
            known = {retrieval_record_id(record): record for record in active_records}
            fresh = {retrieval_record_id(record): record for record in map(_stored_chunk_answer_record, loaded)}
            for record in ranked_children:
                uid = retrieval_record_id(record)
                if uid in fresh:
                    metadata = {**(record.metadata or {}), **(fresh[uid].metadata or {})}
                    # 遅延取得で並べ替えの点数・順序を消さない。画像等の新しい原文情報だけを補う。
                    metadata.update({key: record.metadata[key] for key in ("rerank", "rrf_score") if key in record.metadata})
                    fresh[uid] = replace(record, metadata=metadata)
            known.update(fresh)
            request = replace(request, ranked_children=[fresh.get(retrieval_record_id(record), record) for record in ranked_children])
            # 呼出元が保持する回復候補にも、実際に読み取った原文を残す。
            if isinstance(active_records, list):
                active_records[:] = known.values()
            request = replace(request, active_records=list(known.values()))
        bundle = build_chunk_context_bundle(request)

        adopted = {
            retrieval_record_id(child.record)
            for parent in bundle.evidence_tree
            for child in parent.children
            if child.role == "neighbor_context"
        }
        anchor_ids_before = {retrieval_record_id(record) for record in selected}
        read_ids = {uid for read in receipt.reads for uid in read.get("read_ids", [])}
        scoped_out = {uid for read in receipt.reads for uid in read.get("scope_excluded_ids", [])}
        relations = [item for read in receipt.reads for item in read.get("relations", []) if item.get("purpose") == "neighbor_context"]
        before_ids = {item["chunk_id"] for item in relations if isinstance(item.get("distance"), int) and item["distance"] < 0}
        after_ids = {item["chunk_id"] for item in relations if isinstance(item.get("distance"), int) and item["distance"] > 0}
        output = {
            "読み取り記録": receipt.reads,
            "読取 Chunk 数": len(read_ids) if receipt.reads else None,
            "範囲適用後の読取 Chunk 数": len(read_ids - scoped_out) if receipt.reads else None,
            "重複除去後の新規読取 Chunk 数": len(read_ids - scoped_out - anchor_ids_before) if receipt.reads else None,
            "重複除去後の読取 ID": sorted(read_ids),
            "既存の起点と重複する ID": sorted(read_ids & anchor_ids_before),
            "範囲外で除外した ID": sorted(scoped_out),
            "範囲外の起点 ID": sorted({uid for read in receipt.reads for uid in read.get("anchor_scope_excluded_ids", [])}),
            "文脈への採用 Chunk 数": len(adopted),
            "採用 ID": sorted(adopted),
            "前方の採用 Chunk 数": len(adopted & before_ids) if adopted <= before_ids | after_ids else None,
            "後方の採用 Chunk 数": len(adopted & after_ids) if adopted <= before_ids | after_ids else None,
            "前後の両方に対応する採用 Chunk 数": len(adopted & before_ids & after_ids) if adopted <= before_ids | after_ids else None,
            "最終採用の意味": "この工程後の回答文脈への採用。モデルによる引用数ではありません。",
            "選択時の除外": list(bundle.excluded_evidence),
            "隣接候補の選択記録": receipt.selection,
        }
        if not selected:
            step.skip("起点根拠がありません。")
            output["実行状態"] = "起点なし"
        elif neighbor_child_count <= 0:
            step.skip("前後の Chunk を加える設定が 0 です。親本文の復元は別に実行します。")
            output["実行状態"] = "設定無効"
        elif receipt.reads and all(read["state"] == "reused" for read in receipt.reads):
            step.skip("読み取り済みの内容を再利用しました。")
            output["実行状態"] = "既読を再利用"
        else:
            output["実行状態"] = "実行済み"
            step.result(f"前後の文脈 {len(adopted)} Chunk を回答文脈に採用しました。")
        if loaded is None or not receipt.reads:
            output["読み取り詳細"] = "未記録"
        step.output(output)
    record_retrieval_stage(
        {
            "stage": "answer_context",
            "attachment_anchor_count": sum(is_attachment_evidence(record) for record in selected),
            "attachment_context_count": sum(
                any(is_attachment_evidence(child.record) for child in parent.children) for parent in bundle.evidence_tree
            ),
            "anchor_limit": top_k,
            "record_limit": max_records,
            "full_parent_text": True,
            "anchor_ids": [retrieval_record_id(record) for record in selected],
            "loaded_ids": [str(chunk.chunk_uid) for chunk in loaded] if loaded is not None else [],
            "selected_ids": [retrieval_record_id(record) for record in bundle.records],
            "excluded": list(bundle.excluded_evidence),
        }
    )
    return bundle


def _restore_deferred_context(
    question: str, context: AnswerContext, settings: Settings, *, top_k: int, neighbor_child_count: int, max_records: int, max_chars: int
) -> AnswerContext:
    """後回しにした文書を戻し、全候補から context を作り直す（護欄: 覆盖度による回退。#1028）。"""
    with _execution_step("文書の選択の解除", "根拠が不足したため、後回しにした文書を戻して再評価します。") as step:
        step.input(
            partial(
                context_snapshot,
                context,
                **{
                    "質問": question,
                    "起点数": top_k,
                    "前後それぞれのChunk数": neighbor_child_count,
                    "根拠数上限": max_records,
                    "文脈文字数上限": max_chars,
                    "文書ごとの起点数上限": settings.max_anchors_per_document,
                },
            )
        )
        bundle = _bundle_context(
            question,
            cap_anchors_per_document(context.ranked_candidates, settings, top_k=top_k),
            context.expansion_records,
            settings,
            top_k=top_k,
            neighbor_child_count=neighbor_child_count,
            max_records=max_records,
            max_chars=max_chars,
        )
        restored = AnswerContext(
            records=list(bundle.records),
            text=bundle.text,
            evidence=bundle.evidence,
            evidence_tree=bundle.evidence_tree,
            status=bundle.status,
            insufficient_reason=bundle.insufficient_reason,
            expansion_records=context.expansion_records,
            ranked_candidates=context.ranked_candidates,
            candidate_count=context.candidate_count,
            deferred_records=(),
            document_selection={**context.document_selection, "restored": True},
        )

        step.output(partial(context_snapshot, restored, **{"新規の根拠がある": _adds_new_records(context, restored)}))
        step.result(f"後回しの候補 {len(context.deferred_records)} 件から文脈を作り直しました")
        step.field("作り直した根拠", evidence_summary(restored.records))
        return restored


def build_adb_hybrid_answer_context(
    question: str,
    run_id: Any,
    preferred_engines: Iterable[str],
    settings: Settings,
    *,
    top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    neighbor_child_count: int = DEFAULT_NEIGHBOR_CHILD_COUNT,
    max_records: int | None = None,
    max_chars: int = MAX_CONTEXT_CHARS,
    retrieval_queries: Sequence[str] | None = None,
    rerank_enabled: bool = True,
    rerank_phase: str = "other",
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    classification_filter: ClassificationFilter | None = None,
    pinned_chunk_run_id: str | None = None,
    runtime_knowledge: RuntimeKnowledgeContext | None = None,
    query_embedder: Callable[[str, Settings], list[float]] | None = None,
    vector_only_queries: Sequence[str] = (),
    page_ranges: Sequence[Mapping[str, Any]] = (),
    trusted_queries: Sequence[str] = (),
    catalog_skip_reasons: Mapping[str, str] | None = None,
) -> AnswerContext:
    """ADB hybrid search で回答用 context を構築する。

    trusted_queries は retrieval_queries のうち呼び出し元が作った検索文（#2651）。原質問との照合
    （LLM の言い換えの逸脱を外す）で外さない。

    page_ranges は検索を絞る文書の頁の範囲（{document_id, page_start, page_end} の列。業務ガイドの手順の参照。
    #2395）。空なら絞らない。絞り込みは注入された検索実装が行う。

    query_embedder は索引と同じ embedder を使う SDK 用。未指定なら検索実装の既定（OCI）を使う。
    vector_only_queries は retrieval_queries のうち全文検索に使わない検索文（HyDE の仮説文。#911）。

    pinned_chunk_run_id 指定時は保存済み実行を直接読み、latest 更新による評価条件の変化を防ぐ。
    存在しない実行は AdbHybridSearchUnavailable とする。ADB と必要に応じ reranker に接続する。
    """
    max_records = _max_context_records(settings) if max_records is None else max_records
    scope = normalize_retrieval_scope(retrieval_scope)
    chunk_run_id = ""
    if scope == RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN:
        chunk_run = (
            load_chunk_run_by_id(settings.output_dir, pinned_chunk_run_id)
            if pinned_chunk_run_id is not None
            else load_latest_or_source_chunk_run(settings.output_dir, run_id)
        )
        if chunk_run is not None:
            chunk_run_id = chunk_run.chunk_run_id
    else:
        chunk_run = None
    if scope == RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN and chunk_run is None:
        raise AdbHybridSearchUnavailable("No chunk run was found. Run chunking, then create and save embeddings to ADB.")

    # SDK・評価からの直接呼出では未指定。回答入口の各回は読込済みの結果を渡す。
    runtime = (
        (
            runtime_knowledge
            or build_runtime_knowledge_context(
                question,
                settings.output_dir,
                settings.runtime_knowledge_path,
                rules_confirmation_enabled=settings.runtime_rules_confirmation_enabled,
                rules_answer_enabled=settings.runtime_rules_answer_enabled,
                terms_search_enabled=settings.runtime_terms_search_enabled,
                terms_answer_enabled=settings.runtime_terms_answer_enabled,
            )
        )
        .with_rule_usage(confirmation=settings.runtime_rules_confirmation_enabled, answer=settings.runtime_rules_answer_enabled)
        .with_term_usage(search=settings.runtime_terms_search_enabled, answer=settings.runtime_terms_answer_enabled)
    )
    grounded = " ".join(label for term in runtime.retrieval_terms for label in term.labels())
    candidate_queries = _retrieval_queries(question, retrieval_queries)
    queries, rejected = filter_queries(question, candidate_queries, grounded_text=grounded)
    if trusted_queries:
        queries, _ = _keep_trusted_queries(candidate_queries, queries, rejected, trusted_queries)
    if getattr(settings, "original_query_only", False):
        # 拡張の寄与を測る比較実験用。原質問は _retrieval_queries が常に先頭へ置く。
        queries = queries[:1]
    search_options = {
        "chunk_run_id": chunk_run_id,
        "retrieval_queries": queries,
        "settings": settings,
        "preferred_engine_ids": preferred_engines,
        "candidate_limit": _retrieval_candidate_limit(top_k, settings),
        "retrieval_scope": scope,
        "classification_filter": classification_filter,
        # 注入された検索実装の既存シグネチャを壊さないよう、指定時だけ渡す。
        **({"query_embedder": query_embedder} if query_embedder is not None else {}),
        **({"vector_only_queries": tuple(vector_only_queries)} if vector_only_queries else {}),
        **({"page_ranges": [dict(item) for item in page_ranges]} if page_ranges else {}),
    }
    # 業務語は #848 で SQL の除外条件から融合後の加点に変わったため、0 件時の再検索（#818）は不要になった。
    search_result = search_adb_hybrid_chunks(**search_options)
    active_records = [_stored_chunk_answer_record(chunk) for chunk in search_result.all_chunks]
    ranked_children = [_stored_chunk_answer_record(chunk) for chunk in search_result.child_chunks]
    if not ranked_children:
        return AnswerContext(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="retrieval returned no active child chunks",
        )

    linked_screens: list[tuple[str, str]] = []
    if getattr(settings, "screen_linking_enabled", False):
        added, linked_screens, expansion = _linked_screen_children(question, search_result.all_chunks, ranked_children, settings)
        ranked_children = [
            *ranked_children,
            *(replace(record, metadata={**record.metadata, "retrieval_admission_stage": "screen"}) for record in added),
        ]
        known = {record.chunk_uid for record in active_records}
        active_records.extend(record for record in expansion if record.chunk_uid not in known)
    else:
        _record_catalog_skip("画面の選択", (catalog_skip_reasons or {}).get("screen", "configuration_disabled"))
    if getattr(settings, "article_catalog_enabled", False):
        before_catalog = {retrieval_record_id(record) for record in ranked_children}
        ranked_children, expansion = _catalog_article_children(
            question,
            ranked_children,
            settings,
            document_records=_original_question_children(question, queries, search_options, settings),
        )
        known = {record.chunk_uid for record in active_records}
        active_records.extend(record for record in expansion if record.chunk_uid not in known)
    else:
        _record_catalog_skip("条の選択", (catalog_skip_reasons or {}).get("article", "configuration_disabled"))
    if getattr(settings, "article_catalog_enabled", False):
        ranked_children = [
            record
            if retrieval_record_id(record) in before_catalog
            else replace(record, metadata={**record.metadata, "retrieval_admission_stage": "article"})
            for record in ranked_children
        ]
    with _execution_step("再ランキング候補の選択", "既存の順位を保ち、重複と各経路・総量の上限を適用します。") as step:
        limits = stage_limits(settings, ranked_children)
        configured_limit = max(0, int(getattr(settings, "rerank_candidate_limit", 0)))
        step.input({"総量の指定": configured_limit, "経路ごとの上限": limits, "候補": candidate_snapshot(ranked_children)})
        ranked_children, candidate_trace = select_candidates(ranked_children, configured_limit, stage_limits=limits)
        record_retrieval_stage({"stage": "rerank_input", **candidate_trace})
        step.output({"判断": candidate_trace, "採用候補": candidate_snapshot(ranked_children)})
        step.result(f"再ランキングへ渡す候補 {len(ranked_children)} 件")
    candidate_count = len(ranked_children)
    ranked_children = rerank_records(
        question,
        ranked_children,
        settings,
        enabled=rerank_enabled,
        # 検索に使った検索文（分解・規定の書き方など）でも関連度を測り、最高値で並べる（#2161）。
        extra_queries=queries[1:],
        rerank_phase=rerank_phase,
    )
    record_retrieval_stage({"stage": "post_rerank_policies", "selected_ids": [retrieval_record_id(record) for record in ranked_children]})
    reserve_rank = int(getattr(settings, "screen_linking_reserve_rank", 0) or 0)
    if linked_screens and reserve_rank > 0:
        from rag_engine.retrieval.screen_catalog import reserve_linked_screens

        ranked_children = reserve_linked_screens(ranked_children, linked_screens, reserve_rank)
    if getattr(settings, "document_selection_enabled", False):
        with _execution_step(
            "文書の選択", "候補を文書ごとに集約し、元の質問との並べ替え（rerank）の点数が明らかに低い文書を後回しにします。"
        ) as step:
            step.input(
                {"候補": candidate_snapshot(ranked_children), "起点数": top_k, "文書の点数比率": settings.document_selection_score_ratio}
            )
            kept_children, deferred_children, selection = select_documents(ranked_children, settings, step, top_k=top_k)
            step.output(
                {"判断": selection, "残す候補": candidate_snapshot(kept_children), "後回しの候補": candidate_snapshot(deferred_children)}
            )
            step.result(
                f"{len(selection['selected'])} 文書を起点にします"
                + (f"、{len(selection['deferred'])} 文書は後回し" if selection["deferred"] else "")
            )
    else:
        kept_children, deferred_children, selection = select_documents(ranked_children, settings, top_k=top_k)
    if int(getattr(settings, "max_anchors_per_document", 0) or 0) > 0:
        with _execution_step("文書ごとの起点の上限", "同じ文書の候補が起点を占めないよう、超えた候補を後ろへ回します。") as step:
            step.input({"候補": candidate_snapshot(kept_children), "起点数": top_k, "文書ごとの上限": settings.max_anchors_per_document})
            kept_children = cap_anchors_per_document(kept_children, settings, top_k=top_k, step=step)
            step.output({"処理後の候補": candidate_snapshot(kept_children)})
    bundle = _bundle_context(
        question,
        kept_children,
        active_records,
        settings,
        top_k=top_k,
        neighbor_child_count=neighbor_child_count,
        max_records=max_records,
        max_chars=max_chars,
    )
    return AnswerContext(
        records=list(bundle.records),
        text=bundle.text,
        evidence=bundle.evidence,
        evidence_tree=bundle.evidence_tree,
        expansion_records=tuple(active_records),
        status=bundle.status,
        insufficient_reason=bundle.insufficient_reason,
        ranked_candidates=tuple(ranked_children),
        candidate_count=candidate_count,
        deferred_records=tuple(deferred_children),
        document_selection=selection,
    )


def _record_search_result(step: _ExecutionStep, context: AnswerContext, *, title: str = "回答に渡す根拠", note: str = "") -> None:
    """文書の検索の結果（候補の件数と経路ごとの件数、根拠の件数と上位の根拠）を工程の詳細に書く（#2341）。"""
    step.output(partial(context_snapshot, context))
    record_search_candidates(step, context.ranked_candidates)
    if context.deferred_records:
        step.field("後回しにした候補", f"{len(context.deferred_records)} 件")
    step.result(f"{title} {evidence_summary(context.records)}{note}")
    add_evidence_lines(step, context.records, title=title)


def _grounded_spans(
    question: str,
    context: AnswerContext,
    image_evidence: Sequence[dict[str, Any]],
    runtime_knowledge: RuntimeKnowledgeContext | None,
    *,
    budget: int | None = None,
    whole_records: bool = False,
    preserve_records: bool = False,
) -> list[dict[str, Any]]:
    """全出典へ本文予算を配分して原文を選び、機能キーとタグを付ける。

    budget は原文の総予算（文字。`RAG_ENGINE_EVIDENCE_BUDGET_CHARS`）。None なら `evidence_spans` の既定値 (#1054)。
    whole_records は全部の根拠が予算に入るとき切らずに渡す（章節の拡張。#1436）。
    preserve_records は選択済みの全文を保持する。呼出側が各モデル入力の容量内で分割読取する。
    """
    budget_option: dict[str, Any] = {"budget": int(budget)} if budget else {}
    if whole_records:
        budget_option["whole_records"] = True
    if preserve_records:
        budget_option["preserve_records"] = True
    if runtime_knowledge is not None and runtime_knowledge.guide_steps:
        optional = set(runtime_knowledge.guide_optional_steps)
        budget_option["priority_source_groups"] = [
            sources for step_id, _title, sources in sorted(runtime_knowledge.guide_steps, key=lambda step: step[0] in optional)
        ]
    from rag_engine.generation.grounded import tag_spans
    from rag_engine.retrieval.metadata_context import with_child_contexts

    selection_question = question
    if runtime_knowledge is not None and runtime_knowledge.has_matches:
        selection_question += "\n" + runtime_knowledge.expanded_question
    # parent は context の順（rerank と context 組み立ての結果）のまま渡す。根拠の並びと文字予算の配分はこの順で
    # 決まる。以前は質問の語が見出しらしい行に現れるかの一致（operation_target_score）で並べ替えていたが、同じ
    # 言い回しで正解の画面が異なる質問を区別できず、rerank が上位に置いた正解の parent を後ろへ送っていた (#1094)。
    records = [
        with_child_contexts(r, [c.record for p in context.evidence_tree if p.record is r for c in p.children]) for r in context.records
    ]
    children = [child.record for parent in context.evidence_tree for child in parent.children]
    anchors = _visual_evidence_anchors(context, image_evidence)
    # 指定された子、または指定された親の検索命中子を保護する。隣接子を一律に必須扱いしない。
    # 検索表現と違う元本文も、所属と位置は抜粋時に再検証する。
    guide_sources = {source for sources in budget_option.get("priority_source_groups", ()) for source in sources}
    guide_anchors = [
        replace(child.record, text=child.record.body_text or child.record.text)
        for parent in context.evidence_tree
        for child in parent.children
        if guide_sources & {child.record.chunk_uid, child.record.chunk_id, child.record.id}
        or (child.role == "retrieved_anchor" and guide_sources & {parent.record.chunk_uid, parent.record.chunk_id, parent.record.id})
    ]
    guide_keys = {(child.chunk_uid or child.id, child.source, child.source_run_id) for child in guide_anchors}
    anchors = [
        *guide_anchors,
        *(child for child in anchors if (child.chunk_uid or child.id, child.source, child.source_run_id) not in guide_keys),
    ]
    preparation: list[dict[str, Any]] = []
    spans = evidence_spans(selection_question, records, anchors=anchors, scope_records=children, diagnostics=preparation, **budget_option)
    if preparation:
        record_retrieval_stage(
            {"stage": "generation_evidence_preparation", "input_stage": "prepared_for_generation", "groups": preparation}
        )
    spans = form_field_kinds.with_field_kind_evidence(form_field_kinds.question_field_kinds(question, context), spans)
    return tag_spans(spans)


def _grounded_prompt(
    question: str,
    spans: Sequence[dict[str, Any]],
    context: AnswerContext,
    images: Sequence[dict[str, Any]],
    question_plan: QuestionPlan | None,
    runtime_knowledge: RuntimeKnowledgeContext | None,
    *,
    template: str | None = None,
    field_kind_note: str = "",
    confirmed_premises: Sequence[dict[str, str]] = (),
) -> str:
    """生成の入力のテンプレートへ、質問・画像の対応・機能別の根拠と補助情報を差し込む。

    template 未指定では固定のテンプレート（``ANSWER_INPUT_TEMPLATE``）を使う。
    field_kind_note は根拠の画面の図の項目の種類の注記（#2404。``form_field_kinds.prompt_note``）。
    """
    from rag_engine.generation.grounded import (
        NO_READING_RULES,
        build_context_block,
        evidence_reading_rules,
        evidence_subset_reading_rule,
        guide_rules_context,
    )

    has_guide = runtime_knowledge is not None and bool(
        runtime_knowledge.guide_task_scope
        or runtime_knowledge.guide_condition_notices
        or runtime_knowledge.guide_acquisition
        or runtime_knowledge.guide_steps
        or runtime_knowledge.guide_step_contracts
    )
    preface = "\n\n".join(
        section
        for section in (
            ("confirmed_premises:\n" + json.dumps(list(confirmed_premises), ensure_ascii=False)) if confirmed_premises else "",
            runtime_knowledge.prompt_context() if runtime_knowledge is not None and runtime_knowledge.has_matches else "",
            ("guide_task_scope:\n" + json.dumps(runtime_knowledge.guide_task_scope, ensure_ascii=False))
            if runtime_knowledge is not None and runtime_knowledge.guide_task_scope
            else "",
            # 確定した条件で選んだ分岐の補足（ガイドの文そのもの）。本文の「業務ガイドの案内」と同じ文を、要約と items が
            # 補足と食い違わないよう生成にも渡す（#3823）。
            ("guide_condition_notices:\n" + json.dumps(list(runtime_knowledge.guide_condition_notices), ensure_ascii=False))
            if runtime_knowledge is not None and runtime_knowledge.guide_condition_notices
            else "",
            ("guide_acquisition:\n" + json.dumps(prompt_acquisition_summary(runtime_knowledge.guide_acquisition), ensure_ascii=False))
            if runtime_knowledge is not None and runtime_knowledge.guide_acquisition
            else "",
            field_kind_note,
        )
        if section
    )
    reading_rules = "\n\n".join(
        part
        for part in (
            evidence_reading_rules(spans, attached_images=bool(images)),
            evidence_subset_reading_rule(*_evidence_subset_counts(context, spans)),
            guide_rules_context(scope=bool(runtime_knowledge.guide_task_scope), notices=bool(runtime_knowledge.guide_condition_notices))
            if runtime_knowledge is not None and has_guide
            else "",
        )
        if part
    )
    return render_prompt_template(
        ANSWER_INPUT_TEMPLATE if template is None else template,
        {
            # 質問も根拠と同じ文字の形で渡す（全角の ＨＲＭ と根拠の HRM を同じ語と読ませる。#1350）。
            "question": fold_width(question).strip(),
            # source などの文字列値は文書由来なので、TRUSTED ブロックを閉じる表記を残さない。
            "evidence_metadata": neutralize_boundary_markers(
                json.dumps(_answer_image_metadata(context.records, image_evidence=images), ensure_ascii=False, indent=2)
            ),
            "answer_context": build_context_block(
                question,
                spans,
                preface=preface,
                guide_steps=runtime_knowledge.guide_steps if runtime_knowledge is not None else (),
                guide_contracts=runtime_knowledge.guide_step_contracts if runtime_knowledge is not None else (),
            ),
            "reading_rules": reading_rules or NO_READING_RULES,
        },
    )


# 生成前に同じ機能ユニットの親を補う予算（文字）と件数。原文予算の 1/4 を上限にし、他の候補を追い出さない。
SAME_UNIT_FILL_CHARS = 12000
SAME_UNIT_FILL_RECORDS = 3
_REMEDY_MODES = ("neighbor_children", "parent", "neighbor_parents", "same_function")


def _expand_context_from_pool(
    question: str,
    context: AnswerContext,
    *,
    modes: Sequence[str] = _REMEDY_MODES,
    max_records: int = 6,
    max_chars: int = MAX_CONTEXT_CHARS,
    deferred: bool = True,
) -> tuple[AnswerContext | None, dict[str, Any]]:
    """既取得poolから同機能の根拠を最大 max_records 件足す。既存の根拠を追い出す拡張は採用しない。

    modes は `recover_context_records` の方式を順に試し、最初に見つかった方式の結果を採用する。deferred=True では、文書の選択で後回しにした
    文書の親を近傍より先に足す（是正の回退。#1028）。生成前の同単元の補充（`_fill_same_unit`）は
    deferred=False で呼び、後回しの文書を答えていない要求が出る前に消費しない。
    """
    visible = list(context.records)
    anchors = [r.chunk_uid for r in visible if r.chunk_uid][:3]
    recovered = ContextRecovery()
    mode = ""
    if not recovered.records and deferred and context.deferred_records:
        # 護欄: 覆盖度による回退（生成側。#1028）。答えていない要求があるとき、文書の選択で後回しにした文書の
        # 親を pool から足す。可視の起点と同じ文書しか探さない近傍・同単元の回復では届かないため先に試す。
        # 足す件数は可視の根拠の数まで。下の併合は可視の 2 倍を上限にするので、超えると既存の根拠を押し出して
        # `would_drop_existing_evidence` で却下され、回退が空振りした（実データで確認）。
        limit = min(max_records, max(1, len(visible)))
        context = _hydrate_recovery_context(context, context.deferred_records, limit)
        recovered, mode = _deferred_document_records(context, limit), "deferred_documents"
    if not recovered.records:
        for mode in modes:
            recovered = recover_context_records(
                visible, context.expansion_records, anchors, mode, max_records=max_records, max_chars=max_chars
            )
            if recovered.records:
                break
    if not recovered.records:
        return None, {"remedy": "context", "reason": "no_new_compatible_evidence"}
    # 追加分も tree 付きにする。tree のない context を merge すると既存分まで tree なしの fallback に落ち、
    # 子チャンクの抜粋・prompt-injection 検査・保存 payload の children が失われる。
    added = context_bundle_from_parent_evidence(
        tuple(
            ContextParentEvidence(
                record=r, role="synthesis_parent" if r.chunk_level == "parent" else "child_fallback", reason="grounded_" + mode
            )
            for r in recovered.records
        ),
        max_chars=MAX_CONTEXT_CHARS,
    )
    if not added.records:
        return None, {"remedy": "context", "reason": "context_budget_exhausted"}
    candidate = merge_evidence(
        context,
        AnswerContext(records=list(added.records), text=added.text, evidence=added.evidence, evidence_tree=added.evidence_tree),
        question,
        max_records=max(1, len(visible)),
        max_chars=max(MAX_CONTEXT_CHARS, max_chars) * 2,
    )
    if not {r.chunk_uid or r.id for r in visible} <= {r.chunk_uid or r.id for r in candidate.records}:
        return None, {"remedy": "context", "reason": "would_drop_existing_evidence"}
    candidate = replace(
        candidate,
        expansion_records=context.expansion_records,
        preferred_child_ids=context.preferred_child_ids,
        ranked_candidates=context.ranked_candidates,
        candidate_count=context.candidate_count,
        deferred_records=() if mode == "deferred_documents" else context.deferred_records,
        document_selection={**context.document_selection, "restored": True} if mode == "deferred_documents" else context.document_selection,
    )
    return candidate, {"remedy": "context", "mode": mode, "added_chunk_ids": [r.chunk_uid for r in recovered.records]}


def _adds_new_records(current: AnswerContext, restored: AnswerContext) -> bool:
    """戻した context に、今の context に無い根拠（親）があるか。"""
    known = {record.chunk_uid or record.id for record in current.records}
    return any((record.chunk_uid or record.id) not in known for record in restored.records)


def _hydrate_recovery_context(context: AnswerContext, candidates: Sequence[AnswerRecord], limit: int) -> AnswerContext:
    """明示した回復先だけを既存の件数枠で読む。未読の親が無いことを根拠不足と取り違えない。"""
    known = {retrieval_record_id(record): record for record in context.expansion_records}
    selected: dict[tuple[str, ...], AnswerRecord] = {}
    for child in candidates:
        if child.chunk_level != CHILD_CHUNK_LEVEL:
            continue
        strategy = child.metadata.get("chunk_strategy")
        parent = child.parent_chunk_uid if strategy in (None, "small_to_big", "small_to_big_parent_child") else ""
        if parent and parent in known:
            continue
        key = (*retrieval_artifact_key(child), parent or retrieval_record_id(child))
        selected.setdefault(key, child)
        if len(selected) >= max(0, limit):
            break
    if not selected or limit <= 0:
        return context
    loaded = injected_context_chunks(anchors=list(selected.values()), settings=None)
    if loaded is None:
        return context
    known.update((retrieval_record_id(record), record) for record in map(_stored_chunk_answer_record, loaded))
    record_retrieval_stage(
        {
            "stage": "context_recovery",
            "anchor_limit": limit,
            "anchor_ids": [retrieval_record_id(record) for record in selected.values()],
            "loaded_ids": [str(chunk.chunk_uid) for chunk in loaded],
        }
    )
    return replace(context, expansion_records=tuple(known.values()))


def _deferred_document_records(context: AnswerContext, max_records: int) -> ContextRecovery:
    """後回しにした文書の child の親を、順位順に最大 max_records 件、既取得 pool から返す (#1028)。"""
    context = _hydrate_recovery_context(context, context.deferred_records, max_records)
    pool = {r.chunk_uid or r.id: r for r in context.expansion_records}
    visible = {r.chunk_uid or r.id for r in context.records}
    selected: dict[str, AnswerRecord] = {}
    for child in context.deferred_records:
        strategy = child.metadata.get("chunk_strategy")
        parent_mode = strategy in (None, "small_to_big", "small_to_big_parent_child")
        parent = (pool.get(child.parent_chunk_uid) or child) if parent_mode and child.parent_chunk_uid else child
        uid = (parent.chunk_uid or parent.id) if parent is not None else ""
        if not uid or uid in visible or uid in selected:
            continue
        selected[uid] = cast(AnswerRecord, parent)
        if len(selected) >= max_records:
            break
    return ContextRecovery(tuple(selected.values()), (), "expanded" if selected else "no_new_compatible_evidence")


def _fill_same_unit(question: str, context: AnswerContext) -> tuple[AnswerContext, dict[str, Any]]:
    """生成の前に、選ばれた親と同じ機能ユニットの親を既取得 pool から補う (#666)。

    1 機能が複数の親に割れて検索が一部しか選ばないと、モデルは残りの手順を画面の生成説明でしか
    見られず原文引用ができない。再検索や I/O はせず、同じ文書・版・ユニットの親だけを
    `SAME_UNIT_FILL_CHARS` / `SAME_UNIT_FILL_RECORDS` の範囲で足す。何を足したかは trace に残す。
    """
    before = {r.chunk_uid or r.id for r in context.records}
    filled, reason = _expand_context_from_pool(
        question,
        context,
        modes=("same_unit",),
        max_records=SAME_UNIT_FILL_RECORDS,
        max_chars=SAME_UNIT_FILL_CHARS,
        deferred=False,
    )
    if filled is None:
        return context, {"applied": False, "reason": reason.get("reason", "")}
    added = [r.chunk_uid or r.id for r in filled.records if (r.chunk_uid or r.id) not in before]
    return filled, {"applied": bool(added), "added_chunk_ids": added}


# tag_spans が検査用に span へ付ける、同じ機能・文書・隣接ページの本文の複製。span 数 n に対して O(n²) の本文になるため
# 回答 JSON と UI へ渡す trace には残さない (#829)。読み手（answer_payload・viewer）は evidence_id / source_id / text / page を使う。
_CHECK_ONLY_SPAN_KEYS = frozenset({"screen_texts", "function_texts", "document_texts", "adjacent_texts"})


def _public_span(span: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in span.items() if key not in _CHECK_ONLY_SPAN_KEYS}


def _evidence_budget_trace(settings: Settings, evidence_budget_chars: int | None, spans: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """生成に渡した根拠の文字予算の診断（#3776）。技術の数値だけを残し、本文・プロンプトは残さない。

    effective_chars は単回の実効上限。章節取得の予算は section_read_budget_chars で分ける。
    本文・出典情報・行番号等の書式を含む描画済みブロックを数える。全文読取の被覆は
    generation_input_budget のバッチ範囲・状態で確認し、最終生成へ渡す抜粋とは区別する。
    """
    from rag_engine.generation.grounded import evidence_block_text, evidence_metadata_text
    from rag_engine.generation.section_expansion import estimate_tokens

    configured = int(getattr(settings, "evidence_budget_chars", 0) or 0) or EVIDENCE_BUDGET_CHARS
    effective = configured
    block = evidence_block_text(spans)
    text_chars = sum(len(str(span.get("text") or "")) for span in spans)
    metadata_chars = sum(len(evidence_metadata_text(span)) for span in spans)
    return {
        "configured_chars": configured,
        "effective_chars": effective,
        "source": "configured",
        "section_read_budget_chars": int(evidence_budget_chars or 0),
        "effective_exceeds_configured": effective > configured,
        "span_count": len(spans),
        "budget_counts": "body_metadata_and_format",
        "evidence_text_chars": text_chars,
        "evidence_metadata_chars": metadata_chars,
        "evidence_format_chars": max(0, len(block) - text_chars - metadata_chars),
        "evidence_block_chars": len(block),
        "evidence_block_tokens_estimate": estimate_tokens(block),
    }


def _span_document_id(span: Mapping[str, Any] | None) -> str:
    """根拠の箇所の文書 id（``document_scope`` の末尾の backend の文書 ID。#2828）。"""
    scope = (span or {}).get("document_scope") or ()
    return str(scope[-1] or "") if scope else ""


def _source_question_scores(context: AnswerContext) -> dict[str, float | None]:
    """根拠の出典の ID ごとの、原質問への rerank の点数。親は親と子の最高値（#4113。分割読取で原文を補う順）。"""
    scores: dict[str, float | None] = {}

    def keep(key: str, score: float | None) -> None:
        if key and (scores.get(key) is None or (score is not None and score > (scores[key] or 0.0))):
            scores[key] = score

    for record in context.records:
        keep(record.chunk_uid or record.id, question_rerank_score(record))
    for parent in context.evidence_tree:
        parent_key = parent.record.chunk_uid or parent.record.id
        for record in (parent.record, *(child.record for child in parent.children)):
            keep(parent_key, question_rerank_score(record))
    return scores


def _invalid_structured_output(exc: Exception) -> bool:
    """LLM の応答が構造化出力の契約を満たさなかった例外か（再試行の後も。#2772）。

    完了の event の応答に本文が無いまま送り直しも尽きたとき（#4197）も、使える出力が無い点は同じなので含める。
    """
    from rag_engine.adapters.oci import LlmRunawayOutput, LlmStreamOutputMissing, is_invalid_structured_output

    return isinstance(exc, LlmRunawayOutput | LlmStreamOutputMissing) or is_invalid_structured_output(exc)


def synthesize_grounded_answer(
    question: str,
    context: AnswerContext,
    settings: Settings,
    *,
    question_plan: QuestionPlan | None = None,
    runtime_knowledge: RuntimeKnowledgeContext | None = None,
    image_evidence: Sequence[dict[str, Any]] = (),
    image_prompt_mode: str,
    answer_llm_provider: str | None = None,
    unread_sections: Sequence[Any] = (),
    unread_documents: Collection[str] = (),
    evidence_budget_chars: int | None = None,
    escalation_probe: bool = False,
    shared_steps: model_cascade.SharedSteps | None = None,
) -> GroundedAnswer:
    """主張と引用の対で回答を 1 回生成し、引用の実在と形式だけを確かめて本文にする（#4714）。

    unread_sections は章節の拡張が上限で読めなかった章節（#1436）。本文の「回答に未反映の関連章節」に出し、
    回答を条件つきにする。出すのは、回答が根拠にした文書と ``unread_documents`` の章節だけ（#2828）。
    evidence_budget_chars は章節を広げたときの原文の予算（None は `settings.evidence_budget_chars`）。
    検索で候補が残らなければ、モデルを呼ばず定型文を返す。

    escalation_probe はモデルのカスケード（#4581）の 1 つ目のモデルの実行で、構造化出力が契約を満たさなければ
    ``model_cascade.EscalationRequested`` を送出する。
    """
    shared = shared_steps if shared_steps is not None else model_cascade.SharedSteps()
    if runtime_knowledge is not None:
        runtime_knowledge = runtime_knowledge.with_rule_usage(
            confirmation=settings.runtime_rules_confirmation_enabled, answer=settings.runtime_rules_answer_enabled
        ).with_term_usage(search=settings.runtime_terms_search_enabled, answer=settings.runtime_terms_answer_enabled)
    from rag_engine.generation import grounded
    from rag_engine.generation.batch_output_repair import batch_failure_summary
    from rag_engine.generation.batched_evidence import prepare_batched_evidence
    from rag_engine.generation.document_background import DocumentBackgroundReader, without_embedded_background
    from rag_engine.generation.input_budget import GenerationInputBudgetExceeded
    from rag_engine.models.llm import GroundedItem
    from rag_engine.retrieval.search_stages import DOCUMENT_INFO_STAGE

    budget_chars = evidence_budget_chars or getattr(settings, "evidence_budget_chars", None)
    document_background_reader = DocumentBackgroundReader()
    input_diagnostic: dict[str, Any] = {}
    if not context.records:
        return GroundedAnswer(
            _with_guide_acquisition_notices(_no_candidates_response(context), runtime_knowledge),
            context,
            tuple(image_evidence),
            image_prompt_mode,
        )
    provider = answer_llm_provider
    generation_settings = settings_for_llm_purpose(settings, LLM_PURPOSE_GENERATION)
    images, mode = tuple(image_evidence), image_prompt_mode
    context, unit_fill = _fill_same_unit(question, context)
    if not shared.term_usage_recorded:
        _record_term_usage(runtime_knowledge, answer=True)
        shared.term_usage_recorded = True
    document_background_reader.remember(context)
    context = without_embedded_background(context)
    field_kinds = form_field_kinds.question_field_kinds(question, context)
    resolved_conditions = form_field_kinds.resolve_conditions(field_kinds, settings.evidence_conditions)
    evidence_plans = tuple(getattr(settings, "evidence_plans", ()) or ())
    runtime_knowledge = form_field_kinds.with_resolved_plan(runtime_knowledge, resolved_conditions, evidence_plans)
    spans = _grounded_spans(question, context, images, runtime_knowledge, budget=budget_chars, preserve_records=True)
    with _execution_step(DOCUMENT_INFO_STAGE) as background_step:
        background_step.input(
            lambda spans=spans: {
                "selected_source_ids": [value["source_id"] for value in spans],
                "selected_documents": [list(key) for key in dict.fromkeys(tuple(value["document_scope"]) for value in spans)],
                "timing": "正式生成の根拠を選択した後",
            }
        )
        context, spans, background_detail = document_background_reader.append(question, context, spans)
        background_step.output(background_detail)
        background_step.field("選択した文書数", background_detail["selected_documents"])

    def render_generation(selected: Sequence[dict[str, Any]]) -> str:
        return _grounded_prompt(
            question,
            selected,
            context,
            images,
            question_plan,
            runtime_knowledge,
            field_kind_note=form_field_kinds.prompt_note(field_kinds, resolved_conditions, selected),
            confirmed_premises=confirmed_premise_records(settings.confirmed_premises, settings.confirmed_premise_sources),
        )

    guided = bool(runtime_knowledge is not None and runtime_knowledge.guide_step_contracts)
    generation_failure = ""
    generation_failure_stage = "answer.generate"
    generation_failure_detail: dict[str, Any] = {}
    structured_output_failed = False
    with _execution_step("回答文の生成と引用の確認", "主張と原文引用の対で回答を作り、引用が資料に実在するかを確かめます。") as step:
        step.input(
            lambda spans=spans: {
                "質問": question,
                "根拠": [
                    {"evidence_id": span.get("evidence_id"), "source_id": span.get("source_id"), "label": span.get("label")}
                    for span in spans
                ],
            }
        )
        preparing_evidence = False
        failed_batches: list[dict[str, Any]] = []
        try:
            generation_system = grounded.generation_system_prompt(
                assume_unconfirmed=runtime_knowledge is not None and runtime_knowledge.assume_unconfirmed
            )
            preparing_evidence = True

            def selected_generation_schema(values: Sequence[dict[str, Any]], *, guided: bool = guided) -> Mapping[str, Any]:
                return grounded.generation_output_model(
                    question, guided=guided, unit_catalog=grounded.generation_unit_catalog(values)
                ).model_json_schema()

            spans, prompt, input_diagnostic = prepare_batched_evidence(
                spans,
                generation_settings,
                question=question,
                read=lambda system, user, schema: grounded.call_with_model_purpose(
                    "evidence.read_batch", parse_text_response, system, user, generation_settings, schema, provider_id=provider
                ),
                unusable_output=_invalid_structured_output,
                tag=lambda values: grounded.tag_spans(values),
                render=render_generation,
                evidence_text=grounded.evidence_block_text,
                system=generation_system,
                schema_for=selected_generation_schema,
                schema=grounded.generation_output_model(question, guided=guided).model_json_schema(),
                provider_id=provider,
                failed_batches=failed_batches,
                source_scores=_source_question_scores(context),
            )
            preparing_evidence = False
            current = grounded.run_round(
                question,
                spans,
                generation_settings,
                prompt=prompt,
                provider_id=provider,
                parse_text=parse_text_response,
                parse_images=parse_multimodal_response,
                guide_steps=runtime_knowledge.guide_steps if runtime_knowledge else (),
                guide_step_contracts=runtime_knowledge.guide_step_contracts if runtime_knowledge else (),
                assume_unconfirmed=runtime_knowledge.assume_unconfirmed if runtime_knowledge else False,
                image_paths=[i["prompt_path"] for i in images if i.get("prompt_path")] if mode == "vision_attachments" else [],
                invalid_output=_invalid_structured_output,
                confirmed_premises=settings.confirmed_premises,
                confirmed_premise_sources=settings.confirmed_premise_sources,
                guide_task_scope=runtime_knowledge.guide_task_scope if runtime_knowledge else (),
            )
            if batch_calls := input_diagnostic.get("batch_count", 0):
                current.timing["batch_reading_calls"] = batch_calls
                current.timing["generation_calls"] = current.timing.get("generation_calls", 0) + batch_calls
            if note := llm_reasoning_effort_note(settings, LLM_PURPOSE_GENERATION, provider):
                step.add("生成の" + note)
        except Exception as exc:
            timed_out = deadline_cut_short()
            input_limited = isinstance(exc, GenerationInputBudgetExceeded)
            if input_limited:
                input_diagnostic = exc.diagnostic
            invalid_draft = not timed_out and (
                isinstance(exc, grounded.DraftGenerationInvalid) or _invalid_structured_output(exc) or input_limited
            )
            if not timed_out and not invalid_draft:
                raise
            generation_failure = str(exc) if isinstance(exc, grounded.DraftGenerationInvalid) else type(exc).__name__
            generation_failure_stage = "evidence.batch_read" if preparing_evidence else "answer.generate"
            if invalid_draft and not input_limited:
                structured_output_failed = True
                if escalation_probe:
                    raise model_cascade.EscalationRequested((SIGNAL_STRUCTURED_OUTPUT_INVALID,)) from exc
            generation_failure_detail = (
                {"category": "input_budget_exceeded", "reason": str(input_diagnostic.get("reason", ""))}
                if input_limited
                else {"category": "deadline"}
                if timed_out
                else batch_failure_summary(failed_batches[-1], exc)
                if preparing_evidence and failed_batches
                else grounded.generation_failure_detail(exc)
            )
            logger.warning(
                "answer generation fell back to document quotes: stage=%s error=%s detail=%s",
                generation_failure_stage,
                generation_failure,
                json.dumps(generation_failure_detail, ensure_ascii=False),
            )
            current = grounded.timed_out_round(question)
            step.result("回答文を生成できなかったため、質問に関係する資料の記載だけを示します")
            step.output({"error_type": type(exc).__name__, "input_budget": input_diagnostic})
        else:
            step.result(f"説明 {current.verified} 件")
            step.output(current.trace)
    rounds = [
        {
            **current.trace(),
            **({"error": generation_failure} if generation_failure else {}),
            **({"timing": {"generation_calls": input_diagnostic.get("batch_count", 0)}} if generation_failure else {}),
        }
    ]
    guide_notices = runtime_knowledge.guide_notices if runtime_knowledge is not None else ()
    guide_notes = (runtime_knowledge.guide_title, guide_notices) if runtime_knowledge is not None and guide_notices else None
    guide_notes_first = bool(guide_notes and runtime_knowledge is not None and runtime_knowledge.guide_condition_notices)
    published = [entry for entry in current.checked if entry.span and entry.published]
    gaps = [entry.item.text for entry in current.checked if entry.item.kind == "gap"]
    from rag_engine.generation.section_expansion import answer_unread_view

    unread_view = answer_unread_view(unread_sections, {_span_document_id(entry.span) for entry in published}, unread_documents)
    unread = [*unread_view.lines, *(runtime_knowledge.guide_acquisition_notices if runtime_knowledge else ())]
    if excluded_count := input_diagnostic.get("excluded_span_count", 0):
        unread.append(current_pack().text("answer_labels", "answer_evidence_budget_limited").format(count=excluded_count))
    settled = grounded.settle_confirmations(
        current.draft,
        known_conditions=(*settings.known_conditions, *((item.label, item.value) for item in resolved_conditions)),
    )
    confirmations = grounded.confirmation_lines(current.draft, question, spans, settled)
    user_conditions = list(settled.user)
    confirmation_section = [*grounded.user_condition_lines(settled), *confirmations]
    external_data_required = requires_environment_data(
        settled, items=current.draft.confirmation_items(), external_data_required=current.draft.external_data_required
    )
    shown = [entry for entry in current.checked if entry.span]
    if generation_failure:
        # 生成の出力が無い。質問に関係する資料の記載だけを示す。
        shown = grounded.deadline_document_quotes(question, spans)
        summary = grounded.answer_labels().neutral_summary
        rendered_items = shown
    else:
        summary = str(current.summary or "").strip()
        if not summary and not published and not any(entry.published for entry in current.applications):
            summary = grounded.answer_labels().insufficient_evidence_summary
        rendered_items = current.checked
    rendered = grounded.render_answer(
        summary if published or not confirmation_section else grounded.answer_labels().confirmation_only_summary,
        rendered_items,
        confirmation_section,
        unverified=unread,
        applications=current.applications,
        subquestions=[unit for unit in current.requests if unit.get("subquestion")],
        guide_notes=guide_notes,
        guide_notes_first=guide_notes_first,
    )
    unsupported_parts = sum(1 for note in current.dropped if not note.get("ignored")) + sum(
        1 for entry in current.applications if not entry.published
    )
    confidence = "low" if not published else current.draft.confidence
    unsettled = [e for e in published if e.item.applies != "matched"]
    if confidence == "high" and (gaps or unsettled or unread or unsupported_parts):
        confidence = "medium"
    from rag_engine.generation.provided_scope import capture_provided_scope

    envelope_entries = [entry for entry in shown if entry.span]
    trace: dict[str, Any] = {
        "provided_evidence_scope": capture_provided_scope(current.citation_trace),
        "pipeline": "grounded",
        "task_contract": task_contract(question),
        "selected_evidence": [_public_span(s) for s in spans],
        "evidence_budget": _evidence_budget_trace(settings, evidence_budget_chars, spans),
        "generation_input_budget": input_diagnostic,
        "same_unit_fill": unit_fill,
        "retrieval_counts": _retrieval_counts(context, spans),
        "rounds": rounds,
        "escalation_signals": list(escalation_reasons((SIGNAL_STRUCTURED_OUTPUT_INVALID,) if structured_output_failed else ())),
        **(
            {"generation_failure": {"error": generation_failure, "stage": generation_failure_stage, **generation_failure_detail}}
            if generation_failure
            else {}
        ),
        "llm_calls": rounds[0].get("timing", {}).get("generation_calls", 1),
        "final_fact_count": len(published),
        **({"form_field_kinds": form_field_kinds.trace_payload(field_kinds, resolved_conditions, evidence_plans)} if field_kinds else {}),
        "grounded_draft": draft_structure(current.draft, settled),
        "applications": [application.to_payload() for application in current.applications],
        **({"unread_sections": unread_view.to_payload()} if unread_view.hidden or unread_view.overflow else {}),
        "citation_lines": rendered.citation_lines,
    }
    envelope = build_envelope(
        requests=current.requests,
        items=[envelope_item(entry) for entry in envelope_entries],
        gaps=[*gaps, *unread],
        confirmations=confirmations,
        user_conditions=user_conditions,
        external_data_required=external_data_required,
        reference_materials=_string_list(current.draft.reference_materials),
        document_items=[text for text in settled.document if f"{REFERENCE_CONFIRMATION_PREFIX}{text}" in confirmations],
        unsupported_parts=unsupported_parts,
    )
    shown_ids: list[tuple[grounded.CheckedItem, list[str]]] = []
    shown_uses: dict[str, str] = {}
    for entry in grounded.cited_entries(shown):
        if entry.span is None:
            continue
        exact, _candidates = grounded.citation_children(entry.span, citation_resolution=entry.citation_resolution)
        ids = exact or [entry.span["source_id"]]
        shown_ids.append((entry, ids))
        for used_id in ids:
            shown_uses[used_id] = merge_answer_use(shown_uses.get(used_id), grounded.answer_use(entry)) or ""
    response = _normalize_answer_response(
        AnswerResponse(
            envelope=envelope,
            answer_text=rendered.text,
            confidence=confidence,
            question_type=tuple(_string_list(current.draft.question_type)),
            cited_evidence=tuple(
                _cited_evidence_entries(
                    [
                        {
                            "evidence_id": used_id,
                            "source": cast(dict[str, Any], e.span).get("source", ""),
                            "page": cast(dict[str, Any], e.span).get("page", ""),
                            "look_at": "引用",
                            "visible_evidence": e.item.quote[:200],
                            "answer_use": shown_uses.get(used_id) or "",
                        }
                        for e, ids in shown_ids
                        for used_id in ids
                    ]
                )
            ),
            reasoning_summary=f"説明 {current.verified} 件",
            insufficient_reason="\n".join(dict.fromkeys([*gaps, *unread])),
            needs_human_review=bool(
                gaps or unread or external_data_required or current.draft.reference_materials or settled.document or unsupported_parts
            ),
            external_data_required=external_data_required,
            external_data_items=settled.system,
            raw_text=current.draft.model_dump_json(),
            evidence_facts=tuple(
                {"source_id": e.span["source_id"], "evidence_id": e.span["evidence_id"], "quote": e.item.quote}
                for e in grounded.cited_entries(published)
                if e.span
            ),
            generation_trace=trace,
        )
    )
    response = replace(
        response, generation_trace={**(response.generation_trace or {}), "document_background": document_background_reader.rounds}
    )
    return GroundedAnswer(response, context, tuple(images), mode)


def _no_candidates_response(context: AnswerContext) -> AnswerResponse:
    """検索で候補が残らなかった回答（モデルを呼ばない）。"""
    text = answer_labels().insufficient_evidence_summary
    return _normalize_answer_response(
        AnswerResponse(
            envelope=build_envelope(requests=(), items=(), gaps=[], confirmations=(), external_data_required=False),
            answer_text=text,
            confidence="low",
            insufficient_reason=text,
            needs_human_review=True,
            generation_trace={"pipeline": "grounded", "llm_calls": 0, "retrieval_counts": _retrieval_counts(context, ())},
        )
    )


def _evidence_subset_counts(context: AnswerContext, spans: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """モデルに渡した根拠の数と検索の候補の数（どちらも検索の子の単位）。親の根拠は束ねた子の数で数える。"""
    shown_parents = {str(span["source_id"]) for span in spans}
    shown = sum(
        sum(1 for child in parent.children if child.role == "retrieved_anchor")
        for parent in context.evidence_tree
        if (parent.record.chunk_uid or parent.record.id) in shown_parents
    )
    return (shown or len(shown_parents)), int(_retrieval_counts(context, spans)["candidate_count"])


def _retrieval_counts(context: AnswerContext, spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """検索の件数を段ごとに記録する（基準で全部落ちた場合と、検索で見つからなかった場合を区別する）。

    candidate_count は基準（rerank の関連度の下限・採用件数の上限）で絞り込む前の候補、found は基準を通った候補、
    passed_to_model はモデルに渡した根拠の単位の数（生成を呼ばない経路は 0）。
    """
    ranked = context.ranked_candidates or tuple(context.records)
    reranked = [record.metadata["rerank"] for record in ranked if isinstance(record.metadata.get("rerank"), dict)]
    scores = [float(row["relevance_score"]) for row in reranked if row.get("relevance_score") is not None]
    return {
        # 件数を持たない context（SDK の組み立てなど）でも「候補 < 通過」にしない。
        "candidate_count": max(context.candidate_count, len(ranked)),
        "found": len(ranked),
        "passed_to_model": len(spans),
        "top_score": max(scores) if scores else None,
        "threshold": next((row["min_relevance_score"] for row in reranked if row.get("min_relevance_score") is not None), None),
    }


_ESCALATION_REASON_LABELS = {"structured_output_invalid": "生成の出力が形式の検査を通らなかった"}


def synthesize_with_model_cascade(
    question: str,
    context: AnswerContext,
    settings: Settings,
    *,
    answer_llm_provider: str | None = None,
    image_evidence: Sequence[dict[str, Any]] = (),
    image_prompt_mode: str,
    **options: Any,
) -> GroundedAnswer:
    """回答の生成の段を、出力が形式の検査を通らなかったときだけ 2 つ目のモデルでやり直す（モデルのカスケード。#4581）。

    2 つ目のモデル（``settings.escalation_llm``）が無ければ、``synthesize_grounded_answer`` と同じ。
    あれば 1 つ目のモデルで生成し、構造化出力が契約を満たさなければ、検索の結果（``context``）をそのまま使って
    生成の段を 2 つ目のモデルでやり直す。1 回の質問で昇格は 1 回だけ。2 つ目のモデルも通らなければ、
    その結果を既存の失敗の扱いで返す。
    カスケードの記録（``generation_trace["model_cascade"]``）は、昇格しなかった実行にも残す（昇格率を数える）。
    """
    primary_provider = answer_llm_provider or settings.default_answer_llm
    cascade = model_cascade.answer_cascade(settings, primary_provider)
    synthesize_options = {"image_evidence": image_evidence, "image_prompt_mode": image_prompt_mode, **options}
    if not cascade.enabled:
        return synthesize_grounded_answer(question, context, settings, answer_llm_provider=answer_llm_provider, **synthesize_options)
    shared = model_cascade.SharedSteps()
    first: GroundedAnswer | None
    try:
        first = synthesize_grounded_answer(
            question,
            context,
            settings,
            answer_llm_provider=answer_llm_provider,
            escalation_probe=True,
            shared_steps=shared,
            **synthesize_options,
        )
    except model_cascade.EscalationRequested as exc:
        first, signals = None, exc.signals
    else:
        signals = tuple((first.response.generation_trace or {}).get("escalation_signals") or ())
    decision = decide_escalation(signals, cascade=cascade, escalated=False)
    if not decision.escalate:
        assert first is not None
        return _with_model_cascade(first, model_cascade_record(cascade, escalated=False))
    second_settings = model_cascade.escalated_settings(settings)
    escalation_provider = settings.escalation_llm
    with _execution_step(
        "高性能モデルで回答し直す",
        "最初のモデルの出力が検査を通らなかったため、取得した根拠をそのまま使い、出力の検査を通らなかったときのモデルで回答を作り直します。",
    ) as step:
        step.input(
            {
                "最初のモデル": cascade.primary_model,
                "回答し直すモデル": cascade.escalation_model,
                "理由": list(decision.reasons),
            }
        )
        step.field("回答し直すモデル", cascade.escalation_model)
        step.field("理由", join_values([_ESCALATION_REASON_LABELS.get(reason, reason) for reason in decision.reasons]))
        step.result(f"{cascade.escalation_model} で回答を作り直します")
        step.output({"escalated": True, "reasons": list(decision.reasons), "stage": model_cascade.ESCALATION_STAGE})
    # 根拠の画像は、2 つ目のモデルが読めるときだけ添付する（#3444。読めなければ文字の説明で答える）。
    second_mode = (
        _image_prompt_mode(image_evidence, second_settings, escalation_provider)
        if image_prompt_mode == "vision_attachments"
        else image_prompt_mode
    )
    second = synthesize_grounded_answer(
        question,
        context,
        second_settings,
        answer_llm_provider=escalation_provider,
        shared_steps=shared,
        **{**synthesize_options, "image_prompt_mode": second_mode},
    )
    return _with_model_cascade(
        second,
        model_cascade_record(cascade, escalated=True, reasons=decision.reasons, stage=model_cascade.ESCALATION_STAGE),
    )


def _with_model_cascade(answer: GroundedAnswer, record: dict[str, Any]) -> GroundedAnswer:
    response = replace(answer.response, generation_trace={**(answer.response.generation_trace or {}), "model_cascade": record})
    return replace(answer, response=response)


def draft_structure(draft: Any, settled: Any) -> dict[str, Any]:
    """回答の診断に残す草稿の構造（#1492）。

    確認の項目（text と事実）・規則で分けた結果・説明の種類と適用（本文・引用は除く）・別の資料・旧版として
    原文のみ提示にした根拠。大きさは草稿の上限（items 40 件・確認の項目 10 件）で抑えられる。
    """
    return {
        "confirmation_items": draft.confirmation_items(),
        "settled_confirmations": settled.diagnostics(),
        "external_data_required": bool(draft.external_data_required),
        "reference_materials": _string_list(draft.reference_materials),
        "request_coverage": [plan.model_dump(mode="json") for plan in draft.request_coverage],
        "summary_item_indexes": list(draft.summary_item_indexes),
        "summary_application_indexes": list(draft.summary_application_indexes),
        "items": [
            {
                "kind": item.kind,
                "request_id": item.request_id,
                "evidence_id": item.evidence_id,
                "applies": item.applies,
                "condition": item.condition,
            }
            for item in draft.items
        ],
        "confidence": draft.confidence,
    }


def _request_coverage_budget(settings: Settings) -> CoverageBudget:
    """設定から欠けた要求の再検索の予算を作る (#1279)。"""
    return CoverageBudget(
        enabled=bool(getattr(settings, "request_coverage_retrieval_enabled", False)),
        max_queries=max(0, int(getattr(settings, "request_coverage_max_queries", 0) or 0)),
        max_chunks=max(0, int(getattr(settings, "request_coverage_max_chunks", 0) or 0)),
        deadline_seconds=max(0.0, float(getattr(settings, "request_coverage_deadline_seconds", 0.0) or 0.0)),
        max_subquestions=max(0, int(getattr(settings, "request_coverage_max_subquestions", 0) or 0)),
        chunks_per_subquestion=max(0, int(getattr(settings, "request_coverage_chunks_per_subquestion", 0) or 0)),
    )


def _cover_guide_steps(
    question: str,
    context: AnswerContext,
    settings: Settings,
    plan: GuidePlan,
    *,
    extra_budget: ExtraRetrievalBudget,
    run_id: Any,
    preferred_engines: Sequence[str],
    top_k: int,
    neighbor_child_count: int,
    rerank_enabled: bool,
    retrieval_scope: str,
    classification_filter: ClassificationFilter | None,
    runtime_knowledge: RuntimeKnowledgeContext | None,
    clock: Callable[[], float] = monotonic,
) -> tuple[AnswerContext, dict[str, Any], tuple[tuple[str, str, tuple[str, ...]], ...]]:
    """業務ガイドの手順ごとに検索し、手順の根拠と参照の頁の根拠を回答の材料に足す (#2395)。

    検索は embedding・hybrid 検索・rerank だけで、文書の選択・画面目録・条の目録（LLM を呼ぶ）は使わない。
    足した根拠は既存の根拠の後ろに置き、既存の根拠は追い出さない。戻り値は (回答に渡す context, 記録,
    手順ごとの根拠（手順 id, 題名, 根拠の chunk_uid の列）)。
    """
    targets = required_guide_targets(plan)
    trace: dict[str, Any] = {
        "schema_version": GUIDE_STEP_TRACE_SCHEMA_VERSION,
        "guide_id": plan.guide_id,
        "revision": plan.revision,
        "llm_calls": 0,
    }
    titles = {step.step_id: step.title for step in plan.steps}
    if not bool(getattr(settings, "guide_step_retrieval_enabled", False)):
        extra_budget.reserve_required(0)
        trace.update(
            skipped="disabled",
            unsearched_step_ids=[step.step_id for step in plan.steps],
            required_acquisition={
                "targets": [
                    {
                        **target.to_payload(),
                        "execution": "not_run",
                        "reason": "disabled",
                        "acquisition": "unknown",
                        "preparation": "unknown",
                        "retained_source_ids": [],
                        "retained_body_chars": 0,
                        "actual_model_input": "not_observed",
                    }
                    for target in targets
                ],
                "incomplete_step_ids": list(dict.fromkeys(owner for target in targets for owner in target.owner_ids)),
            },
            budget=extra_budget.to_payload(),
        )
        return context, trace, tuple((step.step_id, step.title, ()) for step in plan.steps)

    catalog_policy = _catalog_skip_policy(settings, "guide_step")
    search_settings = replace(
        settings,
        document_selection_enabled=False,
        screen_linking_enabled=False,
        article_catalog_enabled=False,
    )
    per_step = max(1, int(getattr(settings, "guide_step_chunks_per_step", 1) or 1))
    reference_cap = max(0, int(getattr(settings, "guide_reference_max_chunks", 0) or 0))
    per_query = max(1, min(int(top_k), max(per_step, reference_cap)))

    def retrieve(text: str, queries: tuple[str, ...], page_ranges: list[dict[str, Any]]) -> AnswerContext:
        return build_adb_hybrid_answer_context(
            text,
            run_id,
            preferred_engines,
            search_settings,
            top_k=per_query,
            neighbor_child_count=neighbor_child_count,
            max_records=per_query,
            retrieval_queries=queries,
            rerank_phase="guide_step",
            catalog_skip_reasons=catalog_policy,
            rerank_enabled=rerank_enabled,
            retrieval_scope=retrieval_scope,
            classification_filter=classification_filter,
            runtime_knowledge=runtime_knowledge,
            page_ranges=page_ranges,
        )

    started = clock()
    results, acquisitions, stop = run_required_guide_searches(
        question,
        targets,
        budget=extra_budget,
        existing_records=context.records,
        deadline_seconds=float(getattr(settings, "guide_step_deadline_seconds", 0.0) or 0.0),
        retrieve=retrieve,
        clock=clock,
    )
    # 必須参照の取得後、参照なしの補助検索と任意手順だけを通常の枠から実行する。
    optional_steps = tuple(
        replace(step, references=()) if step.required else step for step in plan.steps if not step.required or step.references
    )
    optional_limit = extra_budget.allowance(int(getattr(settings, "guide_step_max_queries", 0) or 0))
    optional_results, optional_stop = run_step_searches(
        question,
        replace(plan, steps=optional_steps),
        limit=optional_limit,
        deadline_seconds=max(0.0, float(getattr(settings, "guide_step_deadline_seconds", 0.0) or 0.0) - (clock() - started)),
        retrieve=retrieve,
        clock=clock,
    )
    extra_budget.take(GUIDE_STEPS_KIND, len(optional_results))
    results.extend(optional_results)
    stop = stop or optional_stop
    existing = {r.chunk_uid or r.id for r in context.records} | {p.record.chunk_uid or p.record.id for p in context.evidence_tree}
    selection = select_step_parents(results, existing, chunks_per_step=per_step, reference_max_chunks=reference_cap)
    added_keys: set[str] = set()
    rejected = ""
    if selection.added:
        parents = tuple(replace(parent, reason=f"guide_{kind}") for _, kind, parent in selection.added)
        added = context_bundle_from_parent_evidence(parents, max_chars=MAX_CONTEXT_CHARS)
        visible = list(context.records)
        candidate = merge_evidence(
            context,
            AnswerContext(records=list(added.records), text=added.text, evidence=added.evidence, evidence_tree=added.evidence_tree),
            question,
            # 既存の根拠と足す根拠がすべて収まる件数（手順の根拠で既存の根拠を追い出さない）。
            max_records=max(1, len(visible) + len(added.records)),
            max_chars=MAX_CONTEXT_CHARS * 2,
        )
        merged_keys = {r.chunk_uid or r.id for r in candidate.records}
        if visible and not {r.chunk_uid or r.id for r in visible} <= merged_keys:
            rejected = "would_drop_existing_evidence"
        else:
            added_keys = {key for key in merged_keys - existing if key}
            pool = {r.chunk_uid or r.id: r for r in (*context.expansion_records, *(r for res in results for r in res.expansion_records))}
            context = replace(
                candidate,
                expansion_records=tuple(pool.values()),
                preferred_child_ids=context.preferred_child_ids,
                ranked_candidates=context.ranked_candidates,
                deferred_records=context.deferred_records,
                document_selection=context.document_selection,
            )
    in_context = {r.chunk_uid or r.id for r in context.records}
    # 手順の根拠は、手順ごとの検索の結果に、手順の参照の頁に当たる回答の材料の根拠を足したもの（#2706）。期限で検索
    # しなかった手順の根拠と、同じ根拠を参照する手順どうしの共有が、検索の打ち切りで見えなくならないようにする。
    referenced = reference_step_sources(plan, context.records)
    step_sources = tuple(
        (
            step.step_id,
            step.title,
            tuple(
                key
                for key in dict.fromkeys((*selection.step_sources.get(step.step_id, ()), *referenced.get(step.step_id, ())))
                if key in in_context
            ),
        )
        for step in plan.steps
    )
    # 期限・回数の上限で検索しなかった（検索がすべて失敗した）手順。根拠の無いことを「資料に無い」と区別する（#2706）。
    unsearched = unsearched_steps(plan, results)
    added_by_step: dict[str, list[str]] = {}
    for step_id, kind, parent in selection.added:
        key = parent.record.chunk_uid or parent.record.id
        if key in added_keys:
            added_by_step.setdefault(f"{step_id}:{kind}", []).append(key)
    sources = dict((step_id, keys) for step_id, _, keys in step_sources)
    by_target = {target.target_id: target for target in targets}
    for acquisition in acquisitions:
        target = by_target[acquisition["target_id"]]
        candidates = set(acquisition["candidate_source_ids"])
        retained = [record for record in target_records(target, context.records) if (record.chunk_uid or record.id) in candidates]
        acquisition["retained_source_ids"] = list(dict.fromkeys(record.chunk_uid or record.id for record in retained))
        acquisition["retained_body_chars"] = sum(len(str(getattr(record, "body_text", "") or record.text).strip()) for record in retained)
        acquisition["preparation"] = "candidate_available" if retained else "unknown"
        acquisition["actual_model_input"] = "not_observed"
        if not retained and not acquisition.get("reason"):
            acquisition["reason"] = "body_not_retained" if candidates else "no_candidates"
    incomplete_targets = [row["target_id"] for row in acquisitions if not row["retained_source_ids"]]
    incomplete_steps = list(
        dict.fromkeys(owner for row in acquisitions if row["target_id"] in incomplete_targets for owner in row["step_ids"])
    )
    trace.update(
        {
            "stop_reason": stop or ("rejected:" + rejected if rejected else ""),
            "unsearched_step_ids": list(unsearched),
            "elapsed_ms": int((clock() - started) * 1000),
            "budget": extra_budget.to_payload(),
            "required_acquisition": {
                "planned_targets": len(targets),
                "logical_attempts": sum(row["execution"] in {"completed", "error"} for row in acquisitions),
                "external_sdk_attempts": "not_measured_here",
                "targets": acquisitions,
                "incomplete_target_ids": incomplete_targets,
                "incomplete_step_ids": incomplete_steps,
                "scope_coverage": "unknown",
                "semantic_support": "not_run",
            },
            "steps": [
                {
                    "id": step.step_id,
                    "title": titles.get(step.step_id, ""),
                    "has_references": bool(step.references),
                    "references": [reference.to_payload() for reference in step.references],
                    "searches": [
                        {
                            "kind": result.kind,
                            "queries": list(result.queries),
                            "candidates": len(result.parents),
                            "added_chunk_ids": added_by_step.get(f"{step.step_id}:{result.kind}", []),
                            "elapsed_ms": result.elapsed_ms,
                            **({"error": result.error} if result.error else {}),
                        }
                        for result in results
                        if step.step_id in (result.owner_ids or (result.step_id,))
                    ],
                    "evidence_count": len(sources.get(step.step_id, ())),
                    "reference_chunk_count": len(added_by_step.get(f"{step.step_id}:reference", [])),
                    # 手順の参照の頁に当たる回答の材料の根拠の数（検索の結果に関係なく手順に紐づける。#2706）。
                    "referenced_count": len(referenced.get(step.step_id, ())),
                }
                for step in plan.steps
            ],
            "added_chunk_ids": sorted(added_keys),
        }
    )
    return context, trace, step_sources


def _with_guide_acquisition_notices(response: AnswerResponse, knowledge: RuntimeKnowledgeContext | None) -> AnswerResponse:
    """生成前の終了でも、取得処理の未完了を資料不足と区別して残す。"""
    if knowledge is None or not knowledge.guide_acquisition_notices:
        return response
    from rag_engine.generation.grounded import render_answer

    rendered = render_answer(response.answer_text, (), unverified=knowledge.guide_acquisition_notices)
    return replace(response, answer_text=rendered.text)


def _guide_acquisition_notices(trace: dict[str, Any]) -> tuple[str, ...]:
    """取得の未完了を業務上の案内や意味支持の判定と分けて示す。"""
    from rag_engine.generation.guide_acquisition import acquisition_notices

    return acquisition_notices((trace.get("required_acquisition") or {}).get("targets") or ())


# 手順ごとの検索をしなかった理由の表示名（#2395）。
_GUIDE_STEP_SKIP_LABELS = {
    "disabled": "しない（設定で無効）",
    "no_budget": "しない（追加の検索の枠か手順ごとの検索の上限が 0）",
}


def _guide_step_lines(trace: dict[str, Any]) -> list[str]:
    """手順ごとの検索の記録の文（処理の経過の段の詳細）。"""
    lines: list[str] = []
    cut = "期限" if trace.get("stop_reason") == "deadline" else "上限"
    for step in trace.get("steps") or ():
        searches = step.get("searches") or []
        if not searches:
            referenced = f"、参照の頁の根拠 {step['referenced_count']} 件" if step.get("referenced_count") else ""
            lines.append(f"{step['id']}「{step['title']}」: 検索しない（{cut}で打ち切り）{referenced}")
            continue
        note = "、参照の頁に絞った検索あり" if any(s["kind"] == "reference" for s in searches) else ""
        lines.append(f"{step['id']}「{step['title']}」: 根拠 {step['evidence_count']} 件{note}")
    return lines


def _synthesize_answer_from_context(question: str, context: AnswerContext, settings: Settings, **options: Any) -> AnswerResponse:
    """回答だけが必要な呼出元（SDK・手動評価）向け。是正後の文脈や画像は返さない。"""
    return synthesize_grounded_answer(question, context, settings, **options).response


def _legacy_schema_notice(document_count: int) -> str:
    """旧 schema のため検索対象外になった文書数の注記。0 件なら空 (#948)。"""
    if document_count <= 0:
        return ""
    return (
        f"※ 検索範囲に旧 schema の文書が {document_count} 件あり、検索対象外です。"
        "対象文書の再チャンキングと「Embedding作成・ADB保存」を再実行してください。"
    )


def _record_search_not_ready(classification_filter: ClassificationFilter, reason: str) -> None:
    """検索の前提が無く検索せずに中止したことを、「文書検索」の工程に残す（#3495）。"""
    with _execution_step("文書検索", _SEARCH_PURPOSE) as step:
        step.input(lambda: {"分類条件": asdict(classification_filter)})
        step.status = "中止"
        _record_search_conditions(step, classification_filter)
        step.result(reason + "回答生成フローは未実行です。")
        step.output({"executed": False, "reason": reason, "query_expansion_executed": False, "generation_executed": False})


def _record_search_conditions(step: _ExecutionStep, classification_filter: ClassificationFilter | None, notice: str = "") -> None:
    """普段と違う検索の条件（指定した分類フィルタ・検索対象外の旧 schema の文書）だけを文書の検索の工程に示す。

    既定の検索範囲・準備できていることは示さない（#3495）。
    """
    if classification_filter is not None and (line := _format_classification_filter(classification_filter)):
        step.field("分類フィルタ", line)
    if notice:
        step.add(notice)


def _format_classification_filter(classification_filter: ClassificationFilter) -> str:
    parts = []
    if classification_filter.large_category:
        parts.append(f"大分類={classification_filter.large_category}")
    if classification_filter.middle_category:
        parts.append(f"中分類={classification_filter.middle_category}")
    if classification_filter.small_category:
        parts.append(f"小分類={classification_filter.small_category}")
    return " / ".join(parts)


RULE_CONFIRMATION_STEP = "登録ルールによる確認の使用"
RULE_ANSWER_STEP = "回答生成での登録ルールの使用"


def _record_rule_usage(runtime: RuntimeKnowledgeContext, *, confirmation: bool) -> None:
    name = RULE_CONFIRMATION_STEP if confirmation else RULE_ANSWER_STEP
    stage = "confirmation" if confirmation else "generation"
    enabled = runtime.rules_confirmation_enabled if confirmation else runtime.rules_answer_enabled
    with _execution_step(name) as step:
        step.input({"原質問": runtime.original_question, "用途": stage, "有効": enabled})
        step.output(
            {
                "状態": "設定オフ" if not enabled else "読込失敗" if runtime.error else "選択完了",
                "読込エラー": runtime.error,
                "選択した登録ルール": [asdict(rule) for rule in runtime.rules_for_stage(stage) if "pinned" not in rule.tags]
                if enabled and not runtime.error
                else [],
                "選択の判断": [dict(item) for item in runtime.rule_applications if item["stage"] == stage] if enabled else [],
            }
        )
        if not enabled:
            step.skip(f"設定がオフのため、{name}をスキップしました。")
            return
        if runtime.error:
            _record_runtime_knowledge_load(step, runtime)
            return
        rules = tuple(rule for rule in runtime.rules_for_stage(stage) if "pinned" not in rule.tags)
        if not rules:
            step.result("適用する登録ルールはありません。")
        else:
            step.result(f"登録ルール {len(rules)} 件を選択しました。確認・回答の実行結果とは別です。")
            for rule in rules:
                step.add(f"採用: {rule.title or rule.rule_id}（用途: {'確認' if confirmation else '回答'}、優先度: {rule.priority}）")
        reasons = {
            "selected": "採用",
            "lower_priority": "上位の優先度のルールを採用",
            "unresolved_exclusive_conflict": "同順位の排他指示が競合",
            "duplicate_instruction": "指示が重複",
            "application_budget": "採用件数の上限",
            "other_purpose": "別の用途",
            "no_confirmation": "確認設定なし",
            "no_generation_instruction": "回答指示なし",
            "invalid_instruction_kind": "未対応の指示の種類",
        }
        for item in runtime.rule_applications:
            if item["stage"] == stage:
                step.add(f"{item['title'] or item['id']}: {reasons.get(item['reason'], item['reason'])}")


TERM_SEARCH_STEP = "用語・同義語による検索文の追加"
TERM_ANSWER_STEP = "回答生成での用語・同義語の使用"


def _describe_term_usage(
    step: _ExecutionStep, runtime: RuntimeKnowledgeContext | None, *, answer: bool, queries: Sequence[str] = ()
) -> None:
    name = TERM_ANSWER_STEP if answer else TERM_SEARCH_STEP
    enabled = runtime is not None and (runtime.terms_answer_enabled if answer else runtime.terms_search_enabled)
    step.input(
        {
            "原質問": runtime.original_question if runtime is not None else None,
            "用途": "回答" if answer else "検索",
            "検索への使用": runtime.terms_search_enabled if runtime is not None else False,
            "回答への使用": runtime.terms_answer_enabled if runtime is not None else False,
        }
    )
    step.output(
        {
            "状態": "設定オフ" if not enabled else "読込失敗" if runtime is not None and runtime.error else "照合完了",
            "読込エラー": runtime.error if runtime is not None else "",
            "一致した用語": [term.to_payload() for term in runtime.matched_terms]
            if enabled and runtime is not None and not runtime.error
            else [],
            "追加検索文": list(queries) if enabled and not answer and runtime is not None and not runtime.error else [],
        }
    )
    if not enabled:
        step.skip(f"設定がオフのため、{name}をスキップしました。")
        return
    assert runtime is not None
    if runtime.error:
        step.status = "補助情報なしで続行"
        step.result(f"用語の照合に失敗したため、用語なしで続行します（{runtime.error}）。")
        return
    if not runtime.matched_terms:
        step.result(
            "登録した用語・同義語に一致しなかったため、回答補助には使用していません。"
            if answer
            else "登録した用語・同義語に一致しなかったため、検索文の追加はありません。"
        )
        return
    step.field("用途", "回答の補助情報（資料の根拠とは別）" if answer else "全文検索・ベクトル検索への検索文の追加")
    for term in runtime.matched_terms:
        step.field("用語", term.term)
        step.field("別名", " / ".join(term.aliases))
        if answer:
            step.field("説明", term.description)
    if answer:
        step.result("一致した用語・別名・説明を回答生成の補助情報として提供します。")
    elif queries:
        step.result(f"検索文 {len(queries)} 本を追加します。")
        step.add(*queries)
    else:
        step.result("一致した用語はありますが、追加する検索文はありません。")


def _record_term_usage(runtime: RuntimeKnowledgeContext | None, *, answer: bool, queries: Sequence[str] = ()) -> None:
    with _execution_step(TERM_ANSWER_STEP if answer else TERM_SEARCH_STEP) as step:
        _describe_term_usage(step, runtime, answer=answer, queries=queries)


def _prepare_term_context(runtime: RuntimeKnowledgeContext, settings: Settings) -> RuntimeKnowledgeContext:
    """独立した兄弟工程。完全な原質問だけを照合し、モデルの言い換えを再入力しない。"""
    with _execution_step(TERM_SEARCH_STEP) as step:
        step.input(
            {
                "原質問": runtime.original_question,
                "検索への使用": settings.runtime_terms_search_enabled,
                "回答への使用": settings.runtime_terms_answer_enabled,
            }
        )
        if not (settings.runtime_terms_search_enabled or settings.runtime_terms_answer_enabled):
            _describe_term_usage(step, runtime, answer=False)
            return runtime
        if runtime.error:
            _describe_term_usage(step, runtime, answer=False)
            return runtime
        try:
            terms = build_runtime_knowledge_context(
                runtime.original_question,
                settings.output_dir,
                settings.runtime_knowledge_path,
                terms_search_enabled=settings.runtime_terms_search_enabled,
                terms_answer_enabled=settings.runtime_terms_answer_enabled,
                match_terms=settings.runtime_terms_search_enabled or settings.runtime_terms_answer_enabled,
            )
            if terms.error:
                if terms.failure is not None:
                    report_degraded("runtime_terms", terms.failure)
                step.status = "補助情報なしで続行"
                step.add(f"読込エラー: {terms.error}")
                step.result(f"用語の照合に失敗したため、用語の追加なしで続行します（{terms.error}）。")
                step.output({"状態": "読込失敗", "読込エラー": terms.error, "一致した用語": [], "追加検索文": []})
                return runtime
            result = replace(runtime, matched_terms=terms.matched_terms).with_term_usage(
                search=settings.runtime_terms_search_enabled,
                answer=settings.runtime_terms_answer_enabled,
            )
            additions: tuple[str, ...] = ()
            if not settings.original_query_only and _dedupe_query_key(result.expanded_question) != _dedupe_query_key(
                result.original_question
            ):
                additions = (result.expanded_question,)
            _describe_term_usage(step, result, answer=False, queries=additions)
            return result
        except Exception as exc:
            report_degraded("runtime_terms", exc)
            failed_runtime = replace(runtime, error=type(exc).__name__, failure=exc)
            _describe_term_usage(step, failed_runtime, answer=False)
            return failed_runtime


def _term_labels(runtime_knowledge: RuntimeKnowledgeContext) -> tuple[str, ...]:
    """検索に使う、一致した用語と別名。"""
    return _dedupe_queries(tuple(label for term in runtime_knowledge.retrieval_terms for label in term.labels()))


def _retrieval_query_plan_payload(
    *,
    settings: Settings | None = None,
    expansion: QueryExpansionResult,
    text_search_info: QuestionTextSearchInfo,
    runtime_knowledge: RuntimeKnowledgeContext | None,
    retrieval_scope: str,
    classification_filter: ClassificationFilter,
    top_k: int,
    vector_queries: Sequence[str] | None = None,
    lexical_queries: Sequence[str] = (),
) -> dict[str, Any]:
    runtime_question = runtime_knowledge.expanded_question if runtime_knowledge is not None else expansion.original_question
    queries = _dedupe_queries(vector_queries or text_search_info.query_variants or expansion.retrieval_queries)
    plan = RetrievalQueryPlan(
        original_question=expansion.original_question,
        runtime_expanded_question=runtime_question,
        vector_queries=tuple(queries),
        text_query_variants=tuple(text_search_info.query_variants),
        oracle_text_queries=tuple(text_search_info.oracle_text_queries),
        text_search_tokens=tuple(text_search_info.tokens),
        text_search_tokenization_traces=tuple(text_search_info.tokenization_traces),
        llm_expansion_queries=tuple(expansion.generated_queries),
        lexical_queries=_dedupe_queries(lexical_queries),
        retrieval_scope=normalize_retrieval_scope(retrieval_scope),
        top_k=max(1, int(top_k or DEFAULT_RETRIEVAL_TOP_K)),
        candidate_limit=_retrieval_candidate_limit(top_k, settings),
        metadata_filter=_retrieval_metadata_filter(
            classification_filter,
        ),
    )
    payload = plan.to_payload()
    payload["domain_keyword_expansion"] = dict(text_search_info.domain_keyword_expansion)
    payload["runtime_expansion_decisions"] = list(runtime_knowledge.expansion_decisions) if runtime_knowledge else []
    return payload


def _text_search_variants(
    queries: Sequence[str], expansion: QueryExpansionResult, sources: Mapping[str, Sequence[str]] | None = None
) -> tuple[str, ...]:
    """全文検索の検索語 trace に使う検索文。"""
    from rag_engine.retrieval.query_channels import expression_channels

    return tuple(
        query
        for query in queries
        if any(channel in {"hybrid", "keyword"} for channel in expression_channels(query, expansion.original_question, sources or {}))
    )


def _text_search_limit_note(text_variants: Sequence[str], settings: Settings) -> str:
    """全文検索の上限（TEXT_SEARCH_QUERY_VARIANT_LIMIT）で切れる検索文の本数を示す。上限内なら空 (#921)。

    検索実装（search_adb_hybrid_chunks）と同じく、vector 専用の検索文を除いた検索文に上限を当てる。
    """
    limit = max(1, int(getattr(settings, "text_search_query_variant_limit", 6) or 6))
    if len(text_variants) <= limit:
        return ""
    return (
        f"      全文検索に使う検索文: 先頭 {limit} 本（TEXT_SEARCH_QUERY_VARIANT_LIMIT）。"
        f"残り {len(text_variants) - limit} 本は意味検索だけに使います。"
    )


def build_question_text_search_info(
    question: str,
    settings: Settings,
    *,
    query_source: str = "原質問のみ",
    query_variants: Sequence[str] | None = None,
) -> QuestionTextSearchInfo:
    """Oracle Text 検索用の token と query trace を作成します。"""
    if settings.hybrid_search_backend == "oracle_native":
        variants = _limited_text_search_query_variants(question, query_variants, settings)
        return QuestionTextSearchInfo(
            tokenizer="oracle_native",
            tokenizer_label="Oracle Text（原生hybrid索引）",
            query_source=query_source,
            query_variants=tuple(variants),
            tokenization_traces=tuple(
                {"query": q, "tokenizer": "oracle_native", "tokens": [], "weights": [], "native_search_text": q} for q in variants
            ),
        )
    config = _question_text_search_tokenizer_config(settings)
    config = normalize_text_search_tokenizer_config(config)
    tokenizer_name = text_search_tokenizer_name(config)
    tokenizer_label = _text_search_tokenizer_label(config, tokenizer_name)
    fingerprint = ""
    try:
        fingerprint = tokenizer_fingerprint(config)
    except Exception:
        fingerprint = ""

    variants = _limited_text_search_query_variants(question, query_variants, settings)
    try:
        domain_keywords = (
            settings.domain_keywords_override
            if settings.domain_keywords_override is not None
            else load_domain_keywords(settings.output_dir)
        )
        query_keywords = {_dedupe_query_key(query): terms for query, terms in settings.retrieval_query_keywords.items()}
        query_terms: list[tuple[str, ...]] = []
        tokenization_traces: list[dict[str, Any]] = []
        oracle_queries: list[str] = []
        seen_queries: set[str] = set()
        for variant in variants:
            tokenization = tokenize_text_search_query_with_trace(
                variant,
                domain_keywords=[*domain_keywords, *query_keywords.get(_dedupe_query_key(variant), ())],
                config=config,
                max_tokens=MAX_TEXT_SEARCH_TOKENS,
            )
            terms = tuple(tokenization.tokens)
            oracle_query = build_oracle_text_query(terms, weights=tokenization.weights)
            if oracle_query and oracle_query not in seen_queries:
                oracle_queries.append(oracle_query)
                seen_queries.add(oracle_query)
            query_terms.append(terms)
            tokenization_traces.append(
                {
                    "query": variant,
                    "tokens": list(terms),
                    "weights": list(tokenization.weights),
                    "tokenizer": tokenization.tokenizer,
                    "fallback_reason": tokenization.fallback_reason,
                    "matched_domain_keywords": list(tokenization.matched_domain_keywords),
                    "selected_domain_keywords": list(tokenization.selected_domain_keywords),
                    "truncated_domain_keywords": list(tokenization.truncated_domain_keywords),
                    "candidate_count": tokenization.candidate_count,
                    "max_tokens": tokenization.max_tokens,
                    "truncated": tokenization.truncated,
                    "truncated_count": tokenization.truncated_count,
                    "truncated_tokens": list(tokenization.truncated_tokens),
                    "candidate_tokens": list(tokenization.candidate_tokens),
                    "protected_tokens": list(tokenization.protected_tokens),
                    "omitted_protected_tokens": list(tokenization.omitted_protected_tokens),
                    "character_limit_tokens": list(tokenization.character_limit_tokens),
                    "oracle_text_query": oracle_query,
                    "oracle_text_query_chars": len(oracle_query),
                }
            )
        if tokenization_traces and all(trace["tokenizer"] == "script" for trace in tokenization_traces):
            tokenizer_name = "script"
            tokenizer_label = _text_search_tokenizer_label(config, tokenizer_name)
        terms = query_terms[0] if query_terms else ()
        oracle_query = oracle_queries[0] if oracle_queries else ""
        return QuestionTextSearchInfo(
            tokenizer=tokenizer_name,
            tokenizer_label=tokenizer_label,
            tokenizer_fingerprint=fingerprint,
            tokens=terms,
            oracle_text_query=oracle_query,
            oracle_text_queries=tuple(oracle_queries),
            query_variants=tuple(variants),
            query_source=query_source,
            tokenization_traces=tuple(tokenization_traces),
        )
    except Exception as exc:
        return QuestionTextSearchInfo(
            tokenizer=tokenizer_name,
            tokenizer_label=tokenizer_label,
            tokenizer_fingerprint=fingerprint,
            query_variants=tuple(variants),
            query_source=query_source,
            error=type(exc).__name__,  # 例外の型の名前だけ（本文は残さない。#3259）
        )


def _text_search_details(info: QuestionTextSearchInfo) -> list[str]:
    """全検索文の採用語を表示し、検索時の候補切り詰めと表示上の省略を区別する。"""
    if info.error:
        return [f"全文検索のキーワード: 抽出に失敗（{info.error}）"]
    if info.tokenizer == "oracle_native":
        return ["全文検索の入力: Oracle原生hybrid索引に検索文を渡します（内部の検索語・検索式はAPIから返されません）"]
    tokens = dict.fromkeys(info.tokens)
    for trace in info.tokenization_traces:
        tokens.update(dict.fromkeys(trace.get("tokens", ())))
    details = ["全文検索のキーワード: " + (" / ".join(tokens) or "なし")]
    if any(trace.get("truncated") for trace in info.tokenization_traces):
        details.append("検索語の上限: 一部の候補は不採用です。上記は全検索文で採用した語（重複除去後）です。")
    return details


def _add_text_search_details(step: _ExecutionStep, info: QuestionTextSearchInfo) -> bool:
    """キーワードを記録する。直前の工程と同じ採用語なら記録せず False を返す。"""
    details = _text_search_details(info)
    if not info.error and any(line.strip() == details[0] for line in _execution_lines.get() or []):
        return False
    # 先頭の「全文検索のキーワード: …」は見出しと値にする（処理の経過の段を開いて見る。#2341）。
    label, _, value = details[0].partition(": ")
    step.field(label, value)
    step.add(*details[1:])
    for index, trace in enumerate(info.tokenization_traces, 1):
        mode = trace.get("tokenizer", info.tokenizer)
        if mode == "oracle_native":
            step.field(f"検索文 {index} の全文検索入力", trace.get("native_search_text", ""))
            step.field(f"検索文 {index} の分割方法", "Oracle Text（原生hybrid索引の設定）")
            step.add("内部の検索語・検索式はAPIから返されないため表示できません。Sudachiは使用していません。")
            continue
        method = "Sudachi＋文字種による補完" if mode == "sudachi+script" else "文字種による分割"
        if reason := trace.get("fallback_reason"):
            method += f"（縮退: {reason}。Sudachi の代わりに文字種で分割）"
        step.field(f"検索文 {index} の分割方法", method)
        tokens, weights = trace.get("tokens", []), trace.get("weights", [])
        main = " / ".join(str(token) for token, weight in zip(tokens, weights, strict=False) if weight >= 1) or "なし"
        extra = " / ".join(str(token) for token, weight in zip(tokens, weights, strict=False) if weight < 1) or "なし"
        step.add(f"検索文 {index}: {trace.get('query', '')}", f"主な検索語: {main}", f"補う検索語: {extra}")
        step.field(f"Oracle Text 検索式 {index}", trace.get("oracle_text_query") or "なし（全文検索は実行しません）")
    return True


# 回答を作らない検索の、根拠を集める流れの工程名（回答を作るときの「回答生成フロー」に当たる。#3506）と根拠の見出し。
EVIDENCE_FLOW_STEP = "根拠検索フロー"
EVIDENCE_RESULT_TITLE = "集めた根拠"
_SEARCH_PURPOSE = "意味の近さ（ベクトル）と語の一致（全文）で探した順位を融合し、上位の根拠とその前後の文脈を集めます。"


# 画面目録と画面の選択の cache。目録は chunk 集合ごと、選択は（質問、chunk 集合）ごと。同じ質問の検索は
# 検索し直すので、選択の LLM 呼び出しを 1 質問 1 回にする。目録を注入したときは、選択を（質問、目録の内容）ごとにする。
# 選択に失敗した結果は cache しない（次の回で選び直す。#3278）。
_SCREEN_CATALOG_CACHE: dict[tuple[int, int], dict[str, list[str]]] = {}
_SCREEN_LINK_CACHE: dict[tuple[object, ...], list[tuple[str, str]]] = {}


def _catalog_skip_policy(settings: Settings, reason: str, *, screen_only: bool = False) -> dict[str, str]:
    kinds = ("screen",) if screen_only else ("screen", "article")
    return {
        kind: reason
        if getattr(settings, "screen_linking_enabled" if kind == "screen" else "article_catalog_enabled", False)
        else "configuration_disabled"
        for kind in kinds
    }


def _record_catalog_skip(name: str, reason: str) -> None:
    message = {
        "configuration_disabled": "設定がオフのため実行しません。",
        "missing_requirement": "未回答の要求の補助検索では画面目録の選択を行いません。",
        "guide_step": "参照先の検索では目録の選択を行いません。",
        "caller_queries": "呼び出し元が検索文を指定したため目録の選択を行いません。",
        "overflow": "上限外の検索文の補助検索では目録の選択を行いません。",
    }[reason]
    with _execution_step(name) as step:
        step.skip(message, reason_code=reason)
        step.input({"実効設定": {"enabled": False}, "理由": reason})
        step.output({"選択状態": "skipped", "理由": reason, "モデルを呼び出した": False})


def _linked_screen_children(
    question: str,
    pool: Sequence[Any],
    ranked_children: Sequence[AnswerRecord],
    settings: Settings,
) -> tuple[list[AnswerRecord], list[tuple[str, str]], list[AnswerRecord]]:
    """画面目録で選んだ画面の child chunk のうちまだ候補に無いもの、選んだ（文書、見出し）、context の展開に足す record を返す (#1108)。

    目録と画面の chunk は、検索範囲の全 chunk（`pool`）から作る。呼び出し元が `AnswerDependencies.screen_catalog` /
    `screen_chunks` を注入したときは、検索結果に無い画面も載せるため、そちらから読む。そのときは読んだ child と親を
    context の展開（親・兄弟）に使えるよう 3 つ目で返す（`pool` から作るときは `pool` に含まれるので空）。
    """
    from rag_engine.dependencies import injected_screen_catalog, injected_screen_chunks
    from rag_engine.retrieval.screen_catalog import MAX_CHILDREN_PER_SCREEN, build_screen_catalog, link_screens, screen_candidates

    with _execution_step("画面の選択", "画面目録から質問を解決する画面を選び、その画面の根拠を検索候補に加えます。") as step:
        step.input(lambda: {"質問": question, "既存の候補": candidate_snapshot(ranked_children), "目録": None})
        catalog = injected_screen_catalog(settings=settings)
        if catalog is not None:
            catalog_source = "injected"
            digest = hashlib.sha256(json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            link_key: tuple[object, ...] = (question, "catalog", digest)
        else:
            pool_key = (id(pool), len(pool))
            catalog = _SCREEN_CATALOG_CACHE.get(pool_key)
            catalog_source = "process" if catalog is not None else "pool"
            if catalog is None:
                if len(_SCREEN_CATALOG_CACHE) >= 4:
                    _SCREEN_CATALOG_CACHE.clear()
                catalog = _SCREEN_CATALOG_CACHE[pool_key] = build_screen_catalog(pool)
            link_key = (question, *pool_key)
        step.input(
            lambda: {
                "質問": question,
                "目録": catalog,
                "目録の取得元": catalog_source,
                "既存の候補": candidate_snapshot(ranked_children),
                "利用可能なプール ID": [getattr(item, "chunk_uid", "") for item in pool],
                "画面ごとの最大 Child 数": MAX_CHILDREN_PER_SCREEN,
            }
        )
        if not catalog:
            step.skip("検索範囲に画面目録がありません。", reason_code="catalog_empty")
            step.output(
                {
                    "選択状態": "skipped",
                    "理由": "catalog_empty",
                    "選択結果の再利用": False,
                    "選択した画面": [],
                    "追加候補": [],
                    "展開用の候補": [],
                }
            )
            return [], [], []
        links = _SCREEN_LINK_CACHE.get(link_key)
        reused_selection = links is not None
        if links is None:
            links = link_screens(question, catalog, parse_text_response, settings)
            if links is None:
                step.output({"選択状態": "failed", "選択結果の再利用": False, "選択した画面": None, "追加候補": [], "展開用の候補": []})
                step.fail("画面の選択に失敗したため、既存の候補で続けます。")
                step.add(f"画面目録（{len(catalog)} 文書）からの画面の選択に失敗したため、画面の根拠を加えずに続けます。")
                return [], [], []
            if len(_SCREEN_LINK_CACHE) >= 256:
                _SCREEN_LINK_CACHE.clear()
            _SCREEN_LINK_CACHE[link_key] = links
        if not links:
            step.output(
                {"選択状態": "no_match", "選択結果の再利用": reused_selection, "選択した画面": [], "追加候補": [], "展開用の候補": []}
            )
            step.record_outcome("no_match", reused=reused_selection)
            step.status = "該当なし"
            step.result(f"画面目録（{len(catalog)} 文書）から該当する画面を選べませんでした。")
            return [], [], []
        existing = {r.chunk_uid for r in ranked_children if r.chunk_uid}
        loaded = injected_screen_chunks(links=links, existing=existing, settings=settings)
        if loaded is not None:
            # 注入された読み込みは child と親を返す。候補に加えるのは画面ごとの上限までの child だけ。
            added = screen_candidates(loaded, links, existing)
            expansion = [_stored_chunk_answer_record(chunk) for chunk in loaded]
        else:
            added = screen_candidates(pool, links, existing)
            expansion = []
        step.field("画面目録", f"{len(catalog)} 文書")
        step.field("選んだ画面", f"{len(links)} 件")
        step.add("選んだ画面:", *numbered([f"{Path(source).name or source} {heading}" for source, heading in links], limit=8))
        step.record_outcome("candidates_found", count=len(added), reused=reused_selection)
        step.result(f"候補に加えた根拠 {len(added)} 件")
        added_records = [_stored_chunk_answer_record(chunk) for chunk in added]
        step.output(
            lambda: {
                "選択状態": "selected",
                "選択結果の再利用": reused_selection,
                "選択した画面": list(links),
                "候補の取得元": "injected" if loaded is not None else "pool",
                "追加候補": candidate_snapshot(added_records),
                "展開用の候補": candidate_snapshot(expansion),
                "追加候補数": len(added_records),
                "最終採用": "この工程の返却候補です。回答文脈への採用は後段で決まります。",
            }
        )
        return added_records, links, expansion


# 目録での条の選択（質問・文書・chunk_set・目録の内容・上限・モデル → 選んだ (section_id, 行)）。要求ごとの
# 再検索で同じ質問の同じ文書を選び直さない。1 段目・2 段目の LLM に失敗した結果は cache しない（次の回で選び直す。#3285）。
# process の中の記録の後ろに、process をまたいで時間で切れる記録（`article_catalog_cache_*`。#3507）を置く。
_ARTICLE_SELECTION_CACHE: dict[tuple[object, ...], list[tuple[str, str]]] = {}
_ARTICLE_CACHE_SIZE = 256
# process をまたぐ記録の key の版。key の形・選択の入力の作り方（目録の prompt・2 段目の候補）を変えたら上げる。
# 2: 質問を NFKC で揃えてから空白を詰める。
_ARTICLE_RECORD_VERSION = 2
# 記録の出どころ（工程「条の選択」に残す）。
ARTICLE_SOURCE_LLM = "llm"
ARTICLE_SOURCE_PROCESS = "process"
ARTICLE_SOURCE_SHARED = "shared"


def _article_cache_store(settings: Settings) -> Any:
    """process をまたぐ条の選択の記録（設定が無い・0 なら None。#3507）。"""
    from rag_engine.resources.shared_cache import shared_result_cache

    return shared_result_cache(
        getattr(settings, "article_catalog_cache_dir", None),
        ttl_seconds=float(getattr(settings, "article_catalog_cache_ttl_seconds", 0.0) or 0.0),
        max_entries=int(getattr(settings, "article_catalog_cache_max_entries", 0) or 0),
    )


def _article_inputs_key(settings: Settings) -> tuple[object, ...]:
    """選択の結果を決める、質問と文書の外の入力: 記録の版・選択の指示・回答のモデル（provider・model・推論の量）・言語資源。

    process をまたいで記録を使うので、モデル・指示・言語資源（2 段目の名指しの条の番号の照合）を変えたら選び直す。
    """
    from rag_engine.config import get_llm_provider
    from rag_engine.retrieval.article_catalog import (
        ARTICLE_DOCUMENT_SELECTION_SYSTEM_PROMPT,
        ARTICLE_REFINE_SYSTEM_PROMPT,
        ARTICLE_SELECTION_SYSTEM_PROMPT,
    )

    instructions = (
        resolve_prompt("article_catalog.system", ARTICLE_SELECTION_SYSTEM_PROMPT),
        resolve_prompt("article_catalog.refineSystem", ARTICLE_REFINE_SYSTEM_PROMPT),
        resolve_prompt("article_catalog.documentSystem", ARTICLE_DOCUMENT_SELECTION_SYSTEM_PROMPT),
    )
    prompts = hashlib.sha256("\n\0".join(instructions).encode("utf-8")).hexdigest()[:16]
    provider_id = str(getattr(settings, "default_answer_llm", "") or "")
    try:
        provider = get_llm_provider(settings, provider_id or None)
        model: tuple[str, ...] = (provider_id, provider.model, provider.reasoning_effort)
    except Exception:  # noqa: BLE001 - provider の無い設定（テストの設定など）は provider の id だけで分ける。
        model = (provider_id,)
    try:
        pack = current_pack().hash
    except Exception:  # noqa: BLE001 - 言語資源を読めないときは版を分けない。
        pack = ""
    return (_ARTICLE_RECORD_VERSION, prompts, model, pack)


def _normalized_catalog_question(question: str) -> str:
    """記録の key の質問: NFKC で揃え（全角・半角の英数字や記号の違い）、前後と連続の空白を詰める。

    同じ文の再実行を同じ key にする。LLM に渡す質問は変えない。
    """
    return normalized_query_key(question)


def _catalog_digest(catalog: Any) -> str:
    """目録の内容（文書名・行・章節の id）の hash。同じ chunk_set でも目録の作り方が変われば選び直す。"""
    payload = json.dumps([catalog.file_name, list(catalog.lines), list(catalog.section_ids)], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _remember(cache: dict[tuple[object, ...], Any], key: tuple[object, ...], value: Any) -> None:
    """process の中の記録に入れる（上限を超えたら全部捨てる。今までと同じ）。"""
    if key not in cache and len(cache) >= _ARTICLE_CACHE_SIZE:
        cache.clear()
    cache[key] = value


def _recorded_articles(value: Any) -> list[tuple[str, str]] | None:
    """process をまたぐ記録の値を条の選択に戻す。形が違えば None（記録が無いのと同じ）。"""
    if not isinstance(value, list):
        return None
    articles: list[tuple[str, str]] = []
    for item in value:
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(part, str) for part in item)):
            return None
        articles.append((item[0], item[1]))
    return articles


def _recorded_documents(value: Any) -> list[str] | None:
    """process をまたぐ記録の値を文書の選択に戻す。形が違えば None。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _catalog_document_ids(*rankings: Sequence[AnswerRecord]) -> list[str]:
    """候補の文書の id を、文書の候補の順位の逆数の合計（RRF と同じ k=60）の大きい順に返す（#2261）。

    最初に出た順位だけで並べると、1 つの chunk が上位に当たった周辺の文書が先に来て、多くの chunk が中位に当たる
    根拠の文書が後ろになる（短い検索文で目立つ）。文書ごとに候補全体の当たり方を合計して並べる。複数の候補の列
    （検索文の候補と元の質問の候補）は列ごとの順位で数えて合計する。
    """
    scores: dict[str, float] = {}
    for records in rankings:
        for position, record in enumerate(records, start=1):
            document_id = str(record.metadata.get("document_id") or "") if isinstance(record.metadata, dict) else ""
            if document_id:
                scores[document_id] = scores.get(document_id, 0.0) + 1.0 / (60 + position)
    return sorted(scores, key=lambda document_id: -scores[document_id])


def _original_question_children(
    question: str,
    queries: Sequence[str],
    search_options: dict[str, Any],
    settings: Settings,
) -> list[AnswerRecord]:
    """元の質問（注入されたとき）で 1 回ベクトル検索した child の候補。条の目録を読む文書の順位だけに使う。

    業務 Agent は検索文を短いキーワードに言い換えることがあり、事例の論点の文書（政令・手続の規則など）が検索文の
    候補の上位に出ない。元の質問の候補は文書の順位に足すだけで、回答の候補・context には足さない（法令以外の根拠を
    減らさない）。元の質問が無い・検索文と同じ・設定が off・検索に失敗したときは空。
    """
    from rag_engine.dependencies import injected_article_question

    if not getattr(settings, "article_catalog_original_question_search", False):
        return []
    original = (injected_article_question() or "").strip()
    if not original or original == question.strip() or original in queries:
        return []
    options = {key: value for key, value in search_options.items() if key not in {"extra_text_queries", "vector_only_queries"}}
    options.update(retrieval_queries=[original], vector_only_queries=(original,))
    try:
        # 文書の検索の中の段（処理の経過。#3475）。検索の埋め込み・DB の検索はこの段の子になる。
        with observe_call("stage", ORIGINAL_QUESTION_SEARCH_STAGE) as observation:
            observation.request(
                lambda: {
                    "原質問": original,
                    "検索文": list(options["retrieval_queries"]),
                    "ベクトルだけの検索文": list(options["vector_only_queries"]),
                    "検索範囲": {key: options[key] for key in ("chunk_run_id", "filters", "top_k") if key in options},
                    "用途": "条目録の文書順位のみ。回答根拠には直接追加しません。",
                }
            )
            try:
                result = search_adb_hybrid_chunks(**options)
            except BaseException as exc:
                error_type = type(exc).__name__
                observation.response(lambda: {"返却なし": True, "例外の型": error_type})
                raise
            observation.response(
                lambda: {
                    "用途": "条目録の文書順位",
                    "読取候補": candidate_snapshot([_stored_chunk_answer_record(chunk) for chunk in result.child_chunks]),
                }
            )
    except Exception as exc:
        logger.warning("article catalog original question search failed", exc_info=True)
        # 元の質問の候補を足さずに続けたことを、呼び出し元の診断に残す（#3286）。
        report_degraded("article_original_question", exc)
        return []
    return [_stored_chunk_answer_record(chunk) for chunk in result.child_chunks]


def _catalog_terms(question: str, settings: Settings) -> tuple[Any, ...]:
    """条の選択に渡す用語: 質問（元の質問と検索文）に一致した登録の用語と、その別名・説明。

    事例の語（業務の通称）と文書の言い方（法令の用語）が違うと、目録の見出しだけでは条を結び付けられない。
    対応は利用者が登録した用語（実行時の知識）が持ち、コードには置かない。読めないときは渡さない。
    """
    from rag_engine.retrieval.article_catalog import CatalogTerm

    runtime = build_runtime_knowledge_context(
        question,
        settings.output_dir,
        settings.runtime_knowledge_path,
        rules_confirmation_enabled=settings.runtime_rules_confirmation_enabled,
        rules_answer_enabled=settings.runtime_rules_answer_enabled,
        terms_search_enabled=settings.runtime_terms_search_enabled,
        terms_answer_enabled=settings.runtime_terms_answer_enabled,
    )
    return tuple(CatalogTerm(term=term.term, aliases=tuple(term.aliases), description=term.description) for term in runtime.retrieval_terms)


@dataclass(frozen=True)
class _CatalogReferences:
    """2 段目に、選んだ条を名指しする別の章の条を候補にした文書（文書名・候補にした数・そのうち足した条の行）。"""

    file_name: str
    candidates: int
    added: tuple[str, ...]


def _select_catalog_articles(
    question: str,
    catalogs: Sequence[Any],
    settings: Settings,
    *,
    terms: Sequence[Any] = (),
    sources: dict[str, int] | None = None,
) -> tuple[list[Any], list[tuple[str, str]], int, list[str], list[_CatalogReferences]]:
    """文書ごとに目録から条を選び（並行に呼ぶ）、文書を順に回した順・選べなかった文書（名前, 理由）・2 段目で足した条の
    数・2 段目に失敗した文書の名前・名指しの条を候補にした文書を返す（#2254・#3517）。

    文書ごとに 2 段で選ぶ。1 段目は目録の行（条の名前）から論点ごとに選び、2 段目は選んだ条と同じ章の条と、選んだ条の
    番号を書き出しで名指しする別の章の条（附則の特例・読替えなど）を本文の書き出しつきで見せて、足りない論点の条を
    足させる（`article_catalog_refine_articles` 件まで。書き出しの読み込み `article_leads` が注入されたときだけ）。
    条の名前が質問の語から遠い条（重なったときの扱い・特例など）を拾うため。名指しの条は cache した結果からは数えない
    （この回に選んだ文書だけを工程に残す。足した条の数と同じ）。

    呼び出し 1 回の出力と時間は役目に合わせて抑える（`article_catalog_output_token_cap` /
    `article_catalog_call_timeout_seconds`。段ごとに数える）。同じ出力を繰り返して書き続ける呼び出しが 1 つあると、
    並行の呼び出しをすべて待つ段全体が数分止まるため。1 段目で超えた文書は選ばずに続け（同じ入力は温度 0 で同じ出力
    になるので送り直さない）、記録にも残さない（次の質問では呼び直す）。2 段目で失敗したら 1 段目の条だけを使い、
    その結果も記録に残さない（#3285）。

    記録は process の中（L1）と、process をまたいで時間で切れる記録（L2。`article_catalog_cache_*`。#3507）の順に引く。
    key は質問（NFKC で揃えて空白を詰めたもの）・文書・chunk_set・目録の内容・上限・用語・2 段目の設定・モデル・指示の版で、同じ入力の
    LLM の結果だけを使い回す（選び方は変えない）。`sources` を渡すと、文書ごとの出どころ（LLM / process の記録 /
    共有の記録）の数を入れる。
    """
    from rag_engine.adapters.oci import IncompleteResponse, bind_output_cap
    from rag_engine.dependencies import injected_article_leads, llm_call_count
    from rag_engine.resources.deadline import Deadline, DeadlineExceeded, bind_deadline, current_deadline
    from rag_engine.retrieval.article_catalog import interleave_selections, refine_articles, select_articles

    limit = int(getattr(settings, "article_catalog_max_articles", 0) or 0)
    refine_limit = int(getattr(settings, "article_catalog_refine_articles", 0) or 0)
    refine_candidates = int(getattr(settings, "article_catalog_refine_candidates", 0) or 0)
    load_leads = injected_article_leads() if refine_limit > 0 and refine_candidates > 0 else None
    model = str(getattr(settings, "default_answer_llm", "") or "")
    max_output = int(getattr(settings, "article_catalog_output_token_cap", 0) or 0)
    timeout = float(getattr(settings, "article_catalog_call_timeout_seconds", 0.0) or 0.0)
    # 登録の用語を変えたら選び直す（用語は選択の入力）。
    term_key = tuple((term.term, tuple(term.aliases), term.description) for term in terms)
    refine_key = (refine_limit, refine_candidates) if load_leads is not None else (0, 0)
    inputs = _article_inputs_key(settings)
    normalized = _normalized_catalog_question(question)
    keys: list[tuple[object, ...]] = [
        (
            "article_selection",
            normalized,
            catalog.document_id,
            catalog.chunk_set_id,
            len(catalog.lines),
            _catalog_digest(catalog),
            limit,
            model,
            term_key,
            refine_key,
            inputs,
        )
        for catalog in catalogs
    ]
    store = _article_cache_store(settings)
    cached: dict[int, list[tuple[str, str]]] = {}
    origin: dict[int, str] = {}
    for index, key in enumerate(keys):
        hit = _ARTICLE_SELECTION_CACHE.get(key)
        if hit is not None:
            cached[index], origin[index] = hit, ARTICLE_SOURCE_PROCESS
            continue
        recorded = _recorded_articles(store.get(key)) if store is not None else None
        if recorded is not None:
            _remember(_ARTICLE_SELECTION_CACHE, key, recorded)
            cached[index], origin[index] = recorded, ARTICLE_SOURCE_SHARED
    missing = [index for index in range(len(keys)) if index not in cached]
    failures: dict[int, str] = {}
    refine_failures: set[int] = set()
    results: dict[int, list[tuple[str, str]]] = {}
    calls: dict[int, int] = {}
    added_counts: dict[int, int] = {}
    references: dict[int, _CatalogReferences] = {}
    if missing:
        parent = current_deadline()

        def call(index: int) -> list[tuple[str, str]]:
            catalog = catalogs[index]
            own = Deadline(timeout, parent=parent) if timeout > 0 else parent

            def failed(exc: Exception) -> None:
                if isinstance(exc, IncompleteResponse):
                    failures[index] = "出力の上限"
                elif isinstance(exc, DeadlineExceeded) and own is not None and own.cut_short:
                    failures[index] = "時間の上限"
                else:
                    failures[index] = "呼び出しの失敗"

            with bind_deadline(own), bind_output_cap(max_output):
                chosen = select_articles(question, catalog, parse_text_response, settings, limit=limit, terms=terms, on_error=failed)
            calls[index] = 1
            if load_leads is None or not chosen:
                return chosen
            second = Deadline(timeout, parent=parent) if timeout > 0 else parent
            try:
                with bind_deadline(second), bind_output_cap(max_output):
                    calls[index] = 2
                    refinement = refine_articles(
                        question,
                        catalog,
                        chosen,
                        lambda sections: load_leads(
                            document_id=catalog.document_id,
                            chunk_set_id=catalog.chunk_set_id,
                            sections=sections,
                            settings=settings,
                        ),
                        parse_text_response,
                        settings,
                        limit=refine_limit,
                        candidate_limit=refine_candidates,
                        terms=terms,
                    )
            except Exception:  # noqa: BLE001 - 2 段目は補助。1 段目の条で続ける。
                logger.warning("article catalog refine failed", exc_info=True)
                refinement = None
            if refinement is None:
                refine_failures.add(index)
                return chosen
            added = refinement.added
            added_counts[index] = len(added)
            if refinement.referencing:
                referencing = set(refinement.referencing)
                references[index] = _CatalogReferences(
                    file_name=catalog.file_name,
                    candidates=len(refinement.referencing),
                    added=tuple(title for section_id, title in added if (section_id, title) in referencing),
                )
            # 足した条は足りない論点の根拠なので、1 段目の条と交互に並べて前に出す（後ろに置くと、文書を順に回した
            # 順で先頭から数える上限（MCP の根拠の件数など）に切られる）。
            merged: list[tuple[str, str]] = []
            for position in range(max(len(chosen), len(added))):
                merged.extend(item[position] for item in (chosen, added) if position < len(item))
            return merged

        with ThreadPoolExecutor(max_workers=len(missing), thread_name_prefix="article-catalog") as pool:
            futures = {index: pool.submit(contextvars.copy_context().run, call, index) for index in missing}
            results = {index: future.result() for index, future in futures.items()}
        # 並行の呼び出しは thread の文脈で数えるので、呼び出し元の文脈の LLM の呼び出しの数に足す。
        llm_call_count.set(llm_call_count.get() + sum(calls.values()))
        if len(_ARTICLE_SELECTION_CACHE) + len(missing) > _ARTICLE_CACHE_SIZE:
            _ARTICLE_SELECTION_CACHE.clear()
        for index, chosen in results.items():
            origin[index] = ARTICLE_SOURCE_LLM
            if index not in failures and index not in refine_failures:
                _ARTICLE_SELECTION_CACHE[keys[index]] = chosen
                if store is not None:
                    store.put(keys[index], [list(item) for item in chosen])
    if sources is not None:
        for value in origin.values():
            sources[value] = sources.get(value, 0) + 1
    # 2 段目に失敗した文書は cache しないが、この回は 1 段目の条で続ける。
    selections = [results[index] if index in results else cached.get(index, []) for index in range(len(keys))]
    failed = [(catalogs[index].file_name, failures[index]) for index in sorted(failures)]
    refine_failed = [catalogs[index].file_name for index in sorted(refine_failures)]
    referenced = [references[index] for index in sorted(references)]
    return interleave_selections(catalogs, selections), failed, sum(added_counts.values()), refine_failed, referenced


# 範囲の文書の選択（質問・文書の一覧・件数・モデル → 選んだ文書）。選択に失敗した結果は cache しない（#3285）。
_ARTICLE_DOCUMENT_CACHE: dict[tuple[object, ...], list[str]] = {}


def _catalog_question(query: str) -> str:
    """条の選択に渡す質問: 元の質問が注入されていれば、元の質問と検索文（#2261）。

    業務 Agent は検索文を短いキーワードに言い換えることがあり、検索文だけでは事例の論点（請求の種類・手続など）が
    落ちて、根拠の文書・条を選べない。検索そのものは検索文で行う。
    """
    from rag_engine.dependencies import injected_article_question

    original = injected_article_question()
    if not original or original == query.strip():
        return query
    return f"{original}\n\n（検索文: {query}）"


def _scope_catalog_documents(question: str, settings: Settings, exclude: set[str]) -> list[str] | None:
    """検索の範囲の条で構成された文書の一覧から、根拠の条がありそうな文書を LLM で選ぶ（#2254）。

    `exclude`（目録を読む候補の文書）と重なる分だけ多く選ばせ、重なりを除いて `article_catalog_scope_documents`
    件まで返す（選んだ文書が候補の文書と重なっても枠を使い切る。#2261）。一覧の注入が無い・件数が 0・一覧が空なら
    選ばない（LLM を呼ばない）。選択の LLM に失敗したら None を返し、cache しない（次の回で選び直す。#3285）。
    """
    return _scope_catalog_documents_with_source(question, settings, exclude)[0]


def _scope_catalog_documents_with_source(question: str, settings: Settings, exclude: set[str]) -> tuple[list[str] | None, str]:
    """`_scope_catalog_documents` と、選択の出どころ（LLM / process の記録 / 共有の記録。選ばなければ空。#3507）。"""
    from rag_engine.dependencies import injected_article_documents
    from rag_engine.retrieval.article_catalog import select_documents

    limit = int(getattr(settings, "article_catalog_scope_documents", 0) or 0)
    load = injected_article_documents()
    if load is None or limit <= 0:
        return [], ""
    documents = [(str(document_id), str(name)) for document_id, name in load(settings=settings)]
    if not documents:
        return [], ""
    overlap = sum(1 for document_id, _ in documents if document_id in exclude)
    ask = limit + overlap
    digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
    key: tuple[object, ...] = (
        "article_documents",
        _normalized_catalog_question(question),
        digest,
        ask,
        str(getattr(settings, "default_answer_llm", "") or ""),
        _article_inputs_key(settings),
    )
    source = ARTICLE_SOURCE_PROCESS
    chosen = _ARTICLE_DOCUMENT_CACHE.get(key)
    store = _article_cache_store(settings) if chosen is None else None
    if chosen is None and store is not None:
        chosen = _recorded_documents(store.get(key))
        if chosen is not None:
            source = ARTICLE_SOURCE_SHARED
            _remember(_ARTICLE_DOCUMENT_CACHE, key, chosen)
    if chosen is None:
        source = ARTICLE_SOURCE_LLM
        chosen = select_documents(question, documents, parse_text_response, settings, limit=ask)
        if chosen is None:
            return None, source
        _remember(_ARTICLE_DOCUMENT_CACHE, key, chosen)
        if store is not None:
            store.put(key, list(chosen))
    return [document_id for document_id in chosen if document_id not in exclude][:limit], source


_ARTICLE_SOURCE_LABELS = {
    ARTICLE_SOURCE_LLM: "LLM",
    ARTICLE_SOURCE_PROCESS: "同じ process の記録",
    ARTICLE_SOURCE_SHARED: "共有の記録",
}


_ARTICLE_SHARED_BYPASS_LABEL = "共有の記録を使わない（評価）"


def _article_sources_label(sources: Mapping[str, int]) -> str:
    """文書ごとの条の選択の出どころの数（例: 「LLM 2 文書・共有の記録 8 文書」）。"""
    parts = [f"{label} {sources[key]} 文書" for key, label in _ARTICLE_SOURCE_LABELS.items() if sources.get(key)]
    return "・".join(parts) if parts else "なし"


def _catalog_article_children(
    question: str,
    ranked_children: Sequence[AnswerRecord],
    settings: Settings,
    *,
    document_records: Sequence[AnswerRecord] = (),
) -> tuple[list[AnswerRecord], list[AnswerRecord]]:
    """条の目録で選んだ条を候補に加え、印を付けた候補の列と context の展開に足す record を返す (#2254)。

    目録は融合の候補に出た条で構成された文書の上位 `article_catalog_max_documents` 件から作り、選んだ条の章節の
    最初の child（読み込みが印を付けて返す）を候補の末尾に加える（すでに候補にある child は印だけ付ける）。選んだ条は
    rerank の順に、context の起点・親の枠の外に `article_catalog_context_records` 件まで足す（context_builder）。
    候補に条で構成された文書があれば、候補に出ない文書も範囲の文書の一覧から LLM が選んで足す
    （`_scope_catalog_documents`）。目録・chunk の読み込みの注入が無い、候補に条で構成された文書が無い、選べなかった
    ときは候補をそのまま返す。
    """
    from rag_engine.dependencies import article_catalog_dependencies
    from rag_engine.retrieval.article_catalog import ARTICLE_CATALOG_KEY, catalog_article_info

    with _execution_step("条の選択", "候補の文書の条の目録から、質問の根拠になる条を選び、その条の根拠を候補に加えます。") as step:
        original_question = question

        def inputs(catalogs: Sequence[Any] = (), terms: Sequence[Any] = ()) -> dict[str, Any]:
            return {
                "受け取った質問": original_question,
                "選択に使う質問": question,
                "既存の候補": candidate_snapshot(ranked_children),
                "文書順位用の候補": candidate_snapshot(document_records),
                "目録": [asdict(item) for item in catalogs],
                "用語": [asdict(item) for item in terms],
                "選択設定": {
                    key: getattr(settings, key, None)
                    for key in (
                        "article_catalog_max_documents",
                        "article_catalog_max_articles",
                        "article_catalog_context_records",
                        "article_catalog_scope_documents",
                        "article_catalog_refine_articles",
                        "article_catalog_refine_candidates",
                        "article_catalog_output_token_cap",
                        "article_catalog_call_timeout_seconds",
                    )
                },
            }

        step.input(inputs)
        injected = article_catalog_dependencies()
        max_documents = int(getattr(settings, "article_catalog_max_documents", 0) or 0)
        if injected is None or max_documents <= 0 or int(getattr(settings, "article_catalog_max_articles", 0) or 0) <= 0:
            reason = (
                "dependencies_unavailable" if injected is None else "document_limit_zero" if max_documents <= 0 else "article_limit_zero"
            )
            step.skip(
                {
                    "dependencies_unavailable": "条の目録の読み込みが提供されていません。",
                    "document_limit_zero": "文書数の上限が0です。",
                    "article_limit_zero": "条の数の上限が0です。",
                }[reason]
            )
            step.record_outcome(reason)
            step.output(
                lambda: {
                    "選択状態": "skipped",
                    "理由": reason,
                    "目録を読み込んだ": False,
                    "返す候補": candidate_snapshot(ranked_children),
                    "展開用の候補": [],
                }
            )
            return list(ranked_children), []
        load_catalogs, load_chunks = injected
        question = _catalog_question(question)
        # 文書の順位は検索文の候補と、元の質問の候補（`document_records`。文書の順位だけに使う）を合わせて決める。
        document_ids = _catalog_document_ids(ranked_children, document_records)
        catalogs = list(load_catalogs(document_ids=document_ids, limit=max_documents, settings=settings))
        if not catalogs:
            # 候補に条で構成された文書が無い質問は、範囲の文書の一覧も読まない（LLM・DB を呼ばない）。
            step.skip("候補の文書に利用できる条の目録がありません。", reason_code="catalog_empty")
            step.output(
                lambda: {
                    "選択状態": "skipped",
                    "理由": "no_catalog",
                    "目録を読み込んだ": True,
                    "返す候補": candidate_snapshot(ranked_children),
                    "展開用の候補": [],
                }
            )
            return list(ranked_children), []
        # 検索の候補に出ない文書（手続の一般的な規則の文書など）は、範囲の文書の一覧から LLM が選んで足す。文書の選択の
        # LLM と目録の読み込みはこの工程の中で行う（処理の経過で条の選択の子にする。#3475）。
        known = {catalog.document_id for catalog in catalogs}
        extra, scope_source = _scope_catalog_documents_with_source(question, settings, known)
        scope_failed = extra is None
        extra = extra or []
        if extra:
            catalogs += list(load_catalogs(document_ids=extra, limit=len(extra), settings=settings))
        terms = _catalog_terms(question, settings)
        step.input(lambda: inputs(catalogs, terms))
        sources: dict[str, int] = {}
        selected, failed, refined, refine_failed, referenced = _select_catalog_articles(
            question, catalogs, settings, terms=terms, sources=sources
        )
        # 選択を LLM で行ったか、記録（同じ process・process をまたぐ共有の記録）から使ったか（#3507）。品質評価は
        # 共有の記録を使わないことも残す（同じ質問の再実行でも実際の費用を測る）。
        from rag_engine.resources.shared_cache import shared_cache_bypassed

        bypass = (_ARTICLE_SHARED_BYPASS_LABEL,) if shared_cache_bypassed() else ()
        step.field("条の選択の出どころ", "・".join((_article_sources_label(sources), *bypass)))
        if scope_source:
            step.field("範囲の文書の選択の出どころ", "・".join((_ARTICLE_SOURCE_LABELS[scope_source], *bypass)))
        if terms:
            step.field("選択に渡した用語", join_values(term.term for term in terms))
        if refined:
            step.field("2 段目で足した条", f"{refined} 件")
        if referenced:
            step.field("選んだ条を名指しする別の章の条（候補）", f"{sum(item.candidates for item in referenced)} 件")
        step.field("条の目録を読んだ文書", f"{len(catalogs)} 件")
        if extra:
            step.field("範囲の一覧から加えた文書", f"{len(extra)} 件")
        if failed:
            step.field("条を選べなかった文書", f"{len(failed)} 件")
        if refine_failed:
            step.field("2 段目の条の選択に失敗した文書", f"{len(refine_failed)} 件")
        step.add("条の目録を読んだ文書:", *numbered([catalog.file_name for catalog in catalogs], limit=8))
        if failed:
            step.add(
                "上限を超えて条を選べなかった文書（選ばずに続けました）:",
                *numbered([f"{name}（{reason}）" for name, reason in failed], limit=8),
            )
        if refine_failed:
            step.add(
                "2 段目の条の選択に失敗した文書（目録から選んだ条だけで続けました）:",
                *numbered(refine_failed, limit=8),
            )
        if referenced:
            step.add(
                "選んだ条を名指しする別の章の条を候補にした文書（候補の数・そのうち足した条）:",
                *numbered(
                    [
                        f"{item.file_name}（{item.candidates} 件" + (f"。足した条: {join_values(item.added)}）" if item.added else "）")
                        for item in referenced
                    ],
                    limit=8,
                ),
            )
        if scope_failed:
            step.add("範囲の条で構成された文書の一覧からの文書の選択に失敗したため、候補の文書だけで続けました。")

        def output() -> dict[str, Any]:
            return {
                "選択状態": "degraded"
                if selected and (failed or scope_failed or refine_failed)
                else "selected"
                if selected
                else "failed"
                if failed or scope_failed
                else "no_match",
                "選んだ条": [asdict(item) for item in selected],
                "選択に失敗した文書": failed,
                "2段目で追加した条数": refined,
                "2段目の失敗": refine_failed,
                "参照元の候補": [asdict(item) for item in referenced],
                "範囲から選んだ追加文書 ID": extra,
                "範囲の文書選択の失敗": scope_failed,
                "選択の取得元": dict(sources),
                "範囲の文書選択の取得元": scope_source,
                "目録": [asdict(item) for item in catalogs],
                "返す候補": candidate_snapshot(ranked_children),
                "展開用の候補": [],
                "Chunkを読み込んだ": False,
            }

        step.output(output)
        reused_selection = bool(sources) and not sources.get(ARTICLE_SOURCE_LLM, 0) and scope_source != ARTICLE_SOURCE_LLM
        step.record_outcome("candidates_found" if selected else "no_match", count=0, reused=reused_selection)
        if selected and (failed or scope_failed or refine_failed):
            step.fail("一部の選択に失敗したため、取得できた候補で続けます。")
        if not selected:
            if failed or scope_failed:
                # 選択の失敗を「該当なし」と分ける（#3285）。
                step.fail("条の選択に失敗したため、既存の候補で続けます。")
                step.add("条の選択に失敗したため、目録の条を加えずに続けます。")
            else:
                step.status = "該当なし"
                step.result("目録から根拠になる条を選べませんでした。")
            return list(ranked_children), []
        loaded = [_stored_chunk_answer_record(chunk) for chunk in load_chunks(articles=selected, settings=settings)]
        # 読み込みは選んだ条の章節の最初の child に印（選んだ順・文書・目録の行）を付けて返す。
        info = {
            record.chunk_uid: article
            for record in loaded
            if record.chunk_level == CHILD_CHUNK_LEVEL and (article := catalog_article_info(record)) is not None
        }
        existing = {record.chunk_uid for record in ranked_children}
        marked = [
            replace(record, metadata={**record.metadata, ARTICLE_CATALOG_KEY: info[record.chunk_uid]})
            if record.chunk_uid in info
            else record
            for record in ranked_children
        ]
        added = [
            replace(record, metadata={**record.metadata, ARTICLE_CATALOG_KEY: {**info[record.chunk_uid], "added": True}})
            for record in loaded
            if record.chunk_uid in info and record.chunk_uid not in existing and record.chunk_level == CHILD_CHUNK_LEVEL
        ]
        step.field("選んだ条", f"{len(selected)} 件")
        step.add("選んだ条:", *numbered([f"{article.file_name} {article.title}" for article in selected], limit=8))
        step.record_outcome(
            "selection_failed" if step.status == "failed" else "candidates_found", count=len(added), reused=reused_selection
        )
        step.result(f"候補に加えた根拠: {len(added)} 件（候補にあった条 {len(info) - len(added)} 件）")
        step.output(
            lambda: {
                **output(),
                "Chunkを読み込んだ": True,
                "読み込んだ候補": candidate_snapshot(loaded),
                "既存候補と重複する読取 ID": sorted({record.chunk_uid for record in loaded} & existing),
                "追加候補": candidate_snapshot(added),
                "返す候補": candidate_snapshot([*marked, *added]),
                "読取数": len(loaded),
                "追加候補数": len(added),
                "最終採用": "この工程の返却候補です。回答文脈への採用は後段で決まります。",
            }
        )
        return [*marked, *added], loaded


def _question_text_search_tokenizer_config(settings: Settings) -> TextSearchTokenizerConfig:
    return TextSearchTokenizerConfig(
        sudachi_dict_type=settings.text_search_tokenizer_sudachi_dict,
        sudachi_config_path=settings.text_search_tokenizer_sudachi_config,
        latin_stemmer=settings.text_search_tokenizer_latin_stemmer,
    )


def _text_search_tokenizer_label(config: TextSearchTokenizerConfig, tokenizer_name: str) -> str:
    cfg = normalize_text_search_tokenizer_config(config)
    # 分割は 1 つ（#588）。Sudachi が使えない環境では文字種の区切りだけになる。
    name = "Sudachi＋文字種の区切り" if tokenizer_name == TEXT_SEARCH_TOKENIZER_HYBRID else "文字種の区切り（Sudachi なし）"
    detail = f"dict={cfg.sudachi_dict_type}, latin={cfg.latin_stemmer}"
    if cfg.sudachi_config_path:
        detail += f", config={cfg.sudachi_config_path}"
    return f"{name} ({detail})"


def answer_question(
    question: str,
    run_id: Any,
    preferred_engines: Iterable[str],
    settings: Settings,
    *,
    chunk_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    chunk_neighbor_count: int = DEFAULT_NEIGHBOR_CHILD_COUNT,
    rerank_enabled: bool | None = None,
    answer_llm_provider: str | None = None,
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    classification_filter: ClassificationFilter | None = None,
) -> str:
    """互換 API として回答本文だけを生成して返します。"""
    return answer_question_result(
        question=question,
        run_id=run_id,
        preferred_engines=preferred_engines,
        settings=settings,
        chunk_top_k=chunk_top_k,
        chunk_neighbor_count=chunk_neighbor_count,
        rerank_enabled=rerank_enabled,
        answer_llm_provider=answer_llm_provider,
        retrieval_scope=retrieval_scope,
        classification_filter=classification_filter,
    ).answer


def _keep_trusted_queries(
    candidates: Sequence[str],
    accepted: Sequence[str],
    rejected: Sequence[dict[str, str]],
    trusted: Sequence[str],
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """原質問との照合で外した検索文のうち、呼び出し元が作ったものを候補の順のまま戻す（#2651）。

    照合は RAG の LLM が作った言い換えの逸脱を外すためのもので、呼び出し元が明示した検索文には当てない。
    """
    trusted_keys = {_dedupe_query_key(query) for query in trusted}
    kept = {_dedupe_query_key(query) for query in accepted} | trusted_keys
    return (
        tuple(query for query in candidates if _dedupe_query_key(query) in kept),
        tuple(item for item in rejected if _dedupe_query_key(item.get("query", "")) not in trusted_keys),
    )


def _record_runtime_knowledge_load(step: _ExecutionStep, runtime_knowledge: RuntimeKnowledgeContext) -> bool:
    """用語・同義語と回答ルールの読み込みの失敗を工程に記録する。読めていれば True（工程の結果と詳細を続けて書く）。

    詳細の行は例外の型の名前だけ（例外の文は path などを含みうる。#3284）。
    """
    if not runtime_knowledge.error:
        return True
    step.add(f"読込エラー: {runtime_knowledge.error}")
    step.status = "補助情報なしで続行"
    return False


@_record_answer_execution
def answer_question_result(
    question: str,
    run_id: Any,
    preferred_engines: Iterable[str],
    settings: Settings,
    *,
    chunk_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    chunk_neighbor_count: int = DEFAULT_NEIGHBOR_CHILD_COUNT,
    rerank_enabled: bool | None = None,
    answer_llm_provider: str | None = None,
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    classification_filter: ClassificationFilter | None = None,
    generate_answer: bool = True,
    complete_evidence: bool = True,
    scope: tuple[str, Sequence[str]] | None = None,
    guide_plan: GuidePlan | None = None,
    caller_queries: Sequence[str] | None = None,
    request_question: str | None = None,
) -> AnswerQuestionResult:
    """検索・LLM の結果と実行記録を返す。

    ``scope`` は利用者が確認の質問で選んだ条件と対象範囲の説明・検索に足す語（チャットの確認。#717）。
    回答のプロンプトの前置きに入れ、語は補助の検索文に足す。範囲での絞り込みは呼び出し側の検索が行う。

    ``guide_plan`` は照合した業務ガイドの手順（分岐で残った手順。#2395）。回答を作るとき、主の検索の後に
    手順ごとに検索し、手順の根拠と参照の頁の根拠を回答の材料に足す。

    ``generate_answer=False`` は回答を作らない（RAG 検索の画面。#3506）。根拠を集め終えるまで（回答フロー・章節の拡張・
    業務ガイドの手順ごとの検索・要求ごとの根拠の確認）は回答と同じ 1 つの流れで、工程・入れ子・LLM の呼び出しも同じ。
    回答に添付する画像と回答の生成の手前で止め、集めた根拠を返す（本文は空）。``complete_evidence=False`` は、その流れの
    主の文書の検索の後で止める（業務 Agent の根拠の取得。根拠の補い（章節を読む・探し直す）は呼び出し元がする）。
    ``generate_answer=False`` とだけ組み合わせる。
    空質問は ValueError、処理失敗は中断記録付きの AnswerExecutionError を送出する。
    ADB の準備不足や空検索は
    未実行工程を明示した結果を返し、回答 LLM を呼ばない。記録は呼び出しごとに分離する。

    ``caller_queries`` は呼び出し元（業務 Agent）が作った検索文（#2651）。渡すと検索文は ``question`` と
    これだけになり、質問に応じた推論の LLM（条の目録の選択・画面目録の連携）を呼ばない。
    ``generate_answer=False`` とだけ組み合わせる。規則による質問の理解・用語の別名の検索文・
    hybrid search・rerank・親子と前後の文脈は通常の検索と同じに行う。

    ``request_question`` は利用者の元の質問（呼び出し元が ``question`` を検索用に言い換えたとき。#2665）。渡すと、
    回答の要求の単位（``task_contract``）・要求ごとの根拠の確認と欠けた要求の探し直し・回答の生成と検査は
    この質問で行い、``question`` は検索（質問の理解・検索文・章節の拡張・業務ガイドの手順の検索）にだけ使う。
    言い換えで番号つきの小問が平文になり・まとめられても、元の要求を落とさない。省略すると
    ``question`` で両方を行う。
    """
    normalized_question = extract_original_question(question)
    if not normalized_question:
        raise ValueError("質問を入力してください。")
    # 回答の対象と要求の単位の正本（#2665）。検索の文（normalized_question）とは分ける。
    request_text = extract_original_question(request_question or "") or normalized_question
    caller_planned = caller_queries is not None
    if caller_planned and generate_answer:
        raise ValueError("呼び出し元の検索文（caller_queries）は回答を作らない検索（generate_answer=False）だけに使います。")
    if generate_answer and not complete_evidence:
        raise ValueError("根拠の補いを省く検索（complete_evidence=False）は回答を作らない検索だけに使います。")
    trusted_queries: tuple[str, ...] = ()
    caller_catalog_policy = _catalog_skip_policy(settings, "caller_queries") if caller_planned else {}
    if caller_planned:
        question_key = _dedupe_query_key(normalized_question)
        trusted_queries = _dedupe_queries(
            tuple(query.strip() for query in caller_queries or () if query.strip() and _dedupe_query_key(query) != question_key)
        )
        # 質問に応じて LLM を呼ぶ検索の補助は、設定に関わらずこの経路では使わない。
        settings = replace(settings, article_catalog_enabled=False, screen_linking_enabled=False)
    # 章節の拡張（#1436）の時間の上限は、回答の開始から数える。
    answer_started_at = monotonic()

    effective_rerank_enabled = bool(settings.default_rerank_enabled) if rerank_enabled is None else bool(rerank_enabled)
    selected_retrieval_scope = normalize_retrieval_scope(retrieval_scope)
    selected_classification_filter = classification_filter or classification_filter_from_values()
    if caller_planned:
        # 呼び出し元が検索文を作った検索（#2651）は、質問に応じた推論の LLM（章節の抜き出し）を呼ばない。
        complete_evidence = False

    question_plan = plan_question(normalized_question)
    # 検索・回答プロファイルの知識の「用語・同義語」と「回答ルール」は使い道が違うので、別の工程（別の span）で示す
    # （#3451）。登録は 1 回だけ読み、最初の工程で読み込みと照合をして、次の工程は同じ結果の回答ルールを示す。
    runtime_knowledge = build_runtime_knowledge_context(
        normalized_question,
        settings.output_dir,
        settings.runtime_knowledge_path,
        rules_confirmation_enabled=settings.runtime_rules_confirmation_enabled,
        rules_answer_enabled=settings.runtime_rules_answer_enabled,
        match_terms=False,
        terms_search_enabled=settings.runtime_terms_search_enabled,
        terms_answer_enabled=settings.runtime_terms_answer_enabled,
    )
    if runtime_knowledge.error and runtime_knowledge.failure is not None:
        report_degraded("runtime_knowledge", runtime_knowledge.failure)

    _record_rule_usage(runtime_knowledge, confirmation=True)
    _record_rule_usage(runtime_knowledge, confirmation=False)
    if scope is not None:
        runtime_knowledge = runtime_knowledge.with_scope(*scope)

    base_expansion = QueryExpansionResult(original_question=normalized_question, generated_queries=trusted_queries)
    # 検索語は検索文の確定後に確定する。準備不足で中止した場合は検索していないため記録しない。
    text_search_info = QuestionTextSearchInfo(query_source=runtime_knowledge.query_source)

    # 検索の前提（チャンクの実行・検索用データ）は工程を開かずに確かめる（#3495）。普段どおりなら何も示さず、
    # 中止したときだけ「文書検索」の工程に理由を残す。backend は何もしない確認を注入する。
    preferred_engine_ids = list(preferred_engines)
    chunk_run_id = ""
    if selected_retrieval_scope == RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN:
        chunk_run = load_latest_or_source_chunk_run(settings.output_dir, run_id)
        if chunk_run is None:
            _record_search_not_ready(selected_classification_filter, "検索対象のチャンクがありません。")
            return _answer_question_result(
                settings=settings,
                expansion=base_expansion,
                answer=(
                    "ADB hybrid search に必要な chunk run がありません。"
                    "チャンキング tab でチャンキングを実行してから、Embedding作成・ADB保存を実行してください。"
                ),
                text_search_info=text_search_info,
                question_plan=question_plan,
                runtime_knowledge=runtime_knowledge,
                retrieval_scope=selected_retrieval_scope,
                classification_filter=selected_classification_filter,
                retrieval_top_k=chunk_top_k,
            )
        chunk_run_id = chunk_run.chunk_run_id

    try:
        readiness = check_adb_hybrid_search_ready(
            chunk_run_id=chunk_run_id,
            settings=settings,
            preferred_engine_ids=preferred_engine_ids,
            retrieval_scope=selected_retrieval_scope,
            classification_filter=selected_classification_filter,
        )
    except AdbHybridSearchUnavailable as exc:
        _record_search_not_ready(selected_classification_filter, "ADB hybrid search を利用できません。")
        return _answer_question_result(
            settings=settings,
            expansion=base_expansion,
            answer=f"ADB hybrid search を使用できません: {exc}",
            text_search_info=text_search_info,
            question_plan=question_plan,
            runtime_knowledge=runtime_knowledge,
            retrieval_scope=selected_retrieval_scope,
            classification_filter=selected_classification_filter,
            retrieval_top_k=chunk_top_k,
        )

    # 旧 schema の文書は検索 SQL の版条件で除外される。件数を回答本文に出す (#948)。
    # 戻り値を返さない差し替え実装でも動くよう、型を確認してから使う。
    legacy_notice = _legacy_schema_notice(readiness.legacy_document_count if isinstance(readiness, AdbSearchReadiness) else 0)

    runtime_knowledge = _prepare_term_context(runtime_knowledge, settings)
    expansion = base_expansion

    with _execution_step(
        "検索文をまとめ、検索語を作る",
        "原質問と用語・同義語による追加検索文をまとめ、重複を除き、全文検索に使う検索語を作ります。",
    ) as step:
        keyword_trace: dict[str, Any] = {}
        candidate_queries = _merge_retrieval_queries(
            normalized_question,
            runtime_knowledge.expanded_question,
            *expansion.retrieval_queries,
        )
        grounded = " ".join(_term_labels(runtime_knowledge))
        retrieval_queries, rejected = filter_queries(normalized_question, candidate_queries, grounded_text=grounded)
        if trusted_queries:
            retrieval_queries, rejected = _keep_trusted_queries(candidate_queries, retrieval_queries, rejected, trusted_queries)
        keyword_trace["combined_rejections"] = list(rejected)
        if getattr(settings, "original_query_only", False):
            # 比較実験用。検索直前にも同じ絞り込みがあるが、ここで適用しないと記録が実際の検索と食い違う。
            retrieval_queries = (normalized_question,)
            step.add("      設定 RETRIEVAL_ORIGINAL_QUERY_ONLY=true のため、原質問だけで検索します。")
        sources = {_dedupe_query_key(normalized_question): "原質問"}
        if runtime_knowledge.expanded_question != runtime_knowledge.original_question:
            # 出どころは実際に語を足したもの（用語・同義語 / 回答ルール / 業務ガイド / 確認で選んだ条件。#3490）。
            sources.setdefault(_dedupe_query_key(runtime_knowledge.expanded_question), runtime_knowledge.query_source)
        for query in expansion.generated_queries:
            sources.setdefault(_dedupe_query_key(query), "呼び出し元")
        query_sources: dict[str, tuple[str, ...]] = {}
        for query in retrieval_queries:
            key = _dedupe_query_key(query)
            labels = [sources.get(key, "追加検索文")]
            if any(_dedupe_query_key(item) == key for item in expansion.generated_queries):
                labels.append("呼び出し元")
            if (
                runtime_knowledge.expanded_question != runtime_knowledge.original_question
                and _dedupe_query_key(runtime_knowledge.expanded_question) == key
            ):
                labels.append(runtime_knowledge.query_source)
            query_sources[query] = tuple(dict.fromkeys(labels))
        step.input(
            {
                "原質問": normalized_question,
                "候補検索文": [
                    {"本文": query, "出どころ": sources.get(_dedupe_query_key(query), "追加検索文")} for query in candidate_queries
                ],
                "一致した用語": [term.to_payload() for term in runtime_knowledge.retrieval_terms],
                "原質問だけで検索": bool(getattr(settings, "original_query_only", False)),
            }
        )
        # 用語・同義語を足した検索文は、足した語を全文検索の語の上限から落とさない（長い質問の後ろの別名が埋もれる #2163）。
        term_labels = _term_labels(runtime_knowledge)
        query_keywords = {runtime_knowledge.expanded_question: term_labels} if term_labels else {}
        settings = replace(settings, retrieval_query_sources=query_sources, retrieval_query_keywords=query_keywords)
        text_search_info = build_question_text_search_info(
            normalized_question,
            settings,
            query_source=_text_search_query_source(runtime_knowledge, expansion),
            query_variants=_text_search_variants(retrieval_queries, expansion, query_sources),
        )
        text_search_info = replace(text_search_info, domain_keyword_expansion=keyword_trace)
        if (tokenizer_failure := sudachi_unavailable(_question_text_search_tokenizer_config(settings))) is not None:
            # 全文検索の語を文字種の区切りだけで作ったことを、回答ごとに 1 回だけ診断に残す（#3286）。
            report_degraded("text_search_tokenizer", tokenizer_failure)
        step.result(
            f"検索文 {len(retrieval_queries)} 本",
            *(
                f"{number}. " + (f"[{source}] " if (source := sources.get(_dedupe_query_key(query))) else "") + _display_query(query)
                for number, query in enumerate(retrieval_queries, 1)
            ),
        )
        if rejected:
            step.add(f"      原質問から外れるため使わない検索文: {len(rejected)} 本")
        if note := _text_search_limit_note(_text_search_variants(retrieval_queries, expansion), settings):
            step.add(note)
        _add_text_search_details(step, text_search_info)
        if text_search_info.error:
            step.status = "検索語の抽出に失敗"
        elif any(trace.get("fallback_reason") for trace in text_search_info.tokenization_traces):
            step.status = "文字種による分割で続行"
        step.output(
            {
                "採用した検索文": [{"本文": query, "出どころ": list(query_sources[query])} for query in retrieval_queries],
                "除外した検索文": list(rejected),
                "原質問だけで検索": bool(getattr(settings, "original_query_only", False)),
                "全文検索の入力": asdict(text_search_info),
            }
        )

    # 根拠を集める流れは回答を作るか否かで分けない（RAG 検索とチャットで同じ工程・入れ子・LLM の呼び出し。#3506）。
    # 回答を作らないときは同じ流れの、回答に添付する画像と回答の生成の手前（業務 Agent の根拠の取得は主の文書の検索の
    # 後）で止める。工程名だけを回答の有無で変える（回答を作らないのに「回答生成」と示さない）。
    flow_name, flow_purpose = (
        ("回答生成フロー", "根拠を集めて確かめ、根拠に基づく回答を作ります。")
        if generate_answer
        else (EVIDENCE_FLOW_STEP, "回答と同じ方法で根拠を集めて確かめます（回答は生成しません）。")
    )

    def evidence_result(context: AnswerContext, *, trace: Mapping[str, Any] | None = None) -> AnswerQuestionResult:
        """回答を作らずに、集めた根拠を返す（本文は空）。根拠の補いの記録は回答と同じ generation_trace に残す。"""
        return record_flow_result(
            _answer_question_result(
                settings=settings,
                expansion=expansion,
                answer="",
                response=AnswerResponse(answer_text="", generation_trace=dict(trace or {})),
                records=context.records,
                evidence_tree=context.evidence_tree,
                text_search_info=text_search_info,
                question_plan=question_plan,
                runtime_knowledge=runtime_knowledge,
                retrieval_scope=selected_retrieval_scope,
                classification_filter=selected_classification_filter,
                retrieval_queries=retrieval_queries,
                retrieval_top_k=chunk_top_k,
                request_question=request_text,
            )
        )

    # 根拠の件数の見出し（回答を作らないときに「回答に渡す」と示さない）。
    evidence_title = "回答に渡す根拠" if generate_answer else EVIDENCE_RESULT_TITLE

    def record_flow_result(result: AnswerQuestionResult) -> AnswerQuestionResult:
        flow_step.output(
            lambda: {
                "回答本文": result.answer_text,
                "回答の構造": result.envelope,
                "返却する根拠": result.evidence_items,
                "引用した根拠ID": result.cited_evidence_ids,
                "検索計画": result.retrieval_query_plan,
                "生成の記録": result.generation_trace,
                "不足理由": result.insufficient_reason,
            }
        )
        return result

    with _execution_step(flow_name, flow_purpose) as flow_step:
        flow_step.input(
            lambda: {
                "回答対象の質問": request_text,
                "検索対象の質問": normalized_question,
                "検索文": tuple(retrieval_queries),
                "検索範囲": selected_retrieval_scope,
                "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                "回答生成の要求": generate_answer,
                "根拠補完の要求": complete_evidence,
                "起点数": chunk_top_k,
                "前後それぞれのChunk数": chunk_neighbor_count,
                "再ランキング": effective_rerank_enabled,
            }
        )
        extra_budget = ExtraRetrievalBudget.from_settings(settings)
        if complete_evidence and guide_plan is not None and settings.guide_step_retrieval_enabled:
            extra_budget.reserve_required(len(required_guide_targets(guide_plan)))
        coverage_usage = CoverageUsage()
        try:
            with _execution_step("文書検索", _SEARCH_PURPOSE) as retrieval_step:
                retrieval_step.input(
                    {
                        "質問": normalized_question,
                        "検索文": tuple(retrieval_queries),
                        "検索範囲": selected_retrieval_scope,
                        "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                        "起点数": chunk_top_k,
                        "前後それぞれのChunk数": chunk_neighbor_count,
                        "再ランキング": effective_rerank_enabled,
                        "呼出側の検索文": tuple(trusted_queries),
                    }
                )
                context = build_adb_hybrid_answer_context(
                    normalized_question,
                    run_id,
                    preferred_engine_ids,
                    settings,
                    top_k=chunk_top_k,
                    neighbor_child_count=chunk_neighbor_count,
                    retrieval_queries=retrieval_queries,
                    rerank_phase="main_search",
                    rerank_enabled=effective_rerank_enabled,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                    runtime_knowledge=runtime_knowledge,
                    trusted_queries=trusted_queries,
                    catalog_skip_reasons=caller_catalog_policy,
                )
                retrieval_step.field("検索文", f"{len(retrieval_queries)} 本")
                _record_search_conditions(retrieval_step, selected_classification_filter, legacy_notice)
                _record_search_result(retrieval_step, context, title=evidence_title)
        except AdbHybridSearchUnavailable as exc:
            flow_step.status = "中止"
            flow_step.add("文書検索を続行できません。" + ("回答文の生成は未実行です。" if generate_answer else ""))
            return record_flow_result(
                _answer_question_result(
                    settings=settings,
                    expansion=expansion,
                    answer=f"ADB hybrid search を使用できません: {exc}",
                    text_search_info=text_search_info,
                    question_plan=question_plan,
                    runtime_knowledge=runtime_knowledge,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                    retrieval_queries=retrieval_queries,
                    retrieval_top_k=chunk_top_k,
                )
            )
        if not complete_evidence:
            # 業務 Agent の根拠の取得: 主の文書の検索の後で止める（根拠の補いは呼び出し元がする）。
            flow_step.result(f"{EVIDENCE_RESULT_TITLE} {evidence_summary(context.records)}（回答は生成しません）")
            return evidence_result(context)
        section_state: dict[str, Any] = {}
        # 業務ガイドの手順が参照する文書と頁（#2828）。章節の拡張はこの範囲の起点を先に広げ、回答の「回答に未反映の
        # 関連章節」はこの文書の章節を残す。
        guide_ranges = guide_plan.reference_ranges if guide_plan is not None else ()

        def complete_sections(candidate: AnswerContext, phase: str, *, reserve: bool = False) -> tuple[AnswerContext, Any]:
            return _expand_context_by_sections(
                normalized_question,
                candidate,
                settings,
                answer_llm_provider=answer_llm_provider,
                rerank_enabled=effective_rerank_enabled,
                started_at=answer_started_at,
                state=section_state,
                phase=phase,
                reserve=reserve,
                priority=guide_ranges,
            )

        # 超限した検索文は原要求ごとに補い、以後の補查と同じ枠・期限・既検索文を使う。
        overflow_catalog_policy = _catalog_skip_policy(settings, "overflow")
        overflow_settings = replace(settings, document_selection_enabled=False, screen_linking_enabled=False, article_catalog_enabled=False)
        overflow_config = normalize_text_search_tokenizer_config(_question_text_search_tokenizer_config(settings))
        overflow_keywords = (
            settings.domain_keywords_override
            if settings.domain_keywords_override is not None
            else load_domain_keywords(settings.output_dir)
        )

        def overflow_retrieve(query: str) -> AnswerContext:
            return build_adb_hybrid_answer_context(
                query,
                run_id,
                preferred_engine_ids,
                overflow_settings,
                top_k=chunk_top_k,
                neighbor_child_count=chunk_neighbor_count,
                retrieval_queries=(query,),
                rerank_phase="missing_requirement",
                catalog_skip_reasons=overflow_catalog_policy,
                rerank_enabled=effective_rerank_enabled,
                retrieval_scope=selected_retrieval_scope,
                classification_filter=selected_classification_filter,
                runtime_knowledge=runtime_knowledge,
            )

        with _execution_step(
            "検索上限の要求別補完", "全文検索の語数上限に達した質問を、元の要求と共通条件に分けて検索します。"
        ) as overflow_step:
            overflow_step.input(
                lambda: {
                    "原質問": request_text,
                    "既検索文": list(retrieval_queries),
                    "既存根拠": candidate_snapshot(context.records),
                    "追加検索予算": extra_budget.to_payload(),
                    "要求別の予算": asdict(_request_coverage_budget(settings)),
                    "使用済みの要求別予算": asdict(coverage_usage),
                    "呼出側で計画済み": caller_planned,
                    "原質問のみ": bool(getattr(settings, "retrieval_original_query_only", False)),
                    "検索範囲": selected_retrieval_scope,
                    "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                }
            )
            overflow_results, overflow_trace = search_overflow_requests(
                request_text,
                tokenize=lambda text: tokenize_text_search_query_with_trace(
                    text, domain_keywords=overflow_keywords, config=overflow_config
                ),
                retrieve=overflow_retrieve,
                extra_budget=extra_budget,
                coverage_budget=_request_coverage_budget(settings),
                usage=coverage_usage,
                searched_queries=retrieval_queries,
                initial_query_overflowed=any(item.get("truncated") for item in text_search_info.tokenization_traces),
                allow_split=not caller_planned and not getattr(settings, "retrieval_original_query_only", False),
                skip_reason="caller_planned" if caller_planned else "original_query_only",
            )
            overflow_added: set[str] = set()
            existing_overflow_ids = {record.chunk_uid or record.id for record in context.records}
            overflow_parents = select_new_parents(
                [TargetedRetrieval(result.request_id, (result.query,), result.context.evidence_tree) for result in overflow_results],
                existing_overflow_ids,
                _request_coverage_budget(settings).max_chunks,
            )
            if overflow_parents:
                bundle = context_bundle_from_parent_evidence(
                    tuple(replace(parent, reason="query_overflow") for _, parent in overflow_parents), max_chars=MAX_CONTEXT_CHARS
                )
                candidate = merge_evidence(
                    context,
                    AnswerContext(
                        records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree
                    ),
                    normalized_question,
                    max_records=len(context.records) + len(bundle.records),
                    max_chars=MAX_CONTEXT_CHARS * 2,
                )
                merged_ids = {record.chunk_uid or record.id for record in candidate.records}
                if existing_overflow_ids <= merged_ids:
                    context = replace(
                        candidate,
                        expansion_records=tuple(
                            dict(
                                (record.chunk_uid or record.id, record)
                                for record in (
                                    *context.expansion_records,
                                    *(record for result in overflow_results for record in result.context.expansion_records),
                                )
                            ).values()
                        ),
                        preferred_child_ids=context.preferred_child_ids,
                        ranked_candidates=context.ranked_candidates,
                        deferred_records=context.deferred_records,
                        document_selection=context.document_selection,
                    )
                    overflow_added = merged_ids - existing_overflow_ids
            coverage_usage.chunks += len(overflow_added)
            overflow_trace["added_chunk_ids"] = sorted(overflow_added)
            overflow_step.output(
                lambda: {
                    "検索結果": overflow_trace,
                    "要求ごとの返却根拠": [
                        {"要求ID": item.request_id, "検索文": item.query, "候補": candidate_snapshot(item.context.records)}
                        for item in overflow_results
                    ],
                    "新規採用ID": sorted(overflow_added),
                    "処理後の根拠": candidate_snapshot(context.records),
                }
            )
            if overflow_trace.get("triggered"):
                statuses = [str(row["status"]) for row in overflow_trace["requests"]]
                overflow_step.result(f"要求 {len(statuses)} 件、新しい根拠 {len(overflow_added)} 件")
                overflow_step.field("追加検索の残り", str(extra_budget.remaining))
                overflow_step.field("未検索", str(sum(status.startswith("unsearched_") for status in statuses)))
                overflow_step.field("失敗", str(statuses.count("failed")))
            else:
                overflow_step.skip(
                    {
                        "caller_planned": "呼出側が検索文を計画済みのため、要求別には分割しません。",
                        "original_query_only": "原質問のみを検索する設定のため、要求別には分割しません。",
                        "tokenization_failed": "検索語の抽出に失敗したため、補完検索を実行していません。",
                    }.get(str(overflow_trace.get("skipped") or ""), "語数上限の超過がないため追加検索は不要です。")
                )
        # 業務ガイドの手順が後に来るなら、その根拠から広げる分の入力を残す。
        context, section_expansion = complete_sections(
            context, "main", reserve=guide_plan is not None and bool(getattr(settings, "guide_step_retrieval_enabled", False))
        )
        guide_step_trace: dict[str, Any] | None = None
        if guide_plan is not None:
            # 業務ガイドの案内（窓口・引き継ぐ条件・承認。#2792）。本文の「業務ガイドの案内」の節に資料とは分けて出す。
            from rag_engine.generation.grounded import guide_notice_lines

            runtime_knowledge = replace(
                runtime_knowledge,
                guide_title=guide_plan.title,
                guide_task_scope=guide_plan.task_scope,
                guide_notices=tuple(
                    guide_notice_lines(
                        guide_plan.contact,
                        guide_plan.handoff_conditions,
                        guide_plan.approval_note,
                        guide_plan.approval_required,
                        branch_notes=guide_plan.branch_notes,
                    )
                ),
                guide_condition_notices=guide_plan.branch_notes,
            )
            with _execution_step(
                "業務ガイドの手順ごとの検索", "業務ガイドの手順ごとに検索し、手順が参照する資料の頁の根拠を回答の材料に加えます。"
            ) as guide_step:
                guide_step.input(
                    lambda: {
                        "質問": normalized_question,
                        "手順計画": asdict(guide_plan),
                        "既存根拠": candidate_snapshot(context.records),
                        "追加検索予算": extra_budget.to_payload(),
                        "検索範囲": selected_retrieval_scope,
                        "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                        "起点数": chunk_top_k,
                        "前後それぞれのChunk数": chunk_neighbor_count,
                        "再ランキング": effective_rerank_enabled,
                        "設定": {
                            key: getattr(settings, key, None)
                            for key in (
                                "guide_step_retrieval_enabled",
                                "guide_step_max_queries",
                                "guide_step_chunks_per_step",
                                "guide_step_deadline_seconds",
                                "guide_reference_max_chunks",
                            )
                        },
                    }
                )
                context, guide_step_trace, step_sources = _cover_guide_steps(
                    normalized_question,
                    context,
                    settings,
                    guide_plan,
                    extra_budget=extra_budget,
                    run_id=run_id,
                    preferred_engines=preferred_engine_ids,
                    top_k=chunk_top_k,
                    neighbor_child_count=chunk_neighbor_count,
                    rerank_enabled=effective_rerank_enabled,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                    runtime_knowledge=runtime_knowledge,
                )
                runtime_knowledge = replace(
                    runtime_knowledge,
                    guide_steps=step_sources,
                    guide_optional_steps=guide_plan.optional_step_ids,
                    guide_step_terms=guide_plan.step_terms,
                    guide_step_contracts=guide_plan.step_contracts,
                    guide_unsearched_steps=tuple(guide_step_trace.get("unsearched_step_ids") or ()),
                    guide_acquisition=tuple((guide_step_trace.get("required_acquisition") or {}).get("targets") or ()),
                    guide_acquisition_notices=_guide_acquisition_notices(guide_step_trace),
                )
                guide_step.output(
                    lambda: {
                        "検索と取得の記録": guide_step_trace,
                        "手順と根拠の対応": step_sources,
                        "処理後の根拠": candidate_snapshot(context.records),
                        "新規採用ID": list(guide_step_trace.get("added_chunk_ids") or ()),
                    }
                )
                skipped = str(guide_step_trace.get("skipped") or "")
                if skipped:
                    guide_step.skip(_GUIDE_STEP_SKIP_LABELS.get(skipped, f"しない（{skipped}）"))
                else:
                    steps = guide_step_trace.get("steps") or []
                    guide_step.result(
                        f"手順 {len(steps)} 件のうち根拠あり {sum(1 for item in steps if item['evidence_count'])} 件",
                        *_guide_step_lines(guide_step_trace),
                    )
                    if guide_step_trace.get("added_chunk_ids"):
                        guide_step.impact(f"手順の根拠 {len(guide_step_trace['added_chunk_ids'])} 件を回答の材料に加えます。")
        # 手順別の検索で新たに得た根拠も、要求ごとの判定に渡す前に最低範囲を読む。
        context, section_expansion = complete_sections(context, "guide_steps")
        if guide_plan is not None and runtime_knowledge is not None and runtime_knowledge.guide_steps:
            # 章節の拡張が child を parent に置き換えた後の ID も、参照の頁から手順に結び直す。
            expanded_sources = reference_step_sources(guide_plan, context.records)
            runtime_knowledge = replace(
                runtime_knowledge,
                guide_steps=tuple(
                    (step_id, title, tuple(dict.fromkeys((*sources, *expanded_sources.get(step_id, ())))))
                    for step_id, title, sources in runtime_knowledge.guide_steps
                ),
            )
        if not context.records or not context.text.strip():
            flow_step.status = "根拠不足で終了"
            flow_step.add("回答に使える根拠がありません。" + ("回答文の生成は未実行です。" if generate_answer else ""))
            # どの段で 0 件になったか（#2401）。回答の記録から検索をやり直さずに原因の段を追う。
            zero_evidence = zero_evidence_stage(context)
            flow_step.field("根拠が 0 件になった段", zero_evidence["label"])
            flow_step.field("検索の候補", f"{zero_evidence['candidate_count']} 件")
            # 候補 0 件でも回答と同じ形で件数を残す（#4735）。
            zero_trace = {
                "zero_evidence": zero_evidence,
                "query_overflow": overflow_trace,
                "retrieval_counts": _retrieval_counts(context, ()),
            }
            if not generate_answer:
                return evidence_result(context, trace=zero_trace)
            # 空検索では LLM に推測させず、表示本文と保存 payload の不足情報を揃える。
            response = AnswerResponse(
                answer_text=(answer_labels().insufficient_evidence_summary + answer_labels().empty_search_guidance),
                confidence="low",
                insufficient_reason="現在の検索範囲では、回答に使える文書の根拠が取得できませんでした。",
                needs_human_review=True,
                generation_trace=zero_trace,
            )
            response = _with_guide_acquisition_notices(response, runtime_knowledge)
            if legacy_notice:
                response = replace(response, answer_text=f"{response.answer_text}\n\n{legacy_notice}")
            return record_flow_result(
                _answer_question_result(
                    settings=settings,
                    expansion=expansion,
                    answer=_format_answer_response(response),
                    response=response,
                    text_search_info=text_search_info,
                    question_plan=question_plan,
                    runtime_knowledge=runtime_knowledge,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                    retrieval_queries=retrieval_queries,
                    retrieval_top_k=chunk_top_k,
                    request_question=request_text,
                )
            )

        context, section_expansion = complete_sections(context, "request_coverage")
        # 根拠の補いの記録（回答の generation_trace と同じ key）。
        evidence_trace: dict[str, Any] = {
            "query_overflow": overflow_trace,
            **({"guide_steps": guide_step_trace} if guide_step_trace is not None else {}),
            **({"section_expansion": section_expansion.trace} if section_expansion is not None else {}),
        }
        if not generate_answer:
            # 回答を作らない検索（RAG 検索の画面）は、回答と同じ方法で集めた根拠をここで返す（#3506）。
            flow_step.result(f"{EVIDENCE_RESULT_TITLE} {evidence_summary(context.records)}（回答は生成しません）")
            return evidence_result(context, trace=evidence_trace)

        with _execution_step(
            "回答に添付する画像", "回答モデルが画像を読めるとき、根拠の資料の図・画面の画像を回答の入力に添付します。"
        ) as image_step:
            # 回答は選んだ回答モデルだけで作る（#3444）。画像を読めるモデルなら根拠の chunk に結び付いた画像を添付し、
            # 読めないモデルは根拠の本文（取り込み時に作った図の説明を含む）だけで答える。質問の語で要否を決めない。
            has_images = any(record_has_image_evidence(record) for record in context.records)
            reads_images = answer_model_reads_images(settings, answer_llm_provider)
            image_step.input(
                lambda: {
                    "質問": normalized_question,
                    "根拠ID": [item.chunk_uid or item.id for item in context.records],
                    "画像を含む根拠ID": [item.chunk_uid or item.id for item in context.records if record_has_image_evidence(item)],
                    "回答モデルの指定": answer_llm_provider,
                    "画像を読める": reads_images,
                }
            )
            image_evidence = (
                answer_image_evidence(context.records, settings.output_dir, question=normalized_question)
                if has_images and reads_images
                else ()
            )
            image_prompt_mode = _image_prompt_mode(image_evidence, settings, answer_llm_provider)
            attached = [item for item in image_evidence if item.get("prompt_path")] if image_prompt_mode == "vision_attachments" else []
            image_step.output(
                lambda: {
                    "入力方式": image_prompt_mode,
                    "準備画像数": len(image_evidence),
                    "添付画像数": len(attached),
                    "理由": (
                        "no_image_evidence"
                        if not has_images
                        else "model_without_vision"
                        if not reads_images
                        else "no_attachable_image"
                        if not attached
                        else "attached"
                    ),
                    "画像": [
                        {**{key: item.get(key) for key in ("image_id", "page", "visual_kind")}, "添付": item in attached}
                        for item in image_evidence
                    ],
                }
            )
            if not has_images:
                # 記録の文からは省くが、処理の経過の段は出るので理由を残す（#2341）。
                image_step.hide()
                image_step.result("根拠に画像がありません")
            elif not reads_images:
                image_step.result("回答モデルが画像を読めないため、画像は添付しません（図の内容は取り込み時に作った文字の説明で読みます）")
            elif not attached:
                image_step.result(
                    "添付できる画像のファイルが無いため、画像は添付しません（図の内容は取り込み時に作った文字の説明で読みます）"
                )
            else:
                image_step.result(f"画像 {len(attached)} 件を添付して回答します")
                image_step.add(
                    "添付する画像:",
                    *numbered(
                        [
                            f"{Path(str(item.get('source') or '')).name or item.get('image_id')}"
                            + (f"（p.{item['page']}）" if item.get("page") else "")
                            + (f" {item['visual_kind']}" if item.get("visual_kind") else "")
                            for item in attached
                        ]
                    ),
                )

        unread_for_answer = list(section_expansion.unread) if section_expansion is not None else []

        grounded_answer = synthesize_with_model_cascade(
            request_text,
            context,
            settings,
            question_plan=question_plan,
            runtime_knowledge=runtime_knowledge,
            image_evidence=image_evidence,
            image_prompt_mode=image_prompt_mode,
            answer_llm_provider=answer_llm_provider,
            unread_sections=unread_for_answer,
            unread_documents={document_id for document_id, _start, _end in guide_ranges},
            evidence_budget_chars=_section_evidence_budget(context, settings, section_expansion),
        )
        # 是正で根拠や画像が増えた場合は、回答を生成した入力を参照欄と payload に使う。
        response, context = grounded_answer.response, grounded_answer.context
        response = replace(
            response,
            generation_trace={
                **(response.generation_trace or {}),
                **evidence_trace,
                "extra_retrieval_budget": extra_budget.to_payload(),
            },
        )
        image_evidence, image_prompt_mode = grounded_answer.image_evidence, grounded_answer.image_prompt_mode
        if legacy_notice:
            response = replace(response, answer_text=f"{response.answer_text}\n\n{legacy_notice}")
        answer = _format_answer_response(response)
        publication = (response.generation_trace or {}).get("finalization", {})
        references = format_references(
            _published_reference_records(context.records, response.generation_trace),
            include_source=bool(publication.get("filtered") or publication.get("citations_synchronized")),
        )
        if references:
            answer = f"{answer}\n\n参照:\n{references}"
        return record_flow_result(
            _answer_question_result(
                settings=settings,
                expansion=expansion,
                answer=answer,
                response=response,
                records=context.records,
                text_search_info=text_search_info,
                question_plan=question_plan,
                runtime_knowledge=runtime_knowledge,
                retrieval_scope=selected_retrieval_scope,
                classification_filter=selected_classification_filter,
                image_evidence=image_evidence,
                image_prompt_mode=image_prompt_mode,
                evidence_tree=context.evidence_tree,
                retrieval_queries=retrieval_queries,
                retrieval_top_k=chunk_top_k,
                request_question=request_text,
            )
        )


def _expand_context_by_sections(
    question: str,
    context: AnswerContext,
    settings: Settings,
    *,
    answer_llm_provider: str | None,
    rerank_enabled: bool,
    started_at: float,
    state: dict[str, Any] | None = None,
    phase: str = "main",
    reserve: bool = False,
    priority: Sequence[tuple[str, int | None, int | None]] = (),
) -> tuple[AnswerContext, Any]:
    """当たった chunk の章節から親の章節へ段階的に広げる (#1436。`section_expansion`)。

    最低範囲（列挙の問は文書全体）までの原文を決定的に足す。state は1回答内で共有し、追加検索後も同じ原文・根拠入力の
    予算・回答全体の期限を使う。無効と読み込み未設定は診断で区別する。
    ``reserve`` は、後の段のために入力の一部（``section_expansion_followup_reserve_percent``）を残す（#2816）。
    ``priority`` は先に広げる範囲（業務ガイドの手順の参照の文書, 始まりの頁, 終わりの頁。#2828）。
    """
    from rag_engine.dependencies import section_dependencies
    from rag_engine.generation import section_expansion as sections
    from rag_engine.resources.deadline import remaining_seconds

    ports = section_dependencies()
    if state is None:
        state = {}

    def record_skip(reason: str) -> None:
        with _execution_step("章節の拡張", "起点根拠の章節から回答の文脈を広げます。") as step:
            step.input(
                {
                    "有効": bool(getattr(settings, "section_expansion_enabled", True)),
                    "広げる段の上限": sections.SectionExpansionLimits.from_settings(settings).max_levels,
                    "起点根拠 ID": list(sections.seed_chunk_ids(context)),
                }
            )
            step.skip(reason)
            step.output({"実行状態": reason, "新規読取 Chunk 数": 0, "重複除去後の新規採用 Chunk 数": 0})

    if not context.records or not context.text.strip():
        record_skip("起点根拠がありません。")
        return context, state.get("result")
    if ports is None or not getattr(settings, "section_expansion_enabled", True):
        reason = "disabled" if not getattr(settings, "section_expansion_enabled", True) else "not_configured"
        result = sections.SectionExpansionResult(
            context=context,
            stop_reason=reason,
            trace={"enabled": reason != "disabled", "stop_reason": reason, "applied": False, "minimum_scope_complete": False},
        )
        record_skip("設定が無効です。" if reason == "disabled" else "章節の読み取りが設定されていません。")
        state["result"] = result
        return context, result

    def signature_of(candidate: AnswerContext) -> tuple[Any, ...]:
        return (
            tuple((record.chunk_uid or record.id, record.text) for record in candidate.records),
            tuple(sections.seed_chunk_ids(candidate)),
        )

    signature = signature_of(context)
    if signature == state.get("signature"):
        record_skip("読み取り済みの同じ文脈を再利用しました。")
        return context, state.get("result")

    def section_rerank(query: str, documents: list[str], settings: Settings, top_n: int | None = None) -> list[RerankTextRank]:
        started = monotonic()
        status = "error"
        try:
            with bind_rerank_context(phase="section_expansion", batch_id=uuid4().hex, query_index=0, query_role="original") as receipt:
                receipt.describe_inputs(query, documents, model=settings.rerank_model, top_n=top_n if top_n is not None else len(documents))
                result = rerank_text_with_scores(query, documents, settings, top_n=top_n)
                status = "completed"
                return result
        finally:
            record_retrieval_stage(
                {
                    "stage": "rerank_query",
                    **receipt.to_dict(),
                    "logical_status": "cancelled" if receipt.status == "cancelled" else status,
                    "logical_elapsed_seconds": monotonic() - started,
                }
            )

    rerank = section_rerank if rerank_enabled and _rerank_configured(settings) else None
    from rag_engine.generation.section_extraction import SectionExtractor

    # 予算に入らない章節は、質問に関係する原文を抜き出してから統合する (#1437)。回答全体の期限と根拠入力の予算を共有する。
    if "extractor" not in state:
        state["extractor"] = SectionExtractor(settings, parse_text_response, provider_id=answer_llm_provider)
        state["reader"] = sections.SectionReader(read_chunks=ports[1], known_ids=set())
        state["outlines"] = {}
    extractor = state["extractor"]
    # 抜き出しを使わない設定（#2800）では、入らない章節を rerank の順で上限まで入れ、残りは未反映として示す。
    large_sections = extractor if getattr(settings, "section_extraction_enabled", True) else sections.no_large_section_handler

    def outline(**kwargs: Any) -> Any:
        uid = kwargs["chunk_uid"]
        if uid not in state["outlines"]:
            state["outlines"][uid] = ports[0](**kwargs)
        return state["outlines"][uid]

    reader = state["reader"]
    event_start = len(reader.read_events)
    completed_before = set(reader.read_sections)
    before_ids = {retrieval_record_id(child.record) for parent in context.evidence_tree for child in parent.children}
    neighbor_ids = {
        retrieval_record_id(child.record)
        for parent in context.evidence_tree
        for child in parent.children
        if child.role == "neighbor_context"
    }
    with _execution_step("章節の拡張", "当たった根拠の章節から親の章節へ段階的に広げ、原文を根拠に加えます。") as step:
        result = sections.expand_sections(
            question,
            context,
            settings,
            section_outline=outline,
            section_chunks=ports[1],
            rerank=rerank,
            large_sections=large_sections,
            started_at=started_at,
            deadline_remaining=remaining_seconds,
            clock=monotonic,
            reader=state["reader"],
            reserve_tokens=sections.SectionExpansionLimits.from_settings(settings).followup_reserve_tokens if reserve else 0,
            priority=tuple(sections.PriorityRange(document_id, start, end) for document_id, start, end in priority),
        )
        result.trace["phase"] = phase
        result.trace["extraction_enabled"] = bool(getattr(settings, "section_extraction_enabled", True))
        if result.trace.get("large_section_handoff") == "extract_then_integrate_unavailable":
            result.trace["large_section_handoff_reason"] = "disabled_by_setting"
        previous = state.get("result")
        if previous is not None:
            result.applied = result.applied or previous.applied
            result.trace["applied"] = result.applied
        result.trace["phases"] = [
            *(previous.trace.get("phases", []) if previous else []),
            {key: value for key, value in result.trace.items() if key != "phases"},
        ]
        if extractor.calls:
            result.trace["extraction"] = {
                "calls": extractor.calls,
                "part_tokens": extractor.limits.part_tokens,
            }
        events = reader.read_events[event_start:]
        read_ids = {uid for event in events if event["state"] == "read" for uid in event["child_ids"]}
        all_ids = {uid for event in events for uid in event["child_ids"]}
        after_ids = {retrieval_record_id(child.record) for parent in result.context.evidence_tree for child in parent.children}
        new_ids = after_ids - before_ids
        completed_now = reader.read_sections - completed_before
        routes = result.trace.get("expansion_routes", [])
        relations = [
            {
                **event,
                "seed_ids": [
                    route["seed_id"]
                    for route in routes
                    if (route["document_id"], route["chunk_set_id"], route["origin_section_id"])
                    == (event["document_id"], event["chunk_set_id"], event["origin_section_id"])
                ],
                "adopted_child_ids": sorted(set(event["child_ids"]) & after_ids),
                "newly_adopted_child_ids": sorted(set(event["child_ids"]) & new_ids),
                "already_in_context_ids": sorted(set(event["child_ids"]) & before_ids),
            }
            for event in events
        ]
        step.input(
            {
                "有効": result.trace.get("enabled"),
                "広げる段の上限": result.trace.get("max_levels"),
                "根拠入力の上限 token": result.trace.get("input_token_limit"),
                "根拠入力の上限（%）": getattr(settings, "section_expansion_input_token_percent", None),
                "予約 token": result.trace.get("reserved_tokens", 0),
                "起点根拠数": len(sections.seed_chunk_ids(context)),
                "準備した章節範囲": routes,
                "実行段階": phase,
            }
        )
        output = {
            "読取章節数": len(
                {(event["document_id"], event["chunk_set_id"], event["section_id"]) for event in events if event["state"] == "read"}
            ),
            "読了した章節数": len(completed_now),
            "新規読取 Chunk 数": len(read_ids),
            "読み取り候補のうち既存文脈と重複する ID": sorted(all_ids & before_ids),
            "前後文脈と重複する ID": sorted(all_ids & neighbor_ids),
            "重複除去後の新規採用 Chunk 数": len(new_ids),
            "新規採用 ID": sorted(new_ids),
            "実際に内容を補充した章節数": len(
                {
                    (event["document_id"], event["chunk_set_id"], event["section_id"])
                    for event in relations
                    if event["newly_adopted_child_ids"]
                }
            ),
            "起点と章節・内容の対応": relations,
            "読取結果": result.trace.get("levels", []),
            "停止理由": _SECTION_STOP_LABELS.get(result.stop_reason, result.stop_reason),
            "未読": [item.to_payload() for item in result.unread],
            "最終採用の意味": "この工程後の回答文脈への採用。モデルによる引用数ではありません。",
        }
        result.trace["expansion_receipt"] = output
        step.output(output)
        if result.stop_reason == sections.STOP_DISABLED:
            step.skip("章節を広げる設定が無効です。")
        else:
            step.result(f"章節 {len(completed_now)} 件を読み終え、新規 {len(new_ids)} Chunk を回答文脈に採用しました。")
        if extractor.calls:
            step.add(f"長い章節は、質問に関係する原文を抜き出してから根拠にしました（抜き出しの呼び出し {extractor.calls} 回）。")
        # 広げた段・判定の回数・止めた理由・読めなかった章節（#2341）。
        step.field("広げた段", f"{result.levels} 段")
        step.field("止めた理由", _SECTION_STOP_LABELS.get(result.stop_reason, result.stop_reason))
        step.field("回答の材料の根拠", evidence_summary(result.context.records))
        if result.unread:
            step.field("読めなかった章節", f"{len(result.unread)} 件")
            step.impact("上限に達したため、読めなかった章節を「回答に未反映の関連章節」に示し、条件つきの回答にします。")
        if result.omitted:
            # 起点を含まない兄弟の章節・業務ガイドの参照の外の文書の章節は、読めなくても回答に出さない（#2816・#2828）。
            step.field(
                "読めなかった参考の章節", f"{len(result.omitted)} 件（当たった章節や業務ガイドの参照の外のため、回答には示しません）"
            )
    state["result"] = result
    state["signature"] = signature_of(result.context)
    return result.context, result


_SECTION_STOP_LABELS = {
    "document_read": "文書全体まで読んだ",
    "max_levels": "広げる段の上限",
    "token_budget": "根拠の入力の上限",
    "time_limit": "回答の時間の上限",
    "no_sections": "章節の対応なし",
    "not_configured": "章節の読み込み未設定",
    "disabled": "無効",
    "scope_read": "必要な範囲まで読んだ",
    "read_error": "原文の読取に失敗",
    "partial_read": "必要な範囲の一部が未読",
}


def _section_evidence_budget(context: AnswerContext, settings: Settings, expansion: Any) -> int | None:
    """章節の補完後も設定の文字上限を使う。最終の描画・モデル入力の予算は送信前に検査する。"""
    if expansion is None or not (expansion.applied or expansion.trace.get("minimum_scope_complete")):
        return None
    from rag_engine.generation.section_expansion import SectionExpansionLimits, context_tokens

    base = int(getattr(settings, "evidence_budget_chars", 0) or EVIDENCE_BUDGET_CHARS)
    limit = SectionExpansionLimits.from_settings(settings).input_tokens
    if context_tokens(context) > limit:
        return max(1, min(base, limit))
    return base


def load_answer_chunks(output_dir: str | Path, run_id: Any, preferred_engines: Iterable[str]) -> list[AnswerRecord]:
    """保存済み chunk run から回答生成用 AnswerRecord を読み込みます。"""
    chunk_run = load_latest_or_source_chunk_run(output_dir, run_id)
    if chunk_run is None:
        return []
    return _preferred_chunk_records(chunk_run, preferred_engines)


def _display_query(query: str) -> str:
    return _trim_generated_text(query, MAX_GENERATED_QUERY_CHARS)


def _answer_question_result(
    expansion: QueryExpansionResult,
    answer: str,
    *,
    settings: Settings | None = None,
    response: AnswerResponse | None = None,
    records: Sequence[AnswerRecord] = (),
    text_search_info: QuestionTextSearchInfo | None = None,
    question_plan: QuestionPlan | None = None,
    runtime_knowledge: RuntimeKnowledgeContext | None = None,
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    classification_filter: ClassificationFilter | None = None,
    image_evidence: Sequence[dict[str, Any]] = (),
    image_prompt_mode: str = "text_only",
    evidence_tree: Sequence[ContextParentEvidence] = (),
    retrieval_queries: Sequence[str] | None = None,
    lexical_queries: Sequence[str] = (),
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    request_question: str = "",
) -> AnswerQuestionResult:
    """``request_question`` は要求の単位の正本の質問（#2665。空なら検索の質問 ``expansion.original_question``）。"""
    parsed = response or AnswerResponse(answer_text=answer, raw_text=answer)
    cited_evidence_ids = tuple(_cited_evidence_ids(parsed.cited_evidence))
    text_search = text_search_info or QuestionTextSearchInfo()
    selected_retrieval_scope = normalize_retrieval_scope(retrieval_scope)
    selected_classification_filter = classification_filter or classification_filter_from_values()
    primary_source_run_id = _primary_source_run_id(records)
    prompt_warnings = prompt_injection_warnings_for_records(records, evidence_tree=evidence_tree)
    rerank_scores = _rerank_scores_from_evidence(records, evidence_tree)
    retrieval_query_plan = _retrieval_query_plan_payload(
        settings=settings,
        expansion=expansion,
        text_search_info=text_search,
        runtime_knowledge=runtime_knowledge,
        retrieval_scope=selected_retrieval_scope,
        classification_filter=selected_classification_filter,
        top_k=retrieval_top_k,
        vector_queries=retrieval_queries,
        lexical_queries=lexical_queries,
    )
    return AnswerQuestionResult(
        answer=answer,
        question_display=expansion.original_question,
        original_question=expansion.original_question,
        retrieval_scope=selected_retrieval_scope,
        classification_filter=selected_classification_filter.to_metadata(),
        generated_queries=expansion.generated_queries,
        answer_text=parsed.answer_text or answer,
        confidence=parsed.confidence,
        question_type=parsed.question_type,
        cited_evidence_ids=cited_evidence_ids,
        cited_evidence=parsed.cited_evidence,
        image_evidence=tuple(dict(item) for item in image_evidence),
        image_prompt_mode=image_prompt_mode,
        primary_source_run_id=primary_source_run_id,
        reasoning_summary=parsed.reasoning_summary,
        insufficient_reason=parsed.insufficient_reason,
        needs_human_review=parsed.needs_human_review,
        external_data_required=parsed.external_data_required,
        external_data_items=parsed.external_data_items,
        evidence_items=tuple(
            answer_evidence_items(
                records, cited_evidence_ids, evidence_tree=evidence_tree, answer_uses=_cited_evidence_uses(parsed.cited_evidence)
            )
        ),
        evidence_facts=parsed.evidence_facts,
        generation_trace=parsed.generation_trace,
        task_contract=task_contract(request_question or expansion.original_question),
        envelope=parsed.envelope
        or fallback_envelope(
            answered=response is not None and bool(parsed.evidence_facts),
            insufficient_reason=parsed.insufficient_reason,
            needs_human_review=parsed.needs_human_review,
            external_data_required=parsed.external_data_required,
            external_data_items=parsed.external_data_items,
        ),
        text_search_tokenizer=text_search.tokenizer,
        text_search_tokenizer_label=text_search.tokenizer_label,
        text_search_tokenizer_fingerprint=text_search.tokenizer_fingerprint,
        text_search_tokens=text_search.tokens,
        text_search_query=text_search.oracle_text_query,
        text_search_queries=text_search.oracle_text_queries or ((text_search.oracle_text_query,) if text_search.oracle_text_query else ()),
        text_search_query_source=text_search.query_source,
        text_search_tokenization_error=text_search.error,
        prompt_injection_risk=bool(prompt_warnings),
        prompt_injection_warnings=prompt_warnings,
        rerank_scores=rerank_scores,
        retrieval_query_plan=retrieval_query_plan,
        question_plan=question_plan,
        runtime_knowledge=runtime_knowledge,
    )


def _answer_record_active(record: AnswerRecord) -> bool:
    return _bool_metadata_value(record.metadata.get("active"), default=True)


def _ranked_records_with_neighbors(
    question: str,
    records: Sequence[AnswerRecord],
    limit: int,
    *,
    retrieval_queries: Sequence[str] | None = None,
    rerank_enabled: bool = False,
    settings: Settings | None = None,
) -> list[AnswerRecord]:
    if limit <= 0:
        return []

    queries = _retrieval_queries(question, retrieval_queries)
    normalized_question = _normalize_text(question)
    terms = _search_terms_for_queries(queries)
    ranked = rank_records_with_rrf(queries, records, len(records)) if len(queries) > 1 else rank_records(question, records, len(records))
    ranked = rerank_records(question, ranked, settings, enabled=rerank_enabled)
    by_page: dict[int, list[AnswerRecord]] = {}
    for record in records:
        by_page.setdefault(record.page, []).append(record)

    selected: list[AnswerRecord] = []
    seen: set[str] = set()

    def add(record: AnswerRecord) -> None:
        if record.id not in seen and len(selected) < limit:
            selected.append(record)
            seen.add(record.id)

    primary_limit = max(1, min(limit, limit // 3))
    primary_records = ranked[:primary_limit]
    for record in primary_records:
        add(record)
        if len(selected) >= limit:
            break

    for record in primary_records:
        for neighbor in _context_neighbors(record, by_page.get(record.page, []), normalized_question, terms)[
            :MAX_CONTEXT_NEIGHBORS_PER_RECORD
        ]:
            add(neighbor)
            if len(selected) >= limit:
                break
        if len(selected) >= limit:
            break

    for record in ranked[primary_limit:]:
        add(record)
        if len(selected) >= limit:
            break

    return selected
