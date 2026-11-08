"""予算の設定と精排後の原文読取を外部 I/O なしで確認する。"""

from pathlib import Path
from typing import Any, cast

import pytest
from rag_engine.config import get_settings as engine_settings
from rag_engine.generation.answer_records import _stored_chunk_answer_record

from app.config import BACKEND_ROOT, Settings
from app.rag.answer_engine import AnswerEngine, _SearchState, build_engine_settings
from app.schemas.search import RetrievedChunk, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle


async def test_search_defers_original_context_until_ranked_anchors_are_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = FakeOracle()
    calls: list[list[str]] = []
    original = oracle.context_group_siblings

    async def siblings(
        anchors: list[RetrievedChunk],
        *,
        max_chunks_per_group: int,
        neighbor_child_count: int | None = None,
    ) -> list[RetrievedChunk]:
        calls.append([anchor.chunk_id for anchor in anchors])
        return await original(
            anchors,
            max_chunks_per_group=max_chunks_per_group,
            neighbor_child_count=neighbor_child_count,
        )

    monkeypatch.setattr(oracle, "context_group_siblings", siblings)
    engine = AnswerEngine(Settings(), oracle=cast(Any, oracle), genai=cast(Any, FakeGenAi()))
    state = _SearchState()
    result = await engine._search(
        SearchRequest(query="合成の質問"),
        state,
        retrieval_queries=["合成の質問"],
        candidate_limit=2,
        expand_references=False,
        defer_context=True,
    )
    assert not calls
    assert all(chunk.chunk_level == "child" for chunk in result.all_chunks)
    # 精排で最後の候補だけが起点に選ばれた場合、ほかの候補を親の読取へ渡さない。
    anchor = _stored_chunk_answer_record(result.child_chunks[-1])
    rows = await engine._context_chunks(state, anchors=[anchor])
    assert calls == [[anchor.chunk_uid]]
    assert any(row.chunk_level == "parent" for row in rows)
    await engine._context_chunks(state, anchors=[anchor])
    assert len(calls) == 1


def test_backend_budget_settings_reach_the_engine(tmp_path: Path) -> None:
    settings = Settings(rag_retrieval_candidate_limit=77, rag_rerank_candidate_limit=91)
    engine = build_engine_settings(settings, output_dir=tmp_path)
    assert engine.retrieval_candidate_limit == 77
    assert engine.rerank_candidate_limit == 91


def test_retrieval_defaults_match_backend_engine_and_env_example(tmp_path: Path) -> None:
    """件数の既定値は backend・rag_engine・.env.example で一致する。"""
    settings = Settings()
    engine = build_engine_settings(settings, output_dir=tmp_path)
    expected = {
        "RAG_RETRIEVAL_QUERY_LIMIT": (settings.rag_retrieval_query_limit, 60),
        "RAG_RETRIEVAL_CANDIDATE_LIMIT": (settings.rag_retrieval_candidate_limit, 140),
        "RAG_CANDIDATE_DIVERSITY_DEPTH": (settings.rag_candidate_diversity_depth, 420),
        "RAG_CANDIDATE_DIVERSITY_MAX_EXTRA": (settings.rag_candidate_diversity_max_extra, 20),
        "RAG_RERANK_CANDIDATE_LIMIT": (settings.rag_rerank_candidate_limit, 200),
        "RAG_RERANK_TOP_N": (settings.rag_rerank_top_n, 60),
        "RAG_SEARCH_TOP_K_DEFAULT": (settings.rag_search_top_k_default, 30),
        "RAG_EVIDENCE_BUDGET_CHARS": (settings.rag_evidence_budget_chars, 40000),
    }
    example = dict(
        line.split("=", 1)
        for line in (BACKEND_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    for name, (actual, value) in expected.items():
        assert actual == value, name
        assert int(example[name]) == value, name
    assert settings.rag_rerank_min_relevance_score == 0.3
    assert float(example["RAG_RERANK_MIN_RELEVANCE_SCORE"]) == 0.3
    assert (engine.retrieval_candidate_limit, engine.rerank_candidate_limit) == (140, 200)
    assert (engine.rerank_top_n, engine.rerank_min_relevance_score) == (60, 0.3)
    assert engine.evidence_budget_chars == 40000
    engine_defaults = engine_settings(environ={}, dotenv_path=None)
    assert (
        engine_defaults.retrieval_candidate_limit,
        engine_defaults.rerank_candidate_limit,
        engine_defaults.rerank_top_n,
        engine_defaults.rerank_min_relevance_score,
        engine_defaults.evidence_budget_chars,
    ) == (140, 200, 60, 0.3, 40000)


def test_default_total_budget_includes_enabled_reference_expansion(tmp_path: Path) -> None:
    from rag_engine.retrieval.candidate_budget import stage_limits

    engine = build_engine_settings(Settings(), output_dir=tmp_path)
    limits = stage_limits(engine, [])
    assert limits["fusion"] == 140
    assert limits["diversity"] == 20
    assert limits["reference"] == 4
    assert limits["article"] == (16 + 2) * (8 + 6)
    assert sum(limits.values()) == 416
    no_catalog = build_engine_settings(
        Settings(rag_article_catalog_enabled=False), output_dir=tmp_path
    )
    assert sum(stage_limits(no_catalog, []).values()) == 164
    disabled = build_engine_settings(
        Settings(rag_reference_expansion_enabled=False), output_dir=tmp_path
    )
    assert stage_limits(disabled, [])["reference"] == 0
