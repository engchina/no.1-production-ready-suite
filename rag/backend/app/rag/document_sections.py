"""文書の章節(章節ナビゲーション)の組み立てと検証(#713)。

章節は「並び順 + 階層(1〜6)」の平らな一覧で持つ(Markdown の見出しと同じ。親は並びから決まる)。
人の修正が無ければ、抽出結果の章節(``DocumentNavigationNode``)をこの形に写して返す。人の修正が
あればそれを返し、人が名前・ページを変えていない抽出の章節は、ページ範囲を今の抽出結果に合わせる。
"""

from collections.abc import Mapping, Sequence

from rag_parser_core.extraction import DocumentNavigationNode

from app.schemas.document import DocumentSection


def sections_from_navigation(nodes: Sequence[DocumentNavigationNode]) -> list[DocumentSection]:
    """抽出結果の章節を、並び順 + 階層の一覧に写す(階層は 1〜6 に収める)。"""
    sections: list[DocumentSection] = []
    for node in nodes:
        title = node.title.strip()
        if not title:
            continue
        previous = sections[-1].level if sections else 0
        sections.append(
            DocumentSection(
                id=node.section_id,
                title=title[:200],
                level=max(1, min(node.depth or 1, previous + 1, 6)),
                page_start=node.page_start,
                page_end=node.page_end,
                origin="extraction",
                source_section_id=node.section_id,
            )
        )
    return sections


def refresh_from_extraction(
    sections: Sequence[DocumentSection], extracted: Sequence[DocumentSection]
) -> list[DocumentSection]:
    """人が変えていない抽出の章節のページ範囲を、今の抽出結果に合わせる。"""
    by_source = {section.source_section_id: section for section in extracted}
    refreshed: list[DocumentSection] = []
    for section in sections:
        source = by_source.get(section.source_section_id) if not section.edited else None
        refreshed.append(
            section.model_copy(
                update={"page_start": source.page_start, "page_end": source.page_end}
            )
            if source is not None
            else section
        )
    return refreshed


def section_errors(sections: Sequence[DocumentSection], page_count: int | None) -> list[str]:
    """保存できない理由(id の重複・階層の飛び・ページ範囲)。問題が無ければ空。"""
    errors: list[str] = []
    seen: set[str] = set()
    previous_level = 0
    for index, section in enumerate(sections, start=1):
        label = f"{index} 行目「{section.title}」"
        if section.id in seen:
            errors.append(f"{label}: id が重複しています。")
        seen.add(section.id)
        if section.level > previous_level + 1:
            errors.append(f"{label}: 階層が直前の章節より 2 段以上深くなっています。")
        previous_level = section.level
        start, end = section.page_start, section.page_end
        if start is not None and end is not None and start > end:
            errors.append(f"{label}: 開始ページが終了ページより後ろです。")
        if page_count is not None and any(
            page is not None and page > page_count for page in (start, end)
        ):
            errors.append(f"{label}: ページが文書のページ数({page_count})を超えています。")
    return errors


def stored_sections(raw: object) -> list[DocumentSection]:
    """保存済みの章節の JSON を読む(壊れた行は飛ばす)。"""
    if not isinstance(raw, list):
        return []
    sections: list[DocumentSection] = []
    for item in raw:
        if isinstance(item, Mapping):
            try:
                sections.append(DocumentSection.model_validate(dict(item)))
            except ValueError:
                continue
    return sections
