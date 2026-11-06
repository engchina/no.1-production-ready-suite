"""構造化producerの前提・照会だけを一組として扱う内部契約。"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class AtomicSourceGroup(StrEnum):
    PREMISE = "premise"
    DATA = "data"


KEY = "_atomic_source_group"
OMISSION = "_atomic_source_omission"


def trusted(item: Mapping[str, Any]) -> bool:
    """外部JSONの文字列ではなく、実producerが付けた型だけを信頼する。"""
    return isinstance(item.get(KEY), AtomicSourceGroup)


def omission_item(source_id: str, source: str, role: AtomicSourceGroup) -> dict[str, Any]:
    """上限超過を空の成功や部分的な結果として渡さず、省略を記録する。"""
    return {"id": source_id, "source": source, "text": "", KEY: role, OMISSION: "producer_field_limit"}
