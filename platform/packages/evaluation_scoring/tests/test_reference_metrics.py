"""参考の指標（順位の指標と語の重なり）の手計算の値（#4714）。"""

from __future__ import annotations

import math

import pytest

from pr_evaluation_scoring.overlap import bleu, rouge_l, rouge_n
from pr_evaluation_scoring.retrieval import average_precision, ndcg_at_k, precision_at_k

RETRIEVED = ["a", "x", "b", "y"]
RELEVANT = {"a", "b", "c"}


def test_precision_counts_misses_kept_in_the_ranked_list() -> None:
    assert precision_at_k(RETRIEVED, RELEVANT, 10) == pytest.approx(0.5)
    assert precision_at_k(RETRIEVED, RELEVANT, 2) == pytest.approx(0.5)
    assert precision_at_k(RETRIEVED, RELEVANT, 1) == pytest.approx(1.0)
    assert precision_at_k([], RELEVANT, 10) == 0.0


def test_ranking_metrics_count_a_duplicate_once() -> None:
    assert precision_at_k(["a", "a", "x"], {"a"}, 10) == pytest.approx(0.5)
    assert average_precision(["a", "a", "x"], {"a"}) == pytest.approx(1.0)


def test_ndcg_is_lower_when_a_miss_comes_first() -> None:
    ideal = 1 + 1 / math.log2(3) + 1 / math.log2(4)
    assert ndcg_at_k(RETRIEVED, RELEVANT, 3) == pytest.approx(1.5 / ideal)
    assert ndcg_at_k(RETRIEVED, RELEVANT, 10) == pytest.approx(1.5 / ideal)
    assert ndcg_at_k(["x", "a"], {"a"}, 3) == pytest.approx(1 / math.log2(3))
    assert ndcg_at_k(["a"], set(), 3) == 0.0


def test_average_precision_divides_by_all_relevant_documents() -> None:
    assert average_precision(RETRIEVED, RELEVANT) == pytest.approx((1 + 2 / 3) / 3)
    assert average_precision(["x"], RELEVANT) == 0.0
    assert average_precision(RETRIEVED, set()) == 0.0


AFFIRMATIVE = ["行き", "ます"]
NEGATIVE = ["行き", "ませ", "ん"]


def test_bleu_separates_affirmative_and_negative_tokens() -> None:
    assert bleu(AFFIRMATIVE, AFFIRMATIVE, order=4) == pytest.approx(1.0)
    expected = math.exp(1 - 3 / 2) * (2 / 3)
    assert bleu(AFFIRMATIVE, NEGATIVE, order=1) == pytest.approx(expected)
    assert bleu(AFFIRMATIVE, NEGATIVE, order=1) < 1.0


def test_bleu_two_uses_both_orders() -> None:
    expected = math.exp(1 - 3 / 2) * math.exp(0.5 * math.log(2 / 3) + 0.5 * math.log(1 / 2))
    assert bleu(AFFIRMATIVE, NEGATIVE, order=2) == pytest.approx(expected)


def test_bleu_of_empty_tokens_is_zero() -> None:
    assert bleu([], NEGATIVE, order=1) == 0.0
    assert bleu(NEGATIVE, [], order=1) == 0.0


def test_rouge_separates_affirmative_and_negative_tokens() -> None:
    assert rouge_n(AFFIRMATIVE, AFFIRMATIVE, 1) == pytest.approx(1.0)
    assert rouge_n(AFFIRMATIVE, NEGATIVE, 1) == pytest.approx(0.4)
    assert rouge_n(AFFIRMATIVE, NEGATIVE, 2) == 0.0
    assert rouge_l(AFFIRMATIVE, NEGATIVE) == pytest.approx(0.4)
    assert rouge_l(["甲", "乙", "丙"], ["甲", "丙"]) == pytest.approx(0.8)
    assert rouge_n([], NEGATIVE, 1) == 0.0
    assert rouge_l([], NEGATIVE) == 0.0
