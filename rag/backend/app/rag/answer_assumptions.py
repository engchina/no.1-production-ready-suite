"""確認を省略する方針を、未確定の条件とともに回答生成へ渡す。"""

from collections.abc import Mapping
from typing import Any


def with_answer_assumptions(
    payload: Mapping[str, Any] | None,
    *,
    policy: str,
    guide: Mapping[str, Any],
) -> dict[str, Any]:
    """保存値を変えず、今回の回答用に方針と業務ガイドの未確定の条件を重ねる。"""
    result = dict(payload or {})
    # 保存された補助情報から、別の回答の方針を引き継がない。
    result.pop("assume_unconfirmed", None)
    result.pop("unconfirmed_conditions", None)
    if policy != "assume":
        return result
    pending = [item for item in guide.get("clarifications", []) if isinstance(item, dict)]
    result.update(assume_unconfirmed=True, unconfirmed_conditions=pending)
    return result
