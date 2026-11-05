"""右辺も式である等式を途中で切らず、各等号の両辺を確かめる。"""

import pytest

from rag_engine.generation.applications import arithmetic_mismatch


@pytest.mark.parametrize(
    "formula",
    [
        "(3 × 800) + (2 × 500) = 2400 + 1000 = 3400円",
        "3400円 = 2400円 + 1000円",
        "2+3=1+4=5",
        "2*3=6=12/2",
        "2-3=-1",
        "2万円+3万円=1万円+4万円=5万円",
    ],
)
def test_equal_expressions_do_not_reject_valid_result(formula: str) -> None:
    assert arithmetic_mismatch(formula) == ""


@pytest.mark.parametrize(
    "formula",
    [
        "(3 × 800) + (2 × 500) = 2400 + 900 = 3400円",
        "(3 × 800) + (2 × 500) = 2400 + 1000 = 3000円",
        "2+3=5=6",
        "2+3=1+3=5",
        "2万円+3万円=1万円+4万円=5円",
    ],
)
def test_wrong_intermediate_or_last_value_is_rejected(formula: str) -> None:
    assert arithmetic_mismatch(formula)


def test_separate_equations_are_not_joined_across_prose() -> None:
    assert arithmetic_mismatch("Aは3×800=2400円、Bは2×500=1000円。合計は2400+1000=3400円。") == ""
    assert arithmetic_mismatch("Aは3×800=2400円、Bは2×500=1000円。合計は2400+1000=3000円。")
