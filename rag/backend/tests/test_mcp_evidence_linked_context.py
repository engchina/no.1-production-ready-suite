"""MCP の根拠の並びで、当たった chunk の章の参照の先・前後の文脈を、実体の拡張の根拠より
先に確保する（#1390）。

#1362 の評価（D の実体のレシピあり・拡張 on）で、拡張の根拠（上限の 3 割）が質問と関係の
薄い属性の chunk で枠を埋め、当たった chunk の章の参照の先・すぐ前の chunk が evidence_limit
20 の外（20〜96 位）へ押された（``ir-room-booking-maintenance`` は共通の保守枠が 54 位で
誤答）。評価の検索の結果（citations）の並びの形を、決定論の根拠の列で確かめる。
"""

from __future__ import annotations

import json
from typing import Any

from app.mcp.tools import (
    ENTITY_EXPANSION_LIMIT_SHARE,
    LINKED_CONTEXT_LIMIT_SHARE,
    mcp_evidence_order,
)
from app.rag.entity_expansion import ENTITY_EXPANSION_KEY, ENTITY_EXPANSION_ROLE
from app.schemas.search import RetrievedChunk

MAINTENANCE = "maintenance-plan"
OPERATIONS = "operations-guide"
LEDGER = "system-ledger"
ORG = "organization-rules"
INCIDENT = "incident-rules"


def _chunk(
    chunk_id: str,
    role: str,
    *,
    document: str,
    index: int,
    rank: int | None = None,
    rerank: float | None = None,
    section: str = "",
    targets: list[str] | None = None,
    expansion: bool = False,
    seed: str = "question",
    group: str | None = None,
) -> RetrievedChunk:
    metadata: dict[str, Any] = {
        "evidence_role": role,
        "chunk_index": index,
        "chunk_set_id": f"{document}-set",
        "chunk_group_id": group or f"{document}-p1",
        "section_path": section,
    }
    if rank is not None:
        metadata["evidence_retrieval_rank"] = rank
    if targets is not None:
        metadata["reference_targets_json"] = json.dumps(
            [
                {"label": path.rsplit(" > ", 1)[-1][:4], "kind": "chapter", "key": "x"}
                | {"document_title": None, "section_path": path}
                for path in targets
            ],
            ensure_ascii=False,
        )
    if expansion:
        metadata[ENTITY_EXPANSION_KEY] = {"entity": chunk_id, "hop": 1, "seed": seed}
    return RetrievedChunk(
        document_id=document,
        chunk_id=chunk_id,
        text=chunk_id,
        score=0.0,
        rerank_score=rerank,
        metadata=metadata,
    )


def _ids(chunks: list[RetrievedChunk]) -> list[str]:
    return [chunk.chunk_id for chunk in chunks]


def _room_booking_citations() -> list[RetrievedChunk]:
    """評価の ``ir-room-booking-maintenance``（「会議室予約システム 定期保守」）の検索の結果の形。

    当たった上位は運用要領の第 7 章（会議室予約システム）と定期保守計画の第 1 章（個別の
    保守枠。「この章に無いシステムは、第 2 章の共通の保守枠で保守します」）。答えの共通の
    保守枠（第 2 章）は第 1 章と同じ親の前後の文脈で、citations では運用要領の多くの章の
    後ろにある。拡張の根拠は、質問の「会議室予約システム」の台帳の行から 1 段でたどった
    組織規程（担当部署の略号・承認者）・障害連絡規程（重要度 C）と、第 1 章の HRM・OMS の
    台帳の行で、どれも質問（定期保守）とは関係が薄い（rerank の関連度が低い）。
    """
    plan = "定期保守計画 2026年度"
    ops = [_chunk("ops-ch7", "retrieved_anchor", document=OPERATIONS, index=7, rank=1, rerank=0.92)]
    maintenance_hits = [
        _chunk(
            "mt-ch1",
            "retrieved_anchor",
            document=MAINTENANCE,
            index=1,
            rank=2,
            rerank=0.76,
            section=f"{plan} > 第 1 章 個別の保守枠",
            targets=[f"{plan} > 第 2 章 共通の保守枠"],
        ),
        _chunk("mt-title", "retrieved_anchor", document=MAINTENANCE, index=0, rank=3, section=plan),
    ]
    # 関連度の低い拡張の根拠（rag_engine は位置を保たず、分数の順に並べた）。
    weak_expansions = [
        _chunk(
            f"weak-{name}",
            ENTITY_EXPANSION_ROLE,
            document=document,
            index=index,
            rank=rank,
            rerank=score,
            expansion=True,
        )
        for name, document, index, rank, score in (
            ("org-codes", ORG, 2, 40, 0.06),
            ("org-approvers", ORG, 3, 41, 0.05),
            ("incident-first-contact", INCIDENT, 1, 42, 0.04),
            ("ledger-hrm", LEDGER, 3, 43, 0.08),
            ("ledger-oms", LEDGER, 4, 44, 0.07),
        )
    ]
    other_hits = [
        _chunk(
            f"ops-hit-{rank}", "retrieved_anchor", document=OPERATIONS, index=rank + 20, rank=rank
        )
        for rank in range(4, 39)
    ]
    ops_context = [
        _chunk(f"ops-ch{index}", "neighbor_context", document=OPERATIONS, index=index)
        for index in [*range(0, 7), *range(8, 20)]
    ]
    maintenance_context = [
        _chunk(
            "mt-ch2-common",
            "neighbor_context",
            document=MAINTENANCE,
            index=2,
            section=f"{plan} > 第 2 章 共通の保守枠",
        ),
        _chunk(
            "mt-ch3-change",
            "neighbor_context",
            document=MAINTENANCE,
            index=3,
            section=f"{plan} > 第 3 章 保守枠の変更",
        ),
    ]
    return [
        *ops,
        *ops_context,
        *maintenance_hits,
        *maintenance_context,
        *weak_expansions,
        *other_hits,
    ]


def test_room_booking_keeps_the_referenced_chapter_inside_the_limit() -> None:
    citations = _room_booking_citations()

    ordered = mcp_evidence_order(citations, 20)
    head = _ids(ordered[:20])

    # 第 1 章が参照する第 2 章（共通の保守枠）が上限の内に入る（修正前は 54 位）。
    assert "mt-ch2-common" in head
    # 当たった上位の chunk は動かない。
    assert head[:3] == ["ops-ch7", "mt-ch1", "mt-title"]
    # 関連度の低い拡張の根拠は確保しない（関連度の順位のまま上限の外）。
    assert not [cid for cid in head if cid.startswith("weak-")]
    # 章の参照の先を、すぐ前・すぐ後の chunk より先に確保する。
    linked = [
        cid for cid in head if cid in {"mt-ch2-common", "ops-ch6", "ops-ch8", "mt-ch3-change"}
    ]
    assert linked[0] == "mt-ch2-common"
    assert len(ordered) == len(citations)


def test_room_booking_still_reserves_a_relevant_expansion_after_the_linked_context() -> None:
    """関連度の高い拡張の根拠（質問のシステムの台帳の行）は、章の参照の先と一緒に上限の内に残る。"""
    citations = _room_booking_citations()
    ledger_row = _chunk(
        "ledger-room-booking",
        ENTITY_EXPANSION_ROLE,
        document=LEDGER,
        index=6,
        rank=45,
        rerank=0.45,
        expansion=True,
        group="ledger-p1",
    )
    # 台帳の行と同じ親の前後の文脈（同じ表の続き）。
    ledger_next = _chunk(
        "ledger-room-booking-next", "neighbor_context", document=LEDGER, index=7, group="ledger-p1"
    )

    ordered = mcp_evidence_order([*citations, ledger_row, ledger_next], 20)
    head = _ids(ordered[:20])

    assert "mt-ch2-common" in head
    assert "ledger-room-booking" in head
    assert "ledger-room-booking-next" in head
    # 章の参照の先（前後の文脈の枠）を先に、拡張の根拠（拡張の枠）を後に置く。
    assert head.index("mt-ch2-common") < head.index("ledger-room-booking")
    assert not [cid for cid in head if cid.startswith("weak-")]


def test_incoming_reference_and_previous_chunk_of_the_hit_are_reserved() -> None:
    """評価の ``ir-incident-phone-unreachable`` / ``ir-access-review`` の形: 当たった
    第 3 章を参照する第 1 章の文（「連絡の手段は第 3 章を参照してください」）と、当たった
    章のすぐ前の章を上限の内に入れる。
    """
    rules = "障害連絡規程"
    hits = [
        _chunk(
            "in-ch3-means",
            "retrieved_anchor",
            document=INCIDENT,
            index=3,
            rank=1,
            rerank=0.9,
            section=f"{rules} > 第 3 章 連絡の手段",
        ),
        *(
            _chunk(f"hit-{rank}", "retrieved_anchor", document=OPERATIONS, index=rank, rank=rank)
            for rank in range(2, 24)
        ),
    ]
    context = [
        _chunk(f"ops-ctx-{n}", "neighbor_context", document=OPERATIONS, index=100 + n)
        for n in range(6)
    ]
    citing = _chunk(
        "in-ch1-first-contact",
        "neighbor_context",
        document=INCIDENT,
        index=1,
        section=f"{rules} > 第 1 章 最初の連絡",
        targets=[f"{rules} > 第 3 章 連絡の手段"],
    )
    previous = _chunk(
        "in-ch2-recovery",
        "neighbor_context",
        document=INCIDENT,
        index=2,
        section=f"{rules} > 第 2 章 復旧の目標",
    )

    ordered = mcp_evidence_order([*hits, *context, previous, citing], 20)
    head = _ids(ordered[:20])

    assert head.index("in-ch1-first-contact") < head.index("in-ch2-recovery")
    assert head[0] == "in-ch3-means"
    # 確保した 2 件の分だけ、末尾の当たりが上限の後ろへ移る（順位の順で残る）。
    assert _ids(ordered[18:20]) == ["in-ch1-first-contact", "in-ch2-recovery"]
    assert _ids(ordered[20:23]) == ["hit-19", "hit-20", "hit-21"]


def test_linked_context_share_is_capped_and_other_documents_are_not_linked() -> None:
    hits = [
        _chunk(
            f"hit-{rank}",
            "retrieved_anchor",
            document=f"doc-{rank}",
            index=5,
            rank=rank,
        )
        for rank in range(1, 26)
    ]
    # 上位 5 件の当たりのすぐ前・すぐ後（10 件）と、別の文書の同じ番号の chunk。
    neighbors = [
        _chunk(f"near-{rank}-{index}", "neighbor_context", document=f"doc-{rank}", index=index)
        for rank in range(1, 6)
        for index in (4, 6)
    ]
    unrelated = _chunk("other-doc", "neighbor_context", document="doc-99", index=4)

    ordered = mcp_evidence_order([*hits, unrelated, *neighbors], 20)
    head = _ids(ordered[:20])

    reserved = [cid for cid in head if cid.startswith("near-")]
    # 上限 20 の 3 割 = 6 件まで。
    assert LINKED_CONTEXT_LIMIT_SHARE == 0.3
    # 起点の順（1 位のすぐ前・すぐ後から）。
    assert reserved == ["near-1-4", "near-1-6", "near-2-4", "near-2-6", "near-3-4", "near-3-6"]
    assert "other-doc" not in head


def test_expansion_without_relevance_after_rerank_is_not_reserved_alone() -> None:
    """rerank を実行した検索で、関連度の無い拡張の根拠（起点にならず前後の文脈に入った
    もの）は単独では確保しない。rerank を実行しなかった検索では今までどおり確保する（#1362）。
    """
    hits = [
        _chunk(f"hit-{rank}", "retrieved_anchor", document=f"doc-{rank}", index=0, rank=rank)
        for rank in range(1, 24)
    ]
    stray = _chunk("stray-expansion", "neighbor_context", document=LEDGER, index=9, expansion=True)
    scored = [hits[0].model_copy(update={"rerank_score": 0.9}), *hits[1:]]

    assert "stray-expansion" not in _ids(mcp_evidence_order([*scored, stray], 20)[:20])
    assert "stray-expansion" in _ids(mcp_evidence_order([*hits, stray], 20)[:20])
    assert ENTITY_EXPANSION_LIMIT_SHARE == 0.3


def test_expansion_from_top_chunk_entities_needs_hit_level_relevance() -> None:
    """上位の chunk の実体から足した根拠（保守計画の章に並ぶ別のシステムの台帳の行など）は、検索で
    当たった chunk と同じ程度の関連度が無ければ確保しない。質問の実体から足した根拠は低い関連度でも
    確保する（橋渡しの行）。
    """
    hits = [
        _chunk(f"hit-{rank}", "retrieved_anchor", document=f"doc-{rank}", index=0, rank=rank)
        for rank in range(1, 24)
    ]
    hits[0] = hits[0].model_copy(update={"rerank_score": 0.9})
    question_row = _chunk(
        "ledger-named",
        ENTITY_EXPANSION_ROLE,
        document=LEDGER,
        index=1,
        rank=30,
        rerank=0.3,
        expansion=True,
        group="ledger-a",
    )
    chunk_row = _chunk(
        "ledger-listed",
        ENTITY_EXPANSION_ROLE,
        document=LEDGER,
        index=5,
        rank=31,
        rerank=0.4,
        expansion=True,
        seed="chunk",
        group="ledger-b",
    )

    head = _ids(mcp_evidence_order([*hits, question_row, chunk_row], 20)[:20])

    assert "ledger-named" in head
    assert "ledger-listed" not in head
