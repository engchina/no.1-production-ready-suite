"""引用読取後の依存群選択。実モデル・DB を呼ばない合成例。"""

from app.rag.reference_selection import ReferenceGroup, select_reference_groups


def test_does_not_split_group_or_remove_anchor_when_budget_is_insufficient() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 1, 1.0, ("x", "y", "z")), ReferenceGroup("b", 2, 0.9, ("w",))],
        existing={"a", "b"},
        budget=2,
    )
    assert result.sources == ("b",)
    assert result.chunk_ids == {"w"}
    assert result.outcomes == {"a": "budget", "b": "selected"}


def test_shared_dependencies_use_budget_once_and_keep_both_source_relations() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 1, 0.9, ("x", "y")), ReferenceGroup("b", 2, 0.8, ("y", "z"))],
        existing={"a", "b"},
        budget=3,
    )
    assert result.sources == ("a", "b")
    assert result.chunk_ids == {"x", "y", "z"}
    assert result.outcomes == {"a": "selected", "b": "selected"}


def test_compares_all_groups_by_relevance_and_marginal_cost() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 1, 0.9, ("x", "y")), ReferenceGroup("b", 2, 0.8, ("z",))],
        existing={"a", "b"},
        budget=2,
    )
    assert result.sources == ("b",)
    assert result.chunk_ids == {"z"}
    assert result.outcomes["a"] == "budget"


def test_unavailable_dependency_does_not_look_complete() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 1, None, ("x",), unavailable=True)], existing={"a"}, budget=4
    )
    assert result.outcomes == {"a": "unavailable"}
    assert result.chunk_ids == set()


def test_preserves_one_hop_and_does_not_move_already_selected_source() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 6, None, ("b", "x")), ReferenceGroup("b", 7, None, ("z", "w"))],
        existing=set(),
        budget=4,
    )
    assert result.sources == ("a",)
    assert result.outcomes["b"] == "referenced"
    result = select_reference_groups(
        [ReferenceGroup("a", 6, None, ("x",)), ReferenceGroup("b", 7, None, ("a", "z"))],
        existing=set(),
        budget=4,
    )
    assert result.sources == ("a", "b")
    assert result.chunk_ids == {"x", "z"}


def test_existing_dependencies_do_not_consume_budget() -> None:
    result = select_reference_groups(
        [ReferenceGroup("a", 1, None, ("b",))], existing={"a", "b"}, budget=0
    )
    assert result.sources == ("a",)
    assert result.chunk_ids == set()
