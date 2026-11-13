"""Wiki 設定の API が対象範囲を確認し、背景構築だけを投入することを確認する。"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import wiki as module
from app.schemas.wiki import KnowledgeBaseWikiUpdate


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Any:
    data: dict[str, Any] = {"enabled": False, "exists": True, "events": []}

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
            return {"READY": 1}

        async def set_enabled(self, kb: str, enabled: bool) -> None:
            data["enabled"] = enabled

    monkeypatch.setattr(module, "OracleClient", Oracle)
    monkeypatch.setattr(module, "WikiStore", Store)
    monkeypatch.setattr(
        module,
        "wiki_jobs",
        SimpleNamespace(notify=lambda *args, **kwargs: data["events"].append((args, kwargs))),
    )
    return data


@pytest.mark.asyncio
async def test_enable_queues_build_and_disable_retains_cached_state(api: Any) -> None:
    result = await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=True))
    assert result.data is not None
    assert result.data.enabled and api["events"] == [(("kb-a",), {})]
    result = await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=False))
    assert result.data is not None
    assert not result.data.enabled and result.data.counts["READY"] == 1
    assert len(api["events"]) == 1


@pytest.mark.asyncio
async def test_invisible_kb_is_404_before_state_read_or_write(api: Any) -> None:
    api["exists"] = False
    with pytest.raises(HTTPException) as caught:
        await module.update_wiki_settings("kb-a", KnowledgeBaseWikiUpdate(enabled=True))
    assert caught.value.status_code == 404 and not api["events"]


def test_switch_rejects_strings_and_unknown_fields() -> None:
    for values in ({"enabled": "false"}, {"enabled": True, "scope": "other"}):
        with pytest.raises(ValidationError):
            KnowledgeBaseWikiUpdate.model_validate(values)
