"""Oracle原生hybrid索引への検索契約。融合は原順位を使う呼出し側で一度だけ行う。"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from rag_engine.models.storage import AdbHybridSearchUnavailable

HybridSearchBackend = Literal["oracle_native", "sudachi"]
NATIVE_HYBRID_INDEX = "RAG_CHUNKS_HYBRID_IDX"
NATIVE_HYBRID_PREFERENCE = "RAG_CHUNKS_HYBRID_PREF"
# SEARCH は filter_by を SQL の条件の文字列へ展開し、約 4,000 バイトを超えると DRG-50857 で失敗する
# (#4149。文書に記載は無く、実測の値)。ベクトルの分岐は全文より少し手前で失敗するので余裕を持たせる。
NATIVE_FILTER_MAX_BYTES = 3600
# 1 回の検索で SEARCH を呼ぶ回数の上限。超えたら範囲を削らず失敗にする。
NATIVE_FILTER_MAX_BATCHES = 32
# 分けた SEARCH を同時に読む数。1 問の接続を増やしすぎない。
NATIVE_FILTER_CONCURRENCY = 3
# 分けるときに 1 回の条件へ見込む、値以外の括弧・列名・OR の分。
_FILTER_GROUP_OVERHEAD_BYTES = 200


class NativeHybridUnavailable(AdbHybridSearchUnavailable):
    """原生索引を選んだが使用できない。別方式へ暗黙に戻さない。"""


@dataclass(frozen=True)
class NativeRankTerms:
    vector_rank: int
    text_rank: int
    vector: float
    text: float

    @property
    def total(self) -> float:
        return self.vector + self.text


def native_rank_terms(
    *,
    vector_rank: object,
    text_rank: object,
    vector_score: float,
    text_score: float,
    similarity: float | None,
    min_similarity: float,
    method_ratio: tuple[float, float],
    k: int,
) -> NativeRankTerms:
    """未命中の既定順位を除き、元の経路順位の寄与を一度だけ計算する。"""
    from rag_engine.retrieval.weighted_rrf import RrfWeights

    weights = RrfWeights(vector=method_ratio[0], keyword=method_ratio[1])
    if k < 1 or not math.isfinite(min_similarity) or not 0 <= min_similarity <= 1:
        raise ValueError("順位定数・類似度の下限が不正です。")
    if any(not math.isfinite(s) or s < 0 for s in (vector_score, text_score)):
        raise ValueError("Oracle の経路スコアが不正です。")

    def rank(value: object, present: bool) -> int:
        if not present:
            return 0
        numeric = float(str(value))
        if not math.isfinite(numeric) or numeric < 1 or not numeric.is_integer():
            raise ValueError("Oracle の経路順位が不正です。")
        return int(numeric)

    vr = rank(vector_rank, vector_score > 0)
    tr = rank(text_rank, text_score > 0)
    if similarity is None or not math.isfinite(similarity) or similarity < min_similarity:
        vr = 0
    return NativeRankTerms(
        vector_rank=vr,
        text_rank=tr,
        vector=weights.channel("vector") / (k + vr) if vr else 0.0,
        text=weights.channel("keyword") / (k + tr) if tr else 0.0,
    )


@dataclass(frozen=True)
class NativeFilterBatch:
    """1 回の SEARCH に渡す範囲。分けた範囲は互いに重ならない。"""

    allowed_ids: tuple[str, ...] = ()
    allowed_scopes: tuple[int, ...] = ()
    allowed_scope_spans: tuple[tuple[int, int], ...] = ()
    allowed_scope_ranges: tuple[tuple[int, int, int], ...] = ()


def _scope_filter_groups(
    *,
    allowed_ids: Sequence[str],
    allowed_scopes: Sequence[int],
    allowed_scope_spans: Sequence[tuple[int, int]],
    allowed_scope_ranges: Sequence[tuple[int, int, int]],
    id_column: str,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    if allowed_scopes:
        scopes = sorted(set(allowed_scopes))
        if any(scope < 1 for scope in scopes):
            raise ValueError("検索範囲の数値識別子が不正です。")
        groups.append({"op": "IN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": scopes})
    for first, last in allowed_scope_spans:
        if first < 1 or first > last:
            raise ValueError("検索範囲の数値識別子が不正です。")
        groups.append({"op": "BETWEEN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [first, last]})
    for scope, first, last in allowed_scope_ranges:
        if scope < 1 or first > last:
            raise ValueError("検索範囲の番号区間が不正です。")
        groups.append(
            {
                "op": "AND",
                "args": [
                    {"op": "=", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [scope]},
                    {"op": "BETWEEN", "col": "CHUNK_INDEX", "type": "number", "args": [first, last]},
                ],
            }
        )
    if allowed_ids:
        groups.append({"op": "IN", "col": id_column, "type": "string", "args": list(allowed_ids)})
    return groups


def _combined_filter(groups: list[dict[str, Any]]) -> dict[str, Any]:
    return groups[0] if len(groups) == 1 else {"op": "OR", "args": groups}


def render_native_filter(node: dict[str, Any]) -> str:
    """filter_by を SEARCH が展開する条件の文字列にする(長さの判定に使う。実行はしない)。

    形は ``DBMS_HYBRID_VECTOR.GET_SQL`` の出力と同じ。実 Oracle のテストで照合する。
    """
    op = node["op"]
    if op in {"AND", "OR"}:
        return "( " + f" {op} ".join(render_native_filter(arg) for arg in node["args"]) + " )"
    column = f'THEBASE."{node["col"]}"'
    values = (
        [f"TO_NUMBER('{value}')" for value in node["args"]]
        if node["type"] == "number"
        else ["'" + str(value).replace("'", "''") + "'" for value in node["args"]]
    )
    if op == "IN":
        return f"( {column} IN ({', '.join(values)} ) )"
    if op == "=":
        return f"( {column} = ({values[0]} ) )"
    if op == "BETWEEN":
        return f"( {column} BETWEEN {values[0]}AND {values[1]} )"
    raise ValueError("検索範囲の条件が不正です。")


def _batch_filter_bytes(batch: NativeFilterBatch, id_column: str) -> int:
    groups = _scope_filter_groups(
        allowed_ids=batch.allowed_ids,
        allowed_scopes=batch.allowed_scopes,
        allowed_scope_spans=batch.allowed_scope_spans,
        allowed_scope_ranges=batch.allowed_scope_ranges,
        id_column=id_column,
    )
    return len(render_native_filter(_combined_filter(groups)).encode("utf-8")) if groups else 0


def _compress_scope_runs(scopes: Sequence[int]) -> tuple[tuple[int, ...], tuple[tuple[int, int], ...]]:
    """連続する識別子を、IN より短くなるときだけ BETWEEN にする。許す行は変わらない。"""
    singles: list[int] = []
    spans: list[tuple[int, int]] = []
    run: list[int] = []

    def flush() -> None:
        if not run:
            return
        span = {"op": "BETWEEN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": [run[0], run[-1]]}
        if len(render_native_filter(span)) + len(" OR ") < sum(len(f"TO_NUMBER('{v}'), ") for v in run):
            spans.append((run[0], run[-1]))
        else:
            singles.extend(run)
        run.clear()

    for scope in sorted(set(scopes)):
        if run and scope != run[-1] + 1:
            flush()
        run.append(scope)
    flush()
    return tuple(singles), tuple(spans)


def native_filter_batches(
    *,
    allowed_ids: Sequence[str] = (),
    allowed_scopes: Sequence[int] = (),
    allowed_scope_ranges: Sequence[tuple[int, int, int]] = (),
    id_column: str = "CHUNK_ID",
) -> list[NativeFilterBatch]:
    """範囲を、展開後の条件が上限に収まる 1 つ以上の SEARCH の範囲にする(#4149)。

    収まる範囲はそのまま渡す。超えたら連続する識別子を BETWEEN にまとめ、それでも超えたら分ける。
    どの形でも許す行は元の範囲と同じ。回数の上限を超える範囲は失敗にする(範囲を黙って削らない)。
    """
    identifiers = tuple(dict.fromkeys(allowed_ids))
    whole = NativeFilterBatch(identifiers, tuple(sorted(set(allowed_scopes))), (), tuple(allowed_scope_ranges))
    if _batch_filter_bytes(whole, id_column) <= NATIVE_FILTER_MAX_BYTES:
        return [whole]
    singles, spans = _compress_scope_runs(whole.allowed_scopes)
    compact = NativeFilterBatch(identifiers, singles, spans, whole.allowed_scope_ranges)
    if _batch_filter_bytes(compact, id_column) <= NATIVE_FILTER_MAX_BYTES:
        return [compact]

    def span_bytes(span: tuple[int, int]) -> int:
        group = {"op": "BETWEEN", "col": "NATIVE_SCOPE_ID", "type": "number", "args": list(span)}
        return len(render_native_filter(group)) + len(" OR ")

    def range_bytes(item: tuple[int, int, int]) -> int:
        groups = _scope_filter_groups(
            allowed_ids=(), allowed_scopes=(), allowed_scope_spans=(), allowed_scope_ranges=(item,), id_column=id_column
        )
        return len(render_native_filter(groups[0])) + len(" OR ")

    terms: list[tuple[str, Any, int]] = [
        *(("scope", value, len(f"TO_NUMBER('{value}'), ")) for value in singles),
        *(("span", value, span_bytes(value)) for value in spans),
        *(("range", value, range_bytes(value)) for value in whole.allowed_scope_ranges),
        *(("id", value, len(value.replace("'", "''").encode("utf-8")) + len("'', ")) for value in identifiers),
    ]
    capacity = NATIVE_FILTER_MAX_BYTES - _FILTER_GROUP_OVERHEAD_BYTES
    batches: list[NativeFilterBatch] = []
    current: dict[str, list[Any]] = {"id": [], "scope": [], "span": [], "range": []}
    used = 0

    def close_batch() -> None:
        batches.append(NativeFilterBatch(tuple(current["id"]), tuple(current["scope"]), tuple(current["span"]), tuple(current["range"])))
        for values in current.values():
            values.clear()

    for kind, value, size in terms:
        if used and used + size > capacity:
            close_batch()
            used = 0
        current[kind].append(value)
        used += size
    close_batch()
    if len(batches) > NATIVE_FILTER_MAX_BATCHES:
        raise NativeHybridUnavailable("検索範囲の条件が原生検索の上限を超えました。範囲を絞るか、検索方式を確認してください。")
    return batches


def native_search_request(
    *,
    query: str,
    vector: Sequence[float],
    channel: Literal["vector", "keyword", "hybrid"],
    allowed_ids: Sequence[str],
    allowed_scopes: Sequence[int] = (),
    allowed_scope_ranges: Sequence[tuple[int, int, int]] = (),
    allowed_scope_spans: Sequence[tuple[int, int]] = (),
    id_column: str = "CHUNK_ID",
    limit: int = 600,
    rank_constant: int = 60,
    accuracy: int = 100,
    method_ratio: tuple[float, float] = (10, 1),
) -> dict[str, Any]:
    """許可されたchunkの集合を検索前に制限する。空の集合では実行しない。"""
    if id_column not in {"CHUNK_ID", "CHUNK_UID"}:
        raise ValueError("検索範囲の列が不正です。")
    identifiers = list(dict.fromkeys(allowed_ids))
    has_scope = identifiers or allowed_scopes or allowed_scope_spans or allowed_scope_ranges
    if not has_scope or limit < 1 or rank_constant < 1 or not 1 <= accuracy <= 100:
        raise ValueError("検索範囲・上限・順位定数が不正です。")
    if channel not in {"vector", "keyword", "hybrid"}:
        raise ValueError("検索方式が不正です。")
    if channel in {"vector", "hybrid"} and (not vector or any(not math.isfinite(v) for v in vector)):
        raise ValueError("検索する埋め込みが不正です。")
    groups = _scope_filter_groups(
        allowed_ids=identifiers,
        allowed_scopes=allowed_scopes,
        allowed_scope_spans=allowed_scope_spans,
        allowed_scope_ranges=allowed_scope_ranges,
        id_column=id_column,
    )
    from rag_engine.retrieval.weighted_rrf import RrfWeights

    weights = RrfWeights(vector=method_ratio[0], keyword=method_ratio[1])
    request: dict[str, Any] = {
        "hybrid_index_name": NATIVE_HYBRID_INDEX,
        "search_fusion": {"vector": "VECTOR_ONLY", "keyword": "TEXT_ONLY", "hybrid": "UNION"}[channel],
        "search_scorer": "WRRF" if channel == "hybrid" else "RRF",
        "filter_by": _combined_filter(groups),
        "return": {
            "topN": limit * 2 if channel == "hybrid" else limit,
            "values": ["rowid", "score", "vector_score", "text_score", "vector_rank", "text_rank"],
        },
    }
    if channel in {"vector", "hybrid"}:
        request["vector"] = {
            "search_vector": list(vector),
            "search_mode": "DOCUMENT",
            "aggregator": "MAX",
            "accuracy": accuracy,
            "filter_type": "PRE_WO",
            "inpath": ["$.embedding"],
            "result_max": limit,
            "rank_penalty": rank_constant,
            "score_weight": weights.vector,
        }
    if channel in {"keyword", "hybrid"}:
        request["text"] = {
            "search_text": query,
            "inpath": ["$.text"],
            "result_max": limit,
            "rank_penalty": rank_constant,
            "score_weight": weights.keyword,
        }
    return request


def request_json(**kwargs: Any) -> str:
    return json.dumps(native_search_request(**kwargs), ensure_ascii=False, allow_nan=False)
