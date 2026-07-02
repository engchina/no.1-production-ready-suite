"""ツリー検索(reasoning_tree_search)の決定論部分と LLM 応答解析のテスト。"""

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.rag.reasoning_tree import (
    MAX_SECTION_CANDIDATES,
    build_section_candidates,
    candidate_lines,
    selected_section_paths,
)
from app.schemas.search import RetrievedChunk


def _summary_chunk(section_path: str, text: str, chunk_id: str = "") -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id=chunk_id or f"doc-1:{section_path}",
        text=text,
        score=0.8,
        file_name="manual.pdf",
        metadata={"section_path": section_path, "content_kind": "section_summary"},
    )


def test_build_section_candidates_parses_title_and_dedupes() -> None:
    """text の「タイトル: 要約」を分解し、section_path 単位で重複除去する。"""
    chunks = [
        _summary_chunk("第1章 > 経費", "経費精算: 承認フローと上限金額を定める。"),
        _summary_chunk("第1章 > 経費", "経費精算: 重複行。", chunk_id="doc-1:dup"),
        _summary_chunk("第2章 > 旅費", "旅費規程の要約のみ(区切りなし)"),
        _summary_chunk("", "section_path 欠損は除外"),
    ]
    candidates = build_section_candidates(chunks)
    assert [c.section_path for c in candidates] == ["第1章 > 経費", "第2章 > 旅費"]
    assert candidates[0].title == "経費精算"
    assert "承認フロー" in candidates[0].summary
    # 区切りが無い場合は section_path をタイトルに使う。
    assert candidates[1].title == "第2章 > 旅費"


def test_build_section_candidates_caps_count() -> None:
    chunks = [_summary_chunk(f"第{i}章", f"第{i}章: 要約") for i in range(40)]
    assert len(build_section_candidates(chunks)) == MAX_SECTION_CANDIDATES


def test_candidate_lines_and_selection_mapping() -> None:
    """LLM の 1 始まり番号を検証済み section_path へ写し、範囲外・重複は無視する。"""
    candidates = build_section_candidates(
        [
            _summary_chunk("第1章", "第1章: A"),
            _summary_chunk("第2章", "第2章: B"),
            _summary_chunk("第3章", "第3章: C"),
        ]
    )
    lines = candidate_lines(candidates)
    assert lines[0].startswith("第1章 | 第1章 | A")
    assert selected_section_paths(candidates, [2, 9, 2, 1], max_sections=3) == ["第2章", "第1章"]
    assert selected_section_paths(candidates, [1, 2, 3], max_sections=2) == ["第1章", "第2章"]
    assert selected_section_paths(candidates, [], max_sections=3) == []


class _SectionSelectLlm(OciEnterpriseAiClient):
    """generate 応答を固定してツリー選択の解析を検証する。"""

    def __init__(self, raw: str | None = None, error: Exception | None = None) -> None:
        super().__init__()
        self.raw = raw or "[]"
        self.error = error

    async def generate(self, prompt: str, context: str, *, system_prompt: str | None = None) -> str:
        if self.error is not None:
            raise self.error
        return self.raw


async def test_select_relevant_sections_parses_numbers() -> None:
    llm = _SectionSelectLlm('["2", "1", "nope", "99"]')
    selected = await llm.select_relevant_sections("承認条件", ["s1", "s2", "s3"], max_sections=3)
    assert selected == [2, 1]


async def test_select_relevant_sections_degrades_on_failure_or_empty() -> None:
    failing = _SectionSelectLlm(error=RuntimeError("down"))
    assert await failing.select_relevant_sections("q", ["s1"]) == []
    empty = _SectionSelectLlm("回答できません")
    assert await empty.select_relevant_sections("q", ["s1"]) == []
    assert await _SectionSelectLlm('["1"]').select_relevant_sections("q", []) == []
