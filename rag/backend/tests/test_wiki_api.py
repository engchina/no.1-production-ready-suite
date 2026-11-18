"""Wiki 設定の API が対象範囲を確認し、背景構築だけを投入することを確認する。"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pr_backend_core.api import OffsetParams
from pydantic import ValidationError

from app.api.routes import wiki as module
from app.schemas.wiki import KnowledgeBaseWikiUpdate

PAGE = OffsetParams(limit=25, offset=0)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Any:
    data: dict[str, Any] = {
        "enabled": False,
        "exists": True,
        "events": [],
        "writes": [],
        "pages": [],
        "status": "READY",
        "actor_ok": True,
    }

    class Oracle:
        async def get_knowledge_base_config(self, kb: str) -> Any:
            return (
                SimpleNamespace(
                    status="ACTIVE", retrieval_config={"wiki_build_enabled": data["enabled"]}
                )
                if data["exists"]
                else None
            )

    class Store:
        def __init__(self, oracle: Any) -> None:
            pass

        async def status_counts(self, filters: Any) -> Any:
            assert filters == {"knowledge_base_id": "kb-a"}
            return {data["status"]: 1}

        async def document_statuses(self, filters: Any, *, limit: int, offset: int) -> Any:
            assert filters == {"knowledge_base_id": "kb-a"}
            data["pages"].append((limit, offset))
            return [
                {
                    "document_id": "doc-a",
                    "file_name": "synthetic-a.pdf",
                    "status": data["status"],
                    "failure_code": "timeout" if data["status"] == "FAILED" else None,
                    "updated_at": None,
                }
            ]

        async def requeue_failed(self, filters: Any) -> None:
            assert filters == {"knowledge_base_id": "kb-a"}
            data["status"] = "QUEUED"

        async def enabled_knowledge_bases(self) -> Any:
            return [("kb-a", "owner-a")] if data["enabled"] else []

        async def set_enabled(self, kb: str, enabled: bool) -> None:
            data["enabled"] = enabled
            data["writes"].append(enabled)

    async def actor(owner: Any, settings: Any) -> None:
        assert owner == "owner-a"
        if not data["actor_ok"]:
            raise PermissionError("合成の権限の変更")

    monkeypatch.setattr(module, "OracleClient", Oracle)
    monkeypatch.setattr(module, "WikiStore", Store)
    monkeypatch.setattr(
        module,
        "wiki_jobs",
        SimpleNamespace(
            notify=lambda *args, **kwargs: data["events"].append((args, kwargs)), actor=actor
        ),
    )
    return data


@pytest.mark.asyncio
async def test_enable_queues_build_and_disable_retains_cached_state(api: Any) -> None:
    result = await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=True), PAGE)
    assert result.data is not None
    assert result.data.enabled and api["events"] == [(("kb-a",), {})]
    result = await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=False), PAGE)
    assert result.data is not None
    assert not result.data.enabled and result.data.counts["READY"] == 1
    assert len(api["events"]) == 1


@pytest.mark.asyncio
async def test_retry_requeues_failed_and_returns_state_after_retry(api: Any) -> None:
    api["enabled"], api["status"], api["actor_ok"] = True, "FAILED", False
    before = await module.get_wiki_settings("kb-a", PAGE)
    assert before.data is not None
    assert before.data.counts == {"FAILED": 1}
    assert before.data.documents.total == 1 and api["pages"][-1] == (PAGE.limit, PAGE.offset)
    assert before.data.documents.items[0].failure_code == "timeout"
    assert before.data.blocked_reason == "actor_unavailable"
    # 再試行した利用者を構築の指定者にし直し、待機へ戻した後の状態を返す（#5466）。
    api["actor_ok"] = True
    result = await module.retry_wiki_build("kb-a", PAGE)
    assert result.data is not None
    assert result.data.counts == {"QUEUED": 1}
    assert result.data.documents.items[0].failure_code is None
    assert result.data.blocked_reason is None
    assert api["writes"] == [True] and api["events"] == [(("kb-a",), {})]


@pytest.mark.asyncio
async def test_retry_requires_enabled_build(api: Any) -> None:
    with pytest.raises(HTTPException) as caught:
        await module.retry_wiki_build("kb-a", PAGE)
    assert caught.value.status_code == 409 and not api["events"] and not api["writes"]


@pytest.mark.asyncio
async def test_invisible_kb_is_404_before_state_read_or_write(api: Any) -> None:
    api["exists"] = False
    with pytest.raises(HTTPException) as caught:
        await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=True), PAGE)
    assert caught.value.status_code == 404 and not api["events"]


def test_switch_rejects_strings_and_unknown_fields() -> None:
    for values in ({"enabled": "false"}, {"enabled": True, "scope": "other"}):
        with pytest.raises(ValidationError):
            KnowledgeBaseWikiUpdate.model_validate(values)
