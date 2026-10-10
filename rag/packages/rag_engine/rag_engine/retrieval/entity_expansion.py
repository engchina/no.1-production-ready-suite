"""実体の 1 段の拡張で足した根拠（#1362）の印と、枠を確保するかの関連度の規則（#1390）。

backend は実体の表から足した chunk の metadata の ``entity_expansion`` に足した理由を付ける。rag_engine は
その根拠も rerank にかけ、関連度が下限以上のものだけ、候補の位置を保ち（rerank で後ろへ下げない）、文書の
選択で後回しにせず、context の親の枠を確保する。下限未満のもの（質問と関係の薄い属性の chunk）は、印の無い
候補と同じく分数の順に並べる。backend の MCP の根拠の並び（``mcp_evidence_order``）も同じ規則で枠を確保する。
"""

from __future__ import annotations

import math
from typing import Any

# 枠を確保する拡張の根拠の rerank の関連度の下限（#1390）。橋渡しの行（台帳の行・略号の表）は質問の語と重なら
# ず、検索で当たった chunk より分数が低いため、当たった chunk と同じ基準では選べない。質問と関係しない属性の
# chunk（別のシステムの台帳の行・質問と別の章）を外す値にする（#1362 の評価の KB で拡張の根拠の関連度を見て
# 決めた）。
ENTITY_EXPANSION_MIN_RELEVANCE = 0.2


def is_entity_expansion_record(record: Any) -> bool:
    """実体の 1 段の拡張で足した根拠か（backend が metadata の ``entity_expansion`` に理由を付ける。#1362）。"""
    metadata = getattr(record, "metadata", None)
    return isinstance(metadata, dict) and isinstance(metadata.get("entity_expansion"), dict)


def entity_expansion_relevant(score: Any) -> bool:
    """拡張の根拠の rerank の関連度が、枠を確保する下限以上か（#1390）。

    関連度が無い（rerank を実行しなかった・失敗した）ときは判断できないので確保する（#1362 と同じ）。
    """
    if score is None or isinstance(score, bool):
        return True
    try:
        value = float(score)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(value) or value >= ENTITY_EXPANSION_MIN_RELEVANCE


def is_reserved_entity_expansion(record: Any) -> bool:
    """候補の位置を保ち、文書の選択で後回しにせず、context の枠を確保する拡張の根拠か（#1362・#1390）。

    実体の拡張の印があり、rerank の関連度（``metadata["rerank"]["relevance_score"]``）が下限以上か、関連度が
    無いもの。
    """
    if not is_entity_expansion_record(record):
        return False
    rerank = record.metadata.get("rerank")
    return entity_expansion_relevant(
        rerank.get("relevance_score") if isinstance(rerank, dict) else None
    )


__all__ = [
    "ENTITY_EXPANSION_MIN_RELEVANCE",
    "entity_expansion_relevant",
    "is_entity_expansion_record",
    "is_reserved_entity_expansion",
]
