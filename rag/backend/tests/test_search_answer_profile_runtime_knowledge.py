"""検索・回答プロファイル単位の用語・ルール(runtime knowledge)API と検索時の反映。"""

import pytest
from pytest import MonkeyPatch

from app.api.routes import search as search_route
from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.main import app
from app.rag.search_answer_profile_config import SearchAnswerProfileConfig
from tests import test_search_search_answer_profile as search_tests
from tests.support import AsgiTestClient
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/search-answer-profiles/bv-1/runtime-knowledge"


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    return fake


def test_edit_terms_rules_and_preview(fake_oracle: FakeKnowledgeOracle) -> None:
    assert client.get(BASE).json()["data"] == {
        "search_answer_profile_id": "bv-1",
        "terms": [],
        "rules": [],
    }

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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"kind": "terms", "name": "  "}, "用語を入力してください。"),
        (
            {"kind": "rules", "name": "", "title": "t", "content": "c"},
            "ルール ID を入力してください。",
        ),
        (
            {"kind": "rules", "name": "R1", "title": "", "content": "c"},
            "ルール名を入力してください。",
        ),
        (
            {"kind": "rules", "name": "R1", "title": "t", "content": " "},
            "ルール内容を入力してください。",
        ),
    ],
    ids=["term", "rule-id", "rule-title", "rule-content"],
)
def test_edit_rejects_empty_required_fields_like_the_screen(
    fake_oracle: FakeKnowledgeOracle, payload: dict[str, str], message: str
) -> None:
    """用語・ルールの必須は backend が正本（#540）。画面と同じ文言の 422 を返し、保存しない。"""
    response = client.post(f"{BASE}/edit", json=payload)
    assert response.status_code == 422
    assert response.json()["error_messages"] == [message]
    assert client.get(BASE).json()["data"]["terms"] == []
    assert client.get(BASE).json()["data"]["rules"] == []


def test_search_context_carries_search_answer_profile_runtime_knowledge(
    monkeypatch: MonkeyPatch,
) -> None:
    payload = {"schema_version": 1, "terms": [{"term": "受注", "aliases": ["注文"]}], "rules": []}

    class KnowledgeViewOracle(search_tests.FakeViewOracle):
        async def get_search_answer_profile_knowledge(
            self, search_answer_profile_id: str, kind: str
        ) -> dict[str, object] | None:
            return payload if kind == "runtime_knowledge" else None

    config = SearchAnswerProfileConfig(knowledge_base_ids=["kb-1"])
    search_tests._install(monkeypatch, {"bv-1": config})
    monkeypatch.setattr(
        search_route,
        "OracleClient",
        lambda *_args, **_kwargs: KnowledgeViewOracle({"bv-1": config}),
    )

    response = client.post(
        "/api/search", json={"query": "注文の登録", "search_answer_profile_id": "bv-1"}
    )

    assert response.status_code == 200
    settings = search_tests.RecordingPipeline.captured_settings
    assert settings is not None
    assert settings.rag_runtime_knowledge == payload
