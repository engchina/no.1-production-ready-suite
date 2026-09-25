"""Navigation tree 構築・要約（Knowhere 由来）の単体テスト。"""

from __future__ import annotations

import pytest

from app.rag.navigation import (
    build_navigation_tree,
    navigation_section_id,
    navigation_summary_elements,
    summarize_navigation_nodes,
)
from app.schemas.extraction import (
    DocumentElement,
    DocumentNavigationNode,
    StructuredExtraction,
)


def test_navigation_summary_elements_are_searchable_and_link_to_section() -> None:
    nodes = [
        DocumentNavigationNode(
            section_id="nav-abc",
            title="第1章",
            section_path=["第1章"],
            depth=1,
            page_start=1,
            summary="クラウド利用料の概要。",
        ),
        DocumentNavigationNode(
            section_id="nav-def",
            title="第2章",
            section_path=["第2章"],
            depth=1,
            summary=None,
        ),
    ]
    elements = navigation_summary_elements(nodes, start_order=10)
    # summary を持つ node だけ element 化される。
    assert len(elements) == 1
    element = elements[0]
    assert element.kind == "text"  # 検索可能 kind
    assert element.content_kind == "section_summary"
    assert "クラウド利用料" in element.text
    assert element.metadata.get("section_id") == "nav-abc"
    assert element.metadata.get("nav_summary") is True
    assert element.element_id == "nav-summary-nav-abc"
    assert element.order == 10


def _element(
    *, text: str, element_id: str, section_path: list[str], order: int, page: int | None = None
) -> DocumentElement:
    return DocumentElement(
        kind="text",
        text=text,
        order=order,
        element_id=element_id,
        section_path=section_path,
        page_number=page,
    )


def _extraction(elements: list[DocumentElement]) -> StructuredExtraction:
    return StructuredExtraction(raw_text="", elements=elements)


def test_build_navigation_tree_reconstructs_hierarchy() -> None:
    extraction = _extraction(
        [
            _element(
                text="導入の本文", element_id="e1", section_path=["第1章", "概要"], order=0, page=1
            ),
            _element(
                text="詳細の本文", element_id="e2", section_path=["第1章", "詳細"], order=1, page=2
            ),
            _element(text="結論本文", element_id="e3", section_path=["第2章"], order=2, page=3),
        ]
    )
    nodes = build_navigation_tree(extraction)
    by_path = {tuple(node.section_path): node for node in nodes}

    # 第1章 は明示要素を持たないが prefix として node 化される。
    assert tuple(["第1章"]) in by_path
    chapter1 = by_path[("第1章",)]
    assert chapter1.parent_section_id is None
    assert chapter1.depth == 1
    # 子 node の id が親に link される。
    assert by_path[("第1章", "概要")].section_id in chapter1.child_section_ids
    assert by_path[("第1章", "詳細")].section_id in chapter1.child_section_ids
    # page 範囲は subtree 全体を覆う。
    assert chapter1.page_start == 1
    assert chapter1.page_end == 2
    # element_ids は完全一致 path の node にのみ付く。
    assert by_path[("第1章", "概要")].element_ids == ["e1"]
    assert chapter1.element_ids == []


def test_navigation_section_id_is_stable() -> None:
    assert navigation_section_id(("第1章", "概要")) == navigation_section_id(("第1章", "概要"))
    assert navigation_section_id(("第1章",)) != navigation_section_id(("第2章",))


def test_build_navigation_tree_is_empty_without_sections() -> None:
    extraction = _extraction(
        [_element(text="見出しのない本文", element_id="e1", section_path=[], order=0)]
    )
    assert build_navigation_tree(extraction) == []


@pytest.mark.anyio
async def test_summarize_navigation_nodes_fills_summaries_with_injected_summarizer() -> None:
    extraction = _extraction(
        [
            _element(text="あ" * 300, element_id="e1", section_path=["第1章"], order=0, page=1),
            _element(text="短文", element_id="e2", section_path=["第2章"], order=1, page=2),
        ]
    )
    nodes = build_navigation_tree(extraction)

    calls: list[str] = []

    async def _summarize(text: str) -> str:
        calls.append(text)
        return f"要約({len(text)}文字)"

    summarized = await summarize_navigation_nodes(nodes, extraction, _summarize, min_chars=200)
    by_path = {tuple(node.section_path): node for node in summarized}
    # 長い章は要約され、短い章（min_chars 未満）は要約されない。
    assert by_path[("第1章",)].summary == "要約(300文字)"
    assert by_path[("第2章",)].summary is None
    assert len(calls) == 1


@pytest.mark.anyio
async def test_summarize_navigation_nodes_respects_max_nodes() -> None:
    extraction = _extraction(
        [
            _element(
                text="本文" * 200, element_id=f"e{i}", section_path=[f"章{i}"], order=i, page=1
            )
            for i in range(5)
        ]
    )
    nodes = build_navigation_tree(extraction)

    async def _summarize(text: str) -> str:
        return "要約"

    summarized = await summarize_navigation_nodes(
        nodes, extraction, _summarize, max_nodes=2, min_chars=10
    )
    assert sum(1 for node in summarized if node.summary) == 2


def test_navigation_round_trips_through_document_payload() -> None:
    extraction = _extraction(
        [_element(text="本文", element_id="e1", section_path=["第1章"], order=0, page=1)]
    )
    extraction = extraction.model_copy(update={"navigation": build_navigation_tree(extraction)})
    payload = extraction.to_document_payload()
    assert payload["navigation"]
    restored = StructuredExtraction.model_validate(payload)
    assert restored.navigation == extraction.navigation
