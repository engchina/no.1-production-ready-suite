"""業務ビュー単位の用語・ルール(runtime knowledge)API と検索時の反映。"""

import pytest
from pytest import MonkeyPatch

from app.api.routes import business_view_knowledge as knowledge_route
from app.api.routes import search as search_route
from app.main import app
from app.rag.business_view_config import BusinessViewConfig
from app.rag.kb_adapter_config import KnowledgeBaseQueryConfig
from tests import test_search_business_view as search_tests
from tests.support import AsgiTestClient
from tests.test_business_view_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/business-views/bv-1/runtime-knowledge"


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    return fake


def test_edit_terms_rules_and_preview(fake_oracle: FakeKnowledgeOracle) -> None:
    assert client.get(BASE).json()["data"] == {"business_view_id": "bv-1", "terms": [], "rules": []}

    term = client.post(
        f"{BASE}/edit",
        json={
            "kind": "terms",
            "name": "受注",
            "labels": "オーダー\n注文",
            "content": "顧客からの注文",
        },
    )
    assert term.status_code == 200
    assert term.json()["data"]["terms"][0]["aliases"] == ["オーダー", "注文"]
    rule = client.post(
        f"{BASE}/edit",
        json={
            "kind": "rules",
            "name": "R1",
            "title": "取消の注意",
            "labels": "取消",
            "content": "取消後は元に戻せません。",
        },
    )
    assert rule.json()["data"]["rules"][0]["id"] == "R1"

    preview = client.post(f"{BASE}/preview", json={"question": "オーダーを取消したい"})
    data = preview.json()["data"]
    assert data["matched_terms"] == ["受注"]
    assert data["matched_rules"] == ["取消の注意"]
    assert "受注" in data["expanded_question"]

    duplicate = client.post(f"{BASE}/edit", json={"kind": "terms", "name": "受注"})
    assert duplicate.status_code == 422
    deleted = client.post(
        f"{BASE}/edit", json={"kind": "terms", "selected": "受注", "delete": True}
    )
    assert deleted.json()["data"]["terms"] == []


def test_search_context_carries_business_view_runtime_knowledge(monkeypatch: MonkeyPatch) -> None:
    payload = {"schema_version": 1, "terms": [{"term": "受注", "aliases": ["注文"]}], "rules": []}

    class KnowledgeViewOracle(search_tests.FakeViewOracle):
        async def get_business_view_knowledge(
            self, business_view_id: str, kind: str
        ) -> dict[str, object] | None:
            return payload if kind == "runtime_knowledge" else None

    config = BusinessViewConfig(
        knowledge_base_ids=["kb-1"], query=KnowledgeBaseQueryConfig(answer_engine="docrag")
    )
    search_tests._install(monkeypatch, {"bv-1": config})
    monkeypatch.setattr(
        search_route,
        "OracleClient",
        lambda *_args, **_kwargs: KnowledgeViewOracle({"bv-1": config}),
    )

    response = client.post("/api/search", json={"query": "注文の登録", "business_view_id": "bv-1"})

    assert response.status_code == 200
    settings = search_tests.RecordingPipeline.captured_settings
    assert settings is not None
    assert settings.rag_runtime_knowledge == payload
    assert settings.rag_answer_engine == "docrag"
