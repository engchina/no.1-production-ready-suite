"""原始ランキングを一度だけ融合する。空・失敗の一覧の重みは再配分しない。"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RrfWeights:
    vector: float = 10
    keyword: float = 1
    original: float = 7
    additional: float = 3

    def __post_init__(self) -> None:
        values = (self.vector, self.keyword, self.original, self.additional)
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("RRF の比重は有限の 0 以上で指定してください。")
        if self.vector + self.keyword <= 0 or self.original + self.additional <= 0:
            raise ValueError("RRF の比重は組ごとに少なくとも一方を 0 より大きくしてください。")

    def channel(self, name: str) -> float:
        if name not in {"vector", "keyword"}:
            raise ValueError("未知の検索方式です。")
        return _ratio(self.vector, self.keyword)[0 if name == "vector" else 1]


def _ratio(left: float, right: float) -> tuple[float, float]:
    scale = max(left, right)
    a, b = left / scale, right / scale
    return a / (a + b), b / (a + b)


def query_key(query: str) -> str:
    return "".join(unicodedata.normalize("NFKC", query).casefold().split())


def unique_queries(queries: Sequence[str]) -> list[str]:
    selected: dict[str, str] = {}
    for query in queries:
        if key := query_key(query):
            selected.setdefault(key, query.strip())
    return list(selected.values())


def source_weights(queries: Sequence[str], weights: RrfWeights) -> dict[str, float]:
    unique = unique_queries(queries)
    if not unique:
        return {}
    if len(unique) == 1:
        return {unique[0]: 1.0}
    original, additional = _ratio(weights.original, weights.additional)
    return {q: original if i == 0 else additional / (len(unique) - 1) for i, q in enumerate(unique)}


@dataclass(frozen=True)
class RankedList:
    query: str
    channel: str
    chunk_ids: tuple[str, ...]
    status: str = "completed"
    error: str = ""
    sources: tuple[str, ...] = ()
    variant: str = ""
    factor: float = 1.0


@dataclass(frozen=True)
class ExecutedList:
    ranking: RankedList
    weight: float
    status: str


@dataclass(frozen=True)
class Contribution:
    chunk_id: str
    list_index: int
    rank: int
    value: float


@dataclass(frozen=True)
class FusionResult:
    scores: dict[str, float]
    lists: tuple[ExecutedList, ...]
    contributions: tuple[Contribution, ...]
    raw_count: int
    unique_count: int

    def diagnostics(self, weights: RrfWeights, sources: Mapping[str, float], k: float) -> dict[str, object]:
        return {
            "method_ratio": [weights.vector, weights.keyword],
            "source_ratio": [weights.original, weights.additional],
            "rank_constant": k,
            "expressions": [{"query": q, "weight": w, "original": i == 0} for i, (q, w) in enumerate(sources.items())],
            "lists": [
                {
                    "query": e.ranking.query,
                    "channel": e.ranking.channel,
                    "variant": e.ranking.variant,
                    "sources": list(e.ranking.sources),
                    "weight": e.weight,
                    "count": len(dict.fromkeys(e.ranking.chunk_ids)),
                    "raw_count": len(e.ranking.chunk_ids),
                    "status": e.status,
                    "error": e.ranking.error,
                }
                for e in self.lists
            ],
            "contributions": [
                {
                    "chunk_id": c.chunk_id,
                    "list_index": c.list_index,
                    "rank": c.rank,
                    "contribution": c.value,
                    "fused_score": self.scores[c.chunk_id],
                }
                for c in self.contributions
            ],
            "raw_count": self.raw_count,
            "unique_count": self.unique_count,
            "candidate_count": len(self.scores),
        }


def fuse_rankings(rankings: Sequence[RankedList], sources: Mapping[str, float], weights: RrfWeights, *, k: float) -> FusionResult:
    if not math.isfinite(k) or k <= 0:
        raise ValueError("順位定数は有限の正数で指定してください。")
    scores: dict[str, float] = {}
    executed: list[ExecutedList] = []
    contributions: list[Contribution] = []
    seen_lists: set[tuple[str, str, str]] = set()
    budgets = {query_key(q): w for q, w in sources.items()}
    raw_count = 0
    identifiers: set[str] = set()
    for ranking in rankings:
        key = (query_key(ranking.query), ranking.channel, ranking.variant)
        if key in seen_lists:
            continue
        seen_lists.add(key)
        weight = budgets[key[0]] * weights.channel(key[1]) * ranking.factor
        status = "failed" if ranking.status == "failed" else "completed" if ranking.chunk_ids else "empty"
        list_index = len(executed)
        executed.append(ExecutedList(ranking, weight, status))
        if status == "failed":
            continue
        raw_count += len(ranking.chunk_ids)
        identifiers.update(ranking.chunk_ids)
        for rank, chunk_id in enumerate(dict.fromkeys(ranking.chunk_ids), start=1):
            value = weight / (k + rank)
            if weight == 0:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + value
            contributions.append(Contribution(chunk_id, list_index, rank, value))
    return FusionResult(scores, tuple(executed), tuple(contributions), raw_count, len(identifiers))


def fusion_details(result: FusionResult, weights: RrfWeights, sources: Mapping[str, float], k: float) -> dict[str, object]:
    """表示用の先頭と完全な計算記録を同じ結果から作る。"""
    data = result.diagnostics(weights, sources, k)
    lines = []
    for i, entry in enumerate(result.lists, 1):
        r = entry.ranking
        state = {"failed": "失敗", "empty": "該当なし", "completed": "完了"}[entry.status]
        lines.append(
            f"一覧 {i}: {r.query} / {'ベクトル検索' if r.channel == 'vector' else '全文検索'} {r.variant} / "
            f"出所: {'・'.join(r.sources) or '追加検索文'} / 重み {entry.weight:.9g} / "
            f"{len(dict.fromkeys(r.chunk_ids))} 件 / {state}" + (f"（{r.error}）" if r.error else "")
        )
    lines += [
        f"{c.chunk_id}: 一覧 {c.list_index + 1}・順位 {c.rank} / 寄与 {c.value:.12g} / 融合スコア {result.scores[c.chunk_id]:.12g}"
        for c in result.contributions
    ]
    return {
        "fields": [
            {"label": "目的", "value": "各ランキングの順位と設定した比重を使い、一度の RRF で検索結果を統合します。"},
            {
                "label": "結果",
                "value": f"重複除去前 {result.raw_count} 件 / 重複除去後 {result.unique_count} 件 / "
                f"正の寄与がある候補 {len(result.scores)} 件",
            },
            {"label": "ベクトル検索と全文検索の比重", "value": f"{weights.vector:g}：{weights.keyword:g}"},
            {"label": "原質問と追加検索文の比重", "value": f"{weights.original:g}：{weights.additional:g}"},
            {"label": "検索文", "value": f"原質問 {min(1, len(sources))} 本 / 追加 {max(0, len(sources) - 1)} 本"},
            {"label": "順位定数", "value": str(k)},
            {"label": "指標", "value": "RRF の融合スコア（大きい順）。確率や回答の信頼度ではありません。"},
        ],
        "lines": lines,
        "code": [{"label": "RRF の計算記録", "language": "json", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "rrf": data,
    }
