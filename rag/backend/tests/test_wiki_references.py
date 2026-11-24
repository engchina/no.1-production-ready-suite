"""Wiki の別の文書への参照（#5784）: 原文にある表記だけを残し、名前の比較形で参照先を決める。"""

import json
from types import SimpleNamespace

from app.clients.wiki_reference_index import DocumentNames, _text_references, section_pages
from app.rag.cross_references import document_reference_keys, reference_name_key
from app.rag.wiki_models import WikiContext, WikiPage, WikiReference, WikiUnit, fingerprint

TEXT = "申請の前に「合成の承認規程」の第 3 条を確かめる。詳細は手順A.txt を参照する。"


def _page(references: list[WikiReference]) -> WikiPage:
    unit = WikiUnit(
        unit_id="unit-a",
        document_id="doc-a",
        chunk_set_id="set-a",
        source_chunk_ids=["chunk-a"],
        section_id="section-a",
        section_path=["第 1 章"],
        title="第 1 章",
        text=TEXT,
        source_hash=fingerprint(TEXT),
    )
    return WikiPage(
        unit=unit,
        navigation=WikiContext(
            unit_id="unit-a",
            context="合成の案内",
            possible_questions=[],
            dependencies=[],
            unresolved=[],
            references=references,
        ),
        input_fingerprint="input-a",
    )


def test_only_references_written_in_the_original_are_kept() -> None:
    page = _page(
        [
            WikiReference(document="合成の承認規程", section="第 3 条", relation="prerequisite"),
            # 原文に無い名前・章節は採らない（モデルが作った名前で結ばない）。
            WikiReference(document="合成の別の規程", section="", relation="exception"),
            WikiReference(document="合成の承認規程", section="第 9 条", relation="reference"),
            WikiReference(document="手順A.txt", section="", relation="reference"),
        ]
    )
    assert [(ref.document, ref.section) for ref in page.navigation.references] == [
        ("合成の承認規程", "第 3 条"),
        ("手順A.txt", ""),
    ]


def test_saved_guidance_without_references_still_reads() -> None:
    """#5784 より前に保存した案内（参照の欄が無い）も読める。"""
    data = _page([]).model_dump()
    del data["navigation"]["references"]
    assert WikiPage.model_validate(data).navigation.references == []


def test_reference_keys_match_titles_and_file_names_exactly() -> None:
    assert reference_name_key("手順A.txt") in document_reference_keys("手順A.txt")
    assert reference_name_key("合成の承認規程") in document_reference_keys(
        "regulation.pdf", ["合成の承認規程"]
    )
    # 1 文字の名前は照合に使わない。
    assert reference_name_key("A") is None
    names = DocumentNames(
        [
            {"document_id": "doc-a", "title": "a.pdf"},
            {"document_id": "doc-b", "title": "手順A.txt"},
        ],
        {"doc-a": ["合成の承認規程"]},
    )
    assert names.resolve("合成の承認規程", exclude="doc-b") == ["doc-a"]
    assert names.resolve("合成の承認規程", exclude="doc-a") == []
    assert names.resolve("手順A.txt", exclude="doc-a") == ["doc-b"]
    assert names.resolve("無い文書.txt", exclude="doc-a") == []


def test_text_references_take_only_other_documents_from_chunk_metadata() -> None:
    targets = [
        {"label": "第3章", "kind": "chapter", "key": "3", "document_title": None},
        {"label": "第 3 条", "kind": "article", "key": "3", "document_title": "合成の承認規程"},
        {"label": "手順A.txt", "kind": "document", "key": "", "document_title": "手順A.txt"},
    ]
    found = _text_references({"reference_targets_json": json.dumps(targets)})
    assert found == [
        ("合成の承認規程", "第 3 条", "reference"),
        ("手順A.txt", "", "reference"),
    ]
    assert _text_references({}) == [] and _text_references({"reference_targets_json": "x"}) == []


def test_section_pages_prefer_the_exact_heading() -> None:
    pages = [
        SimpleNamespace(name=name, unit=SimpleNamespace(section_path=path))
        for name, path in (
            ("a1", ["第 1 章", "第 1 節"]),
            ("a3", ["第 1 章"]),
            ("b1", ["付録"]),
        )
    ]
    assert [page.name for page in section_pages(pages, "第 1 章")] == ["a3"]
    assert [page.name for page in section_pages(pages, "第 1 節")] == ["a1"]
    # 当たる見出しが無い・表記が無いときは並びを変えない。
    assert [page.name for page in section_pages(pages, "第 9 章")] == ["a1", "a3", "b1"]
    assert [page.name for page in section_pages(pages, "")] == ["a1", "a3", "b1"]
