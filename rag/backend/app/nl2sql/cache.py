"""NL2SQL Cache アダプター(意味キャッシュポリシーの手動選択プリセット)。

off(既定)/ nl_sql / nl_result / sql_result を束ねる。NL 類似は埋め込みの cosine 類似度で判定し、
本番では Oracle 26ai ベクトル検索(OCI Cohere 埋め込み)で同等の照会を行う。本モジュールは
**非 network・決定論**の純 Python 実装(埋め込みは呼び出し側が注入、時刻は ``now`` で注入可能)。

3 層:
- ``nl_sql``   : NL→SQL(類似質問の生成済み SQL を再利用)
- ``nl_result``: NL→結果(決定論クエリの結果を再利用)
- ``sql_result``: SQL→結果(正規化 SQL 完全一致で結果を再利用)
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.config import Nl2SqlCachePolicy, Settings

CachePolicy = Nl2SqlCachePolicy
DEFAULT_CACHE_POLICY: CachePolicy = "off"
CACHE_POLICY_ORDER: tuple[CachePolicy, ...] = ("off", "nl_sql", "nl_result", "sql_result")

_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CacheAdapterParams:
    """Cache 段へ渡す解決済み effective パラメータ。"""

    policy: CachePolicy
    cache_nl_to_sql: bool
    cache_nl_to_result: bool
    cache_sql_to_result: bool
    similarity_threshold: float
    ttl_seconds: int


@dataclass(frozen=True)
class CachePolicyStatus:
    """1 キャッシュポリシーの選択状態と効果。"""

    name: CachePolicy
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    cache_nl_to_sql: bool
    cache_nl_to_result: bool
    cache_sql_to_result: bool


@dataclass(frozen=True)
class CacheAdapterRuntimeSettings:
    """Cache アダプターの非機密 runtime snapshot。"""

    policy: CachePolicy
    cache_nl_to_sql: bool
    cache_nl_to_result: bool
    cache_sql_to_result: bool
    similarity_threshold: float
    ttl_seconds: int
    policies: tuple[CachePolicyStatus, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CacheLookup:
    """キャッシュ照会結果。"""

    hit: bool
    value: object | None = None
    similarity: float = 0.0
    source: str | None = None  # "nl" / "sql" / None


_POLICY_SPECS: dict[CachePolicy, dict[str, object]] = {
    "off": {"origin": "default", "recommended_for": ("鮮度最優先", "既定")},
    "nl_sql": {"origin": "semantic", "recommended_for": ("反復質問", "生成コスト削減")},
    "nl_result": {"origin": "semantic", "recommended_for": ("決定論クエリ", "実行コスト削減")},
    "sql_result": {"origin": "exact", "recommended_for": ("同一 SQL の再実行",)},
}


def normalize_cache_policy(value: object) -> CachePolicy:
    """未知のキャッシュ名は既定 off へ寄せる。"""
    text = str(value or "").strip().lower()
    if text in _POLICY_SPECS:
        return text
    return DEFAULT_CACHE_POLICY


def _policy_flags(policy: CachePolicy) -> tuple[bool, bool, bool]:
    return (
        policy == "nl_sql",
        policy == "nl_result",
        policy == "sql_result",
    )


def resolve_cache_adapter(settings: Settings) -> CacheAdapterParams:
    """Settings から Cache アダプターの effective パラメータを作る。"""
    policy = normalize_cache_policy(getattr(settings, "nl2sql_cache_policy", DEFAULT_CACHE_POLICY))
    nl_sql, nl_result, sql_result = _policy_flags(policy)
    return CacheAdapterParams(
        policy=policy,
        cache_nl_to_sql=nl_sql,
        cache_nl_to_result=nl_result,
        cache_sql_to_result=sql_result,
        similarity_threshold=float(getattr(settings, "nl2sql_cache_similarity_threshold", 0.95)),
        ttl_seconds=int(getattr(settings, "nl2sql_cache_ttl_seconds", 300)),
    )


def cache_adapter_runtime_settings(settings: Settings) -> CacheAdapterRuntimeSettings:
    """Settings から Cache アダプター readiness snapshot を作る。"""
    params = resolve_cache_adapter(settings)
    statuses = tuple(
        CachePolicyStatus(
            name=name,
            origin=str(_POLICY_SPECS[name]["origin"]),
            recommended_for=tuple(_POLICY_SPECS[name]["recommended_for"]),  # type: ignore[arg-type]
            selected=name == params.policy,
            cache_nl_to_sql=_policy_flags(name)[0],
            cache_nl_to_result=_policy_flags(name)[1],
            cache_sql_to_result=_policy_flags(name)[2],
        )
        for name in CACHE_POLICY_ORDER
    )
    return CacheAdapterRuntimeSettings(
        policy=params.policy,
        cache_nl_to_sql=params.cache_nl_to_sql,
        cache_nl_to_result=params.cache_nl_to_result,
        cache_sql_to_result=params.cache_sql_to_result,
        similarity_threshold=params.similarity_threshold,
        ttl_seconds=params.ttl_seconds,
        policies=statuses,
    )


def normalize_sql_for_cache(sql: str) -> str:
    """SQL→結果キャッシュ用の正準キー(小文字・空白圧縮・末尾セミコロン除去)。"""
    text = (sql or "").strip().rstrip(";").strip()
    text = _WS_RE.sub(" ", text)
    return text.lower()


def make_cache_key(text: str) -> str:
    """テキストの決定論ハッシュキー。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """2 ベクトルの cosine 類似度(長さ不一致/零ベクトルは 0.0)。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class _NlEntry:
    embedding: tuple[float, ...]
    kind: str  # "sql" / "result"
    value: object
    stored_at: float


class SemanticCache:
    """決定論の in-memory 意味キャッシュ(NL 類似 + SQL 完全一致)。

    本番では Oracle 26ai ベクトル検索へ置き換えるが、インターフェース(lookup/store)は同一。
    時刻は ``now`` 引数で注入し、TTL を決定論的にテストできる。
    """

    def __init__(self, params: CacheAdapterParams) -> None:
        self._params = params
        self._nl_entries: list[_NlEntry] = []
        self._sql_entries: dict[str, tuple[object, float]] = {}

    def _expired(self, stored_at: float, now: float) -> bool:
        ttl = self._params.ttl_seconds
        if ttl <= 0:
            return False
        return (now - stored_at) > ttl

    def _nl_enabled(self, kind: str) -> bool:
        if kind == "sql":
            return self._params.cache_nl_to_sql
        if kind == "result":
            return self._params.cache_nl_to_result
        return False

    def store_nl(
        self, embedding: Sequence[float], value: object, *, kind: str, now: float = 0.0
    ) -> bool:
        """NL 埋め込み→値(kind=sql/result)を格納する。policy 無効時は何もしない。"""
        if not self._nl_enabled(kind):
            return False
        self._nl_entries.append(
            _NlEntry(
                embedding=tuple(float(x) for x in embedding), kind=kind, value=value, stored_at=now
            )
        )
        return True

    def lookup_nl(self, embedding: Sequence[float], *, kind: str, now: float = 0.0) -> CacheLookup:
        """NL 埋め込みで類似 >= threshold の最良エントリを返す。"""
        if not self._nl_enabled(kind):
            return CacheLookup(hit=False)
        query = tuple(float(x) for x in embedding)
        best: CacheLookup = CacheLookup(hit=False)
        for entry in self._nl_entries:
            if entry.kind != kind or self._expired(entry.stored_at, now):
                continue
            sim = cosine_similarity(query, entry.embedding)
            if sim >= self._params.similarity_threshold and sim > best.similarity:
                best = CacheLookup(hit=True, value=entry.value, similarity=sim, source="nl")
        return best

    def store_sql(self, sql: str, value: object, *, now: float = 0.0) -> bool:
        """正規化 SQL→結果を格納する。policy が sql_result でなければ何もしない。"""
        if not self._params.cache_sql_to_result:
            return False
        self._sql_entries[normalize_sql_for_cache(sql)] = (value, now)
        return True

    def lookup_sql(self, sql: str, *, now: float = 0.0) -> CacheLookup:
        """正規化 SQL 完全一致で結果を返す(sql_result policy のみ)。"""
        if not self._params.cache_sql_to_result:
            return CacheLookup(hit=False)
        key = normalize_sql_for_cache(sql)
        hit = self._sql_entries.get(key)
        if hit is None:
            return CacheLookup(hit=False)
        value, stored_at = hit
        if self._expired(stored_at, now):
            del self._sql_entries[key]
            return CacheLookup(hit=False)
        return CacheLookup(hit=True, value=value, similarity=1.0, source="sql")
