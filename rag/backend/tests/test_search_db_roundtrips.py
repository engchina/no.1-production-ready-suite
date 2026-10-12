"""回答の検索の DB の往復を減らしても、検索の結果が変わらないこと(#1490)。

- 読み取りの計測(SQL の回数・行数・LOB の読み取り)と、LOB を値で受け取る範囲。
- 前後の文脈(同じ chunk group の sibling)を起点をまとめた SQL で読む(起点の順・起点ごとの並び)。
- vector / keyword と検索文ごとの検索を並行に読む(融合の結果は直列と同じ)。
- 1 回の回答の中で同じ検索文の結果を使い回す(CRAG の 2 回目)。
"""

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

import pytest

import app.rag.answer_engine as answer_engine_module
import app.rag.pipeline as pipeline_module
from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient
from app.clients.oracle_read_stats import (
    current_db_call_stats,
    read_lobs_as_values,
    track_db_calls,
)
from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest


class _Lob:
    """python-oracledb の LOB のロケータの代役(``read()`` で本文を返す)。"""

    def __init__(self, value: str) -> None:
        self.value = value

    def read(self) -> str:
        return self.value


class _LobCursor:
    """``fetch_lobs`` を受ける cursor の代役。``fetch_lobs=False`` なら CLOB を str で返す。"""

    description = (("chunk_id",), ("chunk_text",))

    def __init__(self, calls: list[object]) -> None:
        self._calls = calls
        self._fetch_lobs: bool | None = None

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, object] | None = None,
        *,
        fetch_lobs: bool | None = None,
    ) -> None:
        self._calls.append(fetch_lobs)
        self._fetch_lobs = fetch_lobs

    def fetchall(self) -> list[tuple[object, ...]]:
        texts = ["本文 1", "本文 2"]
        if self._fetch_lobs is False:
            return [("c1", texts[0]), ("c2", texts[1])]
        return [("c1", _Lob(texts[0])), ("c2", _Lob(texts[1]))]

    def close(self) -> None:
        pass


class _PlainCursor(_LobCursor):
    """``fetch_lobs`` を受けない cursor(テストの代役など)。"""

    def execute(  # type: ignore[override]
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> None:
        self._calls.append("plain")


class _Connection:
    def __init__(self, cursor_type: type[_LobCursor]) -> None:
        self.calls: list[object] = []
        self._cursor_type = cursor_type

    def cursor(self) -> _LobCursor:
        return self._cursor_type(self.calls)


def test_fetch_all_reads_lobs_as_values_only_in_scope() -> None:
    connection = _Connection(_LobCursor)
    expected = [
        {"chunk_id": "c1", "chunk_text": "本文 1"},
        {"chunk_id": "c2", "chunk_text": "本文 2"},
    ]

    with track_db_calls() as outside:
        rows = oracle_module._fetch_all(connection, "SELECT chunk_id, chunk_text FROM t", {})  # type: ignore[arg-type]
    with track_db_calls() as inside, read_lobs_as_values():
        values = oracle_module._fetch_all(connection, "SELECT chunk_id, chunk_text FROM t", {})  # type: ignore[arg-type]

    # 値は従来(LOB を .read() した値)と同じ。
    assert rows == expected
    assert values == expected
    assert connection.calls == [None, False]
    assert outside.snapshot()["db_lob_read_count"] == 2
    assert outside.snapshot()["db_round_trip_count"] == 3
    assert inside.snapshot()["db_lob_read_count"] == 0
    assert inside.snapshot()["db_round_trip_count"] == 1
    assert inside.snapshot()["db_row_count"] == 2


def test_fetch_all_does_not_pass_fetch_lobs_to_cursor_without_parameter() -> None:
    connection = _Connection(_PlainCursor)

    with read_lobs_as_values():
        rows = oracle_module._fetch_all(connection, "SELECT chunk_id, chunk_text FROM t", {})  # type: ignore[arg-type]

    assert rows == [
        {"chunk_id": "c1", "chunk_text": "本文 1"},
        {"chunk_id": "c2", "chunk_text": "本文 2"},
    ]
    assert connection.calls == ["plain"]


async def test_track_db_calls_counts_threads_and_nested_scopes() -> None:
    def record() -> None:
        stats = current_db_call_stats()
        assert stats is not None
        stats.record_execute(rows=3, seconds=0.5)

    with track_db_calls() as outer:
        await asyncio.to_thread(record)
        with track_db_calls() as inner:
            await asyncio.gather(asyncio.to_thread(record), asyncio.to_thread(record))

    assert inner.snapshot()["db_execute_count"] == 2
    assert outer.snapshot()["db_execute_count"] == 3
    assert outer.snapshot()["db_row_count"] == 9
    assert outer.snapshot()["db_seconds"] == 1.5
    assert current_db_call_stats() is None


async def test_observe_stage_adds_db_metrics_to_span(monkeypatch: pytest.MonkeyPatch) -> None:
    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(pipeline_module, "record_trace_span", lambda **kwargs: spans.append(kwargs))

    async def with_sql() -> str:
        stats = current_db_call_stats()
        assert stats is not None
        stats.record_execute(rows=4, seconds=0.25)
        stats.record_lob_read()
        return "ok"

    async def without_sql() -> str:
        return "ok"

    await pipeline_module._observe_stage("trace-1", "retrieval", with_sql())
    await pipeline_module._observe_stage("trace-1", "answer_guardrail", without_sql())

    assert spans[0]["attributes"] == {
        "db_execute_count": 1,
        "db_row_count": 4,
        "db_lob_read_count": 1,
        "db_round_trip_count": 2,
        "db_seconds": 0.25,
    }
    # SQL を実行しなかった工程には足さない。
    assert spans[1]["attributes"] == {}


def _anchor(chunk_id: str, *, index: int, chunk_set_id: str | None) -> RetrievedChunk:
    metadata: dict[str, Any] = {"chunk_index": index, "chunk_group_id": f"grp-{chunk_id}"}
    if chunk_set_id is not None:
        metadata["chunk_set_id"] = chunk_set_id
    else:
        metadata["recipe_id"] = "recipe-legacy"
    return RetrievedChunk(
        document_id="doc-1", chunk_id=chunk_id, text=chunk_id, score=0.5, metadata=metadata
    )


def _sibling_row(anchor_id: str, offset: int, *, anchor_ord: int | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "document_id": "doc-1",
        "chunk_id": f"{anchor_id}+{offset}",
        "chunk_text": f"{anchor_id} の前後 {offset}",
        "metadata_json": "{}",
        "chunk_index": 100 + offset,
        "chunk_set_id": "set-a",
        "file_name": "a.pdf",
        "category_name": None,
        "superseded_by_document_id": None,
        "score": 0,
    }
    if anchor_ord is not None:
        row["anchor_ord"] = anchor_ord
        row["context_rank"] = offset
    return row


async def test_context_group_siblings_batch_keeps_anchor_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """起点を 50 件ずつ 1 回の SQL で読み、返す順は起点の順・起点ごとの並びのまま。

    chunk_set の無い起点(レシピ導入前)は 1 件ずつ読む。
    """
    anchors = [_anchor(f"a{index}", index=100, chunk_set_id="set-a") for index in range(60)]
    anchors.insert(30, _anchor("legacy", index=100, chunk_set_id=None))
    client = OracleClient(settings=Settings.model_construct())
    statements: list[tuple[str, dict[str, object]]] = []

    async def fake_fetch_all(
        statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        binds = dict(binds or {})
        statements.append((statement, binds))
        if "anchor_ord" in statement:
            rows = [
                _sibling_row(str(binds[f"ca_chunk_{ordinal}"]), offset, anchor_ord=ordinal)
                for ordinal in range(oracle_module._CONTEXT_ANCHOR_BATCH_SIZE)
                if binds[f"ca_chunk_{ordinal}"] is not None
                for offset in (1, 2)
            ]
            # SQL の並びに頼らず、起点の位置で組み立て直すこと。
            return list(reversed(rows))
        return [_sibling_row(str(binds["anchor_chunk_id"]), offset) for offset in (1, 2)]

    monkeypatch.setattr(client, "_fetch_all", fake_fetch_all)

    siblings = await client.context_group_siblings(anchors, max_chunks_per_group=2)

    # 60 件の chunk_set の起点は 2 回、レシピ導入前の起点は 1 回。
    assert len(statements) == 3
    assert sum("anchor_ord" in statement for statement, _ in statements) == 2
    expected_order = [f"{anchor.chunk_id}+{offset}" for anchor in anchors for offset in (1, 2)]
    reversed_pairs = [
        f"{anchor.chunk_id}+{offset}"
        for anchor in anchors
        for offset in ((2, 1) if anchor.metadata.get("chunk_set_id") else (1, 2))
    ]
    # 同じ起点の中の並びは SQL の並び(context_rank の順)。fake は逆順に返したので、起点の順は
    # 保ちつつ起点の中は fake の順になる。実 DB は context_rank の順に返す。
    assert [chunk.chunk_id for chunk in siblings] == reversed_pairs
    assert sorted(chunk.chunk_id for chunk in siblings) == sorted(expected_order)
    assert [chunk.metadata["context_anchor_chunk_id"] for chunk in siblings] == [
        anchor.chunk_id for anchor in anchors for _ in (1, 2)
    ]
    assert all(chunk.score == 0.5 for chunk in siblings)


async def test_context_group_siblings_batch_sql_is_stable_for_any_batch_size() -> None:
    """起点の数に関係なく SQL の文は同じ(解析済みの文を使い回す)。"""
    one = oracle_module._context_group_siblings_batch_sql(
        [(_anchor("a", index=1, chunk_set_id="set-a"), "grp-a", 1)], max_chunks_per_group=4
    )
    many = oracle_module._context_group_siblings_batch_sql(
        [(_anchor(f"a{i}", index=i, chunk_set_id="set-a"), f"grp-{i}", i) for i in range(50)],
        max_chunks_per_group=4,
    )

    assert one[0] == many[0]
    # 文書と chunk_set は起点の表の値(bind ではない)。
    assert ":filter_document_id" not in one[0]
    assert ":filter_chunk_set_id" not in one[0]
    assert "filter_document_id" not in one[1]
    with pytest.raises(ValueError):
        oracle_module._context_group_siblings_batch_sql(
            [(_anchor(f"a{i}", index=i, chunk_set_id="set-a"), f"grp-{i}", i) for i in range(51)],
            max_chunks_per_group=4,
        )


def _hit(chunk_id: str, score: float, *, mode: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id=chunk_id,
        text=chunk_id,
        score=score,
        metadata={"chunk_index": int(chunk_id.rsplit("-", 1)[1]), "retrieval_mode": mode},
    )


class _DelayedBranches(OracleClient):
    """vector と keyword の終わる順を変えられる OracleClient。"""

    def __init__(self, *, vector_delay: float, keyword_delay: float) -> None:
        super().__init__(settings=Settings.model_construct(rag_rrf_k=60))
        self._vector_delay = vector_delay
        self._keyword_delay = keyword_delay

    async def vector_search(
        self, embedding: list[float], top_k: int, filters: dict[str, str] | None = None
    ) -> list[RetrievedChunk]:
        await asyncio.sleep(self._vector_delay)
        return [_hit(f"c-{i}", 1.0 - i / 10, mode="vector") for i in (1, 2, 3)]

    async def keyword_search(
        self, query: str, top_k: int, filters: dict[str, str] | None = None
    ) -> list[RetrievedChunk]:
        await asyncio.sleep(self._keyword_delay)
        return [_hit(f"c-{i}", 0.5 - i / 10, mode="keyword") for i in (3, 4, 1)]


async def test_hybrid_search_result_does_not_depend_on_branch_completion_order() -> None:
    vector_first = await _DelayedBranches(vector_delay=0.0, keyword_delay=0.02).hybrid_search(
        "質問", [0.1], 3
    )
    keyword_first = await _DelayedBranches(vector_delay=0.02, keyword_delay=0.0).hybrid_search(
        "質問", [0.1], 3
    )

    assert [hit.model_dump() for hit in vector_first] == [hit.model_dump() for hit in keyword_first]
    # 両方に当たった c-1 / c-3 が上位(RRF)。
    assert [hit.chunk_id for hit in vector_first] == ["c-1", "c-3", "c-2"]


class _SearchOracle:
    """検索文ごとに決まった候補を返し、後ろの検索文ほど早く終わる Oracle の代役。"""

    def __init__(self) -> None:
        self.searched: list[str] = []
        self.concurrent = 0
        self.max_concurrent = 0

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        self.searched.append(query)
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            number = int(query.rsplit(" ", 1)[1])
            await asyncio.sleep(0.002 * (10 - number))
            ids = [(number + offset) % 7 for offset in range(4)]
            return [
                _hit(f"c-{chunk}", 1.0 - rank / 10, mode=mode.value)
                for rank, chunk in enumerate(ids)
            ]
        finally:
            self.concurrent -= 1

    async def document_classifications(self, document_ids: list[str]) -> dict[str, Any]:
        return {}

    async def chunk_set_first_page_contexts(self, chunk_set_ids: list[str]) -> dict[str, Any]:
        return {}

    async def context_group_siblings(
        self, anchors: list[RetrievedChunk], *, max_chunks_per_group: int
    ) -> list[RetrievedChunk]:
        return []


class _GenAi:
    async def embed(
        self, texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
    ) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


def _dump(result: Any) -> tuple[list[tuple[str, float]], list[str]]:
    return (
        [(chunk.chunk_uid, chunk.metadata.get("rrf_score")) for chunk in result.child_chunks],
        [chunk.chunk_uid for chunk in result.all_chunks],
    )


async def _search(
    oracle: _SearchOracle, queries: list[str], state: _SearchState | None = None
) -> tuple[Any, _SearchState]:
    engine = AnswerEngine(Settings(), oracle=oracle, genai=_GenAi())  # type: ignore[arg-type]
    state = state or _SearchState()
    result = await engine._search(
        SearchRequest(query=queries[0], filters={"knowledge_base_id": "kb-1"}),
        state,
        retrieval_queries=queries,
        candidate_limit=6,
        expand_references=False,
    )
    return result, state


async def test_search_result_is_same_with_parallel_and_sequential_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = [f"検索文 {number}" for number in range(6)]

    monkeypatch.setattr(answer_engine_module, "_SEARCH_QUERY_CONCURRENCY", 1)
    sequential_oracle = _SearchOracle()
    sequential, _ = await _search(sequential_oracle, queries)
    monkeypatch.setattr(answer_engine_module, "_SEARCH_QUERY_CONCURRENCY", 4)
    parallel_oracle = _SearchOracle()
    parallel, _ = await _search(parallel_oracle, queries)

    assert sequential_oracle.max_concurrent == 1
    assert parallel_oracle.max_concurrent == 4
    assert _dump(parallel) == _dump(sequential)
    assert _dump(parallel)[0]


async def test_search_reuses_query_results_within_one_answer() -> None:
    """CRAG の 2 回目は、前の回と同じ検索文を読み直さず、結果は初めから検索したときと同じ。"""
    first_queries = [f"検索文 {number}" for number in range(4)]
    second_queries = [*first_queries, "検索文 5"]
    oracle = _SearchOracle()

    _, state = await _search(oracle, first_queries)
    oracle.searched.clear()
    reused, _ = await _search(oracle, second_queries, state)
    fresh, _ = await _search(_SearchOracle(), second_queries)

    assert oracle.searched == ["検索文 5"]
    assert _dump(reused) == _dump(fresh)


def test_pool_max_uses_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, object]] = []

    def fake_create_pool(**kwargs: object) -> object:
        created.append(kwargs)
        return object()

    monkeypatch.setattr(oracle_module, "_SHARED_ORACLE_POOL", None)
    fake_oracledb: Callable[[str], object] = lambda name: type(  # noqa: E731
        "FakeOracledb",
        (),
        {"create_pool": staticmethod(fake_create_pool), "POOL_GETMODE_TIMEDWAIT": 3},
    )
    monkeypatch.setattr("app.clients.oracle.importlib.import_module", fake_oracledb)
    settings = Settings.model_construct(
        oracle_user="rag_app",
        oracle_password="db-secret",
        oracle_dsn="ragdb_high",
        oracle_client_lib_dir="",
        oracle_wallet_dir="",
        oracle_wallet_password="",
        oracle_pool_max=16,
    )

    OracleClient(settings=settings).connection_pool()

    assert created[0]["max"] == 16
    assert Settings.model_fields["oracle_pool_max"].default == 16
