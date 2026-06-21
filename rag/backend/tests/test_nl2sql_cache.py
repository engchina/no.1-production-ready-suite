"""NL2SQL Cache アダプター(意味キャッシュ)のテスト。"""

from typing import Any

from app.config import Settings
from app.nl2sql.cache import (
    CACHE_POLICY_ORDER,
    CacheAdapterParams,
    SemanticCache,
    cache_adapter_runtime_settings,
    cosine_similarity,
    normalize_cache_policy,
    normalize_sql_for_cache,
    resolve_cache_adapter,
)


def _params(policy: str = "off", **overrides: Any) -> CacheAdapterParams:
    return resolve_cache_adapter(Settings(nl2sql_cache_policy=policy, **overrides))


def test_off_disables_all_layers() -> None:
    params = _params("off")
    assert (params.cache_nl_to_sql, params.cache_nl_to_result, params.cache_sql_to_result) == (
        False,
        False,
        False,
    )
    cache = SemanticCache(params)
    assert cache.store_nl([1.0, 0.0], "SELECT 1", kind="sql") is False
    assert cache.lookup_nl([1.0, 0.0], kind="sql").hit is False


def test_nl_sql_hit_on_similar_embedding() -> None:
    params = _params("nl_sql", nl2sql_cache_similarity_threshold=0.9)
    cache = SemanticCache(params)
    assert cache.store_nl([1.0, 0.0, 0.0], "SELECT * FROM employee", kind="sql") is True
    hit = cache.lookup_nl([1.0, 0.0, 0.0], kind="sql")
    assert hit.hit is True
    assert hit.value == "SELECT * FROM employee"
    assert hit.source == "nl"


def test_nl_sql_miss_on_dissimilar_embedding() -> None:
    params = _params("nl_sql", nl2sql_cache_similarity_threshold=0.9)
    cache = SemanticCache(params)
    cache.store_nl([1.0, 0.0, 0.0], "SELECT * FROM employee", kind="sql")
    # 直交ベクトルは cosine 0 → ヒットしない。
    assert cache.lookup_nl([0.0, 1.0, 0.0], kind="sql").hit is False
    # nl_sql policy では result 層は無効。
    assert cache.lookup_nl([1.0, 0.0, 0.0], kind="result").hit is False


def test_ttl_expiry_is_deterministic() -> None:
    params = _params("nl_sql", nl2sql_cache_similarity_threshold=0.9, nl2sql_cache_ttl_seconds=300)
    cache = SemanticCache(params)
    cache.store_nl([1.0, 0.0], "SELECT 1", kind="sql", now=0.0)
    assert cache.lookup_nl([1.0, 0.0], kind="sql", now=100.0).hit is True
    assert cache.lookup_nl([1.0, 0.0], kind="sql", now=400.0).hit is False


def test_sql_result_exact_match_after_normalization() -> None:
    params = _params("sql_result")
    cache = SemanticCache(params)
    assert cache.store_sql("SELECT *  FROM t;", [{"x": 1}]) is True
    hit = cache.lookup_sql("select * from t")
    assert hit.hit is True
    assert hit.value == [{"x": 1}]
    assert hit.source == "sql"


def test_normalize_sql_for_cache() -> None:
    assert normalize_sql_for_cache("SELECT *   FROM t ;") == "select * from t"


def test_cosine_similarity_edges() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_normalize_and_runtime_settings() -> None:
    assert normalize_cache_policy("nope") == "off"
    assert normalize_cache_policy("nl_result") == "nl_result"
    runtime = cache_adapter_runtime_settings(Settings(nl2sql_cache_policy="nl_result"))
    assert tuple(s.name for s in runtime.policies) == CACHE_POLICY_ORDER
    assert [s.name for s in runtime.policies if s.selected] == ["nl_result"]
    assert runtime.cache_nl_to_result is True
