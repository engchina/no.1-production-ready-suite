"""SQL の種類(label)ごとの回数・時間を、既存の DB の計測に足すこと(#5184)。

- label ごとの回数・秒が、計測の範囲(入れ子を含む)に正しく足される。
- 並行の 2 経路(``asyncio.gather`` と ``asyncio.to_thread``)で、label と各 SQL の秒が混ざらない。
- 原生検索(``search_many``)の各 SQL が、範囲の確認・範囲の解決・距離順・全文(batch ごと)・
  原生のベクトル・範囲の古さの確認の label で数えられる。
- 回答の記録の工程(``execution_steps``)に、既存の DB の計測値の隣に ``db_by_label`` で出る。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from time import perf_counter
from typing import Any

from rag_engine.generation.answer_models import AnswerQuestionResult

from app.clients import oracle as oracle_module
from app.clients.oracle import OracleClient
from app.clients.oracle_read_stats import (
    current_db_call_label,
    current_db_call_stats,
    db_call_label,
    track_db_calls,
)
from app.config import Settings
from app.rag.answer_engine import _outcome_from_result, _SearchState
from app.schemas.search import SearchMode


def test_labels_add_count_and_seconds_to_nested_scopes() -> None:
    with track_db_calls() as outer:
        stats = current_db_call_stats()
        assert stats is not None
        stats.record_execute(rows=1, seconds=0.5)
        with track_db_calls() as inner, db_call_label("keyword"):
            inner_stats = current_db_call_stats()
            assert inner_stats is not None
            inner_stats.record_execute(rows=2, seconds=0.25)
            inner_stats.record_execute(rows=2, seconds=0.125)
            with db_call_label("retry"):
                # 入れ子の label は「外側.内側」。
                assert current_db_call_label() == "keyword.retry"
        assert current_db_call_label() == "other"

    assert inner.label_snapshot() == {"keyword": {"count": 2, "seconds": 0.375}}
    assert outer.label_snapshot() == {
        "keyword": {"count": 2, "seconds": 0.375},
        "other": {"count": 1, "seconds": 0.5},
    }
    # 既存の合計値は変わらない(label の値は合計の内訳)。
    assert outer.snapshot()["db_execute_count"] == 3
    assert outer.snapshot()["db_seconds"] == 0.875
    assert outer.step_snapshot()["db_by_label"] == outer.label_snapshot()


class _SleepCursor:
    """``execute`` に指定の秒だけかかる cursor の代役(SQL の本文・bind は使わない)。"""

    description = (("value",),)

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def execute(self, statement: str, parameters: Mapping[str, object] | None = None) -> None:
        time.sleep(self._seconds)

    def fetchall(self) -> list[tuple[object, ...]]:
        return [(1,)]

    def close(self) -> None:
        pass


class _SleepConnection:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def cursor(self) -> _SleepCursor:
        return _SleepCursor(self._seconds)


async def test_parallel_paths_keep_their_own_label_and_seconds() -> None:
    """並行に読む 2 経路の label が混ざらず、各 SQL の秒はその SQL の実行の時間になる。"""
    both_labelled = asyncio.Barrier(2)

    async def path(label: str, seconds: float, repeat: int) -> None:
        with db_call_label(label):
            # 両方の経路が label を付けてから SQL を読む(label を付ける順と SQL が重なる)。
            await both_labelled.wait()
            await asyncio.gather(
                *(
                    asyncio.to_thread(
                        oracle_module._fetch_all,
                        _SleepConnection(seconds),  # type: ignore[arg-type]
                        "SELECT 1 FROM dual",
                        {},
                    )
                    for _ in range(repeat)
                )
            )

    with track_db_calls() as stats:
        await asyncio.gather(path("slow", 0.5, 1), path("fast", 0.02, 2))

    labels = stats.label_snapshot()
    assert set(labels) == {"slow", "fast"}
    assert labels["slow"]["count"] == 1
    assert labels["fast"]["count"] == 2
    # 並行に重なっても、遅い SQL の秒が速い経路に入らない(壁時計の重なりを足さない)。
    assert labels["slow"]["seconds"] >= 0.5
    assert 0.04 <= labels["fast"]["seconds"] < 0.4
    assert current_db_call_label() == "other"


class _LabelOracle(OracleClient):
    """原生検索の SQL の種類を、SQL の形で合成の行に答える代役。"""

    def __init__(self, *, exact_max_chunks: int, scopes: list[int]) -> None:
        super().__init__(
            Settings(
                rag_hybrid_search_backend="oracle_hybrid_index",
                rag_native_vector_exact_max_chunks=exact_max_chunks,
                rag_native_vector_shortage_oversample_factor=1,
            )
        )
        self.scopes = scopes
        self.search_batches = 0

    async def _fetch_all(
        self, sql: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        started = perf_counter()
        rows: list[dict[str, object]] = []
        if "FROM user_indexes" in sql:
            rows = [{"index_name": "RAG_CHUNKS_HYBRID_IDX"}]
        elif "allowed_count" in sql:
            rows = [
                {"native_scope_id": scope, "allowed_count": 2, "vector_count": 2}
                for scope in self.scopes
            ]
        elif "COUNT(*) AS chunk_count" in sql:
            rows = [{"native_scope_id": scope, "chunk_count": 2} for scope in self.scopes]
        elif "DBMS_HYBRID_VECTOR.SEARCH" in sql:
            self.search_batches += 1
        # 実際の SQL の実行と同じく、終わったときに計測の範囲へ足す。
        oracle_module._record_db_execute(started, rows=len(rows))
        return rows


# 連続しない chunk set の番号(全文の範囲の条件が複数の batch に分かれる)。
_FRAGMENTED_SCOPES = [10_000_000 + index * 2 for index in range(1000)]


async def test_search_many_labels_each_native_sql() -> None:
    client = _LabelOracle(exact_max_chunks=1_000_000, scopes=_FRAGMENTED_SCOPES)
    searches = [(f"合成の検索文 {index}", [1.0] * 1536, SearchMode.HYBRID) for index in range(2)]

    with track_db_calls() as stats:
        await client.search_many(searches, 4)

    labels = stats.label_snapshot()
    batches = client.search_batches // len(searches)
    assert batches > 1
    assert {name: values["count"] for name, values in labels.items()} == {
        "native_ready": 1,
        # 件数の集計と chunk set の全体の件数(範囲の解決は要求で 1 回)。
        "native_scope": 2,
        "vector_exact": 2,
        # 全文の SQL は batch ごとに 1 回(回数 = 読んだ batch の数)。
        "keyword": batches * len(searches),
        "scope_stale": 1,
    }
    assert stats.snapshot()["db_execute_count"] == sum(
        values["count"] for values in labels.values()
    )


async def test_native_vector_index_reads_are_labelled_vector_native() -> None:
    client = _LabelOracle(exact_max_chunks=0, scopes=[7])

    with track_db_calls() as stats:
        await client.search_many([("合成の検索文", [1.0] * 1536, SearchMode.VECTOR)], 4)

    counts = {name: values["count"] for name, values in stats.label_snapshot().items()}
    assert counts == {
        "native_ready": 1,
        "native_scope": 2,
        "vector_native": 1,
        "scope_stale": 1,
    }


def test_step_records_db_by_label_next_to_db_metrics() -> None:
    step: dict[str, Any] = {
        "name": "文書検索",
        "status": "complete",
        "elapsed_seconds": 1.5,
        "llm_calls": 0,
        "depth": 1,
        "db_execute_count": 3,
        "db_seconds": 1.25,
        "db_by_label": {
            "keyword": {"count": 2, "seconds": 1.0},
            "native_scope": {"count": 1, "seconds": 0.25},
            # 形の崩れた値は写さない。
            "broken": {"count": "2", "seconds": 1.0},
            "empty": {"count": 0, "seconds": 0.0},
        },
    }
    result = AnswerQuestionResult(
        answer="合成の回答",
        question_display="合成の質問",
        original_question="合成の質問",
        execution_steps=(step, {"name": "回答生成", "status": "complete"}),
    )

    steps = _outcome_from_result(result, _SearchState()).diagnostics["execution_steps"]

    assert steps[0]["db_execute_count"] == 3
    assert steps[0]["db_seconds"] == 1.25
    assert steps[0]["db_by_label"] == {
        "keyword": {"count": 2, "seconds": 1.0},
        "native_scope": {"count": 1, "seconds": 0.25},
    }
    # label の無い工程には足さない。
    assert "db_by_label" not in steps[1]
