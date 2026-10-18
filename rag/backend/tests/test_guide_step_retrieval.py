"""業務ガイドの手順ごとの検索と参照の頁の固定(#2395)。OCI・Oracle はスタブ(合成の fixture)。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import Settings
from app.rag.answer_engine import AnswerEngine
from app.rag.support_guide import reference_issues
from app.rag.support_guide_runtime import (
    apply_guide_to_diagnostics,
    guide_step_plan,
    match_guide,
)
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from app.schemas.support_guide import SupportGuideContent
from tests.test_answer_engine import FakeGenAi, FakeOracle, _doc_chunk, _evidence_ids, _fake_llm


class GuideStepOracle(FakeOracle):
    """質問の文では手順 A の資料だけを、手順 B の手がかり・参照の頁でだけ手順 B の資料を返す。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.step_a = _doc_chunk("doc-a", "設定 A は画面 A で変更します。")
        self.step_b = _doc_chunk("doc-b", "設定 A を変えたあとは、画面 B で反映ボタンを押します。")
        self.page = _doc_chunk("doc-c", "画面 B の項目の説明です。反映の前に確認します。")

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        filters = dict(filters or {})
        self.calls.append((query, filters))
        if "page_ranges" in filters:
            return [self.page]
        return [self.step_b] if "画面 B" in query else [self.step_a]

    async def context_group_siblings(
        self, anchors: list[RetrievedChunk], *, max_chunks_per_group: int
    ) -> list[RetrievedChunk]:
        return []


def _guide(decision: str = "answer") -> dict[str, Any]:
    return {
        "guide_id": "g-1",
        "revision": 2,
        "decision": decision,
        "step_plan": [
            {"id": "s1", "title": "画面 A で変える", "retrieval_hints": [], "references": []},
            {
                "id": "s2",
                "title": "画面 B で反映する",
                "retrieval_hints": ["画面 B 反映"],
                "references": [
                    {
                        "document_id": "doc-c",
                        "title": "文書 C",
                        "page_ranges": [{"page_start": 2, "page_end": 3}],
                    }
                ],
            },
        ],
    }


async def _answer(monkeypatch: pytest.MonkeyPatch, oracle: GuideStepOracle, **settings: Any) -> Any:
    import rag_engine.adapters.oci as engine_oci

    monkeypatch.setattr(engine_oci, "parse_text_response", _fake_llm)
    engine = AnswerEngine(
        Settings(
            rag_query_strategy="simple_retrieval",
            rag_answer_flow="standard_rag",
            rag_rerank_enabled=False,
            **settings,
        ),
        oracle=oracle,  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )
    return await engine.run(
        SearchRequest(query="設定 A の変更方法は？", filters={"knowledge_base_id": "kb-1"})
    )


async def test_guide_steps_add_step_b_and_reference_page_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """手順ごとに検索し、手順 B の根拠と参照の頁の根拠を回答の材料に入れる。"""
    oracle = GuideStepOracle()

    outcome = await _answer(monkeypatch, oracle, rag_support_guide=_guide())

    assert {"doc-a:c1", "doc-b:c1", "doc-c:c1"} <= _evidence_ids(outcome)
    # 参照の頁の検索は、その文書の頁の範囲に絞る(利用者の KB の範囲は保つ)。
    page_filters = [filters for _, filters in oracle.calls if "page_ranges" in filters]
    assert page_filters
    assert json.loads(page_filters[0]["page_ranges"]) == [
        {"document_id": "doc-c", "page_start": 2, "page_end": 3}
    ]
    assert page_filters[0]["knowledge_base_id"] == "kb-1"
    trace = outcome.diagnostics["guide_step_retrieval"]
    assert trace["guide_id"] == "g-1" and trace["llm_calls"] == 0
    step_b = trace["steps"][1]
    assert step_b["has_references"] is True
    assert step_b["evidence_count"] >= 1
    assert [search["kind"] for search in step_b["searches"]] == ["reference", "step"]
    assert any(
        step["name"] == "業務ガイドの手順ごとの検索"
        for step in outcome.diagnostics["execution_steps"]
    )


async def test_guide_steps_skip_without_answer_decision_or_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """確認・引き継ぎのガイド、設定で無効なときは手順ごとに検索しない。"""
    oracle = GuideStepOracle()
    outcome = await _answer(monkeypatch, oracle, rag_support_guide=_guide(decision="clarify"))
    assert "guide_step_retrieval" not in outcome.diagnostics
    assert "doc-b:c1" not in _evidence_ids(outcome)

    disabled = GuideStepOracle()
    outcome = await _answer(
        monkeypatch,
        disabled,
        rag_support_guide=_guide(),
        rag_guide_step_retrieval_enabled=False,
    )
    assert outcome.diagnostics["guide_step_retrieval"]["skipped"] == "disabled"
    assert not any("page_ranges" in filters for _, filters in disabled.calls)


def test_with_page_ranges_intersects_user_scope() -> None:
    """利用者の確認で絞った頁の範囲があれば、重なる頁だけを検索する(範囲を広げない)。"""
    from app.rag.answer_engine import _with_page_ranges

    wanted = [{"document_id": "doc-c", "page_start": 2, "page_end": 5}]
    plain = _with_page_ranges({"knowledge_base_id": "kb-1"}, wanted)
    assert plain is not None
    assert json.loads(plain["page_ranges"]) == wanted
    scoped = _with_page_ranges(
        {"page_ranges": json.dumps([{"document_id": "doc-c", "page_start": 4, "page_end": 9}])},
        wanted,
    )
    assert scoped is not None
    assert json.loads(scoped["page_ranges"]) == [
        {"document_id": "doc-c", "page_start": 4, "page_end": 5}
    ]
    other = json.dumps([{"document_id": "doc-x", "page_start": 1, "page_end": 2}])
    assert _with_page_ranges({"page_ranges": other}, wanted) is None


def test_build_engine_settings_passes_guide_step_settings(tmp_path: Any) -> None:
    """手順ごとの検索の設定と追加の検索の枠を rag_engine の設定へ渡す。"""
    from app.rag.answer_engine import build_engine_settings

    default = build_engine_settings(Settings(), output_dir=tmp_path)
    custom = build_engine_settings(
        Settings(
            rag_guide_step_retrieval_enabled=False,
            rag_guide_step_max_queries=2,
            rag_guide_step_chunks_per_step=3,
            rag_guide_reference_max_chunks=1,
            rag_guide_step_deadline_seconds=2.5,
            rag_extra_retrieval_max_queries=5,
        ),
        output_dir=tmp_path,
    )

    assert default.guide_step_retrieval_enabled is True
    assert (default.guide_step_max_queries, default.extra_retrieval_max_queries) == (4, 6)
    assert custom.guide_step_retrieval_enabled is False
    assert (custom.guide_step_max_queries, custom.guide_step_chunks_per_step) == (2, 3)
    assert (custom.guide_reference_max_chunks, custom.guide_step_deadline_seconds) == (1, 2.5)
    assert custom.extra_retrieval_max_queries == 5


def _content(**changes: Any) -> SupportGuideContent:
    base: dict[str, Any] = {
        "title": "設定の変更",
        "goal": {"expected_result": "設定が変わる", "match_terms": ["設定 A"]},
        "conditions": [
            {
                "id": "kind",
                "label": "種類",
                "allowed_values": ["甲", "乙"],
                "question": "種類は？",
            }
        ],
        "steps": [
            {"id": "s1", "title": "画面 A で変える", "retrieval_hints": ["画面 A"]},
            {
                "id": "s2",
                "title": "画面 B で反映する",
                "depends_on": ["s1"],
                "references": [
                    {"document_id": "doc-b", "page_ranges": [{"page_start": 4, "page_end": 6}]}
                ],
            },
            {
                "id": "s3",
                "title": "乙の場合の手順",
                "references": [{"document_id": "doc-c", "page_ranges": [{"page_start": 7}]}],
            },
        ],
        "branches": [
            {"id": "b1", "when": {"condition_id": "kind", "values": ["乙"]}, "goto_step": "s3"}
        ],
    }
    base.update(changes)
    return SupportGuideContent.model_validate(base)


def test_schema_v2_page_ranges_and_step_references() -> None:
    content = _content()

    assert content.schema_version == 2
    # 終わりの頁を省けば 1 頁。
    assert content.steps[2].references[0].page_ranges[0].page_end == 7
    with pytest.raises(ValueError, match="頁の範囲の終わり"):
        _content(
            references=[{"document_id": "d", "page_ranges": [{"page_start": 5, "page_end": 2}]}]
        )
    with pytest.raises(ValueError):
        SupportGuideContent.model_validate({**content.model_dump(), "schema_version": 1})


def test_guide_step_plan_excludes_steps_of_branches_that_do_not_apply() -> None:
    """分かっている条件で外れた分岐の手順は、手順ごとの検索に渡さない。"""
    match = match_guide([("g", 1, _content())], "設定 A を変えたい", {"kind": "甲"})
    assert match is not None

    plan = guide_step_plan(match)

    assert [step["id"] for step in plan] == ["s1", "s2"]
    assert plan[1]["references"] == [
        {"document_id": "doc-b", "title": "", "page_ranges": [{"page_start": 4, "page_end": 6}]}
    ]
    applies = match_guide([("g", 1, _content())], "設定 A を変えたい", {"kind": "乙"})
    assert applies is not None
    assert [step["id"] for step in guide_step_plan(applies)] == ["s1", "s2", "s3"]


async def test_reference_issues_check_step_references() -> None:
    async def lookup(document_id: str) -> str | None:
        return "見つかりません" if document_id == "doc-c" else None

    issues = await reference_issues(_content(), lookup)

    assert [issue.path for issue in issues] == ["steps[2].references[0]"]


def test_step_retrieval_record_goes_to_guide_diagnostics_not_envelope() -> None:
    diagnostics: dict[str, Any] = {
        "envelope": {"outcome": "answered"},
        "guide_step_retrieval": {"steps": [{"id": "s2", "evidence_count": 1}]},
    }

    apply_guide_to_diagnostics(diagnostics, {"guide_id": "g-1", "revision": 2})

    assert diagnostics["guide"]["step_retrieval"]["steps"][0]["id"] == "s2"
    assert "step_retrieval" not in diagnostics["envelope"]["guide"]
    assert "guide_step_retrieval" not in diagnostics
