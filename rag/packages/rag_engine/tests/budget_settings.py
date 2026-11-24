"""組分け・縮約の論理を小さな数で確かめるため、根拠の文字予算をモデルの入力の上限と切り離した設定（#5813）。

本番の ``Settings.evidence_budget_chars`` は回答のモデルの入力の上限の 1/2 で決まる（その導出は
``test_evidence_budget_derivation.py`` で確かめる）。ここでは予算の値だけを固定し、入力の上限（token の判定）は
各テストの設定のままにする。
"""

from dataclasses import dataclass, fields
from typing import Any

from rag_engine.config import Settings


@dataclass(frozen=True)
class BudgetSettings(Settings):
    """根拠の文字予算を ``fixed_evidence_budget_chars`` に固定した設定。"""

    fixed_evidence_budget_chars: int = 0

    @property
    def evidence_budget_chars(self) -> int:
        return self.fixed_evidence_budget_chars


def with_evidence_budget(settings: Settings, chars: int, **changes: Any) -> BudgetSettings:
    """``settings`` の値を写し、根拠の文字予算を ``chars`` 字に固定する。"""
    values = {field.name: getattr(settings, field.name) for field in fields(Settings)}
    return BudgetSettings(**{**values, **changes, "fixed_evidence_budget_chars": chars})
