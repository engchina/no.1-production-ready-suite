"""判定の一致の集計（人手の判定と評価器の判定。#3418・#2577）。

評価器（LLM による判定・検索の評価器など）の出力を、人手の判定（正解のラベル）と照らして、件数・
一致率・Cohen の κ・混同行列・誤合格 / 誤不合格の率を求める。判定できなかった件（評価器のラベルが
無い件）は一致にも不一致にも数えず、件数（`unjudged`）と判定率（`coverage`）に出す。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LabelPair:
    """1 件の人手の判定と評価器の判定（評価器が判定できなければ None）。"""

    item_id: str
    human: str
    judge: str | None


@dataclass(frozen=True)
class Agreement:
    """一致の集計。

    `labels` はラベルの並び（混同行列の行・列の順）。`confusion[人手][評価器]` は件数。
    `positive` を合格のラベルにしたときの誤合格（人手は不合格・評価器は合格）と誤不合格の率は、
    人手のラベルごとの件数を分母にする（その件数が 0 なら None）。
    """

    labels: tuple[str, ...]
    total: int
    judged: int
    unjudged: int
    coverage: float | None
    accuracy: float | None
    kappa: float | None
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    false_pass_rate: float | None = None
    false_fail_rate: float | None = None


def cohen_kappa(pairs: Sequence[tuple[str, str]], labels: Sequence[str]) -> float | None:
    """Cohen の κ（偶然の一致を除いた一致の度合い）。件が無い・期待の一致が 1 なら None。"""
    total = len(pairs)
    if not total:
        return None
    observed = sum(1 for human, judge in pairs if human == judge) / total
    expected = sum(
        (sum(1 for human, _ in pairs if human == label) / total)
        * (sum(1 for _, judge in pairs if judge == label) / total)
        for label in labels
    )
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)


def agreement(
    items: Sequence[LabelPair],
    labels: Sequence[str],
    *,
    positive: str | None = None,
    negative: str | None = None,
    digits: int = 4,
) -> Agreement:
    """人手の判定と評価器の判定の一致を集計する。

    `labels` に無い人手のラベルは誤りとして ValueError にする（ラベルの集合を黙って広げない）。
    評価器のラベルが `labels` に無い・None の件は、判定できなかった件に数える。
    """
    known = tuple(labels)
    unknown = sorted({item.human for item in items if item.human not in known})
    if unknown:
        raise ValueError(f"人手のラベルが語彙に無い: {', '.join(unknown)}")
    pairs = [(item.human, item.judge) for item in items if item.judge in known]
    judged = len(pairs)
    total = len(items)
    confusion = {human: {judge: 0 for judge in known} for human in known}
    for human, judge in pairs:
        confusion[human][str(judge)] += 1
    typed = [(human, str(judge)) for human, judge in pairs]

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, digits) if denominator else None

    kappa = cohen_kappa(typed, known)
    false_pass = false_fail = None
    if positive is not None and negative is not None:
        negatives = sum(confusion[negative].values())
        positives = sum(confusion[positive].values())
        false_pass = rate(confusion[negative][positive], negatives)
        false_fail = rate(confusion[positive][negative], positives)
    return Agreement(
        labels=known,
        total=total,
        judged=judged,
        unjudged=total - judged,
        coverage=rate(judged, total),
        accuracy=rate(sum(1 for human, judge in typed if human == judge), judged),
        kappa=None if kappa is None else round(kappa, digits),
        confusion=confusion,
        false_pass_rate=false_pass,
        false_fail_rate=false_fail,
    )
