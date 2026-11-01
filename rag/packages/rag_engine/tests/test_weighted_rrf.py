"""一覧の寄与を合成順位で厳密に検証する。"""

import pytest

from rag_engine.retrieval.weighted_rrf import RankedList, RrfWeights, fuse_rankings, source_weights


def test_defaults_use_one_pass_with_a_shared_additional_budget():
    weights = RrfWeights()
    sources = source_weights(["原質問", "追加 A", "追加 B", "追加 A", "原質問"], weights)
    assert sources == pytest.approx({"原質問": 0.7, "追加 A": 0.15, "追加 B": 0.15})
    lists = [RankedList(query, channel, ("a", "b")) for query in sources for channel in ("vector", "keyword")]
    result = fuse_rankings(lists, sources, weights, k=60)
    assert [entry.weight for entry in result.lists] == pytest.approx([7/11, .7/11, 1.5/11, .15/11, 1.5/11, .15/11])
    assert result.scores == pytest.approx({"a": 1 / 61, "b": 1 / 62})
    assert sum(c.value for c in result.contributions if c.chunk_id == "a") == pytest.approx(result.scores["a"])


def test_one_additional_expression_exact_weights():
    w = RrfWeights()
    s = source_weights(["q", "a"], w)
    result = fuse_rankings([RankedList(q, c, ("x",)) for q in s for c in ("vector", "keyword")], s, w, k=60)
    assert [x.weight for x in result.lists] == pytest.approx([7/11, .7/11, 3/11, .3/11])


def test_empty_failure_and_vector_only_do_not_redistribute():
    w = RrfWeights(vector=3, keyword=1, original=1, additional=1)
    s = source_weights(["q", "hyde"], w)
    result = fuse_rankings(
        [
            RankedList("q", "vector", (), status="failed", error="TimeoutError"),
            RankedList("q", "keyword", ()),
            RankedList("hyde", "vector", ("a", "a", "b")),
        ],
        s,
        w,
        k=60,
    )
    assert result.scores == pytest.approx({"a": 0.375 / 61, "b": 0.375 / 62})
    assert [x.status for x in result.lists] == ["failed", "empty", "completed"]
    assert len(result.lists) == 3


def test_no_additional_expression_normalizes_original_only():
    w = RrfWeights(original=0, additional=1)
    assert source_weights([" q ", "q", ""], w) == {"q": 1}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"vector": -1},
        {"vector": 0, "keyword": 0},
        {"original": 0, "additional": 0},
        {"additional": float("nan")},
        {"keyword": float("inf")},
    ],
)
def test_invalid_ratios(kwargs):
    with pytest.raises(ValueError):
        RrfWeights(**kwargs)


def test_trace_details_use_the_same_calculation_and_safe_shared_format():
    import json

    from rag_engine.retrieval.weighted_rrf import fusion_details

    w = RrfWeights()
    s = source_weights(["q", "a"], w)
    r = fuse_rankings(
        [
            RankedList("q", "vector", ("x",), sources=("原質問",)),
            RankedList("a", "keyword", (), status="failed", error="TimeoutError", sources=("用語・同義語",)),
        ],
        s,
        w,
        k=60,
    )
    details = fusion_details(r, w, s, 60)
    assert details["code"]
    actual = json.loads(details["code"][0]["text"])
    assert actual["method_ratio"] == [10, 1]
    assert actual["source_ratio"] == [7, 3]
    assert actual["rank_constant"] == 60
    assert actual["lists"][1]["status"] == "failed"
    assert actual["lists"][1]["weight"] == pytest.approx(.3/11)
    assert actual["contributions"][0]["contribution"] == r.scores["x"]


def test_normalized_duplicates_and_large_finite_ratios():
    w = RrfWeights(1e308, 1e308, 1e308, 1e308)
    s = source_weights(["Ａ", "a", " B ", "b"], w)
    assert s == {"Ａ": 0.5, "B": 0.5}
    r = fuse_rankings(
        [RankedList("Ａ", "vector", ("x",)), RankedList("a", "vector", ("x",)), RankedList("B", "keyword", ("y",))], s, w, k=60
    )
    assert len(r.lists) == 2
    assert r.scores == pytest.approx({"x": 0.25 / 61, "y": 0.25 / 61})


def test_zero_weight_distinguishes_deduplication_from_candidate_admission():
    weights = RrfWeights(vector=0, keyword=1)
    sources = source_weights(["q"], weights)
    result = fuse_rankings([RankedList("q", "vector", ("a",)), RankedList("q", "keyword", ("b",))], sources, weights, k=60)
    record = result.diagnostics(weights, sources, 60)
    assert record["raw_count"] == 2
    assert record["unique_count"] == 2
    assert record["candidate_count"] == 1
    assert result.scores == pytest.approx({"b": 1 / 61})
