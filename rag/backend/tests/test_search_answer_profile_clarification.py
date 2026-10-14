"""検索・回答プロファイルのルールの確認の質問(チャットの確認。#717)。"""

import json
from typing import Any

import pytest

from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.main import app
from app.rag.search_answer_profile_knowledge import (
    clarification_record,
    resolve_clarification,
)
from app.schemas.search_answer_profile_knowledge import ClarificationAnswer
from tests.support import AsgiTestClient
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/search-answer-profiles/bv-1"

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


def test_assume_policy_does_not_suggest_clarifications(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """方針が「前提を示して回答する」なら、ルールに当たっても確認を出さない（#1839）。"""
    from app.rag.search_answer_profile_config import SearchAnswerProfileConfig

    client.put(
        f"{BASE}/runtime-knowledge/rules/R01/clarification", json={"clarification": CLARIFICATION}
    )
    original = fake_oracle.get_search_answer_profile

    async def assume(search_answer_profile_id: str) -> Any:
        view = await original(search_answer_profile_id)
        assert view is not None
        return view.model_copy(
            update={
                "config": SearchAnswerProfileConfig(
                    knowledge_base_ids=["kb-1"], clarification_policy="assume"
                )
            }
        )

    monkeypatch.setattr(fake_oracle, "get_search_answer_profile", assume)
    data = client.post(
        f"{BASE}/clarifications/suggest", json={"query": "申請の期限はいつですか"}
    ).json()["data"]
    assert data["suggestion"] is None


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

    # 確認を保存した後に章節のページが変わったら、今のページで絞る(#721)。
    moved = resolve_clarification(
        _payload(),
        ClarificationAnswer(rule_id="R01", option_ids=["travel"]),
        {("doc-travel", "sec-6"): (4, 5), ("doc-travel", "other"): (9, 9)},
    )
    assert moved is not None
    assert json.loads(moved[1]) == [{"document_id": "doc-travel", "page_start": 4, "page_end": 5}]
    assert moved[0].label.endswith("（p.4–5）")

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


def test_other_text_is_used_even_when_the_rule_saved_allow_other_false() -> None:
    """「その他（自由入力）」は常に出す（#1839）。古い保存の allow_other=False でも受け付ける。"""
    payload = {"rules": [{"id": "R01", "clarification": {**CLARIFICATION, "allow_other": False}}]}
    resolved = resolve_clarification(
        payload, ClarificationAnswer(rule_id="R01", other_text="海外出張の日当")
    )
    assert resolved is not None
    assert "その他（利用者の入力）: 海外出張の日当" in resolved[0].context


def test_clarification_record_for_the_conversation() -> None:
    """会話に残す「回答前に確認した内容」（#1839）。名前は保存済みのルールから引き直す。"""
    answer = ClarificationAnswer(rule_id="R01", option_ids=["travel"], other_text=" 海外 ")
    assert clarification_record(_payload(), "R01", answer) == {
        "outcome": "answered",
        "entries": [
            {
                "prompt": CLARIFICATION["question"],
                "selected": ["出張旅費"],
                "other_text": "海外",
            }
        ],
    }
    assert clarification_record(_payload(), "R01", None) == {
        "outcome": "skipped",
        "entries": [{"prompt": CLARIFICATION["question"]}],
    }
    assert clarification_record(_payload(), "R99", None) is None


def test_single_choice_rejects_multiple_options() -> None:
    payload = {"rules": [{"id": "R01", "clarification": {**CLARIFICATION, "multiple": False}}]}
    answer = ClarificationAnswer(rule_id="R01", option_ids=["travel", "expense"])
    assert resolve_clarification(payload, answer) is None
