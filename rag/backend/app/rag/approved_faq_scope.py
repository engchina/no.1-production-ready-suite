"""承認済み FAQ の対象の範囲（対象のナレッジベース）と根拠の状態の判定（#4621）。

FAQ は検索・回答プロファイルの層の知識で、対象は「そのプロファイルの参照 KB の部分集合」。

- 利用者が FAQ の対象の KB を**すべて**使えるときだけ、その FAQ を提示・回答に使う（回答の時点の
  利用者で判定。使えない FAQ は候補から外し、存在も示さない）。
- 根拠の状態は読むたびに今の文書・KB の状態から計算する（文書の削除・KB の付け替え・プロファイルの
  編集・cascade の削除など変化の経路が多く、各所で印を付けると取りこぼすため）。
  - 対象の KB が無い・プロファイルから外れた・引用した文書が無い（削除・対象の KB から外れた）
    → 要確認（停止。提示・回答に使わない）。
  - 引用した文書の版が承認の時点と違う → 原文が更新された（使い続ける。管理画面で示す）。

この module は DB を読まない（状態は呼び出し元が渡す）。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from rag_engine.knowledge.approved_faq import ApprovedFaqApproval, ApprovedFaqRecord

from app.rag.request_context import current_audit_request_context

# 要確認の理由（画面は code で文言を引く）。
REVIEW_NO_TARGET_KNOWLEDGE_BASES = "no_target_knowledge_bases"
REVIEW_KNOWLEDGE_BASE_REMOVED = "knowledge_base_removed"
REVIEW_SOURCE_DOCUMENT_MISSING = "source_document_missing"


@dataclass(frozen=True)
class FaqSourceDocumentState:
    """引用した文書の今の状態（版の印と、所属する KB）。"""

    version: str
    knowledge_base_ids: frozenset[str]


@dataclass(frozen=True)
class ApprovedFaqScopeState:
    """FAQ 1 件の根拠の状態。``review_reasons`` があれば停止（要確認）。"""

    record: ApprovedFaqRecord
    review_reasons: tuple[str, ...] = ()
    source_updated: bool = False

    @property
    def active(self) -> bool:
        return not self.review_reasons


def document_version(indexed_at: datetime | None, superseded_by_document_id: str | None) -> str:
    """文書の版の印。検索に出す版の切替（``indexed_at``）と新しい版の文書の設定（#1248）で変わる。"""
    stamp = indexed_at.astimezone(UTC).isoformat() if indexed_at is not None else ""
    return f"{stamp}|{superseded_by_document_id or ''}"


def evaluate_approved_faq_scope(
    record: ApprovedFaqRecord,
    profile_knowledge_base_ids: Collection[str],
    documents: Mapping[str, FaqSourceDocumentState],
) -> ApprovedFaqScopeState:
    """今のプロファイルの参照 KB と文書の状態から、FAQ が使えるか・原文が更新されたかを決める。"""
    targets = set(record.target_knowledge_base_ids or ())
    reasons: list[str] = []
    if not targets:
        reasons.append(REVIEW_NO_TARGET_KNOWLEDGE_BASES)
    elif not targets <= set(profile_knowledge_base_ids):
        reasons.append(REVIEW_KNOWLEDGE_BASE_REMOVED)
    updated = False
    for source in record.source_documents:
        state = documents.get(source.document_id)
        if state is None or not (state.knowledge_base_ids & targets):
            # 削除された・対象の KB から外れた文書は、FAQ の範囲の根拠にならない。
            if REVIEW_SOURCE_DOCUMENT_MISSING not in reasons:
                reasons.append(REVIEW_SOURCE_DOCUMENT_MISSING)
            continue
        if state.version != source.version:
            updated = True
    return ApprovedFaqScopeState(
        record=record, review_reasons=tuple(reasons), source_updated=updated
    )


def permitted_for_current_user(record: ApprovedFaqRecord) -> bool:
    """利用者が FAQ の対象の KB をすべて使えるか（None は制限なし。対象が空なら使えない）。"""
    targets = record.target_knowledge_base_ids or ()
    if not targets:
        return False
    allowed = current_audit_request_context().allowed_knowledge_base_ids
    return allowed is None or set(targets) <= allowed


def current_approval() -> ApprovedFaqApproval:
    """今の操作者(利用者の UUID。表示名は一覧で引く)と時刻を承認として記録する。"""
    return ApprovedFaqApproval(
        approved_by=current_audit_request_context().user_uuid or "",
        approved_at=datetime.now(UTC).isoformat(),
    )


def default_target_knowledge_base_ids(profile_knowledge_base_ids: Sequence[str]) -> list[str]:
    """管理者が選ばないときの対象（プロファイルの全 KB。最も厳しい）。"""
    return list(dict.fromkeys(profile_knowledge_base_ids))


def validate_target_knowledge_base_ids(
    requested: Sequence[str] | None, profile_knowledge_base_ids: Sequence[str]
) -> list[str]:
    """選んだ対象を検証する（None は既定の全 KB）。空・プロファイルに無い KB は ValueError。"""
    if requested is None:
        targets = default_target_knowledge_base_ids(profile_knowledge_base_ids)
    else:
        targets = list(dict.fromkeys(item.strip() for item in requested if item.strip()))
    if not targets:
        raise ValueError("対象のナレッジベースを 1 つ以上選んでください。")
    unknown = [item for item in targets if item not in set(profile_knowledge_base_ids)]
    if unknown:
        raise ValueError(
            "対象のナレッジベースには、この検索・回答プロファイルのナレッジベースを選んでください。"
        )
    return targets


def cited_knowledge_base_ids(
    document_ids: Sequence[str],
    documents: Mapping[str, FaqSourceDocumentState],
    profile_knowledge_base_ids: Sequence[str],
) -> list[str]:
    """引用した文書を含むプロファイルの参照 KB（プロファイルの順）。無ければ空。"""
    cited = {
        knowledge_base_id
        for document_id in document_ids
        if (state := documents.get(document_id)) is not None
        for knowledge_base_id in state.knowledge_base_ids
    }
    return [item for item in dict.fromkeys(profile_knowledge_base_ids) if item in cited]
