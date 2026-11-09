"""回答フローで受け渡すデータ型と、設定値・表示名の正規化。I/O は行わない。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rag_engine.chunking import DEFAULT_RETRIEVAL_TOP_K
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.retrieval.context_builder import ContextEvidence, ContextParentEvidence
from rag_engine.retrieval.question_planning import QuestionPlan
from rag_engine.retrieval.scope import RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN, RETRIEVAL_SCOPE_KNOWLEDGE_BASE, normalize_retrieval_scope

# 回答で引用した箇所の使われ方（#2040）。confirmed は「資料により確認できた事項」・手順の節の説明（質問の条件への該当を
# 監査で確認した。原文の条件つきを含む）で使用した箇所、reference は「参考情報」の節（降格した原文・該当が
# 未確認の説明）と「資料から確認できなかった事項」の説明で使用した箇所。画面の件数とバッジ、MCP の evidence が使う。
ANSWER_USE_CONFIRMED = "confirmed"
ANSWER_USE_REFERENCE = "reference"
ANSWER_USES = (ANSWER_USE_CONFIRMED, ANSWER_USE_REFERENCE)


def merge_answer_use(current: str | None, new: str | None) -> str | None:
    """同じ箇所を複数の item が使ったときの使われ方。1 つでも確かめた説明で使えば confirmed。"""
    if ANSWER_USE_CONFIRMED in (current, new):
        return ANSWER_USE_CONFIRMED
    return current or new


QUESTION_DISPLAY_METADATA_SEPARATOR = "--- 回答生成の実行記録 ---"

QUERY_EXPANSION_SEPARATOR = "--- 生成された検索質問 / 検索文 ---"

QUESTION_TEXT_SEARCH_SEPARATOR = "--- Oracle Text 検索語 ---"

QUERY_UNDERSTANDING_SEPARATOR = "--- 細粒度クエリ理解（Intent Classification / Slot Filling） ---"

QUESTION_PLAN_SEPARATOR = "--- 質問処理計画 ---"

# 旧形式の質問の表示から元の質問を取り出すための区切り（今の表示には書かない）。旧形式の記録と一致させるため
# 文字列は変えない（表示の名前は「用語・同義語」「回答ルール」。#3490）。
RUNTIME_KNOWLEDGE_SEPARATOR = "--- 実行時用語・ルール ---"

INQUIRY_CONDITIONS_SEPARATOR = QUERY_UNDERSTANDING_SEPARATOR

LEGACY_QUESTION_METADATA_SEPARATORS = (
    "--- 回答生成メモ ---",
    "--- Oracle Text keyword tokens（原質問） ---",
    "--- Question Plan ---",
    "--- Runtime Glossary / Rules ---",
    "--- Inquiry Conditions ---",
    "--- 問い合わせ条件 ---",
)

# 回答の流れは 1 本（検索して根拠で回答する）。回答の記録の answer_flow の値（FAQ・業務ガイドの経路と区別する）。
STANDARD_ANSWER_FLOW = "standard_rag"


@dataclass(frozen=True)
class AnswerRecord:
    """回答生成 context に投入する解析レコードまたはチャンクを表します。"""

    id: str
    engine: str
    engine_label: str
    page: int
    seq_no: int
    category: str
    text: str
    source: str = ""
    source_run_id: str = ""
    chunk_id: str = ""
    chunk_uid: str = ""
    chunk_level: str = "record"
    chunk_seq: int = 0
    parent_chunk_id: str = ""
    parent_chunk_uid: str = ""
    child_chunk_ids: tuple[str, ...] = ()
    page_end: int | None = None
    source_seq_ranges: tuple[dict[str, int], ...] = field(default_factory=tuple)
    source_record_refs: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    # 検索用 metadata を含まない元本文。空の場合は従来の record.text を使用する。
    body_text: str = ""

    @property
    def citation(self) -> str:
        """回答根拠欄に表示する短い引用文字列を返します。"""
        if self.chunk_id:
            seq_ranges = _format_seq_ranges(self.source_seq_ranges)
            pages = _page_span(self.page, self.page_end or self.page)
            parent = f" / {self.parent_chunk_id}" if self.parent_chunk_id else ""
            return f"{self.chunk_id}{parent} / {pages} / {seq_ranges}"
        return f"p.{self.page} #{self.seq_no}"


@dataclass(frozen=True)
class AnswerContext:
    """回答に使う根拠レコードと検索 trace を保持します。

    `text` は `ContextBundle.text` と同じ可読 trace で、回答 prompt には渡さない（prompt は
    `evidence_spans` が `records` から切り出す）。空判定・評価ツール・SDK が読む。
    """

    records: list[AnswerRecord]
    text: str
    evidence: tuple[ContextEvidence, ...] = ()
    evidence_tree: tuple[ContextParentEvidence, ...] = ()
    status: str = "ready"
    insufficient_reason: str = ""
    preferred_child_ids: tuple[str, ...] = ()
    # 評価後の局所拡張用。生成 prompt へは選択済み根拠だけを渡す。
    expansion_records: tuple[AnswerRecord, ...] = ()
    # 文書の選択 (#1028): rerank 後の全候補 child（順位順）、後回しにした文書の child、選択の trace。
    # 不足時はこれらから後回しの文書を戻して再評価・再生成する。
    ranked_candidates: tuple[AnswerRecord, ...] = ()
    # rerank の基準（関連度の下限・採用件数の上限）で絞り込む前の候補数（#4735）。基準で全部落ちたときも 0 にならない。
    candidate_count: int = 0
    deferred_records: tuple[AnswerRecord, ...] = ()
    document_selection: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerResponse:
    """LLM から得た回答本文と参照レコードを保持します。"""

    answer_text: str
    confidence: str = ""
    question_type: tuple[str, ...] = ()
    cited_evidence: tuple[dict[str, Any], ...] = ()
    reasoning_summary: str = ""
    insufficient_reason: str = ""
    needs_human_review: bool | None = None
    external_data_required: bool | None = None
    external_data_items: tuple[str, ...] = ()
    raw_text: str = ""
    evidence_facts: tuple[dict[str, str], ...] = ()
    generation_trace: dict[str, Any] = field(default_factory=dict)
    # 回答の構造（AnswerEnvelope。#1235）。grounded の確定の段で組み立てる。無い経路は結果で補う。
    envelope: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryExpansionResult:
    """検索 query、生成元 strategy、検索前の暫定データ確認観点を保持します。"""

    original_question: str
    # 呼び出し元（業務 Agent）が渡した検索文だけが入る。回答の検索は元の質問のほかに検索文を足さない。
    generated_queries: tuple[str, ...] = ()

    @property
    def retrieval_queries(self) -> tuple[str, ...]:
        """検索へ投入する query を重複なく返します。"""
        return _dedupe_queries((self.original_question, *self.generated_queries))


@dataclass(frozen=True)
class QuestionTextSearchInfo:
    """Oracle Text 検索に使う質問文字列、token、query を保持します。"""

    tokenizer: str = ""
    tokenizer_label: str = ""
    tokenizer_fingerprint: str = ""
    tokens: tuple[str, ...] = ()
    oracle_text_query: str = ""
    oracle_text_queries: tuple[str, ...] = ()
    query_variants: tuple[str, ...] = ()
    query_source: str = "原質問のみ"
    tokenization_traces: tuple[dict[str, Any], ...] = ()
    domain_keyword_expansion: dict[str, Any] = field(default_factory=dict)
    target_text_queries: tuple[str, ...] = ()  # 対象語の部分語の Oracle Text フレーズ検索式 (#730)
    error: str = ""


@dataclass(frozen=True)
class RetrievalQueryPlan:
    """複数 query の生成元と検索投入時の役割を保持します。"""

    original_question: str
    runtime_expanded_question: str
    vector_queries: tuple[str, ...] = ()
    text_query_variants: tuple[str, ...] = ()
    oracle_text_queries: tuple[str, ...] = ()
    text_search_tokens: tuple[str, ...] = ()
    text_search_tokenization_traces: tuple[dict[str, Any], ...] = ()
    llm_expansion_queries: tuple[str, ...] = ()
    lexical_queries: tuple[str, ...] = ()
    target_text_queries: tuple[str, ...] = ()  # 対象語の部分語の Oracle Text フレーズ検索式 (#730)
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN
    top_k: int = DEFAULT_RETRIEVAL_TOP_K
    candidate_limit: int = 0  # 実行時に `_retrieval_candidate_limit(top_k)` を入れる。trace 表示用
    metadata_filter: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "original_question": self.original_question,
            "runtime_expanded_question": self.runtime_expanded_question,
            "vector_queries": list(self.vector_queries),
            "text_query_variants": list(self.text_query_variants),
            "oracle_text_queries": list(self.oracle_text_queries),
            "text_search_tokens": list(self.text_search_tokens),
            "text_search_tokenization_traces": list(self.text_search_tokenization_traces),
            "llm_expansion_queries": list(self.llm_expansion_queries),
            "lexical_queries": list(self.lexical_queries),
            "target_text_queries": list(self.target_text_queries),
            "retrieval_scope": self.retrieval_scope,
            "top_k": self.top_k,
            "candidate_limit": self.candidate_limit,
            "metadata_filter": dict(self.metadata_filter),
        }


@dataclass(frozen=True)
class AnswerQuestionResult:
    """回答生成全体の trace、根拠、出力 payload をまとめます。"""

    answer: str
    question_display: str
    original_question: str
    answer_flow: str = STANDARD_ANSWER_FLOW
    retrieval_scope: str = RETRIEVAL_SCOPE_CURRENT_CHUNK_RUN
    classification_filter: dict[str, str] = field(default_factory=dict)
    generated_queries: tuple[str, ...] = ()
    answer_text: str = ""
    confidence: str = ""
    question_type: tuple[str, ...] = ()
    cited_evidence_ids: tuple[str, ...] = ()
    cited_evidence: tuple[dict[str, Any], ...] = ()
    image_evidence: tuple[dict[str, Any], ...] = ()
    image_prompt_mode: str = "text_only"
    primary_source_run_id: str = ""
    reasoning_summary: str = ""
    insufficient_reason: str = ""
    needs_human_review: bool | None = None
    external_data_required: bool | None = None
    external_data_items: tuple[str, ...] = ()
    evidence_items: tuple[dict[str, Any], ...] = ()
    text_search_tokenizer: str = ""
    text_search_tokenizer_label: str = ""
    text_search_tokenizer_fingerprint: str = ""
    text_search_tokens: tuple[str, ...] = ()
    text_search_query: str = ""
    text_search_queries: tuple[str, ...] = ()
    text_search_query_source: str = ""
    text_search_tokenization_error: str = ""
    prompt_injection_risk: bool = False
    prompt_injection_warnings: tuple[str, ...] = ()
    rerank_scores: tuple[dict[str, Any], ...] = ()
    retrieval_query_plan: dict[str, Any] = field(default_factory=dict)
    execution_steps: tuple[dict[str, Any], ...] = ()
    question_plan: QuestionPlan | None = None
    runtime_knowledge: RuntimeKnowledgeContext | None = None

    evidence_facts: tuple[dict[str, str], ...] = ()
    generation_trace: dict[str, Any] = field(default_factory=dict)
    task_contract: dict[str, Any] = field(default_factory=dict)
    # 回答の構造（AnswerEnvelope。#1235）。
    envelope: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundedAnswer:
    """是正で文脈や画像が変わり得るため、回答と、それを生成した入力を一緒に返す。"""

    response: AnswerResponse
    context: AnswerContext
    image_evidence: tuple[dict[str, Any], ...]
    image_prompt_mode: str


def extract_original_question(question: Any) -> str:
    """再実行時に表示文字列から元の質問だけを取り出します。"""
    text = str(question or "").replace("\r\n", "\n").strip()
    for separator in (
        QUESTION_DISPLAY_METADATA_SEPARATOR,
        QUERY_EXPANSION_SEPARATOR,
        QUESTION_TEXT_SEARCH_SEPARATOR,
        QUESTION_PLAN_SEPARATOR,
        RUNTIME_KNOWLEDGE_SEPARATOR,
        INQUIRY_CONDITIONS_SEPARATOR,
        *LEGACY_QUESTION_METADATA_SEPARATORS,
    ):
        if separator in text:
            text = text.split(separator, 1)[0].rstrip()
    return text.strip()


def retrieval_scope_label(scope_id: str) -> str:
    """検索範囲 ID を UI 表示ラベルへ変換します。"""
    scope = normalize_retrieval_scope(scope_id)
    if scope == RETRIEVAL_SCOPE_KNOWLEDGE_BASE:
        return "Knowledge Base（全登録ファイル）"
    return "現在のファイル"


def _dedupe_queries(queries: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    selected: list[str] = []
    for query in queries:
        text = str(query or "").strip()
        key = _dedupe_query_key(text)
        if not key or key in seen:
            continue
        selected.append(text)
        seen.add(key)
    return tuple(selected)


def _dedupe_query_key(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_text(str(value or "")))


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold()


def _page_span(page_start: int, page_end: int) -> str:
    if page_start == page_end:
        return f"p.{page_start}"
    return f"p.{page_start}-{page_end}"


def _format_seq_ranges(ranges: Sequence[dict[str, int]]) -> str:
    pieces = []
    for item in ranges:
        page = item.get("page")
        start = item.get("seq_start")
        end = item.get("seq_end")
        if start == end:
            pieces.append(f"p.{page} #{start}")
        else:
            pieces.append(f"p.{page} #{start}-{end}")
    return ", ".join(pieces)
