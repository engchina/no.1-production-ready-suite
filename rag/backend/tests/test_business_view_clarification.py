"""業務ビューのルールの確認の質問(チャットの確認。#717)。"""

import json
from typing import Any

import pytest

from app.api.routes import business_view_knowledge as knowledge_route
from app.main import app
from app.rag.business_view_knowledge import resolve_clarification
from app.schemas.business_view_knowledge import ClarificationAnswer
from tests.support import AsgiTestClient
from tests.test_business_view_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/business-views/bv-1"

CLARIFICATION: dict[str, Any] = {
    "question": "どの規程についてのご質問ですか？",
    "multiple": True,
    "allow_other": True,
    "options": [
        {
            "id": "travel",
            "label": "出張旅費",
            "search_terms": ["出張", "日当"],
            "premise": "利用者は出張旅費規程について質問している",
            "sections": [
                {
                    "document_id": "doc-travel",
                    "document_name": "出張旅費規程.pdf",
                    "section_id": "sec-6",
                    "title": "第6条 申請と精算",
                    "page_start": 2,
                    "page_end": 3,
                }
            ],
        },
        {"id": "expense", "label": "経費精算", "search_terms": ["精算マニュアル"]},
    ],
}


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    client.post(
        f"{BASE}/runtime-knowledge/edit",
        json={
            "kind": "rules",
            "name": "R01",
            "title": "期限の確認",
            "labels": "期限\n締め切り",
            "content": "期限は規程ごとに違う。",
        },
    )
    return fake


def test_clarification_is_saved_on_the_rule_and_suggested_for_matching_questions(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    saved = client.put(
        f"{BASE}/runtime-knowledge/rules/R01/clarification", json={"clarification": CLARIFICATION}
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["rules"][0]["clarification"]["question"] == (
        "どの規程についてのご質問ですか？"
    )

    suggestion = client.post(
        f"{BASE}/clarifications/suggest", json={"query": "申請の期限はいつですか"}
    ).json()["data"]["suggestion"]
    assert suggestion["rule_id"] == "R01"
    assert [option["id"] for option in suggestion["clarification"]["options"]] == [
        "travel",
        "expense",
    ]

    # 質問で既に答えている(選択肢の名前・検索に足す語が入っている)ときは聞き返さない。
    answered = client.post(
        f"{BASE}/clarifications/suggest", json={"query": "出張の申請の期限はいつですか"}
    ).json()["data"]
    assert answered["suggestion"] is None
    # ルールに一致しない質問にも出さない。
    unrelated = client.post(f"{BASE}/clarifications/suggest", json={"query": "日当はいくら"})
    assert unrelated.json()["data"]["suggestion"] is None

    # 外すと出さない。
    client.put(f"{BASE}/runtime-knowledge/rules/R01/clarification", json={"clarification": None})
    removed = client.post(f"{BASE}/clarifications/suggest", json={"query": "申請の期限は"})
    assert removed.json()["data"]["suggestion"] is None


def test_clarification_validation(fake_oracle: FakeKnowledgeOracle) -> None:
    url = f"{BASE}/runtime-knowledge/rules/R01/clarification"
    one_option = {**CLARIFICATION, "options": CLARIFICATION["options"][:1]}
    assert client.put(url, json={"clarification": one_option}).status_code == 422
    duplicate = {**CLARIFICATION, "options": [CLARIFICATION["options"][0]] * 2}
    assert client.put(url, json={"clarification": duplicate}).status_code == 422
    missing = client.put(
        f"{BASE}/runtime-knowledge/rules/R99/clarification", json={"clarification": CLARIFICATION}
    )
    assert missing.status_code == 404


def _payload() -> dict[str, Any]:
    return {"rules": [{"id": "R01", "title": "期限の確認", "clarification": CLARIFICATION}]}


def test_resolve_clarification_builds_the_scope_and_page_ranges() -> None:
    resolved = resolve_clarification(
        _payload(),
        ClarificationAnswer(rule_id="R01", option_ids=["travel"], other_text="海外出張"),
    )
    assert resolved is not None
    scope, page_ranges = resolved
    assert json.loads(page_ranges) == [
        {"document_id": "doc-travel", "page_start": 2, "page_end": 3}
    ]
    assert scope.search_terms == ("出張", "日当")
    assert scope.label == "「出張旅費規程.pdf」の「第6条 申請と精算」（p.2–3）"
    assert "選んだ答え: 出張旅費" in scope.context
    assert "前提: 利用者は出張旅費規程について質問している" in scope.context
    assert "その他（利用者の入力）: 海外出張" in scope.context

    # 章節の無い選択肢は範囲を絞らない。
    expense = resolve_clarification(
        _payload(), ClarificationAnswer(rule_id="R01", option_ids=["expense"])
    )
    assert expense is not None and expense[1] == ""


@pytest.mark.parametrize(
    "answer",
    [
        ClarificationAnswer(rule_id="R99", option_ids=["travel"]),
        ClarificationAnswer(rule_id="R01", option_ids=["unknown"]),
    ],
    ids=["unknown-rule", "unknown-option"],
)
def test_resolve_clarification_rejects_unknown_rules_and_options(
    answer: ClarificationAnswer,
) -> None:
    assert resolve_clarification(_payload(), answer) is None


def test_single_choice_rejects_multiple_options() -> None:
    payload = {"rules": [{"id": "R01", "clarification": {**CLARIFICATION, "multiple": False}}]}
    answer = ClarificationAnswer(rule_id="R01", option_ids=["travel", "expense"])
    assert resolve_clarification(payload, answer) is None
