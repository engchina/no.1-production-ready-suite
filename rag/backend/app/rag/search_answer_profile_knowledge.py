"""検索・回答プロファイル単位の知識(ドメインキーワード等)を Oracle の JSON payload で管理する。

payload 形式と正規化・候補生成は rag_poc の ``rag_engine.knowledge`` をそのまま使う。
保存先はファイルではなく ``rag_search_answer_profile_knowledge``(検索・回答プロファイル × 種別)。
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pr_backend_core.logging import safe_exception_fields
from rag_engine.knowledge.approved_faq import (
    APPROVED_FAQ_APPROVED_STATUS,
    APPROVED_FAQ_IMPORT_MODES,
    APPROVED_FAQ_NEGATIVE_QUESTIONS_KEY,
    APPROVED_FAQ_TARGET_KNOWLEDGE_BASES_KEY,
    DEFAULT_APPROVED_FAQ_MIN_SCORE,
    DEFAULT_APPROVED_FAQ_NEGATIVE_MIN_SCORE,
    DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT,
    ApprovedFaqImportRow,
    ApprovedFaqMutationResult,
    ApprovedFaqRanking,
    ApprovedFaqRecord,
    ApprovedFaqSemanticIndex,
    ApprovedFaqSourceDocument,
    ApprovedFaqSuggestion,
    _approved_faq_semantic_questions,
    add_approved_faq_record,
    apply_approved_faq_import_rows,
    approved_faq_records_signature,
    approved_faq_scope_raw,
    build_approved_faq_semantic_index,
    delete_approved_faq_records,
    load_approved_faq_excel_rows,
    load_approved_faq_payload,
    load_approved_faq_records,
    load_approved_faq_semantic_index,
    normalize_approved_faq_negative_questions,
    rank_approved_faq_questions,
)
from rag_engine.knowledge.domain_keyword_candidates import (
    DomainKeywordCandidate,
    DomainKeywordSourceText,
    suggest_domain_keyword_candidates,
)
from rag_engine.knowledge.domain_keywords import (
    DOMAIN_KEYWORDS_SCHEMA_VERSION,
    MAX_DOMAIN_KEYWORDS,
    normalize_domain_keyword,
    normalize_domain_keywords,
)
from rag_engine.knowledge.runtime_knowledge import (
    MAX_RULES,
    MAX_TERMS,
    RuntimeKnowledgeContext,
    build_runtime_knowledge_context,
)
from rag_engine.knowledge.runtime_knowledge_management import (
    edit_knowledge,
    load_knowledge_snapshot,
)
from rag_engine.retrieval.text_search_tokenizer import TextSearchTokenizerConfig

from app.config import Settings, get_settings
from app.rag.answer_engine import AnswerScope
from app.rag.approved_faq_scope import (
    ApprovedFaqScopeState,
    FaqSourceDocumentState,
    cited_knowledge_base_ids,
    current_approval,
    default_target_knowledge_base_ids,
    evaluate_approved_faq_scope,
    permitted_for_current_user,
    validate_target_knowledge_base_ids,
)
from app.schemas.search import PageRange, format_page_ranges
from app.schemas.search_answer_profile import SearchAnswerProfileDetail
from app.schemas.search_answer_profile_knowledge import (
    ClarificationAnswer,
    ClarificationSection,
    RuleClarification,
)

logger = logging.getLogger(__name__)

DOMAIN_KEYWORDS_KIND = "domain_keywords"
DEFAULT_CANDIDATE_LIMIT = 50
DEFAULT_CANDIDATE_SOURCE_CHUNKS = 2000


class SearchAnswerProfileKnowledgeStore(Protocol):
    async def get_search_answer_profile_knowledge(
        self, search_answer_profile_id: str, kind: str
    ) -> dict[str, object] | None: ...

    async def save_search_answer_profile_knowledge(
        self, search_answer_profile_id: str, kind: str, payload: dict[str, object]
    ) -> None: ...

    async def list_search_answer_profile_chunk_texts(
        self, knowledge_base_ids: list[str], *, limit: int
    ) -> list[tuple[str, str, str]]: ...


@dataclass(frozen=True)
class DomainKeywordSuggestion:
    candidates: list[DomainKeywordCandidate]
    processed_chunk_count: int


async def load_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> list[str]:
    """保存済みドメインキーワード(正規化済み)を返す。未登録は空。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, DOMAIN_KEYWORDS_KIND
    )
    if payload is None:
        return []
    return normalize_domain_keywords(payload.get("keywords"))


def validate_domain_keywords(values: list[str]) -> list[str]:
    """既存の正規化で重複を除き、上限超過は切り捨てず拒否する。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = normalize_domain_keyword(value)
        key = keyword.casefold()
        if keyword and key not in seen:
            seen.add(key)
            result.append(keyword)
            if len(result) > MAX_DOMAIN_KEYWORDS:
                raise ValueError(f"ドメインキーワードは {MAX_DOMAIN_KEYWORDS} 件までです。")
    return result


async def save_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    keywords: list[str],
) -> list[str]:
    """正規化(重複・空白・長さ・件数上限)して保存し、保存後の一覧を返す。"""
    normalized = validate_domain_keywords(keywords)
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id,
        DOMAIN_KEYWORDS_KIND,
        {
            "schema_version": DOMAIN_KEYWORDS_SCHEMA_VERSION,
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "keywords": normalized,
        },
    )
    return normalized


async def suggest_domain_keywords(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    knowledge_base_ids: list[str],
    *,
    limit: int = DEFAULT_CANDIDATE_LIMIT,
    max_source_chunks: int = DEFAULT_CANDIDATE_SOURCE_CHUNKS,
) -> DomainKeywordSuggestion:
    """参照 KB の配信中 chunk から TF-IDF でキーワード候補を作る(LLM は使わない)。"""
    rows = await store.list_search_answer_profile_chunk_texts(
        knowledge_base_ids, limit=max_source_chunks
    )
    sources = [
        DomainKeywordSourceText(chunk_id=chunk_id, document_id=document_id, text=text)
        for chunk_id, document_id, text in rows
        if text.strip()
    ]
    existing = await load_domain_keywords(store, search_answer_profile_id)
    candidates = suggest_domain_keyword_candidates(
        sources,
        existing_keywords=existing,
        tokenizer_config=TextSearchTokenizerConfig(),
        limit=limit,
    )
    return DomainKeywordSuggestion(candidates=candidates, processed_chunk_count=len(sources))


# --- Approved FAQ(類似問)-----------------------------------------------------

APPROVED_FAQ_KIND = "approved_faq"
FAQ_SEMANTIC_CACHE_KEY = "semantic_index_cache"
# (texts, input_type) -> vectors。backend の Cohere embedding(OciGenAiClient.embed)を渡す。
FaqEmbedInputType = Literal["SEARCH_DOCUMENT", "SEARCH_QUERY"]
FaqEmbedder = Callable[[list[str], FaqEmbedInputType], Awaitable[list[list[float]]]]
APPROVED_FAQ_PREVIEW_ROWS = 10


class ApprovedFaqStore(SearchAnswerProfileKnowledgeStore, Protocol):
    """FAQ の対象の範囲と根拠の状態を判定するために、プロファイルと文書の今の状態も読む。"""

    async def get_search_answer_profile(
        self, search_answer_profile_id: str
    ) -> SearchAnswerProfileDetail | None: ...

    async def approved_faq_source_document_states(
        self, document_ids: Sequence[str]
    ) -> dict[str, FaqSourceDocumentState]: ...


@dataclass(frozen=True)
class ApprovedFaqMutation:
    records: list[ApprovedFaqRecord]
    inserted_count: int
    deleted_count: int


@dataclass(frozen=True)
class ApprovedFaqSnapshot:
    """FAQ の payload と、各 FAQ の今の根拠の状態（読むたびに計算する。#4621）。"""

    payload: dict[str, object] | None
    states: list[ApprovedFaqScopeState]
    profile_knowledge_base_ids: list[str]

    @property
    def records(self) -> list[ApprovedFaqRecord]:
        return [state.record for state in self.states]

    def usable_records(self) -> list[ApprovedFaqRecord]:
        """今の利用者に提示・回答で使える FAQ。

        停止していない・承認済み・対象の KB をすべて使える FAQ だけ。
        """
        return [
            state.record
            for state in self.states
            if state.active
            and state.record.status == APPROVED_FAQ_APPROVED_STATUS
            and permitted_for_current_user(state.record)
        ]


FAQ_ENABLED_KEY = "enabled"
FAQ_DIRECT_ANSWER_ENABLED_KEY = "direct_answer_enabled"
# FAQ の payload に持つ、検索・回答プロファイルごとの設定の key(FAQ の更新関数は知らない)。
FAQ_SETTING_KEYS = (FAQ_ENABLED_KEY, FAQ_DIRECT_ANSWER_ENABLED_KEY)


def approved_faq_enabled(payload: Mapping[str, object] | None) -> bool:
    """回答の前に類似問を提示するか(検索・回答プロファイルごと。未設定はオン。#684)。"""
    return (payload or {}).get(FAQ_ENABLED_KEY) is not False


def approved_faq_direct_answer_enabled(payload: Mapping[str, object] | None) -> bool:
    """一致度が閾値以上のとき FAQ の回答をそのまま返すか(プロファイルごと。未設定はオフ。#4788)。

    類似問の提示がオフのプロファイルは FAQ を使わないので、こちらもオフ。
    """
    return (
        approved_faq_enabled(payload) and (payload or {}).get(FAQ_DIRECT_ANSWER_ENABLED_KEY) is True
    )


def migrate_approved_faq_targets(
    payload: Mapping[str, object] | None, profile_knowledge_base_ids: Sequence[str]
) -> dict[str, object] | None:
    """対象の KB を持たない FAQ に、今のプロファイルの全 KB を書く（既存の FAQ の移行。#4621）。

    変えたときだけ新しい payload を返す（変える record が無ければ None）。
    """
    raw_records = (payload or {}).get("records")
    if payload is None or not isinstance(raw_records, list):
        return None
    targets = default_target_knowledge_base_ids(profile_knowledge_base_ids)
    migrated = [
        {**raw, APPROVED_FAQ_TARGET_KNOWLEDGE_BASES_KEY: targets}
        if isinstance(raw, dict) and APPROVED_FAQ_TARGET_KNOWLEDGE_BASES_KEY not in raw
        else raw
        for raw in raw_records
    ]
    if migrated == raw_records:
        return None
    return {**payload, "records": migrated}


async def _profile_knowledge_base_ids(
    store: ApprovedFaqStore, search_answer_profile_id: str
) -> list[str] | None:
    """プロファイルの今の参照 KB（プロファイルが見えなければ None）。"""
    view = await store.get_search_answer_profile(search_answer_profile_id)
    return None if view is None else view.config.normalized_knowledge_base_ids()


async def _load_faq_payload(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    profile_knowledge_base_ids: Sequence[str],
) -> dict[str, object] | None:
    """FAQ の payload を読み、対象の KB が無い FAQ があれば移行して保存する（1 回だけ）。

    ponytail: 移行の保存と管理の更新が重なると後勝ち（移行は最初の 1 回の読み込みだけ）。
    """
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    migrated = migrate_approved_faq_targets(payload, profile_knowledge_base_ids)
    if migrated is None:
        return payload
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, migrated
    )
    return migrated


async def load_approved_faq_snapshot(
    store: ApprovedFaqStore, search_answer_profile_id: str
) -> ApprovedFaqSnapshot:
    """FAQ と、今のプロファイルの参照 KB・文書の状態から計算した根拠の状態を返す。"""
    profile_knowledge_base_ids = await _profile_knowledge_base_ids(store, search_answer_profile_id)
    if profile_knowledge_base_ids is None:
        return ApprovedFaqSnapshot(payload=None, states=[], profile_knowledge_base_ids=[])
    payload = await _load_faq_payload(store, search_answer_profile_id, profile_knowledge_base_ids)
    if payload is None:
        records: list[ApprovedFaqRecord] = []
    else:
        with _faq_file(payload) as path:
            records = load_approved_faq_records(path)
    document_ids = [source.document_id for record in records for source in record.source_documents]
    documents = (
        await store.approved_faq_source_document_states(document_ids) if document_ids else {}
    )
    return ApprovedFaqSnapshot(
        payload=payload,
        states=[
            evaluate_approved_faq_scope(record, profile_knowledge_base_ids, documents)
            for record in records
        ],
        profile_knowledge_base_ids=profile_knowledge_base_ids,
    )


async def load_approved_faq_enabled(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> bool:
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    return approved_faq_enabled(payload)


async def save_approved_faq_settings(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    changes: Mapping[str, bool],
) -> None:
    """プロファイルごとの FAQ の設定を保存する(FAQ の登録内容は変えない)。

    ``changes`` は変える設定だけ(``FAQ_SETTING_KEYS``)。
    """
    unknown = set(changes) - set(FAQ_SETTING_KEYS)
    if unknown:
        raise ValueError(f"FAQ の設定に無い項目です: {sorted(unknown)}")
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND
    )
    with _faq_file(payload) as path:
        # 未登録なら FAQ の標準形式(空)に設定だけを持たせる。
        base = load_approved_faq_payload(path)
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, {**base, **changes}
    )


async def find_approved_faq(
    store: ApprovedFaqStore, search_answer_profile_id: str, faq_id: str
) -> ApprovedFaqRecord | None:
    """利用者が選んだ類似問を、検索・回答プロファイルの承認済み FAQ から引き直す。

    提示がオンで、今の利用者が使える FAQ（停止していない・対象の KB をすべて使える）だけを返す。
    使えない FAQ は無いものと同じ None（存在を示さない。#4621）。
    """
    snapshot = await load_approved_faq_snapshot(store, search_answer_profile_id)
    if snapshot.payload is None or not approved_faq_enabled(snapshot.payload):
        return None
    return next((record for record in snapshot.usable_records() if record.id == faq_id), None)


async def mutate_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    operation: Callable[[Path, list[str]], ApprovedFaqMutationResult],
) -> ApprovedFaqMutation:
    """rag_poc の FAQ 更新関数(ファイル前提)を一時ファイル上で実行し、結果を DB へ保存する。

    ``operation`` にはプロファイルの今の参照 KB も渡す(対象の KB の既定と検証に使う)。
    ponytail: 同時編集は後勝ち。競合検出が必要になったら revision を条件に MERGE する。
    """
    profile_knowledge_base_ids = await _profile_knowledge_base_ids(store, search_answer_profile_id)
    if profile_knowledge_base_ids is None:
        raise KeyError(search_answer_profile_id)
    payload = await _load_faq_payload(store, search_answer_profile_id, profile_knowledge_base_ids)
    with _faq_file(payload) as path:
        result = operation(path, profile_knowledge_base_ids)
        updated = load_approved_faq_payload(path)
        records = load_approved_faq_records(path)
    if payload is not None:
        # FAQ の更新関数は設定を知らないので、プロファイルごとの設定を引き継ぐ。
        updated = {**updated, **{key: payload[key] for key in FAQ_SETTING_KEYS if key in payload}}
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, updated
    )
    return ApprovedFaqMutation(
        records=records,
        inserted_count=result.inserted_count,
        deleted_count=result.deleted_count,
    )


async def add_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    *,
    question: str,
    answer: str,
    target_knowledge_base_ids: Sequence[str] | None = None,
    negative_questions: Sequence[str] = (),
) -> ApprovedFaqMutation:
    """手入力の FAQ を追加する。対象の KB は選んだもの(未指定はプロファイルの全 KB)。

    ``negative_questions`` は否定の質問(この FAQ を当てたくない質問の例。#4788)。
    """
    return await mutate_approved_faq(
        store,
        search_answer_profile_id,
        lambda path, profile_kbs: add_approved_faq_record(
            path,
            question=question,
            approved_answer=answer,
            target_knowledge_base_ids=validate_target_knowledge_base_ids(
                target_knowledge_base_ids, profile_kbs
            ),
            approval=current_approval(),
            negative_questions=negative_questions,
        ),
    )


async def save_approved_faq_negative_questions(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    faq_id: str,
    negative_questions: Sequence[str],
) -> None:
    """FAQ の否定の質問を置き換える(質問・回答・対象・承認は変えない。#4788)。

    FAQ が無ければ KeyError。FAQ の質問(別名を含む)と同じ文・上限を超える数は ValueError。
    """
    profile_knowledge_base_ids = await _profile_knowledge_base_ids(store, search_answer_profile_id)
    if profile_knowledge_base_ids is None:
        raise KeyError(search_answer_profile_id)
    payload = await _load_faq_payload(store, search_answer_profile_id, profile_knowledge_base_ids)
    raw_records = (payload or {}).get("records")
    records = raw_records if isinstance(raw_records, list) else []
    index = next(
        (
            position
            for position, raw in enumerate(records)
            if isinstance(raw, dict) and str(raw.get("id") or "").strip() == faq_id
        ),
        None,
    )
    if payload is None or index is None:
        raise KeyError(faq_id)
    with _faq_file({**payload, "records": [records[index]]}) as path:
        record = next(iter(load_approved_faq_records(path)), None)
    if record is None:
        raise KeyError(faq_id)
    cleaned = normalize_approved_faq_negative_questions(negative_questions, record.match_questions)
    updated_records = list(records)
    updated_records[index] = {**records[index], APPROVED_FAQ_NEGATIVE_QUESTIONS_KEY: list(cleaned)}
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, {**payload, "records": updated_records}
    )


async def delete_approved_faq(
    store: ApprovedFaqStore, search_answer_profile_id: str, ids: list[str]
) -> ApprovedFaqMutation:
    return await mutate_approved_faq(
        store,
        search_answer_profile_id,
        lambda path, _profile_kbs: delete_approved_faq_records(path, ids),
    )


async def confirm_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    faq_id: str,
    *,
    target_knowledge_base_ids: Sequence[str] | None = None,
) -> None:
    """管理者が FAQ の対象の KB と根拠を確かめ直す(要確認からの復帰・対象の変更。#4621)。

    対象の KB を選び直し(未指定はプロファイルの全 KB)、引用した文書のうち今も対象の KB に
    ある文書だけを今の版で記録し直し、承認者と時刻を記録する。FAQ が無ければ KeyError。
    """
    profile_knowledge_base_ids = await _profile_knowledge_base_ids(store, search_answer_profile_id)
    if profile_knowledge_base_ids is None:
        raise KeyError(search_answer_profile_id)
    targets = validate_target_knowledge_base_ids(
        target_knowledge_base_ids, profile_knowledge_base_ids
    )
    payload = await _load_faq_payload(store, search_answer_profile_id, profile_knowledge_base_ids)
    raw_records = (payload or {}).get("records")
    records = raw_records if isinstance(raw_records, list) else []
    index = next(
        (
            position
            for position, raw in enumerate(records)
            if isinstance(raw, dict) and str(raw.get("id") or "").strip() == faq_id
        ),
        None,
    )
    if payload is None or index is None:
        raise KeyError(faq_id)
    with _faq_file({**payload, "records": [records[index]]}) as path:
        record = next(iter(load_approved_faq_records(path)), None)
    if record is None:
        raise KeyError(faq_id)
    document_ids = [source.document_id for source in record.source_documents]
    documents = (
        await store.approved_faq_source_document_states(document_ids) if document_ids else {}
    )
    kept = [
        ApprovedFaqSourceDocument(document_id=document_id, version=state.version)
        for document_id in document_ids
        if (state := documents.get(document_id)) is not None
        and state.knowledge_base_ids & set(targets)
    ]
    updated_records = list(records)
    updated_records[index] = {
        **records[index],
        **approved_faq_scope_raw(
            target_knowledge_base_ids=targets,
            source_documents=kept,
            approval=current_approval(),
        ),
    }
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, APPROVED_FAQ_KIND, {**payload, "records": updated_records}
    )


async def approved_faq_row_with_citations(
    store: ApprovedFaqStore, search_answer_profile_id: str, row: ApprovedFaqImportRow
) -> ApprovedFaqImportRow:
    """回答 feedback から作った FAQ 行に、引用の文書・対象の KB・承認を付ける(#4621)。

    対象の KB は引用した文書を含むプロファイルの参照 KB(引用が無い・どの参照 KB にも無ければ
    プロファイルの全 KB)。文書は今の版の印で記録する(見えない文書は印なしで残し、要確認になる)。
    """
    profile_knowledge_base_ids = (
        await _profile_knowledge_base_ids(store, search_answer_profile_id) or []
    )
    cited = list(dict.fromkeys(row.cited_document_ids))
    documents = await store.approved_faq_source_document_states(cited) if cited else {}
    targets = cited_knowledge_base_ids(
        cited, documents, profile_knowledge_base_ids
    ) or default_target_knowledge_base_ids(profile_knowledge_base_ids)
    return replace(
        row,
        target_knowledge_base_ids=tuple(targets),
        source_documents=tuple(
            ApprovedFaqSourceDocument(
                document_id=document_id,
                version=state.version if (state := documents.get(document_id)) else "",
            )
            for document_id in cited
        ),
        approval=current_approval(),
    )


def read_approved_faq_excel(content: bytes, file_name: str) -> list[ApprovedFaqImportRow]:
    """QUESTION / ANSWER 列の Excel を FAQ 取込行へ読む(不正な形式は ValueError)。"""
    suffix = Path(file_name or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("Excel ファイル(.xlsx / .xls)を指定してください。")
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / Path(file_name).name
        path.write_bytes(content)
        return load_approved_faq_excel_rows(path)


async def import_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    rows: list[ApprovedFaqImportRow],
    *,
    mode: str,
    target_knowledge_base_ids: Sequence[str] | None = None,
) -> ApprovedFaqMutation:
    """FAQ 行を取り込む。対象の KB を持たない行(Excel)には選んだ KB(未指定は全 KB)を付ける。"""
    if mode not in APPROVED_FAQ_IMPORT_MODES:
        raise ValueError(f"取込モードが不正です: {mode}")

    def operation(path: Path, profile_kbs: list[str]) -> ApprovedFaqMutationResult:
        approval = current_approval()
        scoped: list[ApprovedFaqImportRow] = []
        for row in rows:
            if row.target_knowledge_base_ids is None:
                targets = validate_target_knowledge_base_ids(target_knowledge_base_ids, profile_kbs)
                row = replace(row, target_knowledge_base_ids=tuple(targets))
            scoped.append(row if row.approval is not None else replace(row, approval=approval))
        return apply_approved_faq_import_rows(path, scoped, mode=mode)

    return await mutate_approved_faq(store, search_answer_profile_id, operation)


async def rank_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    question: str,
    *,
    limit: int = DEFAULT_APPROVED_FAQ_SUGGESTION_LIMIT,
    min_score: float | None = None,
    negative_min_score: float = DEFAULT_APPROVED_FAQ_NEGATIVE_MIN_SCORE,
    embed: FaqEmbedder | None = None,
    embedding_model: str = "",
    embedding_dimensions: int = 1536,
    snapshot: ApprovedFaqSnapshot | None = None,
) -> ApprovedFaqRanking:
    """質問に近い承認済み FAQ(順位順)と、否定の質問に当たって外した FAQ を返す。

    文字列類似度に加え、embed 指定時は rag_poc の意味照合(FAQ 質問の embedding index)を使う。
    index は FAQ payload の ``semantic_index_cache`` に保持し、FAQ 集合の署名が変われば作り直す。
    embedding に失敗しても文字列照合で続ける。

    照合するのは今の利用者が使える FAQ だけ(停止していない・対象の KB をすべて使える。#4621)。
    index は全 FAQ で作る(利用者ごとに作り直さない)。質問が FAQ の質問より否定の質問に近い FAQ は
    候補から外す(#4788)。``snapshot`` は呼び出し元が読んだ FAQ(読み直さない)。
    """
    if not question.strip():
        return ApprovedFaqRanking()
    if snapshot is None:
        snapshot = await load_approved_faq_snapshot(store, search_answer_profile_id)
    payload = snapshot.payload
    if payload is None or not approved_faq_enabled(payload):
        return ApprovedFaqRanking()
    records = snapshot.records
    usable = snapshot.usable_records()
    if not usable:
        return ApprovedFaqRanking()
    semantic_index: ApprovedFaqSemanticIndex | None = None
    query_embedding: list[float] | None = None
    if embed is not None:
        try:
            semantic_index, cache = await _faq_semantic_index(
                payload, records, embed, model=embedding_model, dimensions=embedding_dimensions
            )
            if cache is not None:
                await store.save_search_answer_profile_knowledge(
                    search_answer_profile_id,
                    APPROVED_FAQ_KIND,
                    {**payload, FAQ_SEMANTIC_CACHE_KEY: cache},
                )
            query_embedding = (await embed([question], "SEARCH_QUERY"))[0]
        except Exception as exc:  # 意味照合は補助。文字列照合で続ける。
            logger.warning(
                "approved faq semantic matching failed", extra={**safe_exception_fields(exc)}
            )
            semantic_index, query_embedding = None, None
    return rank_approved_faq_questions(
        question,
        usable,
        semantic_index=semantic_index,
        semantic_query_embedding=query_embedding,
        limit=limit,
        min_score=DEFAULT_APPROVED_FAQ_MIN_SCORE if min_score is None else min_score,
        negative_min_score=negative_min_score,
    )


async def suggest_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    question: str,
    **options: Any,
) -> list[ApprovedFaqSuggestion]:
    """質問に近い承認済み FAQ を返す(否定の質問に当たった FAQ は含めない)。"""
    ranking = await rank_approved_faq(store, search_answer_profile_id, question, **options)
    return list(ranking.suggestions)


class FaqEmbeddingClient(Protocol):
    async def embed(
        self, texts: list[str], *, input_type: FaqEmbedInputType = "SEARCH_DOCUMENT"
    ) -> list[list[float]]: ...


def approved_faq_match_options(
    settings: Settings, genai: Callable[[], FaqEmbeddingClient]
) -> dict[str, Any]:
    """全体設定から決まる FAQ の照合の引数(否定の質問の下限と、意味照合の embedding)。

    類似問の提示・送信の前の確認・回答の前の照合で同じ値を使う。``genai`` は embedding を
    呼ぶときにだけ作る(意味照合がオフ・照合しないときは作らない)。
    """
    embed: FaqEmbedder | None = None
    if settings.rag_approved_faq_semantic_enabled:

        async def embed(texts: list[str], input_type: FaqEmbedInputType) -> list[list[float]]:
            return await genai().embed(texts, input_type=input_type)

    return {
        "negative_min_score": settings.rag_approved_faq_negative_min_score,
        "embed": embed,
        "embedding_model": settings.oci_genai_embedding_model,
        "embedding_dimensions": settings.oci_genai_embedding_dim,
    }


@dataclass(frozen=True)
class DirectFaqMatch:
    """FAQ の回答をそのまま返す設定がオンのプロファイルでの、質問と FAQ の照合の結果(#4788)。"""

    min_score: float
    # 1 位の候補(閾値未満を含む。候補が無ければ None)。
    best: ApprovedFaqSuggestion | None
    # 否定の質問に当たって候補から外した FAQ。
    negative_excluded: tuple[ApprovedFaqSuggestion, ...] = ()

    @property
    def answer(self) -> ApprovedFaqSuggestion | None:
        """回答をそのまま返す FAQ(1 位の一致度が閾値以上のときだけ)。"""
        return self.best if self.best is not None and self.best.score >= self.min_score else None


async def load_direct_faq_snapshot(
    store: ApprovedFaqStore, search_answer_profile_id: str
) -> ApprovedFaqSnapshot | None:
    """FAQ の回答をそのまま返す設定がオンで、今の利用者が使える FAQ があるプロファイルの FAQ。

    設定がオフ(既定)・使える FAQ が無い・読めないときは None(照合しない。embedding も呼ばない)。
    """
    try:
        snapshot = await load_approved_faq_snapshot(store, search_answer_profile_id)
    except Exception as exc:  # 照合は補助。呼び出し元はいつもの確認・検索・回答に進む。
        logger.warning("approved faq direct match failed", extra={**safe_exception_fields(exc)})
        return None
    if not approved_faq_direct_answer_enabled(snapshot.payload) or not snapshot.usable_records():
        return None
    return snapshot


async def match_direct_approved_faq(
    store: ApprovedFaqStore,
    search_answer_profile_id: str,
    question: str,
    settings: Settings,
    genai: Callable[[], FaqEmbeddingClient],
    *,
    snapshot: ApprovedFaqSnapshot | None = None,
) -> DirectFaqMatch | None:
    """質問を承認済み FAQ と照合し、回答をそのまま返せるかを決める(LLM を呼ばない。#4788)。

    プロファイルの「そのまま返す」がオフ(既定)・FAQ が無いときは None。使える FAQ・否定の質問の
    扱いは類似問の提示(``rank_approved_faq``)と同じ。照合は補助なので、失敗しても None(呼び出し
    元はいつもの確認・検索・回答に進む)。``snapshot`` は ``load_direct_faq_snapshot`` で読んだ FAQ
    (読み直さない)。
    """
    if not question.strip():
        return None
    if snapshot is None:
        snapshot = await load_direct_faq_snapshot(store, search_answer_profile_id)
        if snapshot is None:
            return None
    try:
        ranking = await rank_approved_faq(
            store,
            search_answer_profile_id,
            question,
            limit=1,
            snapshot=snapshot,
            **approved_faq_match_options(settings, genai),
        )
    except Exception as exc:
        logger.warning("approved faq direct match failed", extra={**safe_exception_fields(exc)})
        return None
    return DirectFaqMatch(
        min_score=settings.rag_approved_faq_direct_answer_min_score,
        best=ranking.suggestions[0] if ranking.suggestions else None,
        negative_excluded=ranking.negative_excluded,
    )


async def _faq_semantic_index(
    payload: Mapping[str, object],
    records: list[ApprovedFaqRecord],
    embed: FaqEmbedder,
    *,
    model: str,
    dimensions: int,
) -> tuple[ApprovedFaqSemanticIndex, dict[str, object] | None]:
    """保存済み index を再利用し、無効なら FAQ 質問を embedding して作り直す(新 cache を返す)。"""
    with tempfile.TemporaryDirectory(prefix="approved-faq-semantic-") as work:
        cache_path = Path(work) / "semantic_index.json"
        cached = payload.get(FAQ_SEMANTIC_CACHE_KEY)
        if isinstance(cached, dict):
            cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            index = load_approved_faq_semantic_index(
                cache_path,
                model=model,
                dimensions=dimensions,
                records_signature=approved_faq_records_signature(records),
            )
            if index is not None:
                return index, None
        questions = [question for _, question in _approved_faq_semantic_questions(records)]
        vectors = await embed(questions, "SEARCH_DOCUMENT") if questions else []
        index = build_approved_faq_semantic_index(
            cache_path,
            records,
            model=model,
            dimensions=dimensions,
            embedder=lambda texts, settings: vectors,
            settings=None,
        )
        return index, json.loads(cache_path.read_text(encoding="utf-8"))


def is_direct_faq_match(suggestion: ApprovedFaqSuggestion, min_score: float) -> bool:
    """一致度が ``min_score``(そのまま返す一致度の下限)以上の候補か。"""
    return suggestion.score >= min_score


@contextmanager
def _faq_file(payload: Mapping[str, object] | None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="approved-faq-") as work:
        path = Path(work) / "approved_faq.json"
        if payload is not None:
            path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield path


# --- 用語・同義語と回答ルール(runtime knowledge)-------------------------------------------

RUNTIME_KNOWLEDGE_KIND = "runtime_knowledge"


async def load_runtime_knowledge_payload(
    store: SearchAnswerProfileKnowledgeStore, search_answer_profile_id: str
) -> dict[str, object]:
    """検索・回答プロファイルの用語・同義語と回答ルールの payload を返す(未登録は空の標準形式)。"""
    payload = await store.get_search_answer_profile_knowledge(
        search_answer_profile_id, RUNTIME_KNOWLEDGE_KIND
    )
    return payload or {"schema_version": 1, "terms": [], "rules": []}


async def edit_runtime_knowledge(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    *,
    kind: str,
    selected: str | None,
    name: str = "",
    title: str = "",
    labels: str = "",
    content: str = "",
    source: str = "",
    purpose: str | None = None,
    instruction_kind: str | None = None,
    exclusive_group: str | None = None,
    priority: int | None = None,
    enabled: bool = True,
    delete: bool = False,
) -> dict[str, object]:
    """rag_poc の edit_knowledge を一時ファイル上で実行し、結果を DB へ保存する。"""
    payload = await load_runtime_knowledge_payload(store, search_answer_profile_id)
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        snapshot = load_knowledge_snapshot(work_dir, path)
        updated, _ = edit_knowledge(
            snapshot,
            kind,
            selected,
            name=name,
            title=title,
            labels=labels,
            content=content,
            source=source,
            purpose=purpose,
            instruction_kind=instruction_kind,
            exclusive_group=exclusive_group,
            priority=priority,
            enabled=enabled,
            delete=delete,
            confirmed=delete,
        )
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, RUNTIME_KNOWLEDGE_KIND, dict(updated.payload)
    )
    return dict(updated.payload)


def preview_runtime_knowledge(
    payload: Mapping[str, object], question: str
) -> RuntimeKnowledgeContext:
    """質問に一致する用語・同義語と回答ルールと拡張後の検索文を返す(保存しない)。"""
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        return build_runtime_knowledge_context(
            question,
            work_dir,
            path,
            rules_confirmation_enabled=True,
            rules_answer_enabled=True,
            terms_search_enabled=True,
            terms_answer_enabled=True,
        )


def match_runtime_knowledge(
    payload: Mapping[str, object], question: str
) -> RuntimeKnowledgeContext:
    """質問に当たる用語と、用途・排他・明示優先度で採用した確認・回答ルールを返す（保存しない）。

    照合・有効状態・用途と排他の判定は回答生成と同じ。生成側は rules_for_stage で取り出す。
    件数・字数で切って省いた数を示すのは呼び出し元(MCP の根拠の取得。#2701)。
    """
    with _runtime_knowledge_dir(payload) as (work_dir, path):
        return build_runtime_knowledge_context(
            question,
            work_dir,
            path,
            term_limit=MAX_TERMS,
            rule_limit=MAX_RULES,
            rules_confirmation_enabled=get_settings().rag_runtime_rules_confirmation_enabled,
            rules_answer_enabled=get_settings().rag_runtime_rules_answer_enabled,
        )


@contextmanager
def _runtime_knowledge_dir(payload: Mapping[str, object]) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="runtime-knowledge-") as work:
        work_dir = Path(work)
        path = work_dir / "runtime_knowledge.json"
        path.write_text(json.dumps(dict(payload), ensure_ascii=False), encoding="utf-8")
        yield work_dir, path


# --- ルールの確認の質問(チャットの確認。#717)-------------------------------------------


def _rules(payload: Mapping[str, object]) -> list[dict[str, object]]:
    rules = payload.get("rules")
    return [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []


def rule_clarification(rule: Mapping[str, object]) -> RuleClarification | None:
    """ルールの確認(無い・壊れているときは None)。"""
    if rule.get("purpose", "both") not in ("confirmation", "both"):
        return None
    raw = rule.get("clarification")
    if not isinstance(raw, Mapping):
        return None
    try:
        return RuleClarification.model_validate(dict(raw))
    except ValueError:
        return None


async def save_rule_clarification(
    store: SearchAnswerProfileKnowledgeStore,
    search_answer_profile_id: str,
    rule_id: str,
    clarification: RuleClarification | None,
) -> dict[str, object]:
    """ルールに確認の質問を保存する(None は外す)。ルールが無ければ KeyError。"""
    payload = await load_runtime_knowledge_payload(store, search_answer_profile_id)
    rules = _rules(payload)
    rule = next((item for item in rules if str(item.get("id", "")) == rule_id), None)
    if rule is None:
        raise KeyError(rule_id)
    if clarification is None:
        rule.pop("clarification", None)
    else:
        rule["clarification"] = clarification.model_dump()
    updated = {**payload, "rules": rules}
    await store.save_search_answer_profile_knowledge(
        search_answer_profile_id, RUNTIME_KNOWLEDGE_KIND, updated
    )
    return updated


def _already_answered(clarification: RuleClarification, answer: ClarificationAnswer | None) -> bool:
    """語の出現ではなく、現在の選択肢に対する明示的な選択だけを確定する。"""
    if answer is None or not answer.option_ids or answer.other_text.strip():
        return False
    selected = set(answer.option_ids)
    return (
        len(selected) == len(answer.option_ids)
        and selected <= {option.id for option in clarification.options}
        and (clarification.multiple or len(selected) == 1)
    )


def suggest_clarification(
    payload: Mapping[str, object], question: str, *, answers: Sequence[ClarificationAnswer] = ()
) -> tuple[Mapping[str, object], RuleClarification] | None:
    """質問に一致したルールのうち、確認を持つ最初の 1 件(質問で既に答えているものは除く)。"""
    found = unanswered_rule_clarifications(payload, question, answers=answers)
    return found[0] if found else None


def unanswered_rule_clarifications(
    payload: Mapping[str, object], question: str, *, answers: Sequence[ClarificationAnswer] = ()
) -> list[tuple[Mapping[str, object], RuleClarification]]:
    """一致したルールの未回答の確認事項。確認の提示と前提の明示で同じ判定を使う。"""
    if not get_settings().rag_runtime_rules_confirmation_enabled:
        return []
    found: list[tuple[Mapping[str, object], RuleClarification]] = []
    by_id = {str(rule.get("id", "")): rule for rule in _rules(payload)}
    for matched in preview_runtime_knowledge(payload, question).rules_for_stage("confirmation"):
        rule = by_id.get(matched.rule_id)
        if rule is None:
            continue
        clarification = rule_clarification(rule)
        selections = [answer for answer in answers if answer.rule_id == matched.rule_id]
        answer = selections[0] if len(selections) == 1 else None
        if clarification is not None and not _already_answered(clarification, answer):
            found.append((rule, clarification))
    return found


def rule_matches(payload: Mapping[str, object], question: str, rule_id: str) -> bool:
    """質問がそのルールに一致するか（MCP の答えを、一致する質問にだけ当てる。#2117）。"""
    if not get_settings().rag_runtime_rules_confirmation_enabled:
        return False
    return any(
        matched.rule_id == rule_id
        for matched in preview_runtime_knowledge(payload, question).matched_rules
    )


def rule_answer(
    payload: Mapping[str, object], rule_id: str, value: str
) -> ClarificationAnswer | None:
    """MCP の ``conditions`` の値（選んだ選択肢の表示名と自由入力を「、」で並べた文）を答えにする。

    Agent は確認の答えを表示名で返す（#2117）。表示名と同じ部分を選択肢、残りを自由入力にする。
    ルール・確認が無い、値が空なら None。
    """
    rule = next((item for item in _rules(payload) if str(item.get("id", "")) == rule_id), None)
    clarification = rule_clarification(rule) if rule is not None else None
    parts = [part.strip() for part in value.split("、") if part.strip()]
    if clarification is None or not parts:
        return None
    # ponytail: 「、」を含む表示名は自由入力として渡る（前提の文には残る）。
    by_label = {option.label: option.id for option in clarification.options}
    picked = list(dict.fromkeys(by_label[part] for part in parts if part in by_label))
    if not clarification.multiple:
        picked = picked[:1]
    other = "、".join(part for part in parts if by_label.get(part) not in picked)
    return ClarificationAnswer(rule_id=rule_id, option_ids=picked, other_text=other[:500])


SectionPages = Mapping[tuple[str, str], tuple[int | None, int | None]]


def _answer_clarification(
    payload: Mapping[str, object], answer: ClarificationAnswer
) -> RuleClarification | None:
    rule = next(
        (item for item in _rules(payload) if str(item.get("id", "")) == answer.rule_id), None
    )
    return rule_clarification(rule) if rule is not None else None


def clarification_record(
    payload: Mapping[str, object], rule_id: str, answer: ClarificationAnswer | None
) -> dict[str, object] | None:
    """会話に残す「回答前に確認した内容」（#1839）。``answer`` が None なら飛ばした記録。

    確認事項と選んだ選択肢の名前は保存済みのルールから引き直す。ルールが見つからなければ None。
    """
    rule = next((item for item in _rules(payload) if str(item.get("id", "")) == rule_id), None)
    clarification = rule_clarification(rule) if rule is not None else None
    if clarification is None:
        return None
    if answer is None:
        return {"outcome": "skipped", "entries": [{"prompt": clarification.question}]}
    labels = [option.label for option in clarification.options if option.id in answer.option_ids]
    entry: dict[str, object] = {
        "prompt": clarification.question,
        "selected": labels,
        "other_text": answer.other_text.strip(),
    }
    if not clarification.options:
        # 選択肢の無い確認事項の答えに「その他: 」を付けない（#3410）。
        entry["free_text_only"] = True
    return {"outcome": "answered", "entries": [entry]}


def clarification_document_ids(
    payload: Mapping[str, object], answer: ClarificationAnswer
) -> set[str]:
    """利用者が選んだ選択肢の章節がある文書(今のページを読み直す対象。#721)。"""
    clarification = _answer_clarification(payload, answer)
    if clarification is None:
        return set()
    return {
        section.document_id
        for option in clarification.options
        if option.id in answer.option_ids
        for section in option.sections
    }


def resolve_clarification(
    payload: Mapping[str, object],
    answer: ClarificationAnswer,
    current_pages: SectionPages | None = None,
) -> tuple[AnswerScope, str] | None:
    """利用者の確認の回答を、回答の前提(AnswerScope)と page_ranges の値にする。

    ルール・選択肢は保存済みの payload から引き直す(画面が送った範囲をそのまま信じない)。
    章節のページは ``current_pages``((文書 ID, 章節 ID) → 今のページ)を使い、章節が今は無い
    ときだけ確認を保存したときのページを使う(#721)。ルール・選択肢が見つからなければ None。
    """
    clarification = _answer_clarification(payload, answer)
    if clarification is None:
        return None
    options = [option for option in clarification.options if option.id in answer.option_ids]
    if len(options) != len(set(answer.option_ids)) or (
        len(options) > 1 and not clarification.multiple
    ):
        return None
    # 「その他（自由入力）」は 3 製品で常に出す（#1839）。`allow_other` は古い保存の値で、読まない。
    other = answer.other_text.strip()
    if not options and not other:
        return None
    pages = current_pages or {}

    def current(section: ClarificationSection) -> ClarificationSection:
        found = pages.get((section.document_id, section.section_id))
        if found is None:
            return section
        return section.model_copy(update={"page_start": found[0], "page_end": found[1]})

    sections = [current(section) for option in options for section in option.sections]
    labels = [
        f"「{section.document_name or section.document_id}」の「{section.title}」"
        + (_pages_label(section.page_start, section.page_end))
        for section in sections
    ]
    lines = [f"確認の質問: {clarification.question}"]
    if options:
        lines.append("選んだ答え: " + "、".join(option.label for option in options))
    lines.extend(f"前提: {option.premise}" for option in options if option.premise.strip())
    if other:
        lines.append(f"その他（利用者の入力）: {other}")
    if labels:
        lines.append("対象範囲: " + "、".join(labels))
    page_ranges = format_page_ranges(
        [
            PageRange(
                document_id=section.document_id,
                page_start=section.page_start,
                page_end=section.page_end,
            )
            for section in sections
        ]
    )
    scope = AnswerScope(
        context="\n".join(lines),
        search_terms=tuple(term for option in options for term in option.search_terms),
        label="、".join(labels),
        confirmed_premises=(
            (
                clarification.question,
                "、".join([*(option.label for option in options), *([other] if other else [])]),
            ),
        ),
    )
    return scope, page_ranges


def _pages_label(start: int | None, end: int | None) -> str:
    if start is None:
        return ""
    return f"（p.{start}）" if end is None or end == start else f"（p.{start}–{end}）"
