"""ツリー検索(reasoning_tree_search / PageIndex-lite)の決定論部分。

navigation 要約チャンク(content_kind=section_summary、`app/rag/navigation.py` が生成)を
候補として LLM に提示し、選ばれた section の配下 chunk を section_path フィルタで検索する。
LLM 呼び出し(`select_relevant_sections`)は pipeline 側で行い、このモジュールは
候補整形・選択結果の検証だけを持つ(unit test 可能な純ロジック)。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.search import RetrievedChunk

SECTION_SUMMARY_CONTENT_KIND = "section_summary"
# LLM へ提示する候補 section 数と、1 候補あたりの要約文字数の上限(プロンプト肥大防止)。
MAX_SECTION_CANDIDATES = 24
MAX_SUMMARY_CHARS = 200


@dataclass(frozen=True)
class SectionCandidate:
    """LLM へ提示する section 候補(navigation 要約チャンク由来)。"""

    section_path: str
    title: str
    summary: str


def build_section_candidates(chunks: list[RetrievedChunk]) -> list[SectionCandidate]:
    """navigation 要約チャンクから section 候補を作る(section_path 単位で重複除去)。"""
    candidates: list[SectionCandidate] = []
    seen: set[str] = set()
    for chunk in chunks:
        section_path = str(chunk.metadata.get("section_path") or "").strip()
        if not section_path or section_path.casefold() in seen:
            continue
        seen.add(section_path.casefold())
        text = (chunk.text or "").strip()
        title, _, summary = text.partition(":")
        if not summary.strip():
            title, summary = section_path, text
        candidates.append(
            SectionCandidate(
                section_path=section_path,
                title=title.strip() or section_path,
                summary=" ".join(summary.split())[:MAX_SUMMARY_CHARS],
            )
        )
        if len(candidates) >= MAX_SECTION_CANDIDATES:
            break
    return candidates


def candidate_lines(candidates: list[SectionCandidate]) -> list[str]:
    """LLM へ提示する「パス | タイトル | 要約」形式の行を作る。"""
    return [
        f"{candidate.section_path} | {candidate.title} | {candidate.summary}"
        for candidate in candidates
    ]


def selected_section_paths(
    candidates: list[SectionCandidate],
    selected_numbers: list[int],
    *,
    max_sections: int,
) -> list[str]:
    """LLM が選んだ番号(1 始まり)を検証済みの section_path へ写す。範囲外は無視する。"""
    paths: list[str] = []
    for number in selected_numbers:
        if not 1 <= number <= len(candidates):
            continue
        path = candidates[number - 1].section_path
        if path not in paths:
            paths.append(path)
        if len(paths) >= max_sections:
            break
    return paths
