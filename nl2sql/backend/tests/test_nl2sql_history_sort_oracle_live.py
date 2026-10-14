"""実 Oracle AI Database で、実行履歴の並べ替えがページをまたいで同じ順になるか（#1764・#1777）。

通常 CI では実行しない。`NL2SQL_RUN_ORACLE_INTEGRATION=1` の明示指定時だけ、
一意な業務プロファイルの ID を付けた一時の履歴の文書を作り、質問・実行日時の昇順・降順で
2 件ずつのページを最後までたどって確かめ、finally で必ず消す。LLM は呼ばない。資格情報は出力しない。
質問は日本語の辞書順（JAPANESE_M）で並ぶ。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

from app.features.nl2sql.incremental_store import (
    HISTORY_QUESTION_SORT_PREFIX_CHARS,
    OracleIncrementalNl2SqlRepository,
)
from app.features.nl2sql.oracle_adapter import OracleNl2SqlAdapter
from app.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_RUN_ORACLE_INTEGRATION") != "1",
    reason="NL2SQL_RUN_ORACLE_INTEGRATION=1 の明示指定が必要です。",
)


@pytest.fixture
def live_settings() -> Iterator[Any]:
    """conftest が PLATFORM_ORACLE_USER=APP を強制するため、共通 .env の接続ユーザーへ戻す。"""
    settings = get_settings()
    env_user = str(
        dotenv_values(Path(__file__).resolve().parents[3] / "platform" / ".env").get(
            "PLATFORM_ORACLE_USER"
        )
        or os.environ.get("PLATFORM_ORACLE_USER", "")
    )
    original_user = settings.oracle_user
    if env_user:
        settings.oracle_user = env_user
    try:
        yield settings
    finally:
        settings.oracle_user = original_user


def _pages(
    repository: OracleIncrementalNl2SqlRepository,
    *,
    profile_id: str,
    sort_key: str,
    direction: str,
) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        documents, cursor, total = repository.list_documents_page(
            "history",
            cursor=cursor,
            limit=2,
            profile_id=profile_id,
            sort_key=sort_key,
            direction=direction,
        )
        assert total == len(_ROWS)
        ids.extend(str(document["id"]) for document in documents)
        if not cursor:
            return ids
    raise AssertionError("ページが終わらない")


_LONG_PREFIX = "長" * (HISTORY_QUESTION_SORT_PREFIX_CHARS + 50)
# 質問の文と実行日時の順を変えておく。binary（文字コード）の順では、カタカナ（アイス）が
# ひらがなの後、「顧客」（U+9867）が「注文」（U+6CE8）の後になる。日本語の辞書順では、
# 英字 → かな（ひらがなとカタカナは同じ音で並び、濁音は清音の後）→ 漢字（JIS X 0208 の順。
# 顧 → 注）。同じ質問（顧客の確認）と、先頭の比べる文字数が同じ長い質問（注文 長…）は
# ENTITY_ID の順で決まる。
_ROWS = [
    ("h1", "顧客の確認", "2026-10-01T00:00:01+00:00"),
    ("h2", "abc の売上", "2026-10-01T00:00:02+00:00"),
    ("h3", f"注文 {_LONG_PREFIX} 1", "2026-10-01T00:00:03+00:00"),
    ("h4", "顧客の確認", "2026-10-01T00:00:04+00:00"),
    ("h5", "がっこうの数", "2026-10-01T00:00:05+00:00"),
    ("h6", f"注文 {_LONG_PREFIX} 2", "2026-10-01T00:00:06+00:00"),
    ("h7", "かきの在庫", "2026-10-01T00:00:07+00:00"),
    ("h8", "アイスの売上", "2026-10-01T00:00:08+00:00"),
]
# memory の store の同じ確認は tests/test_nl2sql_history_paging.py の
# test_memory_history_sort_matches_the_oracle_japanese_order（同じ質問・同じ期待の順）。
_QUESTION_ASC = ["h2", "h8", "h7", "h5", "h1", "h4", "h3", "h6"]


def test_history_sort_pages_through_all_rows_in_order_on_oracle(live_settings: Any) -> None:
    adapter = OracleNl2SqlAdapter(live_settings)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=adapter.state_connection)
    tag = uuid.uuid4().hex[:10]
    profile_id = f"t1777_{tag}"
    entity_ids = {key: f"t1777_{tag}_{key}" for key, _question, _created in _ROWS}
    try:
        for key, question, created_at in _ROWS:
            repository.put_document(
                "history",
                entity_ids[key],
                {"id": key, "question": question, "created_at": created_at},
                profile_id=profile_id,
                status="unrated",
            )

        assert (
            _pages(repository, profile_id=profile_id, sort_key="question", direction="asc")
            == _QUESTION_ASC
        )
        assert _pages(
            repository, profile_id=profile_id, sort_key="question", direction="desc"
        ) == list(reversed(_QUESTION_ASC))
        created_asc = [key for key, _question, _created in _ROWS]
        assert (
            _pages(repository, profile_id=profile_id, sort_key="created_at", direction="asc")
            == created_asc
        )
        assert _pages(
            repository, profile_id=profile_id, sort_key="created_at", direction="desc"
        ) == list(reversed(created_asc))
    finally:
        for entity_id in entity_ids.values():
            repository.delete_document("history", entity_id)
