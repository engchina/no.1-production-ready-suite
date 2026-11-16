"""原生検索の範囲を読んだ後に切替が commit されても、対象の文書の根拠を落とさない（#4179）。"""

import json
from collections.abc import Mapping

import pytest

from app.clients.oracle import NativeScopeReuse, OracleClient, _oracle_retrieval_where
from app.config import Settings
from app.schemas.search import SearchMode
from tests.test_native_hybrid import _text_row

OLD_SCOPE = 101
NEW_SCOPE = 202


class SwitchingOracle(OracleClient):
    """最初の検索の直前に切替が commit される状況を、SQL の種類で再現する。"""

    def __init__(self, *, switch_during_read: bool = True, fail_check: bool = False) -> None:
        super().__init__(
            Settings(
                rag_hybrid_search_backend="oracle_hybrid_index",
                rag_native_vector_exact_max_chunks=0,
            )
        )
        self.active_scope = OLD_SCOPE
        self.switch_during_read = switch_during_read
        self.fail_check = fail_check
        self.resolves = 0
        self.checks = 0
        self.ready_checks = 0
        self.reads: list[int] = []
        self.read_conditions: list[tuple[str, dict[str, object]]] = []

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        binds = dict(binds or {})
        if "FROM user_indexes" in sql:
            self.ready_checks += 1
            return [{"index_name": "RAG_CHUNKS_HYBRID_IDX"}]
        if "native_checked_scope_ids" in binds:
            self.checks += 1
            if self.fail_check:
                raise RuntimeError("synthetic check failure")
            checked = json.loads(str(binds["native_checked_scope_ids"]))
            return [{"scope_id": scope} for scope in checked if scope != self.active_scope]
        if "allowed_count" in sql:
            self.resolves += 1
            return [{"native_scope_id": self.active_scope, "allowed_count": 1, "vector_count": 1}]
        if "chunk_count" in sql:
            return [{"native_scope_id": self.active_scope, "chunk_count": 1}]
        assert "DBMS_HYBRID_VECTOR.SEARCH" in sql
        if self.switch_during_read:
            self.active_scope = NEW_SCOPE
        scope = json.loads(str(binds["native_spec"]))["filter_by"]["args"][0]
        self.reads.append(scope)
        self.read_conditions.append(
            (sql, {key: value for key, value in binds.items() if key != "native_spec"})
        )
        # 旧版の行は切替の transaction で消えているので、範囲が古いと 0 件になる。
        return [_text_row(f"chunk-{scope}", 1)] if scope == self.active_scope else []


@pytest.mark.asyncio
async def test_rereads_with_current_scope_when_switch_committed_after_resolve() -> None:
    client = SwitchingOracle()

    rows = await client._native_rows("keyword", "合成", (), top_k=3, filters={})

    assert [row["chunk_id"] for row in rows] == [f"chunk-{NEW_SCOPE}"]
    assert client.reads == [OLD_SCOPE, NEW_SCOPE]
    assert client.resolves == 2


@pytest.mark.asyncio
async def test_unchanged_scope_reads_once_with_one_check() -> None:
    client = SwitchingOracle(switch_during_read=False)

    rows = await client._native_rows("keyword", "合成", (), top_k=3, filters={})

    assert [row["chunk_id"] for row in rows] == [f"chunk-{OLD_SCOPE}"]
    assert client.reads == [OLD_SCOPE]
    assert (client.resolves, client.checks) == (1, 1)


@pytest.mark.asyncio
async def test_search_many_checks_once_and_rereads_every_query_with_one_resolve() -> None:
    client = SwitchingOracle()
    searches = [(f"合成 {index}", [0.1] * 3, SearchMode.KEYWORD) for index in range(3)]

    results = await client.search_many(searches, 3, {})

    assert [[hit.chunk_id for hit in hits] for hits in results] == [[f"chunk-{NEW_SCOPE}"]] * 3
    assert client.reads[-3:] == [NEW_SCOPE] * 3
    assert (client.resolves, client.checks) == (2, 1)


@pytest.mark.asyncio
async def test_check_failure_keeps_rows_already_read() -> None:
    client = SwitchingOracle(switch_during_read=False, fail_check=True)

    rows = await client._native_rows("keyword", "合成", (), top_k=3, filters={})

    assert [row["chunk_id"] for row in rows] == [f"chunk-{OLD_SCOPE}"]
    assert client.reads == [OLD_SCOPE]


@pytest.mark.asyncio
async def test_rereads_only_once_even_if_switched_again() -> None:
    client = SwitchingOracle()
    original = client._fetch_all

    async def switch_every_read(
        sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if "DBMS_HYBRID_VECTOR.SEARCH" in sql:
            client.active_scope = NEW_SCOPE if client.active_scope == OLD_SCOPE else OLD_SCOPE
            client.switch_during_read = False
        return await original(sql, binds)

    client._fetch_all = switch_every_read  # type: ignore[method-assign]

    await client._native_rows("keyword", "合成", (), top_k=3, filters={})

    assert len(client.reads) == 2


KEYWORD_SEARCHES = [("合成", [0.1] * 3, SearchMode.KEYWORD)]


@pytest.mark.asyncio
async def test_search_many_reuses_ready_check_and_scope_within_one_answer() -> None:
    """補いの検索は準備の確認と範囲の解決を使い回し、範囲の古さの確認は毎回行う(#5184)。"""
    client = SwitchingOracle(switch_during_read=False)
    reuse = NativeScopeReuse()

    first = await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)
    second = await client.search_many(
        [("合成 2", [0.1] * 3, SearchMode.KEYWORD)], 3, {}, reuse=reuse
    )

    assert [[hit.chunk_id for hit in hits] for hits in [*first, *second]] == [
        [f"chunk-{OLD_SCOPE}"]
    ] * 2
    assert (client.ready_checks, client.resolves, client.checks) == (1, 1, 2)


@pytest.mark.asyncio
async def test_reused_scope_is_resolved_again_when_switched_between_searches() -> None:
    """前の検索の後に切替が commit されたら、使い回した範囲を捨てて解決し直す(#4179・#5184)。"""
    client = SwitchingOracle(switch_during_read=False)
    reuse = NativeScopeReuse()
    await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)

    client.active_scope = NEW_SCOPE
    switched = await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)
    after = await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)

    assert [hit.chunk_id for hit in switched[0]] == [f"chunk-{NEW_SCOPE}"]
    assert [hit.chunk_id for hit in after[0]] == [f"chunk-{NEW_SCOPE}"]
    assert client.reads == [OLD_SCOPE, OLD_SCOPE, NEW_SCOPE, NEW_SCOPE]
    # 解決し直した範囲を次の検索で使う(3 回目は解決しない)。
    assert (client.resolves, client.checks) == (2, 3)


@pytest.mark.asyncio
async def test_reuse_is_per_filter_and_keeps_access_conditions_on_every_read() -> None:
    """範囲は検索の条件ごとに持ち、使い回しても読むたびに同じ権限の条件を掛ける(#5184)。"""
    client = SwitchingOracle(switch_during_read=False)
    reuse = NativeScopeReuse()
    kb_a = {"knowledge_base_id": "kb-a"}
    kb_b = {"knowledge_base_id": "kb-b"}

    await client.search_many(KEYWORD_SEARCHES, 3, kb_a, reuse=reuse)
    await client.search_many(KEYWORD_SEARCHES, 3, kb_a, reuse=reuse)
    await client.search_many(KEYWORD_SEARCHES, 3, kb_b, reuse=reuse)

    assert client.resolves == 2
    where_a, binds_a = _oracle_retrieval_where(kb_a)
    where_b, binds_b = _oracle_retrieval_where(kb_b)
    expected = [(where_a, binds_a), (where_a, binds_a), (where_b, binds_b)]
    assert len(client.read_conditions) == len(expected)
    for (sql, binds), (where, where_binds) in zip(client.read_conditions, expected, strict=True):
        assert f"WHERE {where}" in sql
        assert {key: binds[key] for key in where_binds} == where_binds


@pytest.mark.asyncio
async def test_failed_scope_is_not_reused() -> None:
    """範囲を読めなかった失敗は使い回さず、次の検索で解決し直す(#5184)。"""
    client = SwitchingOracle(switch_during_read=False)
    original = client._fetch_all
    failures = [RuntimeError("synthetic scope failure")]

    async def fail_first_resolve(
        sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        if "allowed_count" in sql and failures:
            raise failures.pop()
        return await original(sql, binds)

    client._fetch_all = fail_first_resolve  # type: ignore[method-assign]
    reuse = NativeScopeReuse()

    failed = await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)
    recovered = await client.search_many(KEYWORD_SEARCHES, 3, {}, reuse=reuse)

    assert getattr(failed[0], "status", "") == "failed"
    assert [hit.chunk_id for hit in recovered[0]] == [f"chunk-{OLD_SCOPE}"]
    assert client.resolves == 1
