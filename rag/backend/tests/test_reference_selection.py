"""引用読取後の参照先の chunk の選択と関連度の順(#5243)。実モデル・DB を呼ばない合成例。"""

from app.rag.reference_selection import ReferenceGroup, relevance_order, select_reference_chunks


def test_adds_every_read_chunk_without_a_count_cap() -> None:
    """群の大きさで選ばない: 読めた参照先の chunk はすべて足す(すべてか無しかの上限は無い)。"""
    result = select_reference_chunks(
        [
            ReferenceGroup("a", 1, tuple(f"x{n}" for n in range(10))),
            ReferenceGroup("b", 2, ("w",)),
        ],
        existing={"a", "b"},
    )
    assert result.sources == ("a", "b")
    assert result.chunk_ids == (*(f"x{n}" for n in range(10)), "w")
    assert result.outcomes == {"a": "selected", "b": "selected"}


def test_shared_chunks_are_added_once_in_first_seen_order() -> None:
    result = select_reference_chunks(
        [ReferenceGroup("a", 1, ("x", "y")), ReferenceGroup("b", 2, ("y", "z"))],
        existing={"a", "b"},
    )
    assert result.sources == ("a", "b")
    assert result.chunk_ids == ("x", "y", "z")


def test_unavailable_source_does_not_look_complete() -> None:
    result = select_reference_chunks([ReferenceGroup("a", 1, (), unavailable=True)], existing={"a"})
    assert result.outcomes == {"a": "unavailable"}
    assert result.chunk_ids == ()


def test_preserves_one_hop_and_does_not_move_already_selected_source() -> None:
    """先の起点の参照先として足した候補は起点として辿らず、辿った起点は参照先として移さない。"""
    result = select_reference_chunks(
        [ReferenceGroup("a", 6, ("b", "x")), ReferenceGroup("b", 7, ("z", "w"))],
        existing=set(),
    )
    assert result.sources == ("a",)
    assert result.chunk_ids == ("b", "x")
    assert result.outcomes["b"] == "referenced"
    result = select_reference_chunks(
        [ReferenceGroup("b", 7, ("a", "z")), ReferenceGroup("a", 6, ("x",))],
        existing=set(),
    )
    # 検索順位の順に辿る(a が先)。b の参照先の a は辿った起点なので移さない。
    assert result.sources == ("a", "b")
    assert result.chunk_ids == ("x", "z")


def test_existing_anchor_chunks_are_not_added_again() -> None:
    result = select_reference_chunks([ReferenceGroup("a", 1, ("b",))], existing={"a", "b"})
    assert result.sources == ("a",)
    assert result.chunk_ids == ()


def test_relevance_order_puts_higher_scores_first_and_keeps_ties_stable() -> None:
    """rerank の点数の高い順。同点と点数の無いもの(rerank の失敗など)は元の順で後ろへ。"""
    order = relevance_order(
        ["a", "b", "c", "d", "e"], {"a": 0.1, "b": 0.9, "c": 0.1, "e": float("nan")}
    )
    assert order == ["b", "a", "c", "d", "e"]
    assert relevance_order(["a", "b"], {}) == ["a", "b"]
