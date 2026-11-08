"""出所に基づく検索経路と native 融合済み得点の合算を検証する。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from rag_engine.retrieval.query_channels import expression_channels
from rag_engine.retrieval.weighted_rrf import RankedList, RrfWeights, fuse_rankings, source_weights

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_search_db_roundtrips import _OneHitPerQueryOracle


@pytest.mark.parametrize(
    "labels,expected",
    [
        (("質問の言い換え・分解",), ("vector",)),
        (("用語・同義語だけ",), ("keyword",)),
        (("原質問 + 用語・同義語",), ("keyword",)),
        (("質問の言い換え・分解", "用語・同義語だけ"), ("vector", "keyword")),
        ((), ("vector",)),
    ],
)
def test_channels_use_provenance_not_words(
    labels: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert expression_channels("合成追加", "合成原質問", {"合成追加": labels}) == expected
    assert expression_channels("Ａ", "a", {"Ａ": labels}) == ("hybrid",)
    assert expression_channels("追加", "原", {"追加": labels}, vector_only=True) == ("vector",)


async def test_terms_are_not_embedded_and_semantic_expansions_never_text_searched() -> None:
    calls = []

    class Oracle(_OneHitPerQueryOracle):
        async def hybrid_search(
            self,
            query: str,
            embedding: list[float],
            top_k: int,
            mode: SearchMode = SearchMode.HYBRID,
            filters: dict[str, str] | None = None,
        ) -> list[RetrievedChunk]:
            calls.append((query, mode, embedding))
            hits = await super().hybrid_search(query, embedding, top_k, mode, filters)
            if mode == SearchMode.HYBRID:
                hits[0].metadata["native_rrf_contribution"] = 1 / 61
            return hits

    class Embedding:
        async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            assert texts == ["原質問", "意味追加"]
            return [[1.0] for _ in texts]

    state = _SearchState()
    engine = AnswerEngine(
        Settings(),
        oracle=cast(Any, Oracle({"原質問": "c-0", "意味追加": "c-1", "用語追加": "c-2"})),
        genai=cast(Any, Embedding()),
    )
    await engine._search(
        SearchRequest(query="原質問"),
        state,
        retrieval_queries=["原質問", "意味追加", "用語追加", "用語追加"],
        expand_references=False,
        settings=SimpleNamespace(
            retrieval_query_sources={
                "意味追加": ("質問の言い換え・分解",),
                "用語追加": ("用語・同義語だけ",),
            }
        ),
    )
    assert [(q, mode) for q, mode, _ in calls] == [
        ("原質問", SearchMode.HYBRID),
        ("意味追加", SearchMode.VECTOR),
        ("用語追加", SearchMode.KEYWORD),
    ]
    assert calls[-1][2] == []
    rrf = state.retrieval_stages[-1]["rrf"]
    assert [r["channel"] for r in rrf["lists"]] == ["hybrid", "vector", "keyword"]
    assert rrf["lists"][0]["score_source"] == "oracle_wrrf"
    assert rrf["fusion_execution"] == "additional_to_native"


def test_native_score_is_scaled_once_without_reciprocal_of_fused_rank() -> None:
    weights = RrfWeights()
    budgets = source_weights(["原", "追加"], weights)
    result = fuse_rankings(
        [
            RankedList("原", "hybrid", ("b", "a"), native_scores=(0.009, 0.003)),
            RankedList("追加", "keyword", ("a",)),
        ],
        budgets,
        weights,
        k=60,
    )
    assert result.scores == pytest.approx({"b": 0.7 * 0.009, "a": 0.7 * 0.003 + 0.09 / 61})
    assert sum(c.value for c in result.contributions if c.chunk_id == "a") == pytest.approx(
        result.scores["a"]
    )


async def test_followup_semantic_query_does_not_become_another_original() -> None:
    calls = []

    class Oracle(_OneHitPerQueryOracle):
        async def hybrid_search(
            self,
            query: str,
            embedding: list[float],
            top_k: int,
            mode: SearchMode = SearchMode.HYBRID,
            filters: dict[str, str] | None = None,
        ) -> list[RetrievedChunk]:
            calls.append((query, mode))
            return await super().hybrid_search(query, embedding, top_k, mode, filters)

    class Embedding:
        async def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[1.0] for _ in texts]

    state = _SearchState()
    engine = AnswerEngine(
        Settings(),
        oracle=cast(Any, Oracle({"原": "c-0", "再検索": "c-1"})),
        genai=cast(Any, Embedding()),
    )
    request = SearchRequest(query="原")
    await engine._search(request, state, retrieval_queries=["原"], expand_references=False)
    await engine._search(request, state, retrieval_queries=["再検索"], expand_references=False)
    assert calls == [("原", SearchMode.HYBRID), ("再検索", SearchMode.VECTOR)]


@pytest.mark.parametrize("native", [True, False])
async def test_only_native_original_result_skips_extra_fusion(
    native: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from unittest.mock import Mock

    from app.rag import answer_engine
    from tests.test_answer_engine import FakeGenAi

    observations: dict[str, Mock] = {}

    @contextmanager
    def observe(operation: str, name: str) -> Iterator[Mock]:
        observation = Mock()
        observations[name] = observation
        yield observation

    class Oracle(_OneHitPerQueryOracle):
        async def hybrid_search(
            self,
            query: str,
            embedding: list[float],
            top_k: int,
            mode: SearchMode = SearchMode.HYBRID,
            filters: dict[str, str] | None = None,
        ) -> list[RetrievedChunk]:
            hits = await super().hybrid_search(query, embedding, top_k, mode, filters)
            if native:
                hits[0].metadata["native_rrf_contribution"] = 0.012
            return hits

    monkeypatch.setattr(answer_engine, "observe_call", observe)
    state = _SearchState()
    engine = AnswerEngine(
        Settings(), oracle=cast(Any, Oracle({"合成": "c-0"})), genai=cast(Any, FakeGenAi())
    )
    await engine._search(
        SearchRequest(query="合成"),
        state,
        retrieval_queries=["合成"],
        expand_references=False,
        defer_context=True,
    )
    observation = observations["検索結果の統合（RRF）"]
    assert observation.request.called
    assert (
        any(
            call.kwargs.get("params", {}).get("status") == "skipped"
            for call in observation.display.call_args_list
        )
        is native
    )
    diagnostic = observation.response.call_args.args[0]
    assert diagnostic["fusion_execution"] == ("native_only" if native else "outer_rrf")
    assert state.chunks["c-0"].metadata["rrf_score"] == pytest.approx(0.012 if native else 1 / 61)
