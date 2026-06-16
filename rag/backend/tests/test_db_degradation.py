"""DB 停止/応答不良時の閲覧系 API 縮退テスト。

ドキュメント一覧・統計・取込ジョブ・ナレッジベース一覧が、DB 不通でも
500 ではなく空データ + warning の 200 応答へ縮退することを検証する。
"""

import asyncio

import pytest

from app.api.routes import documents as documents_route
from app.api.routes import knowledge_bases as knowledge_bases_route
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)


class _RaisingOracle:
    """全 DB 呼び出しが即時に例外を送出する fake(DB 接続不可を再現)。"""

    def __getattr__(self, _name: str):
        async def _raise(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("database is down")

        return _raise


class _HangingOracle:
    """全 DB 呼び出しが返らない fake(DB 応答待ちハングを再現)。"""

    def __getattr__(self, _name: str):
        async def _hang(*_args: object, **_kwargs: object) -> object:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

        return _hang


def _set_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    settings = documents_route.get_settings()
    monkeypatch.setattr(settings, "db_read_timeout_seconds", seconds)


@pytest.mark.parametrize(
    ("path", "fake"),
    [
        ("/api/documents", _RaisingOracle),
        ("/api/documents/stats", _RaisingOracle),
        ("/api/documents/ingestion-jobs", _RaisingOracle),
    ],
)
def test_document_reads_degrade_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    fake: type,
) -> None:
    """DB 接続不可でも documents 系 GET は 200 + warning で縮退する。"""
    monkeypatch.setattr(documents_route, "OracleClient", lambda: fake())

    response = client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert len(body["warning_messages"]) == 1
    assert "データベース" in body["warning_messages"][0]


def test_document_list_degrades_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 応答待ちでハングしても timeout 縮退して空一覧を返す。"""
    _set_timeout(monkeypatch, 0.01)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: _HangingOracle())

    response = client.get("/api/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0
    assert "0.01 秒以内に応答しませんでした" in body["warning_messages"][0]


def test_document_stats_degrades_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """統計は DB 不通時にゼロ集計へ縮退する。"""
    monkeypatch.setattr(documents_route, "OracleClient", lambda: _RaisingOracle())

    response = client.get("/api/documents/stats")

    body = response.json()
    assert body["data"] == {"total": 0, "by_status": {}}


def test_knowledge_base_list_degrades_on_db_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB 接続不可でもナレッジベース一覧は 200 + warning で縮退する。"""
    monkeypatch.setattr(knowledge_bases_route, "OracleClient", lambda: _RaisingOracle())

    response = client.get("/api/knowledge-bases")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == []
    assert len(body["warning_messages"]) == 1
