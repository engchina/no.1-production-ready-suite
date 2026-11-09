"""回答と標準回答の語の重なり（BLEU・ROUGE。合否に使わない参考値）。

語の並びの一致だけを見るので、言い換えは低く、数値を 1 つ誤っても高く出る。
分割は言語ごとに使う側が行い、ここは token 列だけを受け取る。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence


def _ngrams(tokens: Sequence[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]


def bleu(candidate: Sequence[str], reference: Sequence[str], *, order: int) -> float:
    """1〜order の語の連なりを同じ重みで見る BLEU（add-one 平滑）。作れない長さは飛ばす。"""
    if not candidate or not reference:
        return 0.0
    log_precision = 0.0
    for n in range(1, order + 1):
        counts = Counter(_ngrams(candidate, n))
        if not counts:
            continue
        available = Counter(_ngrams(reference, n))
        matched = sum(min(count, available[gram]) for gram, count in counts.items())
        log_precision += math.log((matched + 1) / (counts.total() + 1)) / order
    penalty = (
        1.0 if len(candidate) > len(reference) else math.exp(1 - len(reference) / len(candidate))
    )
    return penalty * math.exp(log_precision)


def _f_score(overlap: int, candidate_count: int, reference_count: int) -> float:
    if not overlap:
        return 0.0
    precision = overlap / candidate_count
    recall = overlap / reference_count
    return 2 * precision * recall / (precision + recall)


def rouge_n(candidate: Sequence[str], reference: Sequence[str], n: int) -> float:
    """語の連なり（重複なし）の F 値。"""
    candidate_grams = set(_ngrams(candidate, n))
    reference_grams = set(_ngrams(reference, n))
    return _f_score(
        len(candidate_grams & reference_grams), len(candidate_grams), len(reference_grams)
    )


def rouge_l(candidate: Sequence[str], reference: Sequence[str]) -> float:
    """最長共通部分列の F 値。"""
    previous = [0] * (len(reference) + 1)
    for token in candidate:
        current = [0]
        for index, other in enumerate(reference):
            current.append(
                previous[index] + 1 if token == other else max(previous[index + 1], current[index])
            )
        previous = current
    return _f_score(previous[-1], len(candidate), len(reference))
