"""文脈の補完: 起点ごとに補い方を 1 つ選び、補った後に重なる根拠を 1 回でまとめる（#5168）。

rerank の後に決まった根拠（起点。検索拡張の根拠 #5162・補いの検索の根拠 #5173 も同じ）ごとに、次の順で補い方を
1 つだけ選ぶ。

1. 章節の対応がある: 章節で補う（``section_expansion``。入力の予算・段の上限は章節の拡張の設定のまま）。
   章節の拡張が予算・時間の上限で所有の章節を読めなかった起点は、次の補い方（親 → 前後）で補う（#5173）。
2. 章節が無い親子分割（small-to-big）: 親の本文で補う。
3. どちらも無い: 前後の chunk を N 件ずつ（``RAG_NEIGHBOR_CHILD_COUNT``。1〜5）。

親子分割の当たった chunk は親を根拠の単位にする（章節の対応があっても同じ。章節の読み込みは章節の親を読むので、
章節で補った結果は親を含む。章節を読めなかったときも当たった chunk の親の本文は残る）。章節の対応がある起点には
前後の chunk を足さない。

補った後に ``consolidate_evidence_tree`` で 1 回だけまとめる。

- 同じ根拠・互いに含む根拠（chunk の集合が他方に含まれる）・重なりの大きい根拠（半分を超える chunk が他方にある）は
  1 つにまとめ、先にある方の位置に置く。
- 補った文脈（前後の chunk・章節の本文）は、同じ文書の最も近い根拠のかたまりに入れる。
- 同じ文書・chunk の組で隣り合う・重なるかたまりは 1 つのかたまりにする。かたまりの中は文書の順。
- 検索拡張の根拠が、ほかの根拠に含まれるか、ほかの根拠のかたまり（その根拠の補った文脈）に入ったときは、
  重ねて置かずに印だけを残す（起点の役割を外す。#5162 の「並びの前後の文脈にある」と同じ扱い）。
- かたまりの並びは、かたまりの中の根拠の最も前の位置（rerank の後の根拠の順。#5165）。record は chunk ごとの
  まま（引用の ID を変えない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, is_dataclass, replace
from typing import Any, Literal

from rag_engine.retrieval.expansion_merge import EXPANSION_EVIDENCE_REASON, MERGE_MARK_KEYS

ContextFillMode = Literal["section", "parent", "neighbors"]
FILL_SECTION: ContextFillMode = "section"
FILL_PARENT: ContextFillMode = "parent"
FILL_NEIGHBORS: ContextFillMode = "neighbors"

# 補った文脈だけのかたまり（起点を持たない）の理由。前後の chunk と章節の本文。
_FILL_REASONS = frozenset({"adjacent_child", "section_expansion"})
# 子の役割の優先（小さいほど強い）。まとめるときに同じ chunk の役割を強い方にする。
_ROLE_PRIORITY = {
    "retrieved_anchor": 0,
    "neighbor_context": 1,
    "same_page_context": 2,
    "parent_context": 3,
    "operation_context": 4,
    "visual_context": 4,
}


def context_fill_mode(*, sectioned: bool, parent_child: bool) -> ContextFillMode:
    """起点の補い方（章節 > 親 > 前後）。"""
    if sectioned:
        return FILL_SECTION
    if parent_child:
        return FILL_PARENT
    return FILL_NEIGHBORS


@dataclass
class _Group:
    position: int
    parent: Any  # ContextParentEvidence
    children: dict[str, Any] = field(default_factory=dict)  # key → ContextChildEvidence


def _key(record: Any) -> str:
    from rag_engine.retrieval.context_builder import record_context_key

    return record_context_key(record)


def _ids(group: _Group) -> set[str]:
    return {key for key in (_key(group.parent.record), *group.children) if key}


def _merge_child(existing: Any, incoming: Any) -> Any:
    """同じ chunk の 2 つの子を 1 つにする（強い役割・小さい順位・拡張の印を残す）。"""
    winner, other = (
        (incoming, existing) if _ROLE_PRIORITY.get(incoming.role, 99) < _ROLE_PRIORITY.get(existing.role, 99) else (existing, incoming)
    )
    ranks = [rank for rank in (existing.retrieval_rank, incoming.retrieval_rank) if rank is not None]
    record = _with_marks(winner.record, other.record)
    return replace(winner, record=record, retrieval_rank=min(ranks) if ranks else None)


def _with_marks(record: Any, source: Any) -> Any:
    marks = {key: value for key, value in (getattr(source, "metadata", None) or {}).items() if key in MERGE_MARK_KEYS}
    current = getattr(record, "metadata", None) or {}
    if not marks or all(key in current for key in marks) or not is_dataclass(record) or isinstance(record, type):
        return record
    return replace(record, metadata={**marks, **current})


def _is_fill(group: _Group) -> bool:
    return group.parent.reason in _FILL_REASONS and not any(child.role == "retrieved_anchor" for child in group.children.values())


def _is_expansion_only(group: _Group) -> bool:
    anchors = [child for child in group.children.values() if child.role == "retrieved_anchor"]
    return bool(anchors) and all(child.reason == EXPANSION_EVIDENCE_REASON for child in anchors)


def _absorb(target: _Group, source: _Group) -> None:
    """source を target にまとめる（target の record を残す。位置は先の方）。"""
    inherit_reason = _is_fill(target) and not _is_fill(source)
    for key, child in source.children.items():
        if (
            source.parent.reason == EXPANSION_EVIDENCE_REASON
            and child.reason == EXPANSION_EVIDENCE_REASON
            and child.role == "retrieved_anchor"
        ):
            # ほかの根拠に含まれる拡張の根拠は、印だけを残す（#5162 の「同じ親にある」と同じ扱い）。
            child = replace(child, role="parent_context")
        target.children[key] = _merge_child(target.children[key], child) if key in target.children else child
    source_key = _key(source.parent.record)
    if source_key in target.children and target.children[source_key].record is not source.parent.record:
        target.children[source_key] = replace(
            target.children[source_key], record=_with_marks(target.children[source_key].record, source.parent.record)
        )
    if inherit_reason:
        # 起点を持つ根拠が補った文脈に含まれた: 起点の理由を引き継ぐ。
        target.parent = replace(target.parent, reason=source.parent.reason)
    target.position = min(target.position, source.position)


def _contains(outer: _Group, inner: _Group) -> bool:
    """inner の chunk が outer に含まれる、または半分を超えて重なる。"""
    inner_ids, outer_ids = _ids(inner), _ids(outer)
    if not inner_ids:
        return False
    if _key(inner.parent.record) == _key(outer.parent.record):
        return True
    shared = len(inner_ids & outer_ids)
    return shared == len(inner_ids) or shared * 2 > len(inner_ids)


def _artifact(group: _Group) -> str:
    from rag_engine.retrieval.context_builder import _artifact_context_key

    return _artifact_context_key(group.parent.record)


def _document(group: _Group) -> str:
    for record in (*(child.record for child in group.children.values()), group.parent.record):
        metadata = getattr(record, "metadata", None) or {}
        if document_id := str(metadata.get("document_id") or ""):
            return document_id
    from rag_engine.retrieval.context_builder import _record_scope_context_key

    return _record_scope_context_key(group.parent.record)


def _seq(record: Any) -> int:
    try:
        return int(getattr(record, "chunk_seq", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _span(group: _Group) -> tuple[int, int]:
    """かたまりの文書の中の位置（子の chunk の番号の範囲。親の番号は子と別の番号のため使わない）。"""
    numbers = [_seq(child.record) for child in group.children.values()] or [_seq(group.parent.record)]
    return min(numbers), max(numbers)


def _distance(left: _Group, right: _Group) -> int:
    (a1, b1), (a2, b2) = _span(left), _span(right)
    return max(0, a2 - b1, a1 - b2)


def _touches(left: _Group, right: _Group) -> bool:
    """同じ文書・chunk の組で、隣り合うか重なる。"""
    if _artifact(left) != _artifact(right):
        return False
    (a1, b1), (a2, b2) = _span(left), _span(right)
    # chunk の番号は 1 から。番号の無い根拠（0）は位置が分からないので隣り合うとしない。
    return min(a1, a2) > 0 and a2 <= b1 + 1 and a1 <= b2 + 1


def consolidate_evidence_tree(tree: Sequence[Any]) -> tuple[Any, ...]:
    """補った後の根拠の木を 1 回でまとめる（同じ・含む・重なる根拠を 1 つに、補った文脈を根拠のかたまりへ）。"""
    from rag_engine.retrieval.context_builder import ContextParentEvidence  # 循環 import を避けて関数内で読む

    groups: list[_Group] = []
    for position, parent in enumerate(tree):
        group = _Group(position=position, parent=parent)
        for child in parent.children:
            key = _key(child.record)
            if key:
                group.children[key] = _merge_child(group.children[key], child) if key in group.children else child
        # 1. 同じ・含む・重なる根拠を 1 つにまとめる（位置は先の方、record は含む方）。
        host = next((other for other in groups if _contains(other, group)), None)
        if host is not None:
            _absorb(host, group)
            continue
        inner = [other for other in groups if _contains(group, other)]
        for other in inner:
            _absorb(group, other)
        groups = [other for other in groups if all(other is not item for item in inner)]
        groups.append(group)
    # 2. 補った文脈を、同じ文書の最も近い根拠のかたまりに入れる（無ければそれだけのかたまり）。
    owners = [group for group in groups if not _is_fill(group)]
    blocks: list[list[_Group]] = [[owner] for owner in owners]
    index = {id(owner): number for number, owner in enumerate(owners)}
    for group in groups:
        if not _is_fill(group):
            continue
        same = [owner for owner in owners if _document(owner) == _document(group)]
        if same:
            nearest = min(same, key=lambda owner: (_distance(owner, group), owner.position))
            blocks[index[id(nearest)]].append(group)
        else:
            blocks.append([group])
    # 3. 隣り合う・重なるかたまりを 1 つにする（つながるかたまりをすべてまとめる）。
    roots = list(range(len(blocks)))

    def root(number: int) -> int:
        while roots[number] != number:
            roots[number] = roots[roots[number]]
            number = roots[number]
        return number

    for left in range(len(blocks)):
        for right in range(left + 1, len(blocks)):
            if root(left) != root(right) and any(_touches(a, b) for a in blocks[left] for b in blocks[right]):
                roots[root(right)] = root(left)
    combined: dict[int, list[_Group]] = {}
    for number, members in enumerate(blocks):
        combined.setdefault(root(number), []).extend(members)

    def block_position(members: list[_Group]) -> int:
        owned = [member.position for member in members if not _is_fill(member)]
        return min(owned or [member.position for member in members])

    result: list[Any] = []
    for block in sorted(combined.values(), key=block_position):
        # 拡張の根拠だけの根拠が、ほかの根拠のかたまり（その根拠の補った文脈）に入ったときは印だけにする。
        regular = any(not _is_fill(member) and not _is_expansion_only(member) for member in block)
        for member in sorted(block, key=lambda item: (_span(item), item.position)):
            children = dict(member.children)
            if regular and _is_expansion_only(member):
                children = {
                    key: replace(child, role="parent_context") if child.reason == EXPANSION_EVIDENCE_REASON else child
                    for key, child in children.items()
                }
            result.append(
                ContextParentEvidence(
                    record=member.parent.record,
                    role=member.parent.role,
                    reason=member.parent.reason,
                    children=tuple(
                        sorted(children.values(), key=lambda child: (_seq(child.record), str(getattr(child.record, "id", "") or "")))
                    ),
                    evidence_score=member.parent.evidence_score,
                )
            )
    return tuple(result)


__all__ = [
    "FILL_NEIGHBORS",
    "FILL_PARENT",
    "FILL_SECTION",
    "ContextFillMode",
    "consolidate_evidence_tree",
    "context_fill_mode",
]
