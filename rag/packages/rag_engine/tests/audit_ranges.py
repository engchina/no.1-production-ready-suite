"""監査のスタブが、モデルに渡された根拠の番号で範囲を返す補助（#4152）。"""

import re
from collections.abc import Mapping
from typing import Any

_LINE = re.compile(r"^(\d+)\| ?(.*)$", re.M)


def line_range(item: Mapping[str, Any], quote: str | None = None) -> dict[str, Any]:
    """モデルに渡した根拠（行頭の「番号|」）で、``quote`` を含む行の範囲（None は本文の全体）。"""
    evidence_id = str(item["evidence_id"])
    lines = [(int(match.group(1)), match.group(2)) for match in _LINE.finditer(str(item["text"]))]
    if not lines:
        return {"evidence_id": evidence_id, "line_start": None, "line_end": None}
    if quote is None:
        return {"evidence_id": evidence_id, "line_start": lines[0][0], "line_end": lines[-1][0]}
    numbers = [number for number, text in lines if text and (text in quote or quote in text)]
    assert numbers, "渡した根拠に無い原文を範囲にしようとした"
    return {"evidence_id": evidence_id, "line_start": numbers[0], "line_end": numbers[-1]}
