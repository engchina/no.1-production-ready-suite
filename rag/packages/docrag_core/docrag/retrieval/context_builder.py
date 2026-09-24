"""検索候補から回答生成向けの context と evidence tree を構築する。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from docrag.chunking import CHILD_CHUNK_LEVEL, PARENT_CHUNK_LEVEL
from docrag.retrieval.operation_context import mentions_operation_section, operation_labels, operation_target_score
from docrag.retrieval.task_contract import task_contract
from docrag.retrieval.evidence_selection import evidence_relevance
from docrag.knowledge.document_metadata import document_context_text
from docrag.retrieval.metadata_context import answer_metadata_context, document_context_key, with_child_contexts
from docrag.parsing.decorative_pictures import (
    DECORATIVE_VISUAL_ROLE,
    INLINE_ICON_VISUAL_ROLE,
    visual_role_from_ref,
)


DEFAULT_MAX_RECORD_TEXT_CHARS = 1200
DEFAULT_MIN_SNIPPET_CHARS = 80
CHILD_EVIDENCE_ROLE_PRIORITY = {
    "retrieved_anchor": 0,
    "neighbor_context": 1,
    "same_page_context": 2,
    "parent_context": 3,
    "operation_context": 4,
    "visual_context": 4,
}
ChildEvidenceRole = Literal[
    "retrieved_anchor",
    "neighbor_context",
    "same_page_context",
    "parent_context",
    "operation_context",
    "visual_context",
]
ParentEvidenceRole = Literal["synthesis_parent", "child_fallback"]

VISUAL_QUESTION_TERMS = (
    "画像",
    "図",
    "グラフ",
    "チャート",
    "スクリーンショット",
    "画面",
    "ボタン",
    "アイコン",
    "表示",
    "赤枠",
    "青枠",
    "黄色",
    "ハイライト",
    "image",
    "picture",
    "figure",
    "chart",
    "graph",
    "screenshot",
    "screen",
    "button",
    "icon",
    "visible",
)


@dataclass(frozen=True)
class ContextEvidence:
    """回答 context に採用した record 単位の根拠を表します。"""
    record: Any
    role: Literal["primary", "supporting"]
    reason: str
    anchor_child_ids: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "id": _record_id(self.record),
            "chunk_uid": str(getattr(self.record, "chunk_uid", "") or ""),
            "chunk_id": str(getattr(self.record, "chunk_id", "") or ""),
            "chunk_level": str(getattr(self.record, "chunk_level", "") or ""),
            "parent_chunk_uid": str(getattr(self.record, "parent_chunk_uid", "") or ""),
            "role": self.role,
            "reason": self.reason,
            "anchor_child_ids": list(self.anchor_child_ids),
            "citation": str(getattr(self.record, "citation", "") or ""),
        }


@dataclass(frozen=True)
class ContextChildEvidence:
    """回答 context に採用した child chunk の根拠を表します。"""
    record: Any
    role: ChildEvidenceRole
    reason: str
    retrieval_rank: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "id": _record_id(self.record),
            "chunk_uid": str(getattr(self.record, "chunk_uid", "") or ""),
            "chunk_id": str(getattr(self.record, "chunk_id", "") or ""),
            "chunk_level": str(getattr(self.record, "chunk_level", "") or ""),
            "parent_chunk_uid": str(getattr(self.record, "parent_chunk_uid", "") or ""),
            "role": self.role,
            "reason": self.reason,
            "retrieval_rank": self.retrieval_rank,
            "citation": str(getattr(self.record, "citation", "") or ""),
        }


@dataclass(frozen=True)
class ContextParentEvidence:
    """child 根拠を束ねた parent chunk 単位の根拠を表します。"""
    record: Any
    role: ParentEvidenceRole
    reason: str
    children: tuple[ContextChildEvidence, ...] = ()
    evidence_score: float = 0.0

    @property
    def anchor_child_ids(self) -> tuple[str, ...]:
        """parent evidence の anchor となる child chunk ID を返します。"""
        return tuple(
            str(getattr(child.record, "chunk_id", "") or "")
            for child in self.children
            if child.role == "retrieved_anchor" and str(getattr(child.record, "chunk_id", "") or "")
        )

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "id": _record_id(self.record),
            "chunk_uid": str(getattr(self.record, "chunk_uid", "") or ""),
            "chunk_id": str(getattr(self.record, "chunk_id", "") or ""),
            "chunk_level": str(getattr(self.record, "chunk_level", "") or ""),
            "parent_chunk_uid": str(getattr(self.record, "parent_chunk_uid", "") or ""),
            "role": self.role,
            "reason": self.reason,
            "parent_evidence_score": self.evidence_score,
            "anchor_child_ids": list(self.anchor_child_ids),
            "citation": str(getattr(self.record, "citation", "") or ""),
            "children": [child.to_payload() for child in self.children],
        }


@dataclass
class _ChildEvidenceBuilder:
    record: Any
    role: ChildEvidenceRole
    reason: str
    retrieval_rank: int | None = None


@dataclass
class _ParentEvidenceBuilder:
    record: Any
    role: ParentEvidenceRole
    reason: str
    children: dict[str, _ChildEvidenceBuilder] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextBundle:
    """回答に使う parent records、primary/supporting evidence、検索 trace を保持します。

    `text` は採用した parent ごとの可読 trace（文書背景・章・親本文・child 一覧）で、空判定・
    評価ツール（`domain_keyword_eval` / `rag_eval`）・SDK が読む。回答 prompt には渡さない。
    prompt 本文は `evidence_spans` が `records` の親本文から自前の予算で切り出す。
    """
    records: list[Any]
    text: str
    evidence: tuple[ContextEvidence, ...] = ()
    evidence_tree: tuple[ContextParentEvidence, ...] = ()
    status: Literal["ready", "insufficient"] = "ready"
    insufficient_reason: str = ""

    @property
    def ready(self) -> bool:
        """後続処理に必要な準備が済んでいるかを返します。"""
        return self.status == "ready" and bool(self.records and self.text.strip())

    @property
    def primary_evidence(self) -> tuple[ContextEvidence, ...]:
        """回答に最も直接使う primary evidence を返します。"""
        return tuple(item for item in self.evidence if item.role == "primary")

    @property
    def supporting_evidence(self) -> tuple[ContextEvidence, ...]:
        """primary 以外の補助 evidence を返します。"""
        return tuple(item for item in self.evidence if item.role == "supporting")

    def trace_payload(self) -> dict[str, Any]:
        """context 構築の根拠を answer trace 用 payload に変換します。"""
        return {
            "status": self.status,
            "insufficient_reason": self.insufficient_reason,
            "evidence": [item.to_payload() for item in self.evidence],
            "evidence_tree": [item.to_payload() for item in self.evidence_tree],
        }


# 検索上位から起点（retrieved_anchor）に採る child の、同じ親あたりの既定上限 (#669)。
MAX_ANCHORS_PER_PARENT = 2


@dataclass(frozen=True)
class ContextBuildRequest:
    """context 構築時の候補、件数上限、近傍展開条件をまとめます。"""
    question: str
    ranked_children: Sequence[Any]
    active_records: Sequence[Any]
    top_k: int
    neighbor_child_count: int
    max_records: int
    max_chars: int
    metadata: dict[str, Any] = field(default_factory=dict)
    # 主検索枠を保持したまま追加できる同一操作の親数。0は既存の総数制限を維持する。
    support_record_limit: int = 0
    # 検索上位から起点（retrieved_anchor）に採る child の、同じ親あたりの上限 (#669)。
    # Settings.max_anchors_per_parent（DOCRAG_MAX_ANCHORS_PER_PARENT）から渡す (#889)。1 未満は 1。
    max_anchors_per_parent: int = MAX_ANCHORS_PER_PARENT


def build_chunk_context_bundle(request: ContextBuildRequest) -> ContextBundle:
    """検索済み child chunk から parent/neighbor を展開して context bundle を作ります。"""
    if request.max_records <= 0:
        return ContextBundle(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="context record budget is zero",
        )

    active_records = [record for record in request.active_records if _record_active(record)]
    active_ids = {record_context_key(record) for record in active_records if record_context_key(record)}
    children = [
        record
        for record in active_records
        if str(getattr(record, "chunk_level", "") or "") == CHILD_CHUNK_LEVEL
    ]
    if not children:
        return ContextBundle(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="no active child chunks are available",
        )

    ranked_children = [
        record
        for record in request.ranked_children
        if record_context_key(record) in active_ids
        and str(getattr(record, "chunk_level", "") or "") == CHILD_CHUNK_LEVEL
    ]
    if not ranked_children:
        return ContextBundle(
            records=[],
            text="",
            status="insufficient",
            insufficient_reason="retrieval returned no active child chunks",
        )

    parents = {
        record_context_key(record): record
        for record in active_records
        if str(getattr(record, "chunk_level", "") or "") == PARENT_CHUNK_LEVEL
        and record_context_key(record)
    }
    children_by_parent: dict[str, list[Any]] = {}
    for child in sorted(
        children,
        key=lambda record: (_record_engine(record), _record_chunk_seq(record)),
    ):
        children_by_parent.setdefault(_child_sibling_context_key(child), []).append(child)

    groups: list[_ParentEvidenceBuilder] = []
    groups_by_id: dict[str, _ParentEvidenceBuilder] = {}

    def group_for_child(child: Any) -> _ParentEvidenceBuilder | None:
        parent_key = _record_parent_context_key(child)
        parent = parents.get(parent_key) if parent_key else None
        record = parent or child
        group_id = record_context_key(record)
        if not group_id:
            return None
        group = groups_by_id.get(group_id)
        if group is not None:
            return group
        group = _ParentEvidenceBuilder(
            record=record,
            role="synthesis_parent" if parent is not None else "child_fallback",
            reason="parent_of_retrieved_child" if parent is not None else "retrieved_child_without_parent",
        )
        groups_by_id[group_id] = group
        groups.append(group)
        return group

    def add_child(
        group: _ParentEvidenceBuilder,
        child: Any,
        role: ChildEvidenceRole,
        reason: str,
        *,
        retrieval_rank: int | None = None,
    ) -> None:
        child_id = record_context_key(child)
        if not child_id:
            return
        existing = group.children.get(child_id)
        if existing is None:
            group.children[child_id] = _ChildEvidenceBuilder(
                record=child,
                role=role,
                reason=reason,
                retrieval_rank=retrieval_rank,
            )
            return
        if _child_role_priority(role) < _child_role_priority(existing.role):
            existing.role = role
            existing.reason = reason
        if retrieval_rank is not None and (
            existing.retrieval_rank is None or retrieval_rank < existing.retrieval_rank
        ):
            existing.retrieval_rank = retrieval_rank

    top_k = max(1, int(request.top_k or 1))
    max_anchors_per_parent = max(1, int(request.max_anchors_per_parent or 1))
    anchors_by_parent: dict[tuple[Any, ...], int] = {}
    anchor_count = 0
    for retrieval_rank, child in enumerate(ranked_children, start=1):
        if anchor_count >= top_k:
            break
        group = group_for_child(child)
        if group is None:
            continue
        # 同じ親の child は起点を最大 MAX_ANCHORS_PER_PARENT 件に抑える。親が 6,000 字だと 1 親に child が
        # 6 件以上あり、上位 top_k が 2〜3 の機能で埋まって別の要求の節（複数要求の問い合わせの片方）が
        # 入らない。親の本文は起点 1 件で全 child が入るため、同じ親の起点を増やしても根拠は増えない (#669)。
        parent_key = _child_sibling_context_key(child)
        if anchors_by_parent.get(parent_key, 0) >= max_anchors_per_parent:
            continue
        anchors_by_parent[parent_key] = anchors_by_parent.get(parent_key, 0) + 1
        anchor_count += 1
        siblings = children_by_parent.get(parent_key, [])
        add_child(
            group,
            child,
            "retrieved_anchor",
            "top_retrieved_child",
            retrieval_rank=retrieval_rank,
        )
        for neighbor in _chunk_neighbors(
            child,
            siblings,
            request.neighbor_child_count,
        ):
            add_child(group, neighbor, "neighbor_context", "adjacent_child")
        for neighbor in _same_page_chunk_neighbors(child, siblings, request.neighbor_child_count):
            add_child(group, neighbor, "same_page_context", "same_page_child")
        if group.role == "synthesis_parent":
            for sibling in siblings:
                add_child(group, sibling, "parent_context", "same_parent_child")

    ranked_groups = sorted(groups, key=_parent_group_sort_key)
    # 同一parentだけでは、スクリーンショットと別parentの操作説明が切り離される。
    # 検索済み主根拠は落とさず、明示された補助枠または空き枠を前後の操作説明に使う。
    # 上限まで候補がある場合も、既存の同一操作を並べてbatch境界の分断を減らす。
    primary = ranked_groups[:request.max_records]
    support_limit = min(3, max(0, request.max_records - len(primary), request.support_record_limit), max(0, request.neighbor_child_count))
    selected_ids = {record_context_key(group.record) for group in primary}
    # 全候補を集めてから機能別に選ぶ。上位画面が補助枠を独占しない。
    candidates = {}
    for group in primary:
        for parent in _operation_parent_neighbors(group.record, list(parents.values())):
            key = record_context_key(parent)
            if key not in selected_ids:
                candidates[key] = parent
    visual_ids = set()
    for group in primary:
        for parent in _visual_context_parents(group.record, list(parents.values())):
            key = record_context_key(parent)
            if key not in selected_ids:
                candidates[key] = parent
                visual_ids.add(key)
    # 上位に別機能だけがある場合も、同じ文書・章の編集対象見出しを持つ
    # 候補を独立した機能として提示する。同一操作とみなして連結はしない。
    scopes = {(_record_scope_context_key(group.record), str(getattr(group.record, "chunk_uid", "") or "").partition(":")[0]) for group in primary}
    chapters = {label.split("-(", 1)[0] for group in primary for label in _operation_labels(group.record) if "-(" in label}
    alternative_ids = set()
    for parent in parents.values() if task_contract(request.question)["goal"] == "procedure" else ():
        labels = _operation_labels(parent)
        if ((_record_scope_context_key(parent), str(getattr(parent, "chunk_uid", "") or "").partition(":")[0]) not in scopes or len(labels) != 1
                or not any("-(" in label and label.split("-(", 1)[0] in chapters for label in labels)
                or operation_target_score(request.question, parent) <= 0):
            continue
        for candidate in [parent, *_operation_parent_neighbors(parent, list(parents.values()))]:
            key = record_context_key(candidate)
            if key not in selected_ids:
                candidates[key] = candidate
                alternative_ids.add(key)
    support = []
    covered: set[tuple[str, str]] = set()
    target_functions = {(_record_scope_context_key(p), label) for p in candidates.values()
                        if operation_target_score(request.question, p) >= 80 for label in _operation_labels(p)}
    while candidates and len(support) < support_limit:
        def candidate_priority(parent):
            labels = {(_record_scope_context_key(parent), s) for s in _operation_labels(parent)}
            text = str(getattr(parent, "text", "") or "")
            entry_bonus = 100 if labels & target_functions and mentions_operation_section(text) else 0
            return (record_context_key(parent) in visual_ids, operation_target_score(request.question, parent) > 0 or bool(entry_bonus), bool(labels - covered),
                    operation_target_score(request.question, parent) + entry_bonus + evidence_relevance(request.question, text),
                    -_record_page(parent), record_context_key(parent))
        parent = max(candidates.values(), key=candidate_priority)
        key = record_context_key(parent)
        del candidates[key]
        covered.update((_record_scope_context_key(parent), s) for s in _operation_labels(parent))
        reason = "visual_context_source" if key in visual_ids else "alternative_operation_context" if key in alternative_ids else "same_operation_context"
        neighbor = _ParentEvidenceBuilder(record=parent, role="synthesis_parent", reason=reason)
        for child in children_by_parent.get(key, []):
            add_child(neighbor, child, "visual_context" if key in visual_ids else "operation_context",
                      "explicit_visual_context_source" if key in visual_ids else "same_operation_across_parent")
        support.append(neighbor)
        selected_ids.add(key)
    selected = primary + support
    if request.neighbor_child_count > 0:
        remaining = {record_context_key(group.record): group for group in selected}
        ordered = []
        for group in selected:
            key = record_context_key(group.record)
            if key not in remaining:
                continue
            ordered.append(remaining.pop(key))
            for neighbor in _operation_parent_neighbors(group.record, [entry.record for entry in selected]):
                adjacent = remaining.pop(record_context_key(neighbor), None)
                if adjacent is not None:
                    ordered.append(adjacent)
        selected = ordered
    evidence_tree = _finalize_parent_evidence(selected)
    return _context_bundle_from_parent_evidence(evidence_tree, max_chars=request.max_chars)


def _operation_labels(record: Any) -> set[str]:
    """共通の機能見出し規則を使い、別画面の隣接展開を防ぐ。"""
    return operation_labels(record)


def _visual_context_parents(anchor: Any, parents: Sequence[Any], *, include_self: bool = False) -> list[Any]:
    """画像に実際に添付した同一版の本文を、明示的な原文 ID から補完する。

    同じページや類似見出しだけでは関連付けない。部分表示、別解析版、別ファイル、
    別チャンク版は除外する。操作の同一性は保証せず、回答側で条件を照合する。
    件数は呼出し側の共通補助枠で制限し、補完先から再帰的には展開しない。
    include_selfは同一parent内の画像・本文の明示対応を検査する場合だけ指定する。
    """
    version = str(getattr(anchor, "chunk_uid", "") or "").partition(":")[0]
    if not version or not getattr(anchor, "source_run_id", ""):
        return []
    wanted = set()
    for ref in getattr(anchor, "source_record_refs", ()):
        for context in ref.get("vision_context_record_refs", ()):
            if (context.get("partially_visible") is False
                    and context.get("category") in {"Text", "List-item"}
                    and context.get("record_id") and context.get("page")):
                wanted.add((context["record_id"], context["page"]))
    if not wanted:
        return []
    return [parent for parent in parents
            if _record_scope_context_key(parent) == _record_scope_context_key(anchor)
            and str(getattr(parent, "chunk_uid", "") or "").partition(":")[0] == version
            and (include_self or record_context_key(parent) != record_context_key(anchor))
            and any((ref.get("record_id"), ref.get("page")) in wanted
                    and ref.get("category") in {"Text", "List-item"}
                    for ref in getattr(parent, "source_record_refs", ()))]


def _operation_parent_neighbors(anchor: Any, parents: Sequence[Any]) -> list[Any]:
    """同一ファイル・解析/チャンク版で操作見出しが一致する前後2ページを返す。

    見出しが無い場合は推測して展開しない。同一ページだけを根拠に別画面を
    混ぜず、近いページ・chunkから採用する。呼び出し側が総件数を制限する。
    """
    labels = _operation_labels(anchor)
    if not labels or sum(bool(re.search(r"[-－—―ー]\([0-9]+\)", label)) for label in labels) > 1:
        return []
    page = _record_page(anchor)
    end = int(getattr(anchor, "page_end", 0) or page)
    scope = _record_scope_context_key(anchor)
    uid = str(getattr(anchor, "chunk_uid", "") or "").partition(":")[0]
    candidates = []
    for parent in parents:
        if record_context_key(parent) == record_context_key(anchor) or _record_scope_context_key(parent) != scope:
            continue
        other_uid = str(getattr(parent, "chunk_uid", "") or "").partition(":")[0]
        if uid and other_uid and uid != other_uid:
            continue
        start = _record_page(parent)
        stop = int(getattr(parent, "page_end", 0) or start)
        distance = max(0, start - end, page - stop)
        other_labels = _operation_labels(parent)
        if sum(bool(re.search(r"[-－—―ー]\([0-9]+\)", label)) for label in other_labels) > 1:
            continue
        if page > 0 and start > 0 and distance <= 2 and labels.intersection(other_labels):
            candidates.append((distance, abs(_record_chunk_seq(parent) - _record_chunk_seq(anchor)), record_context_key(parent), parent))
    return [item[-1] for item in sorted(candidates, key=lambda item: item[:3])]


def context_bundle_from_parent_evidence(
    evidence_tree: Sequence[ContextParentEvidence],
    *,
    max_chars: int,
) -> ContextBundle:
    """parent evidence tree から LLM 用 context bundle を組み立てます。"""
    return _context_bundle_from_parent_evidence(evidence_tree, max_chars=max_chars)


def _child_role_priority(role: str) -> int:
    return CHILD_EVIDENCE_ROLE_PRIORITY.get(role, 99)


def _finalize_parent_evidence(
    groups: Sequence[_ParentEvidenceBuilder],
) -> tuple[ContextParentEvidence, ...]:
    finalized: list[ContextParentEvidence] = []
    for group in groups:
        children = tuple(
            ContextChildEvidence(
                record=item.record,
                role=item.role,
                reason=item.reason,
                retrieval_rank=item.retrieval_rank,
            )
            for item in sorted(
                group.children.values(),
                key=lambda child: (
                    _record_engine(child.record),
                    _record_chunk_seq(child.record),
                    _record_id(child.record),
                ),
            )
        )
        finalized.append(
            ContextParentEvidence(
                record=group.record,
                role=group.role,
                reason=group.reason,
                children=children,
                evidence_score=_parent_group_evidence_score(group),
            )
        )
    return tuple(finalized)


def _parent_group_sort_key(group: _ParentEvidenceBuilder) -> tuple[Any, ...]:
    anchor_ranks = [
        child.retrieval_rank
        for child in group.children.values()
        if child.role == "retrieved_anchor" and child.retrieval_rank is not None
    ]
    best_rank = min(anchor_ranks) if anchor_ranks else 1_000_000
    return (
        -_parent_group_evidence_score(group),
        best_rank,
        _record_page(group.record),
        _record_chunk_seq(group.record),
        record_context_key(group.record),
    )


def _parent_group_evidence_score(group: _ParentEvidenceBuilder) -> float:
    anchor_children = [child for child in group.children.values() if child.role == "retrieved_anchor"]
    if not anchor_children:
        return 0.0
    best_rank = min(
        child.retrieval_rank
        for child in anchor_children
        if child.retrieval_rank is not None
    ) if any(child.retrieval_rank is not None for child in anchor_children) else 1_000_000
    rank_score = 1.0 / max(1, best_rank)
    anchor_bonus = min(max(0, len(anchor_children) - 1), 6) * 0.35
    support_bonus = min(
        sum(1 for child in group.children.values() if child.role in {"neighbor_context", "same_page_context"}),
        6,
    ) * 0.04
    parent_context_bonus = min(
        sum(1 for child in group.children.values() if child.role == "parent_context"),
        8,
    ) * 0.01
    rerank_score = max(
        (_metadata_float(child.record, "rerank", "relevance_score") for child in anchor_children),
        default=0.0,
    )
    rrf_score = max(
        (_metadata_float(child.record, "adb_hybrid", "rrf_score") for child in anchor_children),
        default=0.0,
    )
    profile_score = max(
        (_metadata_float(child.record, "adb_hybrid", "profile_score") for child in anchor_children),
        default=0.0,
    )
    return round(
        rank_score
        + anchor_bonus
        + support_bonus
        + parent_context_bonus
        + rerank_score
        + (rrf_score * 8.0)
        + min(profile_score * 0.05, 0.5),
        6,
    )


def _metadata_float(record: Any, namespace: str, key: str) -> float:
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return 0.0
    bucket = metadata.get(namespace)
    if not isinstance(bucket, dict):
        return 0.0
    try:
        value = float(bucket.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return value


def _context_bundle_from_parent_evidence(
    evidence_tree: Sequence[ContextParentEvidence],
    *,
    max_chars: int,
) -> ContextBundle:
    """parent evidence を records・evidence・trace text にまとめる。

    max_chars は親本文（`record.text`）1 件あたりの上限で、trace の前置きや child 一覧の分は数えない。
    回答 prompt は `evidence_spans` が自前の予算で親本文から抜粋するため、ここでは prompt の分割や
    予算配分はしない。親本文が上限を超える候補は chunking の parent size を見直す合図として除外し、
    全候補が超えていれば insufficient にする。
    """
    if max_chars <= 0:
        return ContextBundle(
            records=[],
            text="",
            evidence=(),
            evidence_tree=(),
            status="insufficient",
            insufficient_reason="context character budget is zero",
        )
    kept = tuple(item for item in evidence_tree if len(str(getattr(item.record, "text", "") or "")) <= max_chars)
    if evidence_tree and not kept:
        return ContextBundle(
            records=[],
            text="",
            evidence=(),
            evidence_tree=(),
            status="insufficient",
            insufficient_reason=(
                "parent chunk exceeds synthesis budget; re-run chunking with a smaller parent size: "
                f"{_record_id(evidence_tree[0].record)}"
            ),
        )
    evidence_tree = kept
    sections = []
    seen_documents = set()
    for item in evidence_tree:
        key = document_context_key(item.record)
        sections.append(_parent_context_section(item, include_first_page=key not in seen_documents,
                                                metadata_max_chars=min(1200, max_chars // 10)))
        seen_documents.add(key)
    text = "\n\n".join(sections).strip()
    if not text:
        return ContextBundle(
            records=[],
            text="",
            evidence=(),
            evidence_tree=(),
            status="insufficient",
            insufficient_reason="no parent context text fits within the token budget",
        )

    records = [item.record for item in evidence_tree]
    evidence = tuple(
        ContextEvidence(
            record=item.record,
            role="supporting" if item.reason in {"same_operation_context", "visual_context_source"} else "primary",
            reason=item.reason,
            anchor_child_ids=item.anchor_child_ids,
        )
        for item in evidence_tree
    )
    return ContextBundle(
        records=records,
        text=text,
        evidence=evidence,
        evidence_tree=evidence_tree,
    )


def _parent_context_section(item: ContextParentEvidence, *, include_first_page: bool = True,
                            metadata_max_chars: int = 1200) -> str:
    record = with_child_contexts(item.record, [child.record for child in item.children])
    lines = [
        _context_prefix(record, include_first_page=include_first_page, metadata_max_chars=metadata_max_chars).rstrip(),
        f"Context role: {item.reason}",
    ]
    anchor_ids = item.anchor_child_ids
    if anchor_ids:
        lines.append("Retrieved anchor children: " + ", ".join(anchor_ids))
    lines.append("Synthesis text:")
    lines.append(str(getattr(record, "text", "") or "").strip())
    if item.children:
        lines.append("Children:")
        for child in item.children:
            rank = f" rank={child.retrieval_rank}" if child.retrieval_rank is not None else ""
            lines.append(
                "- "
                f"{_record_id(child.record)} / {child.role}{rank} / "
                f"{_record_page_label(child.record)} / {_child_context_preview(child.record)}"
            )
    return "\n".join(line for line in lines if line is not None).strip()


def _child_context_preview(record: Any) -> str:
    return trim_context_text(str(getattr(record, "text", "") or ""), 180)


def _record_page_label(record: Any) -> str:
    page = _record_page(record)
    page_end = _record_page_end(record)
    if not page:
        return "p.-"
    return f"p.{page}" if page == page_end else f"p.{page}-{page_end}"


def context_bundle_from_records(
    records: Sequence[Any],
    *,
    max_chars: int,
    evidence: Sequence[ContextEvidence] = (),
    max_record_text_chars: int = DEFAULT_MAX_RECORD_TEXT_CHARS,
) -> ContextBundle:
    """legacy record 列から context bundle を作ります。"""
    selected: list[Any] = []
    lines: list[str] = []
    used_chars = 0
    seen_documents = set()
    for record in records:
        key = document_context_key(record)
        prefix = _context_prefix(record, include_first_page=key not in seen_documents,
                                 metadata_max_chars=min(1200, max_chars // 10))
        remaining = max_chars - used_chars - len(prefix) - 1
        if remaining <= DEFAULT_MIN_SNIPPET_CHARS and lines:
            break
        snippet = trim_context_text(
            str(getattr(record, "text", "") or ""),
            min(max_record_text_chars, max(DEFAULT_MIN_SNIPPET_CHARS, remaining)),
        )
        line = f"{prefix}{snippet}"
        if lines and used_chars + len(line) + 1 > max_chars:
            break
        selected.append(record)
        seen_documents.add(key)
        lines.append(line)
        used_chars += len(line) + 1

    if not selected or not "\n".join(lines).strip():
        return ContextBundle(
            records=[],
            text="",
            evidence=(),
            status="insufficient",
            insufficient_reason="no context text fits within the token budget",
        )

    selected_ids = {_record_id(record) for record in selected}
    selected_evidence = tuple(item for item in evidence if _record_id(item.record) in selected_ids)
    if not selected_evidence:
        selected_evidence = tuple(
            ContextEvidence(record=record, role="primary", reason="ranked_context")
            for record in selected
        )
    return ContextBundle(
        records=selected,
        text="\n".join(lines),
        evidence=selected_evidence,
    )


def should_include_image_evidence(
    question: str,
    *,
    question_plan: Any | None = None,
    inquiry_conditions: Any | None = None,
    records: Sequence[Any] = (),
) -> bool:
    """質問と根拠の性質から画像 evidence を prompt 添付すべきか判定します。"""
    if not any(record_has_image_evidence(record) for record in records):
        return False
    if bool(getattr(question_plan, "requires_visual_context", False)):
        return True
    if bool(getattr(inquiry_conditions, "requires_visual_evidence", False)):
        return True
    if _looks_visual_question(question):
        return True
    # 操作に関する問いは「画像」と明記しなくても、同じ根拠のボタン配置が必要になる。
    operation_question = any(term in question.casefold() for term in (
        "入力", "選択", "印字", "出力", "登録", "一覧", "操作", "設定", "できない", "該当", "how to", "select", "print"))
    return operation_question and any(
        record_has_image_evidence(record) and any(term in str(getattr(record, "text", "")) for term in ("ボタン", "アイコン", "画面", "click", "button"))
        for record in records)


def record_has_image_evidence(record: Any) -> bool:
    """record metadata に利用可能な画像 evidence があるかを判定します。"""
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    return any(_iter_image_evidence_refs(metadata))


def _iter_image_evidence_refs(metadata: dict[str, Any]):
    raw_images = metadata.get("image_evidence")
    if isinstance(raw_images, list):
        for raw in raw_images:
            if _is_usable_image_ref(raw):
                yield raw

    table_context = metadata.get("table_context")
    if not isinstance(table_context, list):
        return
    for table in table_context:
        if not isinstance(table, dict):
            continue
        raw_visuals = table.get("visual_evidence")
        if not isinstance(raw_visuals, list):
            continue
        for raw in raw_visuals:
            if _is_usable_image_ref(raw):
                yield raw


def _is_usable_image_ref(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if str(raw.get("raw_type") or "") == "picture_ocr_text":
        return False
    if visual_role_from_ref(raw) in {DECORATIVE_VISUAL_ROLE, INLINE_ICON_VISUAL_ROLE}:
        return False
    if raw.get("rag_excluded"):
        return False
    return bool(str(raw.get("image_id") or raw.get("record_id") or "").strip())


def trim_context_text(value: str, max_length: int) -> str:
    """context 上限に収まるよう文字列を安全に切り詰めます。"""
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(0, max_length - 3)] + "..."


def _context_prefix(record: Any, *, include_first_page: bool = True, metadata_max_chars: int = 1200) -> str:
    citation = str(getattr(record, "citation", "") or "")
    engine_label = str(getattr(record, "engine_label", "") or "")
    category = str(getattr(record, "category", "") or "")
    metadata = getattr(record, "metadata", {})
    document = metadata.get("document") if isinstance(metadata, dict) else None
    details = trim_context_text(document_context_text(document), 500) if isinstance(document, dict) else ""
    # 文書の時点を回答にも渡す。技術ID・品質スコアを根拠本文へ混ぜない。
    if isinstance(document, dict) and document.get("source_uri"):
        source = f"Source URL: {document['source_uri']}"
        # URL の途中切断で誤った出典を生成しない。長い URL は保存 metadata にのみ残す。
        if len(details) + len(source) + 3 <= 700:
            details = " / ".join(part for part in (details, source) if part)
    suffix = f"\nDocument: {details}\n" if details else " "
    context = answer_metadata_context(record, max_chars=metadata_max_chars,
                                      include_first_page=include_first_page, include_document=False)
    return f"[{citation} / {engine_label} / {category}]" + suffix + (context + '\n' if context else '')


def _chunk_neighbors(child: Any, siblings: Sequence[Any], neighbor_child_count: int) -> list[Any]:
    count = max(0, int(neighbor_child_count or 0))
    if count <= 0:
        return []
    index_by_id = {record_context_key(record): index for index, record in enumerate(siblings)}
    index = index_by_id.get(record_context_key(child))
    if index is None:
        return []
    neighbors: list[Any] = []
    for offset in range(1, count + 1):
        before = index - offset
        after = index + offset
        if before >= 0:
            neighbors.append(siblings[before])
        if after < len(siblings):
            neighbors.append(siblings[after])
    return neighbors


def _same_page_chunk_neighbors(
    child: Any,
    children: Sequence[Any],
    neighbor_child_count: int,
) -> list[Any]:
    count = max(0, int(neighbor_child_count or 0))
    if count <= 0:
        return []
    child_pages = set(range(_record_page(child), _record_page_end(child) + 1))
    candidates = [
        record
        for record in children
        if record_context_key(record) != record_context_key(child)
        and child_pages & set(range(_record_page(record), _record_page_end(record) + 1))
    ]
    candidates.sort(
        key=lambda record: (
            abs(_record_chunk_seq(record) - _record_chunk_seq(child)),
            _record_chunk_seq(record),
        )
    )
    return candidates[:count]


def _record_active(record: Any) -> bool:
    metadata = getattr(record, "metadata", None)
    if not isinstance(metadata, dict):
        return True
    value = metadata.get("active")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text not in {"0", "false", "no", "off", "inactive"}


def _record_id(record: Any) -> str:
    return str(
        getattr(record, "id", None)
        or getattr(record, "chunk_id", None)
        or getattr(record, "citation", None)
        or ""
    )


def record_context_key(record: Any) -> str:
    """重複除外に使う record の安定 key を作ります。"""
    chunk_key = _record_chunk_context_key(record)
    if chunk_key:
        return chunk_key
    record_id = _record_id(record)
    if not record_id:
        return ""
    return _scoped_record_context_key("record", record, record_id)


def _record_chunk_context_key(record: Any) -> str:
    chunk_uid = str(getattr(record, "chunk_uid", "") or "").strip()
    if chunk_uid:
        return f"chunk_uid:{chunk_uid}"
    chunk_id = str(getattr(record, "chunk_id", "") or "").strip()
    if not chunk_id:
        return ""
    return _scoped_record_context_key("chunk", record, chunk_id)


def _record_parent_context_key(record: Any) -> str:
    parent_chunk_uid = str(getattr(record, "parent_chunk_uid", "") or "").strip()
    if parent_chunk_uid:
        return f"chunk_uid:{parent_chunk_uid}"
    parent_chunk_id = str(getattr(record, "parent_chunk_id", "") or "").strip()
    if not parent_chunk_id:
        return ""
    return _scoped_record_context_key("chunk", record, parent_chunk_id)


def _child_sibling_context_key(record: Any) -> str:
    return _record_parent_context_key(record) or _record_scope_context_key(record)


def _record_scope_context_key(record: Any) -> str:
    return _scoped_record_context_key("scope", record, "")


def _scoped_record_context_key(kind: str, record: Any, value: str) -> str:
    source_run_id = str(getattr(record, "source_run_id", "") or "")
    source = str(getattr(record, "source", "") or getattr(record, "source_file_name", "") or "")
    engine = _record_engine(record)
    scope_parts = (source_run_id, source, engine)
    if not any(scope_parts):
        return f"{kind}:{value}" if value else kind
    escaped = [part.replace("::", r"\::") for part in (kind, *scope_parts, value)]
    return "::".join(escaped)


def _record_engine(record: Any) -> str:
    return str(getattr(record, "engine", "") or "")


def _record_chunk_seq(record: Any) -> int:
    try:
        return int(getattr(record, "chunk_seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _record_page(record: Any) -> int:
    try:
        return int(getattr(record, "page", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _record_page_end(record: Any) -> int:
    try:
        value = getattr(record, "page_end", None)
        return int(value if value is not None else _record_page(record))
    except (TypeError, ValueError):
        return _record_page(record)


def _looks_visual_question(question: str) -> bool:
    normalized = str(question or "").lower()
    return any(term.lower() in normalized for term in VISUAL_QUESTION_TERMS)
