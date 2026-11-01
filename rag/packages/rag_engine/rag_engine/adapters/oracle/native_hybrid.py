"""Oracle原生hybrid索引への検索契約。融合は原順位を使う呼出し側で一度だけ行う。"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any, Literal

from rag_engine.models.storage import AdbHybridSearchUnavailable

HybridSearchBackend = Literal["oracle_native", "sudachi"]
NATIVE_HYBRID_INDEX = "RAG_CHUNKS_HYBRID_IDX"
NATIVE_HYBRID_PREFERENCE = "RAG_CHUNKS_HYBRID_PREF"


class NativeHybridUnavailable(AdbHybridSearchUnavailable):
    """原生索引を選んだが使用できない。別方式へ暗黙に戻さない。"""


def native_search_request(
    *,
    query: str,
    vector: Sequence[float],
    channel: Literal["vector", "keyword"],
    allowed_ids: Sequence[str],
    id_column: str = "CHUNK_ID",
    limit: int = 600,
    rank_constant: int = 60,
    accuracy: int = 100,
) -> dict[str, Any]:
    """許可されたchunkの集合を検索前に制限する。空の集合では実行しない。"""
    if id_column not in {"CHUNK_ID", "CHUNK_UID"}:
        raise ValueError("検索範囲の列が不正です。")
    identifiers = list(dict.fromkeys(allowed_ids))
    if not identifiers or limit < 1 or rank_constant < 1 or not 1 <= accuracy <= 100:
        raise ValueError("検索範囲・上限・順位定数が不正です。")
    if channel not in {"vector", "keyword"}:
        raise ValueError("検索方式が不正です。")
    if channel == "vector" and (not vector or any(not math.isfinite(v) for v in vector)):
        raise ValueError("検索する埋め込みが不正です。")
    groups = [{"op": "IN", "col": id_column, "type": "string", "args": identifiers[n : n + 1000]} for n in range(0, len(identifiers), 1000)]
    return {
        "hybrid_index_name": NATIVE_HYBRID_INDEX,
        "search_fusion": "VECTOR_ONLY" if channel == "vector" else "TEXT_ONLY",
        "search_scorer": "RRF",
        "filter_by": groups[0] if len(groups) == 1 else {"op": "OR", "args": groups},
        channel if channel == "vector" else "text": {
            **(
                {
                    "search_vector": list(vector),
                    "search_mode": "DOCUMENT",
                    "aggregator": "MAX",
                    "accuracy": accuracy,
                    "filter_type": "PRE_WO",
                    "inpath": ["$.embedding"],
                }
                if channel == "vector"
                else {"search_text": query, "inpath": ["$.text"]}
            ),
            "result_max": limit,
            "rank_penalty": rank_constant,
        },
        "return": {"topN": limit, "values": ["rowid", "vector_score", "text_score", "vector_rank", "text_rank"]},
    }


def request_json(**kwargs: Any) -> str:
    return json.dumps(native_search_request(**kwargs), ensure_ascii=False, allow_nan=False)
