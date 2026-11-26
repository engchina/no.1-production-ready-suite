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
