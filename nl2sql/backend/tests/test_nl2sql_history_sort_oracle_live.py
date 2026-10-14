"""実 Oracle AI Database で、実行履歴の並べ替えがページをまたいで同じ順になるか（#1764）。

通常 CI では実行しない。`NL2SQL_RUN_ORACLE_INTEGRATION=1` の明示指定時だけ、
一意な業務プロファイルの ID を付けた一時の履歴の文書を作り、質問・実行日時の昇順・降順で
2 件ずつのページを最後までたどって確かめ、finally で必ず消す。LLM は呼ばない。資格情報は出力しない。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

from app.features.nl2sql.incremental_store import OracleIncrementalNl2SqlRepository
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
        assert total == 6
        ids.extend(str(document["id"]) for document in documents)
        if not cursor:
            return ids
    raise AssertionError("ページが終わらない")


def test_history_sort_pages_through_all_rows_in_order_on_oracle(live_settings: Any) -> None:
    adapter = OracleNl2SqlAdapter(live_settings)
    repository = OracleIncrementalNl2SqlRepository(connection_factory=adapter.state_connection)
    tag = uuid.uuid4().hex[:10]
    profile_id = f"t1764_{tag}"
    long_prefix = "長" * 450
    # 質問の文と実行日時の順を変えておく。同じ質問（d）と、先頭 400 文字が同じ長い質問（z）は
    # ENTITY_ID の順で決まる。
    rows = [
        ("h1", "d 部署の確認", "2026-10-01T00:00:01+00:00"),
        ("h2", "a 売上の確認", "2026-10-01T00:00:02+00:00"),
        ("h3", f"z {long_prefix} 1", "2026-10-01T00:00:03+00:00"),
        ("h4", "d 部署の確認", "2026-10-01T00:00:04+00:00"),
        ("h5", "b 顧客の確認", "2026-10-01T00:00:05+00:00"),
        ("h6", f"z {long_prefix} 2", "2026-10-01T00:00:06+00:00"),
    ]
    entity_ids = {key: f"t1764_{tag}_{key}" for key, _question, _created in rows}
    try:
        for key, question, created_at in rows:
            repository.put_document(
                "history",
                entity_ids[key],
                {"id": key, "question": question, "created_at": created_at},
                profile_id=profile_id,
                status="unrated",
            )

        assert _pages(repository, profile_id=profile_id, sort_key="question", direction="asc") == [
            "h2",
            "h5",
            "h1",
            "h4",
            "h3",
            "h6",
        ]
        assert _pages(repository, profile_id=profile_id, sort_key="question", direction="desc") == [
            "h6",
            "h3",
            "h4",
            "h1",
            "h5",
            "h2",
        ]
        assert _pages(
            repository, profile_id=profile_id, sort_key="created_at", direction="asc"
        ) == ["h1", "h2", "h3", "h4", "h5", "h6"]
        assert _pages(
            repository, profile_id=profile_id, sort_key="created_at", direction="desc"
        ) == ["h6", "h5", "h4", "h3", "h2", "h1"]
    finally:
        for entity_id in entity_ids.values():
            repository.delete_document("history", entity_id)
