"""実際の Unstructured の要素から section_path を作る回帰（#4741）。版を上げたら確かめる。"""

from rag_parser_core.registry import _unstructured_adapter_result
from rag_parser_core.source import SourceModality, SourceProfile

PARAGRAPH = (
    "この段落では合成の処理の流れについて説明する。処理の開始時には構成の読み込みと整合の確認を行い、"
    "確認が終わった単位から順に次の工程へ渡す。工程の途中で中断した場合は、最後に完了した境界から再開する。"
)
CHAPTERS = [
    "第1章 合成の概要",
    "第2章 合成の構成",
    "第3章 合成の領域",
    "第4章 合成の指標",
    "第5章 合成の切替",
]


def _elements(data: bytes, content_type: str, extension: str, modality: str = "text"):
    profile = SourceProfile.model_construct(
        extension=extension,
        content_type=content_type,
        modality=SourceModality(modality),
        sanitized_file_name=f"synthetic{extension}",
        unsupported_reason=None,
    )
    result = _unstructured_adapter_result(data, source_profile=profile, content_type=content_type)
    assert result.extraction is not None
    return result.extraction.elements


def test_plain_text_paragraphs_never_enter_section_path() -> None:
    source = "\n".join(line for chapter in CHAPTERS for line in (chapter, *[PARAGRAPH] * 4))
    elements = _elements(source.encode(), "text/plain", ".txt")
    paths = {tuple(element.section_path) for element in elements}
    assert paths == {(chapter,) for chapter in CHAPTERS}
    for element in elements:
        if element.text == PARAGRAPH:
            assert element.kind == "text"


def _assert_hierarchy(elements) -> None:
    by_text = {element.text: element.section_path for element in elements}
    assert by_text["合成の文書"] == ["合成の文書"]
    assert by_text["第1章 合成の概要"] == ["合成の文書", "第1章 合成の概要"]
    assert by_text["項目 1"] == ["合成の文書", "第1章 合成の概要", "項目 1"]
    assert by_text["項目 2"] == ["合成の文書", "第1章 合成の概要", "項目 2"]
    assert by_text["第2章 合成の構成"] == ["合成の文書", "第2章 合成の構成"]
    body = [element.section_path for element in elements if element.text == PARAGRAPH]
    assert body == [
        ["合成の文書", "第1章 合成の概要", "項目 1"],
        ["合成の文書", "第1章 合成の概要", "項目 2"],
    ]


def test_markdown_headings_build_hierarchy() -> None:
    source = (
        "# 合成の文書\n\n## 第1章 合成の概要\n\n### 項目 1\n\n"
        f"{PARAGRAPH}\n\n### 項目 2\n\n{PARAGRAPH}\n\n## 第2章 合成の構成\n"
    )
    _assert_hierarchy(_elements(source.encode(), "text/markdown", ".md"))


def test_html_headings_build_hierarchy() -> None:
    source = (
        "<html><body><h1>合成の文書</h1><h2>第1章 合成の概要</h2><h3>項目 1</h3>"
        f"<p>{PARAGRAPH}</p><h3>項目 2</h3><p>{PARAGRAPH}</p>"
        "<h2>第2章 合成の構成</h2></body></html>"
    )
    _assert_hierarchy(_elements(source.encode(), "text/html", ".html", "html"))
