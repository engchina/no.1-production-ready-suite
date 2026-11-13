"""長い原文 ID の短縮と、読取範囲・依存近傍の境界を検証する。"""

import json

import pytest

from app.rag.wiki_models import WikiDependency, WikiFilePlan, scoped_plan
from app.rag.wiki_navigation import PlanningHandles, terminal_sections
from tests.test_wiki_retrieval import retriever


def test_handles_preserve_question_and_restore_only_bound_ids() -> None:
    identity = "a" * 64
    schema = scoped_plan(WikiFilePlan, {"document_ids": ([identity], 1)})
    data = {"question": identity, "files": [{"document_id": identity, "title": identity}]}
    handles = PlanningHandles(data, schema.model_json_schema())
    wire = handles.encode_data(data)
    assert wire["question"] == identity and wire["files"][0]["title"] == identity
    assert wire["files"][0]["document_id"] == "D1"
    assert (
        handles.encode_schema(schema.model_json_schema())["properties"]["document_ids"]["items"][
            "const"
        ]
        == "D1"
    )
    restored = handles.decode(
        {"document_ids": ["D1"], "search_expressions": [], "unresolved": ["D1"]}
    )
    assert schema.model_validate(restored).document_ids == [identity]
    assert restored["unresolved"] == ["D1"]
    with pytest.raises(ValueError, match="候補"):
        handles.decode({"document_ids": ["D2"]})


def test_terminal_sections_never_expand_a_common_ancestor() -> None:
    search, store, _, _ = retriever()
    pages = [
        page.model_copy(
            update={
                "unit": page.unit.model_copy(update={"section_path": ["文書", "章", f"節{i // 2}"]})
            }
        )
        for i, page in enumerate(store.pages)
    ]
    members, cards = terminal_sections(pages)
    assert len(members) == 2
    assert all(len(ids) == 2 for ids in members.values())
    assert all(len(card["path"]) == 3 for card in cards.values())
    assert set(uid for ids in members.values() for uid in ids) == {p.unit.unit_id for p in pages}


@pytest.mark.asyncio
@pytest.mark.parametrize("depth", [0, 1, 2])
async def test_dependency_neighborhood_exposes_unexpanded_boundary(depth: int) -> None:
    search, store, _, model = retriever()
    template = store.pages[0]
    store.pages = [
        template.model_copy(
            update={
                "unit": template.unit.model_copy(
                    update={"unit_id": f"unit-{i}", "source_chunk_ids": [f"chunk-{i}"]}
                ),
                "navigation": template.navigation.model_copy(
                    update={
                        "unit_id": f"unit-{i}",
                        "dependencies": [
                            WikiDependency(unit_id=f"unit-{i + 1}", relation="reference")
                        ]
                        if i < 99
                        else [],
                    }
                ),
            }
        )
        for i in range(100)
    ]
    search.settings = search.settings.model_copy(update={"rag_wiki_dependency_depth": depth})
    result = await search.search(
        "合成の質問", profile_id="profile-a", filters={}, allow_planning=False
    )
    assert len(result.chunks) == depth + 1
    assert result.trace["unexpanded_dependency_units"] == 1
    assert result.trace["dependency_graph_complete"] is False
    assert result.trace["dependency_depth"] == depth
    assert not model.calls


@pytest.mark.asyncio
async def test_navigation_input_contains_no_duplicate_original_bodies() -> None:
    search, store, _, model = retriever()
    for page in store.pages:
        page.unit.text = "長い原文固有値" * 1000
    await search.search("合成の質問", profile_id="profile-a", filters={}, allow_planning=True)
    wire = json.dumps(model.calls[1], ensure_ascii=False)
    assert "長い原文固有値" not in wire
    assert all(len(unit["unit_id"]) < 8 for unit in model.calls[1]["units"])
    assert all("context" in unit and "path" in unit for unit in model.calls[1]["units"])
