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
    _merge_text_rerank_candidates,
    _preferred_chunk_records,
    _records_by_rerank_ranks,
    _rerank_configured,
    _rerank_document,
    _retrieval_candidate_limit,
    _search_terms_for_queries,
    _split_text_rerank_candidates,
    _stored_chunk_answer_record,
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
    _normalized_query_text,
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
from rag_engine.retrieval.attachment_evidence import is_attachment_evidence
from rag_engine.retrieval.candidate_budget import artifact_key as retrieval_artifact_key
from rag_engine.retrieval.candidate_budget import record_id as retrieval_record_id
from rag_engine.retrieval.candidate_budget import select_candidates, stage_limits
from rag_engine.retrieval.character_forms import fold_model_view, fold_width
from rag_engine.retrieval.context_builder import (
    MAX_ANCHORS_PER_PARENT,
    ContextBuildRequest,
    ContextChildEvidence,
    ContextParentEvidence,
    build_chunk_context_bundle,
    context_bundle_from_parent_evidence,
    context_bundle_from_records,
    record_has_image_evidence,
    uses_parent_context,
)
from rag_engine.retrieval.context_fill import consolidate_evidence_tree, context_fill_mode
from rag_engine.retrieval.context_recovery import ContextRecovery, recover_context_records
from rag_engine.retrieval.evidence_selection import EVIDENCE_BUDGET_CHARS, evidence_spans
from rag_engine.retrieval.evidence_selection import new_evidence_count as _new_evidence_count
from rag_engine.retrieval.expansion_merge import GUIDE_STEP_KEY, QUERY_OVERFLOW_KEY, SUPPLEMENT_KEYS
from rag_engine.retrieval.extra_retrieval import GUIDE_STEPS_KIND, REQUEST_COVERAGE_KIND, ExtraRetrievalBudget
from rag_engine.retrieval.guide_steps import (
    GUIDE_STEP_TRACE_SCHEMA_VERSION,
    GuidePlan,
    StepSearch,
    reference_step_sources,
    required_guide_targets,
    run_required_guide_searches,
    run_step_searches,
    target_records,
    unsearched_steps,
)
from rag_engine.retrieval.query_identity import dedupe_queries, dedupe_query_key, normalized_query_key
from rag_engine.retrieval.query_overflow import search_overflow_requests
from rag_engine.retrieval.question_planning import QuestionPlan, plan_question
from rag_engine.retrieval.request_coverage import CoverageBudget, CoverageUsage
from rag_engine.retrieval.rerank_order import order_reranked_candidates
from rag_engine.retrieval.rerank_scores import best_rerank_score, question_rerank_score, rerank_first_key, rerank_score
from rag_engine.retrieval.scope import (
    RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN,
    RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
    AdbSearchReadiness,
    normalize_retrieval_scope,
)
from rag_engine.retrieval.task_contract import (
    REQUIRED_ASPECTS,
    task_contract,
)
from rag_engine.retrieval.text_search_tokenizer import (
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
        # 並べ替えた後の上位と関連度（処理の経過の段を開いて見る。#2341）。
        record_rerank_top(step, ranked)
        step.output({"判定": receipt, "処理後の候補": candidate_snapshot(ranked)})
        return ranked


# rerank に使う検索文の上限（原質問のほか。LLM ではなく rerank の呼び出しの数を抑える。#2161）。
MAX_EXTRA_RERANK_QUERIES = 6


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


def _order_by_score(
    records: Sequence[AnswerRecord], settings: Settings, step: Any, receipt: dict[str, Any], *, rerank_phase: str
) -> list[AnswerRecord]:
    """合格した候補を rerank の点数の順（MMR）に並べ、工程の記録に残す（#5165。検索の順は混ぜない）。"""
    tokenizer_config = TextSearchTokenizerConfig(
        sudachi_dict_type=getattr(settings, "text_search_tokenizer_sudachi_dict", ""),
        sudachi_config_path=getattr(settings, "text_search_tokenizer_sudachi_config", ""),
        latin_stemmer=getattr(settings, "text_search_tokenizer_latin_stemmer", ""),
    )
    ordered = order_reranked_candidates(records, tokenizer_config=tokenizer_config)
    receipt["並び"] = "rerank の点数の順（MMR で内容の似た候補を後ろへ）"
    step.field("並び", "rerank の点数の順（MMR で内容の似た候補を後ろへ回します）")
    record_retrieval_stage(
        {
            "stage": "rerank_order",
            "phase": rerank_phase,
            "order": [retrieval_record_id(record) for record in ordered],
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
    """rerank 本体。無効・未設定・失敗時は取得順を保持する（MMR も行わない）。

    成功したときは関連度の下限（``rerank_min_relevance_score``）未満を外し、残りを rerank の点数の順（MMR）に並べる。
    下限以上が 0 件なら根拠なしで、下限を下げてやり直さない（#5165）。件数はここで切らず、起点数（top_k）で 1 回だけ
    切る（context の組み立て）。

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
    # 全部が下限未満なら根拠なし（下限を下げてやり直さない。#5165）。
    receipt["関連度下限の判断"] = threshold_rows
    excluded = sum(row["status"] == "excluded" for row in threshold_rows)
    unscored = sum(row["status"] == "kept_unscored" for row in threshold_rows)
    attachments = sum(row["status"] == "kept_attachment" for row in threshold_rows)
    step.field("関連度スコアの下限", str(threshold_rows[0]["minimum"]))
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
            "decisions": threshold_rows,
            "excluded_count": excluded,
            "unscored_kept_count": unscored,
            "attachment_kept_count": attachments,
            "outside_scoring_count": len(protected_candidates) + len(remainder),
        }
    )
    if any(row["adopted_score"] is not None for row in threshold_rows):
        reranked_candidates = _order_by_score(reranked_candidates, cast(Settings, settings), step, receipt, rerank_phase=rerank_phase)
    return _merge_text_rerank_candidates(candidates, protected_candidates, reranked_candidates) + remainder


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


def cap_anchors_per_document(
    ranked_children: Sequence[AnswerRecord], settings: Settings | None, *, top_k: int, step: Any | None = None
) -> list[AnswerRecord]:
    """1 つの文書から採る候補を `max_anchors_per_document` 件までにする（#2167・#5165。0 は上限なし）。

    rerank の後の並びの順に文書ごとに数え、上限を超えた候補は外す（後ろへ回して使い直さない）。会話の添付は
    数えない。検索拡張の根拠は候補に入らない（#5162）。
    """
    from rag_engine.retrieval.metadata_context import document_context_key  # 循環 import を避けて関数内で読む

    limit = int(getattr(settings, "max_anchors_per_document", 0) or 0)
    if limit <= 0:
        return list(ranked_children)
    counts: dict[tuple[str, ...], int] = {}
    kept: list[AnswerRecord] = []
    dropped: list[AnswerRecord] = []
    for record in ranked_children:
        # 会話の添付（#3100）は数えない。
        if is_attachment_evidence(record):
            kept.append(record)
            continue
        key = document_context_key(record)
        if counts.get(key, 0) < limit:
            counts[key] = counts.get(key, 0) + 1
            kept.append(record)
        else:
            dropped.append(record)
    if step is not None:
        head = {id(record) for record in kept[:top_k]}
        removed = [record for record in ranked_children[:top_k] if id(record) not in head]
        step.result(f"文書ごとの上限 {limit} 件: {len(dropped)} 件を外しました（上位 {top_k} 件のうち {len(removed)} 件）")
        # 外した候補と、代わりに起点に入った候補（#2341）。
        if removed:
            step.add("外した候補:", *numbered([record_label(record) for record in removed]))
            original = {id(record) for record in ranked_children[:top_k]}
            raised = [record for record in kept[:top_k] if id(record) not in original]
            if raised:
                step.add("代わりに起点に入った候補:", *numbered([record_label(record) for record in raised]))
    return kept


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
    expansions: Sequence[AnswerRecord] = (),
    supplements: Sequence[AnswerRecord] = (),
    section_fill: bool = False,
):
    """rerank 済みの child を起点に文脈を補って context bundle を作る（文脈の補完 #5168）。

    expansions は検索拡張の根拠（#5162）。起点・親・前後の文脈を決めた後に最後の根拠の並びへ合わせる。
    supplements は補いの検索の根拠（#5173。検索ごとに rerank・下限・``top_k`` の切りを通したもの）。検索拡張の後に
    同じ規則で合わせる。
    section_fill は、この後に章節の拡張（``_expand_context_by_sections``）を行う経路か。そのとき章節の対応が
    ある起点は章節で補い、前後の chunk を読まない（補い方は起点ごとに 1 つ。章節 > 親 > 前後）。
    """
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
        expansion_children=list(expansions),
        supplement_children=list(supplements),
    )
    # 同じ起点選択を先に行い、未採用の候補の親本文・近傍を読まない。文字予算は原文の取得後に適用する。
    provisional = build_chunk_context_bundle(replace(request, max_chars=2**31 - 1, select_anchors_only=True))
    anchor_ids = {
        retrieval_record_id(child.record)
        for parent in provisional.evidence_tree
        for child in parent.children
        if child.role == "retrieved_anchor"
    }
    selected = list(
        {
            retrieval_record_id(record): record
            for record in (*ranked_children, *expansions, *supplements)
            if retrieval_record_id(record) in anchor_ids
        }.values()
    )
    sectioned = _sectioned_anchor_ids(selected, settings) if section_fill else frozenset()
    request = replace(request, section_anchor_ids=sectioned)
    modes = {
        retrieval_record_id(record): context_fill_mode(
            sectioned=retrieval_record_id(record) in sectioned, parent_child=uses_parent_context(record)
        )
        for record in selected
    }
    with (
        _execution_step("文脈の補完", "起点ごとに章節・親・前後の chunk のどれか 1 つで文脈を補い、重なる根拠をまとめます。") as step,
        bind_context_read_receipt() as receipt,
    ):
        step.input(
            {
                "前後それぞれの最大 Chunk 数": neighbor_child_count,
                "起点根拠数": len(selected),
                "起点根拠 ID": [retrieval_record_id(record) for record in selected],
                "起点ごとの補い方": modes,
                "文脈の最大レコード数": max_records,
            }
        )
        # 章節で補う起点は前後の chunk を読まない（親子分割の親・画像の原文は読む）。
        loaded = injected_context_chunks(
            anchors=selected, settings=settings, **({"section_anchor_ids": sorted(sectioned)} if sectioned else {})
        )
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
            request = replace(
                request,
                ranked_children=[fresh.get(retrieval_record_id(record), record) for record in ranked_children],
                expansion_children=[fresh.get(retrieval_record_id(record), record) for record in expansions],
                supplement_children=[_with_fresh_source(record, fresh) for record in supplements],
            )
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
        output["補い方ごとの起点数"] = {mode: list(modes.values()).count(mode) for mode in dict.fromkeys(modes.values())}
        if not selected:
            step.skip("起点根拠がありません。")
            output["実行状態"] = "起点なし"
        elif receipt.reads and all(read["state"] == "reused" for read in receipt.reads):
            step.skip("読み取り済みの内容を再利用しました。")
            output["実行状態"] = "既読を再利用"
        else:
            output["実行状態"] = "実行済み"
            counts = output["補い方ごとの起点数"]
            step.result(
                f"章節 {counts.get('section', 0)} 件・親 {counts.get('parent', 0)} 件・前後 {counts.get('neighbors', 0)} 件の起点を補い、"
                f"前後の文脈 {len(adopted)} Chunk を回答文脈に採用しました（章節の本文は章節の拡張で読みます）。"
            )
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


def _with_fresh_source(record: AnswerRecord, fresh: Mapping[str, AnswerRecord]) -> AnswerRecord:
    """読み直した原文に、補いの検索の印と並べ替えの点数を残す（#5173）。"""
    loaded = fresh.get(retrieval_record_id(record))
    if loaded is None:
        return record
    metadata = {**(loaded.metadata or {}), **{key: value for key, value in (record.metadata or {}).items() if key not in loaded.metadata}}
    metadata.update({key: record.metadata[key] for key in (*SUPPLEMENT_KEYS, "rerank", "rrf_score") if key in (record.metadata or {})})
    return replace(loaded, metadata=metadata)


def _sectioned_anchor_ids(anchors: Sequence[AnswerRecord], settings: Settings) -> frozenset[str]:
    """章節の対応がある起点（章節で文脈を補う起点。#5168）。章節の拡張が無効・未設定なら空。

    章節の拡張と同じ ``section_outline`` を読む（backend は起点ごとの結果を回答の中で使い回す）。
    """
    from rag_engine.dependencies import section_dependencies
    from rag_engine.generation.section_expansion import SectionExpansionLimits, SectionOutline

    ports = section_dependencies()
    if (
        ports is None
        or not getattr(settings, "section_expansion_enabled", True)
        or SectionExpansionLimits.from_settings(settings).max_levels <= 0
    ):
        return frozenset()
    found: set[str] = set()
    for record in anchors:
        uid = retrieval_record_id(record)
        try:
            outline = ports[0](chunk_uid=uid)
        except Exception:  # noqa: BLE001 - 対応を読めない起点は章節で補わない（前後か親で補う）
            continue
        if isinstance(outline, SectionOutline) and outline.chain():
            found.add(uid)
    return frozenset(found)


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
    query_embedder: Callable[[str, Settings], list[float]] | None = None,
    page_ranges: Sequence[Mapping[str, Any]] = (),
    text_queries: Mapping[str, str] | None = None,
    section_fill: bool = False,
    fill: bool = True,
) -> AnswerContext:
    """ADB hybrid search で回答用 context を構築する。

    text_queries は検索文ごとの全文検索の文（主検索で、原質問に用語・同義語などの語を足した文。#5159）。
    ベクトル検索はその検索文で、全文検索はこの文で同じ 1 回の検索として行う。無い検索文はその文で両方を検索する。

    page_ranges は検索を絞る文書の頁の範囲（{document_id, page_start, page_end} の列。業務ガイドの手順の参照。
    #2395）。空なら絞らない。絞り込みは注入された検索実装が行う。

    query_embedder は索引と同じ embedder を使う SDK 用。未指定なら検索実装の既定（OCI）を使う。

    section_fill は、この後に章節の拡張を行う経路か（#5168。``_bundle_context``）。

    fill=False は文脈を補わずに、検索 → rerank → 下限 → 文書ごとの上限 → ``top_k`` で切った起点だけを返す（#5173。
    回答の流れの主の検索と補いの検索）。records と根拠の木は起点（rerank の後の順）、expansion_records は検索の候補の
    原文、expansion_candidates は検索拡張の根拠。文脈は ``fill_answer_context`` で、補いの検索の根拠と一緒に 1 回補う。

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

    queries = _retrieval_queries(question, retrieval_queries)
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
        **({"page_ranges": [dict(item) for item in page_ranges]} if page_ranges else {}),
        **({"text_queries": dict(text_queries)} if text_queries else {}),
    }
    # 業務語は #848 で SQL の除外条件から融合後の加点に変わったため、0 件時の再検索（#818）は不要になった。
    search_result = search_adb_hybrid_chunks(**search_options)
    active_records = [_stored_chunk_answer_record(chunk) for chunk in search_result.all_chunks]
    ranked_children = [_stored_chunk_answer_record(chunk) for chunk in search_result.child_chunks]
    # 検索拡張の根拠（#5162）。rerank の候補に入れず、context を作るときに最後の根拠の並びへ合わせる。
    # 拡張を持たない検索の実装（SDK・評価の注入）は expansion_chunks を返さない。
    expansion_candidates = tuple(_stored_chunk_answer_record(chunk) for chunk in getattr(search_result, "expansion_chunks", None) or ())
    if not ranked_children:
        return AnswerContext(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="retrieval returned no active child chunks",
        )

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
    # rerank の後は「並び（rerank の点数と MMR）→ 関連度の下限 → 文書ごとの上限 → top_k で切る → 検索拡張の合わせ込み」
    # の順で、以後は足すだけで削除・並べ替えをしない（#5165）。top_k の切りは context の組み立てで 1 回だけ行う。
    kept_children = ranked_children
    if int(getattr(settings, "max_anchors_per_document", 0) or 0) > 0:
        with _execution_step("文書ごとの上限", "同じ文書の候補が起点を占めないよう、1 つの文書から採る候補を上限までにします。") as step:
            step.input({"候補": candidate_snapshot(ranked_children), "起点数": top_k, "文書ごとの上限": settings.max_anchors_per_document})
            kept_children = cap_anchors_per_document(ranked_children, settings, top_k=top_k, step=step)
            step.output({"処理後の候補": candidate_snapshot(kept_children)})
    anchors = AnswerContext(
        records=[],
        text="",
        expansion_records=tuple(active_records),
        ranked_candidates=tuple(ranked_children),
        candidate_count=candidate_count,
        expansion_candidates=expansion_candidates,
    )
    if fill:
        return fill_answer_context(
            question,
            anchors,
            settings,
            top_k=top_k,
            neighbor_child_count=neighbor_child_count,
            max_records=max_records,
            max_chars=max_chars,
            section_fill=section_fill,
        )
    selected = _cut_anchors(question, kept_children, active_records, settings, top_k=top_k, max_records=max_records)
    tree = tuple(
        ContextParentEvidence(
            record=record,
            role="child_fallback",
            reason="retrieved_chunk",
            children=(ContextChildEvidence(record=record, role="retrieved_anchor", reason="top_retrieved_child", retrieval_rank=rank),),
        )
        for rank, record in enumerate(selected, start=1)
    )
    return replace(anchors, records=list(selected), text="\n".join(record.text for record in selected), evidence_tree=tree)


def _cut_anchors(
    question: str,
    kept_children: Sequence[AnswerRecord],
    active_records: Sequence[AnswerRecord],
    settings: Settings,
    *,
    top_k: int,
    max_records: int,
) -> list[AnswerRecord]:
    """``top_k`` で切った起点（文脈の補完と同じ起点の選び方。rerank の後の順。#5173）。"""
    provisional = build_chunk_context_bundle(
        ContextBuildRequest(
            question=question,
            ranked_children=list(kept_children),
            active_records=list(active_records),
            top_k=max(1, int(top_k or DEFAULT_RETRIEVAL_TOP_K)),
            neighbor_child_count=0,
            max_anchors_per_parent=_max_anchors_per_parent(settings),
            max_records=max_records,
            max_chars=2**31 - 1,
            select_anchors_only=True,
        )
    )
    anchor_ids = {
        retrieval_record_id(child.record)
        for parent in provisional.evidence_tree
        for child in parent.children
        if child.role == "retrieved_anchor"
    }
    return list({retrieval_record_id(record): record for record in kept_children if retrieval_record_id(record) in anchor_ids}.values())


def fill_answer_context(
    question: str,
    anchors: AnswerContext,
    settings: Settings,
    *,
    top_k: int,
    neighbor_child_count: int,
    max_records: int | None = None,
    max_chars: int = MAX_CONTEXT_CHARS,
    supplements: Sequence[AnswerRecord] = (),
    supplement_pool: Sequence[AnswerRecord] = (),
    section_fill: bool = False,
) -> AnswerContext:
    """主の検索の起点に検索拡張と補いの検索の根拠を合わせ、文脈を補う（#5162・#5168・#5173）。

    anchors は ``build_adb_hybrid_answer_context`` の結果（文脈を補った後でもよい）。rerank の後の候補
    （``ranked_candidates``）から同じ規則（文書ごとの上限・``top_k``）で起点を選び直すので、主の検索の起点は同じものに
    なり外れない。supplements は補いの検索の根拠（印つき）、supplement_pool はその検索の候補の原文。
    """
    max_records = _max_context_records(settings) if max_records is None else max_records
    # 文書ごとの上限（#5165）は主の検索の工程で記録済み。同じ規則で同じ候補を残す。
    kept_children = cap_anchors_per_document(list(anchors.ranked_candidates), settings, top_k=top_k)
    pool = {retrieval_record_id(record): record for record in (*anchors.expansion_records, *supplement_pool)}
    active_records = list(pool.values())
    if not kept_children and not supplements:
        return replace(
            anchors,
            records=[],
            text="",
            evidence=(),
            evidence_tree=(),
            status="insufficient",
            insufficient_reason=anchors.insufficient_reason or "retrieval returned no active child chunks",
        )
    bundle = _bundle_context(
        question,
        kept_children,
        active_records,
        settings,
        top_k=top_k,
        neighbor_child_count=neighbor_child_count,
        max_records=max_records,
        max_chars=max_chars,
        expansions=anchors.expansion_candidates,
        supplements=supplements,
        section_fill=section_fill,
    )
    return AnswerContext(
        records=list(bundle.records),
        text=bundle.text,
        evidence=bundle.evidence,
        evidence_tree=bundle.evidence_tree,
        expansion_records=tuple(active_records),
        status=bundle.status,
        insufficient_reason=bundle.insufficient_reason,
        ranked_candidates=anchors.ranked_candidates,
        candidate_count=anchors.candidate_count,
        expansion_candidates=anchors.expansion_candidates,
    )


def _record_search_result(step: _ExecutionStep, context: AnswerContext, *, title: str = "回答に渡す根拠", note: str = "") -> None:
    """文書の検索の結果（候補の件数と経路ごとの件数、根拠の件数と上位の根拠）を工程の詳細に書く（#2341）。"""
    step.output(partial(context_snapshot, context))
    record_search_candidates(step, context.ranked_candidates)
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
) -> tuple[AnswerContext | None, dict[str, Any]]:
    """既取得poolから同機能の根拠を最大 max_records 件足す。既存の根拠を追い出す拡張は採用しない。

    modes は `recover_context_records` の方式を順に試し、最初に見つかった方式の結果を採用する。
    """
    visible = list(context.records)
    anchors = [r.chunk_uid for r in visible if r.chunk_uid][:3]
    recovered = ContextRecovery()
    mode = ""
    for mode in modes:
        recovered = recover_context_records(visible, context.expansion_records, anchors, mode, max_records=max_records, max_chars=max_chars)
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
    )
    return candidate, {"remedy": "context", "mode": mode, "added_chunk_ids": [r.chunk_uid for r in recovered.records]}


def _adds_new_records(current: AnswerContext, restored: AnswerContext) -> bool:
    """戻した context に、今の context に無い根拠（親）があるか。"""
    known = {record.chunk_uid or record.id for record in current.records}
    return any((record.chunk_uid or record.id) not in known for record in restored.records)


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


def _expansion_sources(context: AnswerContext) -> frozenset[str]:
    """検索拡張の印を持つ根拠の出典の ID（#5162。入力の上限で外さない）。親は子のどれかに印があれば含める。"""
    from rag_engine.retrieval.expansion_merge import is_expansion_record

    return frozenset(
        parent.record.chunk_uid or parent.record.id
        for parent in context.evidence_tree
        if any(is_expansion_record(record) for record in (parent.record, *(child.record for child in parent.children)))
    )


def _invalid_structured_output(exc: Exception) -> bool:
    """LLM の応答が構造化出力の契約を満たさなかった例外か（再試行の後も。#2772）。

    完了の event の応答に本文が無いまま送り直しも尽きたとき（#4197）も、使える出力が無い点は同じなので含める。
    """
    from rag_engine.adapters.oci import LlmStreamOutputMissing, is_invalid_structured_output

    return isinstance(exc, LlmStreamOutputMissing) or is_invalid_structured_output(exc)


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
        runtime_knowledge = runtime_knowledge.with_term_usage(
            search=settings.runtime_terms_search_enabled, answer=settings.runtime_terms_answer_enabled
        )
    from rag_engine.generation import grounded
    from rag_engine.generation.batch_output_repair import batch_failure_summary
    from rag_engine.generation.document_background import DocumentBackgroundReader, without_embedded_background
    from rag_engine.generation.evidence_selection import select_evidence
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
        selecting_evidence = False
        failed_batches: list[dict[str, Any]] = []
        try:
            generation_system = grounded.generation_system_prompt(
                assume_unconfirmed=runtime_knowledge is not None and runtime_knowledge.assume_unconfirmed
            )
            selecting_evidence = True

            def selected_generation_schema(values: Sequence[dict[str, Any]], *, guided: bool = guided) -> Mapping[str, Any]:
                return grounded.generation_output_model(
                    question, guided=guided, unit_catalog=grounded.generation_unit_catalog(values)
                ).model_json_schema()

            # 上限内なら 1 回の生成、上限を超えれば原文を外さずに分割読取（組ごとの読取＋最終生成の 1 回。#5098）。
            spans, prompt, input_diagnostic = select_evidence(
                spans,
                generation_settings,
                question=question,
                render=render_generation,
                evidence_text=grounded.evidence_block_text,
                tag=grounded.tag_spans,
                system=generation_system,
                schema_for=selected_generation_schema,
                read=lambda system, user, schema: grounded.call_with_model_purpose(
                    "evidence.read_batch", parse_text_response, system, user, generation_settings, schema, provider_id=provider
                ),
                unusable_output=_invalid_structured_output,
                provider_id=provider,
                source_scores=_source_question_scores(context),
                protected_sources=_expansion_sources(context),
                failed_batches=failed_batches,
            )
            selecting_evidence = False
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
            generation_failure_stage = (
                ("evidence.batch_read" if failed_batches else "evidence.select") if selecting_evidence else "answer.generate"
            )
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
                if selecting_evidence and failed_batches
                else grounded.generation_failure_detail(exc)
            )
            logger.warning(
                "answer generation fell back to document quotes: stage=%s error=%s detail=%s",
                generation_failure_stage,
                generation_failure,
                json.dumps(generation_failure_detail, ensure_ascii=False),
            )
            current = grounded.timed_out_round(question)
            step.fail("回答文を生成できなかったため、質問に関係する資料の記載だけを示します", reason_code="answer_generation_failed")
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
    if current.draft._full_answer is not None:
        from rag_engine.generation.full_answer_wire import render_full_answer

        rendered = render_full_answer(current.draft, current.checked)
        notices = grounded.render_answer("", (), (), unverified=unread, guide_notes=guide_notes, guide_notes_first=guide_notes_first)
        if notices.text:
            rendered.text += "\n\n" + notices.text
    else:
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
    confidence = current.draft.confidence if current.draft._full_answer is not None or published else "low"
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
        "response_kind": {
            "declared": current.draft._declared_response_kind,
            "recorded": current.draft._response_kind,
            "source": "typed_answer_parts" if current.draft._declared_response_kind != current.draft._response_kind else "declared",
        },
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
        response_kind=cast(Any, current.draft._response_kind),
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

    candidate_count は基準（rerank の関連度の下限）で絞り込む前の候補、found は基準を通った候補、
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
    """同じ回答モデルで一度生成する。技術再試行は adapter の共通枠だけで行う。"""
    return synthesize_grounded_answer(
        question,
        context,
        settings,
        answer_llm_provider=answer_llm_provider,
        image_evidence=image_evidence,
        image_prompt_mode=image_prompt_mode,
        **options,
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


def _merged_supplement_keys(context: AnswerContext, mark: str) -> list[str]:
    """補いの検索（``mark`` の印）で新しく足した根拠の key（根拠の木の親。#5173）。並びにあって印だけのものは数えない。"""
    from rag_engine.retrieval.expansion_merge import EXPANSION_EVIDENCE_REASON

    return list(
        dict.fromkeys(
            parent.record.chunk_uid or parent.record.id
            for parent in context.evidence_tree
            for child in parent.children
            if child.role == "retrieved_anchor"
            and child.reason == EXPANSION_EVIDENCE_REASON
            and isinstance((child.record.metadata or {}).get(mark), dict)
        )
    )


def _with_supplement_mark(record: AnswerRecord, mark: str, payload: Mapping[str, Any]) -> AnswerRecord:
    """補いの検索の根拠に印を付ける（同じ根拠の印は値の列をつなぐ。#5173）。"""
    metadata = dict(record.metadata or {})
    current = metadata.get(mark)
    merged = dict(current) if isinstance(current, dict) else {}
    for key, value in payload.items():
        values = [*merged.get(key, ()), *(value if isinstance(value, (list, tuple)) else (value,))]
        merged[key] = list(dict.fromkeys(values))
    metadata[mark] = merged
    return replace(record, metadata=metadata)


def _context_owner_keys(context: AnswerContext) -> dict[str, str]:
    """根拠の木の chunk の key → それを持つ根拠（親）の key。"""
    owners: dict[str, str] = {}
    for parent in context.evidence_tree:
        parent_key = parent.record.chunk_uid or parent.record.id
        owners.setdefault(parent_key, parent_key)
        for child in parent.children:
            owners.setdefault(child.record.chunk_uid or child.record.id, parent_key)
    return owners


def _search_guide_steps(
    question: str,
    context: AnswerContext,
    settings: Settings,
    plan: GuidePlan,
    *,
    extra_budget: ExtraRetrievalBudget,
    run_id: Any,
    preferred_engines: Sequence[str],
    neighbor_child_count: int,
    rerank_enabled: bool,
    retrieval_scope: str,
    classification_filter: ClassificationFilter | None,
    clock: Callable[[], float] = monotonic,
) -> tuple[list[StepSearch], list[dict[str, Any]], dict[str, Any]]:
    """業務ガイドの手順ごとに検索する (#2395)。戻り値は (検索の結果, 必須の取得の記録, 記録)。

    検索ごとに「検索 → rerank → 下限 → 文書ごとの上限 → ``top_k``（``guide_step_chunks_per_step``）で切る」だけを行い
    （#5173）、切った根拠を返す。主の検索の根拠への合わせ込みと文脈の補完は呼び出し元が主の検索と一緒に行う。
    """
    targets = required_guide_targets(plan)
    trace: dict[str, Any] = {
        "schema_version": GUIDE_STEP_TRACE_SCHEMA_VERSION,
        "guide_id": plan.guide_id,
        "revision": plan.revision,
        "llm_calls": 0,
    }
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
        return [], [], trace

    per_step = max(1, int(getattr(settings, "guide_step_chunks_per_step", 1) or 1))

    def retrieve(text: str, queries: tuple[str, ...], page_ranges: list[dict[str, Any]]) -> AnswerContext:
        return build_adb_hybrid_answer_context(
            text,
            run_id,
            preferred_engines,
            settings,
            top_k=per_step,
            neighbor_child_count=neighbor_child_count,
            retrieval_queries=queries,
            rerank_phase="guide_step",
            rerank_enabled=rerank_enabled,
            retrieval_scope=retrieval_scope,
            classification_filter=classification_filter,
            page_ranges=page_ranges,
            fill=False,
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
    trace.update(
        {
            "stop_reason": stop or optional_stop,
            "unsearched_step_ids": list(unsearched_steps(plan, results)),
            "elapsed_ms": int((clock() - started) * 1000),
            "top_k": per_step,
            "budget": extra_budget.to_payload(),
        }
    )
    return results, acquisitions, trace


def _guide_step_supplements(results: Sequence[StepSearch]) -> tuple[list[AnswerRecord], list[AnswerRecord]]:
    """手順の検索で切った根拠（印つき。検索の順）と、その検索の候補の原文（#5173）。"""
    supplements: dict[str, AnswerRecord] = {}
    pool: list[AnswerRecord] = []
    for result in results:
        pool.extend(result.expansion_records)
        for parent in result.parents:
            record = parent.record
            key = record.chunk_uid or record.id
            if not key:
                continue
            mark = {"step_ids": list(result.owner_ids or (result.step_id,)), "kinds": [result.kind]}
            supplements[key] = _with_supplement_mark(supplements.get(key, record), GUIDE_STEP_KEY, mark)
    return list(supplements.values()), pool


def _finish_guide_steps(
    plan: GuidePlan,
    results: Sequence[StepSearch],
    acquisitions: list[dict[str, Any]],
    trace: dict[str, Any],
    context: AnswerContext,
) -> tuple[dict[str, Any], tuple[tuple[str, str, tuple[str, ...]], ...]]:
    """文脈を補った後の根拠で、手順ごとの根拠と必須の取得の記録を決める（#2706・#5173）。"""
    if trace.get("skipped"):
        return trace, tuple((step.step_id, step.title, ()) for step in plan.steps)
    targets = {target.target_id: target for target in required_guide_targets(plan)}
    titles = {step.step_id: step.title for step in plan.steps}
    owners = _context_owner_keys(context)
    in_context = {record.chunk_uid or record.id for record in context.records}
    added_keys = set(_merged_supplement_keys(context, GUIDE_STEP_KEY))

    def located(parent: Any) -> str:
        key = parent.record.chunk_uid or parent.record.id
        return owners.get(key, key)

    # 手順の根拠は、手順ごとの検索で切った根拠に、手順の参照の頁に当たる回答の材料の根拠を足したもの（#2706）。
    referenced = reference_step_sources(plan, context.records)
    searched: dict[str, list[str]] = {}
    for result in results:
        for owner in result.owner_ids or (result.step_id,):
            searched.setdefault(owner, []).extend(located(parent) for parent in result.parents)
    step_sources = tuple(
        (
            step.step_id,
            step.title,
            tuple(key for key in dict.fromkeys((*searched.get(step.step_id, ()), *referenced.get(step.step_id, ()))) if key in in_context),
        )
        for step in plan.steps
    )
    for acquisition in acquisitions:
        target = targets[acquisition["target_id"]]
        candidates = {owners.get(key, key) for key in acquisition["candidate_source_ids"]}
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
    sources = dict((step_id, keys) for step_id, _, keys in step_sources)
    trace.update(
        {
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
                            "added_chunk_ids": sorted({located(parent) for parent in result.parents} & added_keys),
                            "elapsed_ms": result.elapsed_ms,
                            **({"error": result.error} if result.error else {}),
                        }
                        for result in results
                        if step.step_id in (result.owner_ids or (result.step_id,))
                    ],
                    "evidence_count": len(sources.get(step.step_id, ())),
                    "reference_chunk_count": len(
                        {
                            located(parent)
                            for result in results
                            if result.kind == "reference" and step.step_id in (result.owner_ids or (result.step_id,))
                            for parent in result.parents
                        }
                        & added_keys
                    ),
                    # 手順の参照の頁に当たる回答の材料の根拠の数（検索の結果に関係なく手順に紐づける。#2706）。
                    "referenced_count": len(referenced.get(step.step_id, ())),
                }
                for step in plan.steps
            ],
            "added_chunk_ids": sorted(added_keys),
        }
    )
    return trace, step_sources


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
            "全文検索の文": list(queries) if enabled and not answer and runtime is not None and not runtime.error else [],
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
            else "登録した用語・同義語に一致しなかったため、全文検索の文に足す語はありません。"
        )
        return
    step.field("用途", "回答の補助情報（資料の根拠とは別）" if answer else "全文検索の文への語の追加（ベクトル検索は原質問のまま）")
    for term in runtime.matched_terms:
        step.field("用語", term.term)
        step.field("別名", " / ".join(term.aliases))
        if answer:
            step.field("説明", term.description)
    if answer:
        step.result("一致した用語・別名・説明を回答生成の補助情報として提供します。")
    elif queries:
        step.result("全文検索の文に一致した用語と別名を足します。")
        step.add(*queries)
    else:
        step.result("一致した用語はありますが、全文検索の文に足す語はありません。")


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
                step.output({"状態": "読込失敗", "読込エラー": terms.error, "一致した用語": [], "全文検索の文": []})
                return runtime
            result = replace(runtime, matched_terms=terms.matched_terms).with_term_usage(
                search=settings.runtime_terms_search_enabled,
                answer=settings.runtime_terms_answer_enabled,
            )
            additions: tuple[str, ...] = ()
            if not settings.original_query_only and dedupe_query_key(result.expanded_question) != dedupe_query_key(
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
    return dedupe_queries(tuple(label for term in runtime_knowledge.retrieval_terms for label in term.labels()))


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
    queries = dedupe_queries(vector_queries or text_search_info.query_variants or expansion.retrieval_queries)
    plan = RetrievalQueryPlan(
        original_question=expansion.original_question,
        runtime_expanded_question=runtime_question,
        vector_queries=tuple(queries),
        text_query_variants=tuple(text_search_info.query_variants),
        oracle_text_queries=tuple(text_search_info.oracle_text_queries),
        text_search_tokens=tuple(text_search_info.tokens),
        text_search_tokenization_traces=tuple(text_search_info.tokenization_traces),
        llm_expansion_queries=tuple(expansion.generated_queries),
        lexical_queries=dedupe_queries(lexical_queries),
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


def build_question_text_search_info(
    question: str,
    settings: Settings,
    *,
    query_source: str = "原質問のみ",
    query_variants: Sequence[str] | None = None,
) -> QuestionTextSearchInfo:
    """Oracle Text 検索用の token と query trace を作成します。"""
    if settings.hybrid_search_backend == "oracle_hybrid_index":
        variants = _limited_text_search_query_variants(question, query_variants, settings)
        return QuestionTextSearchInfo(
            tokenizer="oracle_hybrid_index",
            tokenizer_label="Oracle Hybrid Vector Index（共通の検索語処理）",
            query_source=query_source,
            query_variants=tuple(variants),
            tokenization_traces=tuple(
                {"query": q, "tokenizer": "oracle_hybrid_index", "tokens": [], "weights": [], "native_search_text": q} for q in variants
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
        query_keywords = {dedupe_query_key(query): terms for query, terms in settings.retrieval_query_keywords.items()}
        query_terms: list[tuple[str, ...]] = []
        tokenization_traces: list[dict[str, Any]] = []
        oracle_queries: list[str] = []
        seen_queries: set[str] = set()
        for variant in variants:
            tokenization = tokenize_text_search_query_with_trace(
                variant,
                domain_keywords=[*domain_keywords, *query_keywords.get(dedupe_query_key(variant), ())],
                config=config,
                max_tokens=settings.text_search_max_tokens,
                max_query_chars=settings.text_search_max_query_chars,
            )
            terms = tuple(tokenization.tokens)
            oracle_query = build_oracle_text_query(terms, weights=tokenization.weights, max_chars=settings.text_search_max_query_chars)
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
    if info.tokenizer == "oracle_hybrid_index":
        return ["全文検索の入力: 共通の検索語処理で全文式を作り、Oracle Hybrid Vector Indexへ渡します"]
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
        if mode == "oracle_hybrid_index":
            step.field(f"検索文 {index} の全文検索入力", trace.get("native_search_text", ""))
            step.field(f"検索文 {index} の分割方法", "共通の検索語処理＋Oracle Hybrid Vector Index")
            step.add(
                "全文式は共通の検索語処理で作成します（Sudachi、利用できない場合は文字種分割）。Oracle内部の展開結果はAPIから返されません。"
            )
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


def _record_runtime_knowledge_load(step: _ExecutionStep, runtime_knowledge: RuntimeKnowledgeContext) -> bool:
    """用語・同義語の読み込みの失敗を工程に記録する。読めていれば True（工程の結果と詳細を続けて書く）。

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
    scope: tuple[str, Sequence[str]] | None = None,
    guide_plan: GuidePlan | None = None,
    request_question: str | None = None,
) -> AnswerQuestionResult:
    """検索・LLM の結果と実行記録を返す。

    ``scope`` は利用者が確認の質問で選んだ条件と対象範囲の説明・検索に足す語（チャットの確認。#717）。
    回答のプロンプトの前置きに入れ、語は補助の検索文に足す。範囲での絞り込みは呼び出し側の検索が行う。

    ``guide_plan`` は照合した業務ガイドの手順（分岐で残った手順。#2395）。回答を作るとき、主の検索の後に
    手順ごとに検索し、手順の根拠と参照の頁の根拠を回答の材料に足す。

    ``generate_answer=False`` は回答を作らない（RAG 検索の画面。#3506）。根拠を集め終えるまで（回答フロー・章節の拡張・
    業務ガイドの手順ごとの検索・要求ごとの根拠の確認）は回答と同じ 1 つの流れで、工程・入れ子・LLM の呼び出しも同じ。
    回答に添付する画像と回答の生成の手前で止め、集めた根拠を返す（本文は空）。検索だけ（``retrieval_only``）と MCP の
    ``rag_retrieve_evidence`` も同じ流れを使う（#5173）。
    空質問は ValueError、処理失敗は中断記録付きの AnswerExecutionError を送出する。
    ADB の準備不足や空検索は
    未実行工程を明示した結果を返し、回答 LLM を呼ばない。記録は呼び出しごとに分離する。

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
    # 章節の拡張（#1436）の時間の上限は、回答の開始から数える。
    answer_started_at = monotonic()

    effective_rerank_enabled = bool(settings.default_rerank_enabled) if rerank_enabled is None else bool(rerank_enabled)
    selected_retrieval_scope = normalize_retrieval_scope(retrieval_scope)
    selected_classification_filter = classification_filter or classification_filter_from_values()
    question_plan = plan_question(normalized_question)
    # 検索・回答プロファイルの知識の「用語・同義語」は 1 回だけ読む。用語の照合は検索・回答の工程で行う（#3451）。
    runtime_knowledge = build_runtime_knowledge_context(
        normalized_question,
        settings.output_dir,
        settings.runtime_knowledge_path,
        match_terms=False,
        terms_search_enabled=settings.runtime_terms_search_enabled,
        terms_answer_enabled=settings.runtime_terms_answer_enabled,
    )
    if runtime_knowledge.error and runtime_knowledge.failure is not None:
        report_degraded("runtime_knowledge", runtime_knowledge.failure)

    if scope is not None:
        runtime_knowledge = runtime_knowledge.with_scope(*scope)

    base_expansion = QueryExpansionResult(original_question=normalized_question)
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
        "意味の近さ（ベクトル）は原質問で、語の一致（全文）は原質問に用語・同義語などの語を足した文で 1 回検索します。"
        "全文検索に使う検索語を作ります。",
    ) as step:
        keyword_trace: dict[str, Any] = {}
        # 主検索は 1 回（#5159）。ベクトル検索は原質問だけ（語を足すと意味がずれる）、全文検索は原質問に足した語つきの文。
        (vector_query,) = _merge_retrieval_queries(normalized_question)
        retrieval_queries: tuple[str, ...] = (vector_query,)
        original_query_only = bool(getattr(settings, "original_query_only", False))
        expanded = runtime_knowledge.expanded_question != runtime_knowledge.original_question
        text_query = vector_query
        if expanded and not original_query_only:
            # 原質問と同じ形（NFKC・制御文字の除去・空白の正規化）にする。
            text_query = (_merge_retrieval_queries(runtime_knowledge.expanded_question) or (vector_query,))[0]
        elif original_query_only:
            # 比較実験用。検索直前にも同じ絞り込みがあるが、ここで適用しないと記録が実際の検索と食い違う。
            step.add("      設定 RETRIEVAL_ORIGINAL_QUERY_ONLY=true のため、全文検索にも原質問だけを使います。")
        # 出どころは実際に語を足したもの（用語・同義語 / 業務ガイド / 確認で選んだ条件。#3490）。
        text_source = runtime_knowledge.query_source if text_query != vector_query else "原質問"
        query_sources: dict[str, tuple[str, ...]] = {vector_query: ("原質問",)}
        text_queries: dict[str, str] = {}
        if text_query != vector_query:
            query_sources[text_query] = (text_source,)
            text_queries[vector_query] = text_query
        step.input(
            {
                "原質問": normalized_question,
                "一致した用語": [term.to_payload() for term in runtime_knowledge.retrieval_terms],
                "原質問だけで検索": original_query_only,
            }
        )
        # 用語・同義語を足した全文検索の文は、足した語を全文検索の語の上限から落とさない（長い質問の後ろの別名が埋もれる #2163）。
        term_labels = _term_labels(runtime_knowledge)
        query_keywords = {text_query: term_labels} if term_labels and text_query != vector_query else {}
        settings = replace(settings, retrieval_query_sources=query_sources, retrieval_query_keywords=query_keywords)
        text_search_info = build_question_text_search_info(
            text_query,
            settings,
            query_source=_text_search_query_source(runtime_knowledge, expansion) if text_query != vector_query else "原質問",
            query_variants=(text_query,),
        )
        text_search_info = replace(text_search_info, domain_keyword_expansion=keyword_trace)
        if (tokenizer_failure := sudachi_unavailable(_question_text_search_tokenizer_config(settings))) is not None:
            # 全文検索の語を文字種の区切りだけで作ったことを、回答ごとに 1 回だけ診断に残す（#3286）。
            report_degraded("text_search_tokenizer", tokenizer_failure)
        step.result(
            "検索 1 回",
            "ベクトル検索の文: [原質問] " + _display_query(vector_query),
            f"全文検索の文: [{text_source}] " + _display_query(text_query),
        )
        _add_text_search_details(step, text_search_info)
        if text_search_info.error:
            step.status = "検索語の抽出に失敗"
        elif any(trace.get("fallback_reason") for trace in text_search_info.tokenization_traces):
            step.status = "文字種による分割で続行"
        step.output(
            {
                "ベクトル検索の文": {"本文": vector_query, "出どころ": ["原質問"]},
                "全文検索の文": {"本文": text_query, "出どころ": [text_source]},
                "原質問だけで検索": original_query_only,
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
                "ベクトル検索の文": vector_query,
                "全文検索の文": text_query,
                "検索範囲": selected_retrieval_scope,
                "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                "回答生成の要求": generate_answer,
                "起点数": chunk_top_k,
                "前後それぞれのChunk数": chunk_neighbor_count,
                "再ランキング": effective_rerank_enabled,
            }
        )
        extra_budget = ExtraRetrievalBudget.from_settings(settings)
        if guide_plan is not None and settings.guide_step_retrieval_enabled:
            extra_budget.reserve_required(len(required_guide_targets(guide_plan)))
        coverage_usage = CoverageUsage()
        # 根拠の流れは回答・回答を作らない検索・検索だけ・MCP で同じ（#5173）: 主の検索 → rerank → 切る → 検索拡張の
        # 合わせ込み → 補いの検索（要求別の補完・業務ガイドの手順）の合わせ込み → 文脈の補完（章節の拡張を含む）。
        try:
            with _execution_step("文書検索", _SEARCH_PURPOSE) as retrieval_step:
                retrieval_step.input(
                    {
                        "質問": normalized_question,
                        "ベクトル検索の文": vector_query,
                        "全文検索の文": text_query,
                        "検索範囲": selected_retrieval_scope,
                        "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                        "起点数": chunk_top_k,
                        "前後それぞれのChunk数": chunk_neighbor_count,
                        "再ランキング": effective_rerank_enabled,
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
                    text_queries=text_queries,
                    rerank_phase="main_search",
                    rerank_enabled=effective_rerank_enabled,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                    section_fill=True,
                )
                retrieval_step.field("全文検索の文", text_source)
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
        section_state: dict[str, Any] = {}
        # 業務ガイドの手順が参照する文書と頁（#2828）。章節の拡張はこの範囲の起点を先に広げ、回答の「回答に未反映の
        # 関連章節」はこの文書の章節を残す。
        guide_ranges = guide_plan.reference_ranges if guide_plan is not None else ()
        # 主の検索の根拠（文脈を補った後の chunk と親）。補いの検索の新しい根拠の数は、これ以外だけを数える。
        main_keys = set(_context_owner_keys(context)) | {record.chunk_uid or record.id for record in context.records}
        supplements: dict[str, AnswerRecord] = {}
        supplement_pool: list[AnswerRecord] = []

        def add_supplements(records: Iterable[AnswerRecord]) -> None:
            for record in records:
                key = record.chunk_uid or record.id
                if not key:
                    continue
                previous = supplements.get(key)
                # 同じ根拠を 2 つの補いの検索が切ったときは、両方の印を残す（位置は先の検索の順）。
                supplements[key] = (
                    record
                    if previous is None
                    else replace(previous, metadata={**(record.metadata or {}), **{k: v for k, v in (previous.metadata or {}).items()}})
                )

        # 超限した検索文は原要求ごとに補い、以後の補查と同じ枠・期限・既検索文を使う。
        overflow_config = normalize_text_search_tokenizer_config(_question_text_search_tokenizer_config(settings))
        overflow_keywords = (
            settings.domain_keywords_override
            if settings.domain_keywords_override is not None
            else load_domain_keywords(settings.output_dir)
        )

        def overflow_retrieve(query: str, top_k: int) -> AnswerContext:
            return build_adb_hybrid_answer_context(
                query,
                run_id,
                preferred_engine_ids,
                settings,
                top_k=top_k,
                neighbor_child_count=chunk_neighbor_count,
                retrieval_queries=(query,),
                rerank_phase="missing_requirement",
                rerank_enabled=effective_rerank_enabled,
                retrieval_scope=selected_retrieval_scope,
                classification_filter=selected_classification_filter,
                fill=False,
            )

        with _execution_step(
            "検索上限の要求別補完", "全文検索の語数上限に達した質問を、元の要求と共通条件に分けて検索します。"
        ) as overflow_step:
            overflow_step.input(
                lambda: {
                    "原質問": request_text,
                    "既検索文": list(dict.fromkeys((*retrieval_queries, *text_queries.values()))),
                    "既存根拠": candidate_snapshot(context.records),
                    "追加検索予算": extra_budget.to_payload(),
                    "要求別の予算": asdict(_request_coverage_budget(settings)),
                    "使用済みの要求別予算": asdict(coverage_usage),
                    "原質問のみ": bool(getattr(settings, "retrieval_original_query_only", False)),
                    "検索範囲": selected_retrieval_scope,
                    "分類条件": asdict(selected_classification_filter) if selected_classification_filter is not None else None,
                }
            )
            overflow_results, overflow_trace = search_overflow_requests(
                request_text,
                tokenize=lambda text: tokenize_text_search_query_with_trace(
                    text,
                    domain_keywords=overflow_keywords,
                    config=overflow_config,
                    max_tokens=settings.text_search_max_tokens,
                    max_query_chars=settings.text_search_max_query_chars,
                ),
                retrieve=overflow_retrieve,
                extra_budget=extra_budget,
                coverage_budget=_request_coverage_budget(settings),
                usage=coverage_usage,
                searched_queries=(*retrieval_queries, *text_queries.values()),
                existing_keys=main_keys,
                initial_query_overflowed=any(item.get("truncated") for item in text_search_info.tokenization_traces),
                allow_split=not getattr(settings, "retrieval_original_query_only", False),
            )
            for result in overflow_results:
                supplement_pool.extend(result.context.expansion_records)
                add_supplements(
                    _with_supplement_mark(record, QUERY_OVERFLOW_KEY, {"request_ids": [result.request_id], "queries": [result.query]})
                    for record in result.context.records
                )
            overflow_candidates = list(
                dict.fromkeys(record.chunk_uid or record.id for result in overflow_results for record in result.context.records)
            )
            overflow_trace["candidate_chunk_ids"] = overflow_candidates
            overflow_step.output(
                lambda: {
                    "検索結果": overflow_trace,
                    "要求ごとの返却根拠": [
                        {"要求ID": item.request_id, "検索文": item.query, "候補": candidate_snapshot(item.context.records)}
                        for item in overflow_results
                    ],
                    "合わせる根拠ID": overflow_candidates,
                }
            )
            if overflow_trace.get("triggered"):
                statuses = [str(row["status"]) for row in overflow_trace["requests"]]
                overflow_step.result(
                    f"要求 {len(statuses)} 件、合わせる根拠 {len(overflow_candidates)} 件"
                    f"（うち主の検索に無いもの {sum(key not in main_keys for key in overflow_candidates)} 件）"
                )
                overflow_step.field("追加検索の残り", str(extra_budget.remaining))
                overflow_step.field("未検索", str(sum(status.startswith("unsearched_") for status in statuses)))
                overflow_step.field("失敗", str(statuses.count("failed")))
            else:
                overflow_step.skip(
                    {
                        "original_query_only": "原質問のみを検索する設定のため、要求別には分割しません。",
                        "tokenization_failed": "検索語の抽出に失敗したため、補完検索を実行していません。",
                    }.get(str(overflow_trace.get("skipped") or ""), "語数上限の超過がないため追加検索は不要です。")
                )
        guide_step_trace: dict[str, Any] | None = None
        guide_results: list[StepSearch] = []
        guide_acquisitions: list[dict[str, Any]] = []
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
                        "前後それぞれのChunk数": chunk_neighbor_count,
                        "再ランキング": effective_rerank_enabled,
                        "設定": {
                            key: getattr(settings, key, None)
                            for key in (
                                "guide_step_retrieval_enabled",
                                "guide_step_max_queries",
                                "guide_step_chunks_per_step",
                                "guide_step_deadline_seconds",
                            )
                        },
                    }
                )
                guide_results, guide_acquisitions, guide_step_trace = _search_guide_steps(
                    normalized_question,
                    context,
                    settings,
                    guide_plan,
                    extra_budget=extra_budget,
                    run_id=run_id,
                    preferred_engines=preferred_engine_ids,
                    neighbor_child_count=chunk_neighbor_count,
                    rerank_enabled=effective_rerank_enabled,
                    retrieval_scope=selected_retrieval_scope,
                    classification_filter=selected_classification_filter,
                )
                guide_records, guide_pool = _guide_step_supplements(guide_results)
                supplement_pool.extend(guide_pool)
                add_supplements(guide_records)
                guide_step.output(
                    lambda: {
                        "検索と取得の記録": guide_step_trace,
                        "合わせる根拠ID": [record.chunk_uid or record.id for record in guide_records],
                    }
                )
                skipped = str(guide_step_trace.get("skipped") or "")
                if skipped:
                    guide_step.skip(_GUIDE_STEP_SKIP_LABELS.get(skipped, f"しない（{skipped}）"))
                else:
                    guide_step.result(
                        f"検索 {len(guide_results)} 回、合わせる根拠 {len(guide_records)} 件"
                        f"（うち主の検索に無いもの {sum((record.chunk_uid or record.id) not in main_keys for record in guide_records)} 件）"
                    )
        if supplements:
            # 主の検索の起点・検索拡張の根拠に補いの検索の根拠を同じ規則で合わせ、文脈を補い直す（#5162・#5168・
            # #5173）。主の検索の起点は同じ選び方で同じものになる。主の検索の根拠が 1 つでも外れる結果は使わない。
            merged = fill_answer_context(
                normalized_question,
                context,
                settings,
                top_k=chunk_top_k,
                neighbor_child_count=chunk_neighbor_count,
                supplements=tuple(supplements.values()),
                supplement_pool=supplement_pool,
                section_fill=True,
            )
            if set(_context_owner_keys(context)) <= set(_context_owner_keys(merged)):
                context = merged
            else:
                overflow_trace["rejected"] = "would_drop_existing_evidence"
        overflow_trace["added_chunk_ids"] = _merged_supplement_keys(context, QUERY_OVERFLOW_KEY)
        context, section_expansion = _expand_context_by_sections(
            normalized_question,
            context,
            settings,
            rerank_enabled=effective_rerank_enabled,
            started_at=answer_started_at,
            state=section_state,
            phase="main",
            priority=guide_ranges,
        )
        # 章節の拡張が所有の章節を読めなかった起点は、次の補い方（親 → 前後）で補う（#5173）。
        context = _fill_unread_section_anchors(context, settings, section_state, neighbor_child_count=chunk_neighbor_count)
        if guide_plan is not None and guide_step_trace is not None:
            guide_step_trace, step_sources = _finish_guide_steps(guide_plan, guide_results, guide_acquisitions, guide_step_trace, context)
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
    rerank_enabled: bool,
    started_at: float,
    state: dict[str, Any] | None = None,
    phase: str = "main",
    priority: Sequence[tuple[str, int | None, int | None]] = (),
) -> tuple[AnswerContext, Any]:
    """当たった chunk の章節から親の章節へ段階的に広げる (#1436。`section_expansion`)。

    最低範囲（列挙の問は文書全体）までの原文を決定的に足す。state は1回答内で共有し、追加検索後も同じ原文・根拠入力の
    予算・回答全体の期限を使う。無効と読み込み未設定は診断で区別する。
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
    # 入らない章節は rerank の順で上限まで入れ、残りは未反映として示す。読了の記録は 1 回の回答の段で共有する。
    if "reader" not in state:
        state["reader"] = sections.SectionReader(read_chunks=ports[1], known_ids=set())
        state["outlines"] = {}

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
            started_at=started_at,
            deadline_remaining=remaining_seconds,
            clock=monotonic,
            reader=state["reader"],
            priority=tuple(sections.PriorityRange(document_id, start, end) for document_id, start, end in priority),
        )
        if result.context is not context:
            # 章節の本文を足した後に 1 回だけ、重なる根拠をまとめ、章節の本文を起点のかたまりへ置く（文脈の補完。#5168）。
            result.context = consolidate_context(result.context)
        result.trace["phase"] = phase
        previous = state.get("result")
        if previous is not None:
            result.applied = result.applied or previous.applied
            result.trace["applied"] = result.applied
        result.trace["phases"] = [
            *(previous.trace.get("phases", []) if previous else []),
            {key: value for key, value in result.trace.items() if key != "phases"},
        ]
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


def _fill_unread_section_anchors(
    context: AnswerContext, settings: Settings, state: Mapping[str, Any], *, neighbor_child_count: int
) -> AnswerContext:
    """章節の拡張が所有の章節を読めなかった起点を、次の補い方（親 → 前後 N 件）で補う（#5173）。

    章節で補う起点（#5168）は前後の chunk を読まない。予算・時間の上限・読み取りの失敗で所有の章節（かその上の章節）を
    読めなかった起点は、親子分割なら親（起点の単位が親なので補い済み）、そうでなければ前後 N 件を読み、
    ``consolidate_context`` で起点のかたまりへ入れる。章節の拡張をしなかった回答（無効・未設定）は、文脈の補完で
    前後か親を補い済みなので何もしない。
    """
    from rag_engine.dependencies import section_dependencies
    from rag_engine.generation.section_expansion import SectionOutline

    reader = state.get("reader")
    ports = section_dependencies()
    if reader is None or ports is None:
        return context
    outlines = state.get("outlines") or {}
    targets: list[AnswerRecord] = []
    for parent in context.evidence_tree:
        for child in parent.children:
            if child.role != "retrieved_anchor" or uses_parent_context(child.record):
                continue
            uid = retrieval_record_id(child.record)
            outline = outlines.get(uid)
            if outline is None:
                try:
                    outline = ports[0](chunk_uid=uid)
                except Exception:  # noqa: BLE001 - 対応を読めない起点は文脈の補完で前後を補い済み
                    continue
            if not isinstance(outline, SectionOutline) or not outline.chain():
                continue
            if any(reader.section_key(outline, section) in reader.read_sections for section in outline.chain()):
                continue
            targets.append(child.record)
    targets = list({retrieval_record_id(record): record for record in targets}.values())
    if not targets:
        return context
    loaded = injected_context_chunks(anchors=targets, settings=settings)
    pool = {
        retrieval_record_id(record): record
        for record in (
            *context.expansion_records,
            *(map(_stored_chunk_answer_record, loaded) if loaded is not None else ()),
            *targets,
        )
    }
    filled = build_chunk_context_bundle(
        ContextBuildRequest(
            question="",
            ranked_children=targets,
            active_records=list(pool.values()),
            top_k=len(targets),
            neighbor_child_count=neighbor_child_count,
            max_anchors_per_parent=len(targets),
            max_records=len(targets) * (2 * max(1, neighbor_child_count) + 1),
            max_chars=2**31 - 1,
        )
    )
    adopted = sorted(
        {
            retrieval_record_id(child.record)
            for parent in filled.evidence_tree
            for child in parent.children
            if child.role == "neighbor_context"
        }
    )
    result = state.get("result")
    if result is not None:
        result.trace["unread_section_fallback"] = {
            "anchor_ids": [retrieval_record_id(record) for record in targets],
            "neighbor_chunk_ids": adopted,
        }
    if not adopted:
        return context
    tree = (*context.evidence_tree, *filled.evidence_tree)
    return consolidate_context(
        replace(
            context,
            records=[item.record for item in tree],
            evidence_tree=tree,
            expansion_records=tuple(pool.values()),
        )
    )


def consolidate_context(context: AnswerContext) -> AnswerContext:
    """根拠の木を ``consolidate_evidence_tree`` でまとめ、records・evidence・text を木から作り直す（#5168）。

    records が根拠の木の親と 1 対 1 でない文脈（木を持たない旧形式）はそのまま返す。
    """
    from rag_engine.retrieval.context_builder import record_context_key

    tree = context.evidence_tree
    if not tree or [record_context_key(item.record) for item in tree] != [record_context_key(record) for record in context.records]:
        return context
    merged = consolidate_evidence_tree(tree)
    bundle = context_bundle_from_parent_evidence(
        merged, max_chars=max([len(str(getattr(item.record, "text", "") or "")) for item in merged] + [MAX_CONTEXT_CHARS])
    )
    if not bundle.records:
        return context
    return replace(context, records=list(bundle.records), text=bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree)


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
