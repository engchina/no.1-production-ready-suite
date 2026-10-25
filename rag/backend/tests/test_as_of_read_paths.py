"""検索の基準日(as_of)の既定と、文書・chunk を ID で読む入口の有効期間(#3249)。

- 検索の入口は、基準日が無ければ共通の設定の時間帯(`PLATFORM_TIMEZONE`)の今日で有効期間を絞る。
  日本時間の日付の境界(UTC 14:59 / 15:00 = JST 23:59 / 0:00)を固定した時計で確かめる。
- ID で読む入口(`rag_read_source` / `rag_outline` / `rag_read_document` / `rag_read_section`・
  回答の検証)は有効期間で絞らない。過去の基準日の検索で返った根拠を、続く呼び出しで今日の日付で読めなくしない。
  tenant・認可の範囲・INDEXED・有効な chunk_set の条件は同じに当てる。

DB は呼ばない(SQL の文と bind を読む)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pr_backend_core.timezones as timezones_module
import pytest

import app.config as config_module
from app.clients.oracle import OracleClient, _oracle_retrieval_where

_PERIOD_PREDICATE = "'$.effective_to'), '9999-12-31') > :filter_as_of"
# 日本時間の 2026-04-01 0:00 の直前と直後(UTC では 2026-03-31)。
_BEFORE_JST_MIDNIGHT = datetime(2026, 3, 31, 14, 59, tzinfo=UTC)
_AT_JST_MIDNIGHT = datetime(2026, 3, 31, 15, 0, tzinfo=UTC)


def _fix_clock(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return moment.astimezone(tz)

    monkeypatch.setattr(timezones_module, "datetime", _FixedDatetime)


@pytest.mark.parametrize(
    ("timezone", "moment", "expected"),
    [
        ("Asia/Tokyo", _BEFORE_JST_MIDNIGHT, "2026-03-31"),
        ("Asia/Tokyo", _AT_JST_MIDNIGHT, "2026-04-01"),
        ("UTC", _AT_JST_MIDNIGHT, "2026-03-31"),
    ],
    ids=["tokyo-before-midnight", "tokyo-at-midnight", "utc"],
)
def test_search_default_as_of_follows_common_timezone(
    monkeypatch: pytest.MonkeyPatch, timezone: str, moment: datetime, expected: str
) -> None:
    _fix_clock(monkeypatch, moment)
    monkeypatch.setattr(config_module.get_settings(), "timezone", timezone)

    sql, binds = _oracle_retrieval_where({})

    assert _PERIOD_PREDICATE in sql
    assert binds["filter_as_of"] == expected


def test_search_keeps_explicit_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    _fix_clock(monkeypatch, _AT_JST_MIDNIGHT)

    sql, binds = _oracle_retrieval_where({"as_of": "2025-04-01"})

    assert _PERIOD_PREDICATE in sql
    assert binds["filter_as_of"] == "2025-04-01"


class _CapturingClient(OracleClient):
    """SQL の文と bind を記録し、行を返さない代役。"""

    captured: list[tuple[str, Mapping[str, object]]]

    async def _fetch_one(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> dict[str, object] | None:
        self.captured.append((statement, dict(binds or {})))
        return None

    async def _fetch_all(
        self, statement: str, binds: Mapping[str, object] | None = None
    ) -> list[dict[str, object]]:
        self.captured.append((statement, dict(binds or {})))
        return []


_READS: list[tuple[str, Callable[[OracleClient], Coroutine[Any, Any, Any]]]] = [
    ("retrievable_chunk", lambda client: client.retrievable_chunk("doc-1", "chunk-1")),
    (
        "retrievable_element_chunk",
        lambda client: client.retrievable_element_chunk("doc-1", "recipe-1", "element-1"),
    ),
    ("retrievable_citation_chunk", lambda client: client.retrievable_citation_chunk("doc-1", "k")),
    ("document_reading_index", lambda client: client.document_reading_index("doc-1")),
    (
        "readable_document_chunks",
        lambda client: client.readable_document_chunks(
            "doc-1", chunk_set_id="cs-1", from_index=0, limit=10
        ),
    ),
    (
        "evaluation_document_locations",
        lambda client: client.evaluation_document_locations(["doc-1"]),
    ),
]


@pytest.mark.parametrize(("name", "read"), _READS, ids=[name for name, _ in _READS])
def test_reads_by_id_do_not_filter_by_effective_period(
    name: str, read: Callable[[OracleClient], Coroutine[Any, Any, Any]]
) -> None:
    client = _CapturingClient.__new__(_CapturingClient)
    client.captured = []

    asyncio.run(read(client))

    assert client.captured, name
    for statement, binds in client.captured:
        assert "effective_to" not in statement
        assert "effective_from" not in statement
        assert "filter_as_of" not in binds
        # 見え方の条件(INDEXED の文書)は検索と同じに当てる。
        assert "d.status = 'INDEXED'" in statement


def test_engine_settings_use_common_timezone(tmp_path: Path) -> None:
    """rag_engine の「今日」(検索の基準日の既定)も共通の設定の時間帯で決める。"""
    from app.config import Settings
    from app.rag.answer_engine import build_engine_settings

    assert build_engine_settings(Settings(timezone="UTC"), output_dir=tmp_path).timezone == "UTC"
    assert build_engine_settings(Settings(), output_dir=tmp_path).timezone == "Asia/Tokyo"
