"""複数引用を一操作へ接続する際の文書の版の境界を検証する。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def evidence_scope_error(spans: Sequence[dict[str, Any]]) -> str:
    """明示metadataの衝突だけを返す。異なるページ・出典というだけでは拒否しない。

    文書の比較や別々の条件分岐でなく、一つの操作関係／操作段落へ引用を結ぶ時に使う。未記載の版は推測しない。
    """
    versions: dict[str, set[str]] = {}
    for span in spans:
        scope = span.get("document_scope") or []
        if len(scope) >= 2 and scope[0] and scope[1]:
            versions.setdefault(scope[0], set()).add(scope[1])
    if any(len(values) > 1 for values in versions.values()):
        return "同一文書の異なる版を一つの操作へ合成できない"
    return ""
