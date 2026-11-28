"""資料選択の範囲と複数タスクを含む版の回帰。"""

from collections.abc import Iterable

import pytest

from pr_backend_core.file_review import (
    FileReview,
    ReviewAction,
    ReviewSource,
    ReviewVersion,
    StaleReview,
    advance_review,
)


def source(documents: Iterable[str]) -> ReviewSource:
    hits = [{"document_id": key, "chunk_id": key + "-1", "file_name": key} for key in documents]
    return ReviewSource(
        title="質問", citations=hits, answer=" ".join(hit["document_id"] for hit in hits)
    )


def initial(**tasks: str) -> FileReview:
    sources = {key: source(documents) for key, documents in tasks.items()}
    return FileReview(
        versions=[ReviewVersion(id="first", answer="初回", sources=sources)],
        seen=set("".join(tasks.values())),
    )


async def compose(sources: dict[str, ReviewSource]) -> str:
    return " / ".join(s.answer for s in sources.values())


async def retrieve(
    task: str, include: list[str], exclude: list[str], generate: bool
) -> ReviewSource:
    if generate:
        assert include
        assert not (set(include) & set(exclude))
        return source(include)
    return source([key for key in "ABCDE" if key not in exclude][:2])


async def apply(state: FileReview, action: str, selected: str = "", task: str = "a") -> FileReview:
    command = ReviewAction.model_validate(
        dict(revision=state.revision, action=action, selected=list(selected), task=task)
    )
    return await advance_review(state, command, state.document_ids(), retrieve, compose)


@pytest.mark.anyio
async def test_discovery_requires_confirmation_and_remembers_all_discarded_documents() -> None:
    first = initial(a="AB")
    pending = await apply(first, "discover", "A")
    assert list(pending.pending["a"].documents()) == ["A", "C", "D"]
    assert pending.kept == {"a": ["A"]}
    assert pending.excluded == {"B": "B"}
    assert pending.versions == first.versions
    assert first.excluded == {}
    answered = await apply(pending, "answer", "AC")
    assert answered.versions[-1].answer == "A C"
    assert answered.versions[-1].rating is None
    assert set(answered.excluded) == {"B", "D"}
    assert not answered.pending
    restored = await apply(answered, "restore", "D")
    again = await apply(restored, "discover", "A")
    assert list(again.pending["a"].documents()) == ["A", "D", "E"]
    assert set(again.excluded) == {"B", "C"}


@pytest.mark.anyio
async def test_exclusion_covers_other_tasks_and_reset_requires_fresh_consent() -> None:
    first = initial(a="AB", b="BC")
    answered = await apply(first, "answer", "A")
    assert answered.versions[-1].answer == "A / C"
    assert answered.versions[0].sources["b"].answer == "B C"
    reset = await apply(answered, "reset")
    assert reset.kept == reset.excluded == {}
    assert reset.versions == answered.versions
    confirmed = await apply(reset, "answer", "B")
    assert confirmed.versions[-1].sources["b"].citations == []
    assert set(confirmed.versions[-1].sources["a"].documents()) == {"B"}


@pytest.mark.anyio
async def test_stale_or_invisible_selection_never_updates_previous_version() -> None:
    first = initial(a="AB")
    cmd = ReviewAction(revision=1, task="a", action="answer", selected=["A"])
    with pytest.raises(StaleReview):
        await advance_review(first, cmd, {"A"}, retrieve, compose)
    with pytest.raises(ValueError, match="表示中"):
        await advance_review(
            first,
            cmd.model_copy(update={"selected": ["B"], "revision": 0}),
            {"A"},
            retrieve,
            compose,
        )
    visible = first.visible({"A"})
    assert visible.versions[0].answer == ""
    assert tuple(visible.versions[0].sources["a"].documents()) == ("A",)
    assert first.revision == 0


@pytest.mark.anyio
async def test_failed_generation_does_not_commit_exclusions() -> None:
    async def broken(_: dict[str, ReviewSource]) -> str:
        raise RuntimeError("生成失敗")

    first = initial(a="AB")
    cmd = ReviewAction(revision=0, task="a", action="answer", selected=["A"])
    with pytest.raises(RuntimeError, match="生成失敗"):
        await advance_review(first, cmd, first.document_ids(), retrieve, broken)
    assert not first.excluded and len(first.versions) == 1


@pytest.mark.anyio
async def test_back_undoes_only_last_discovery_and_can_answer_from_previous_files() -> None:
    original = initial(a="AB", b="B")
    first = await apply(original, "discover", "A")
    second = await apply(first, "discover", "C")
    assert set(second.excluded) == {"A", "B", "D"}
    returned = await apply(FileReview.model_validate_json(second.model_dump_json()), "back")
    assert returned.revision == second.revision + 1
    assert returned.versions == original.versions
    assert set(returned.excluded) == {"B"}
    assert list(returned.pending["a"].documents()) == ["A", "C", "D"]
    assert returned.method_tasks == {"a"}
    assert returned.kept["a"] == ["A"]
    assert "E" not in returned.seen
    assert second.visible({"C"}).previous_discovery == {}
    revised = await apply(returned, "answer", "D")
    assert set(revised.versions[-1].sources["a"].documents()) == {"D"}


@pytest.mark.anyio
async def test_back_with_no_new_candidates_uses_no_retrieval_and_preserves_other_task() -> None:
    async def empty(
        task: str, include: list[str], exclude: list[str], generate: bool
    ) -> ReviewSource:
        return source([])

    original = initial(a="B", b="BC")
    original.excluded["A"] = "A"
    original.kept["b"] = ["B", "C"]
    discover = ReviewAction(revision=0, action="discover", task="a")
    none = await advance_review(original, discover, {"A", "B", "C"}, empty, compose)
    assert not none.pending["a"].citations and none.kept["b"] == ["C"]

    async def unexpected(*args: object) -> ReviewSource:
        raise AssertionError("戻る操作は検索しない")

    back = ReviewAction(revision=1, action="back", task="a")
    before = await advance_review(none, back, {"A", "B", "C"}, unexpected, compose)
    assert before.excluded == {"A": "A"} and not before.pending
    assert set(before.kept["b"]) == {"B", "C"}
    saved_before_change = none.model_dump(exclude={"previous_discovery", "method_tasks"})
    legacy = await apply(FileReview.model_validate(saved_before_change), "back")
    assert legacy.excluded == none.excluded and not legacy.pending
    assert (await apply(legacy, "restore", "B")).excluded == {"A": "A"}


def limited(state: FileReview, action: str, selected: str, method: str | None) -> ReviewAction:
    return ReviewAction.model_validate(
        dict(
            revision=state.revision, action=action, selected=list(selected), task="a", method=method
        )
    )


@pytest.mark.parametrize(
    ("action", "method"),
    [("answer", "only"), ("discover", "mix"), ("answer", None), ("discover", None)],
    ids=["only", "mix", "candidates-answer", "candidates-more"],
)
@pytest.mark.anyio
async def test_selection_over_the_limit_is_rejected(action: str, method: str | None) -> None:
    """選んだ資料を回答・次の検索へ持ち越す操作は、上限を超えた選択を受け付けない（#5981）。"""
    first = initial(a="ABCD")
    with pytest.raises(ValueError, match="一度に選べる資料は 3 件までです。"):
        await advance_review(
            first,
            limited(first, action, "ABCD", method),
            first.document_ids(),
            retrieve,
            compose,
            selection_limit=3,
        )
    # 上限ちょうどは通る。
    accepted = await advance_review(
        first,
        limited(first, action, "ABC", method),
        first.document_ids(),
        retrieve,
        compose,
        selection_limit=3,
    )
    assert accepted.kept["a"] == ["A", "B", "C"]


@pytest.mark.anyio
async def test_replace_and_unlimited_calls_are_not_limited() -> None:
    """「別の資料を探す」（replace）と、上限を渡さない呼び出しには掛けない（#5981）。"""
    first = initial(a="ABCD")
    replaced = await advance_review(
        first,
        limited(first, "discover", "ABCD", "replace"),
        first.document_ids(),
        retrieve,
        compose,
        selection_limit=3,
    )
    assert replaced.kept["a"] == ["A", "B", "C", "D"]
    unlimited = await advance_review(
        first, limited(first, "answer", "ABCD", "only"), first.document_ids(), retrieve, compose
    )
    assert len(unlimited.versions) == 2
    # 回答し直す操作は、方法の指定に関わらず上限を掛ける（replace を名乗っても通さない）。
    with pytest.raises(ValueError, match="3 件まで"):
        await advance_review(
            first,
            limited(first, "answer", "ABCD", "replace"),
            first.document_ids(),
            retrieve,
            compose,
            selection_limit=3,
        )


@pytest.mark.anyio
async def test_stale_revision_wins_over_the_selection_limit() -> None:
    first = initial(a="ABCD")
    command = ReviewAction(revision=5, action="answer", selected=list("ABCD"), task="a")
    with pytest.raises(StaleReview):
        await advance_review(
            first, command, first.document_ids(), retrieve, compose, selection_limit=3
        )
