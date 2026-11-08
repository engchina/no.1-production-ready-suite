"""承認済み FAQ の対象のナレッジベース・根拠の状態・承認者（#4621）。

- 利用者が FAQ の対象の KB をすべて使えるときだけ、提示（suggest）とチャット（find）で使う。
- 使えない FAQ は候補から外し、チャットで ID を指定されても「無い」と同じ扱い（存在を示さない）。
- 引用した文書の削除・対象の KB のプロファイルからの除外で停止（要確認）、版の更新は印だけ。
- 既存の FAQ の対象は移行の時点のプロファイルの全 KB、承認者と時刻を保存のたびに記録する。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.api.routes import feedback as feedback_route
from app.api.routes import search_answer_profile_knowledge as knowledge_route
from app.clients.oracle import OracleClient
from app.config import Settings
from app.main import app
from app.rag.approved_faq_scope import (
    FaqSourceDocumentState,
    document_version,
    evaluate_approved_faq_scope,
)
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.rag.search_answer_profile_knowledge import (
    APPROVED_FAQ_KIND,
    find_approved_faq,
    suggest_approved_faq,
)
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient
from tests.test_feedback_promotion import FakePromotionClient, _detail
from tests.test_oracle_adapter import FakeOraclePool, _run_inline
from tests.test_search_answer_profile_domain_keywords import FakeKnowledgeOracle

client = AsgiTestClient(app)
BASE = "/api/search-answer-profiles/bv-1/approved-faq"


class StubGenAi:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def embed(self, texts: list[str], *, input_type: str = "") -> list[list[float]]:
        return [[1.0, 0.0] + [0.0] * 1534 for _ in texts]


@pytest.fixture
def fake_oracle(monkeypatch: pytest.MonkeyPatch) -> FakeKnowledgeOracle:
    fake = FakeKnowledgeOracle()
    fake.knowledge_base_ids = ["kb-1", "kb-2"]
    monkeypatch.setattr(knowledge_route, "OracleClient", lambda: fake)
    monkeypatch.setattr(knowledge_route, "OciGenAiClient", StubGenAi)
    return fake


@contextmanager
def _kbs(*knowledge_base_ids: str) -> Iterator[None]:
    """利用者が使える KB を決めた context（None 相当は system_access を使う）。"""
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="faq-scope-test", allowed_knowledge_base_ids=frozenset(knowledge_base_ids)
        )
    )
    try:
        yield
    finally:
        reset_audit_request_context(token)


def _record(
    faq_id: str,
    question: str,
    targets: list[str] | None,
    *,
    documents: list[tuple[str, str]] = (),  # type: ignore[assignment]
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": faq_id,
        "question": question,
        "approved_answer": f"{question} の回答",
        "status": "approved",
        "source_documents": [
            {"document_id": document_id, "version": version} for document_id, version in documents
        ],
    }
    if targets is not None:
        raw["target_knowledge_base_ids"] = targets
    return raw


def _store(fake: FakeKnowledgeOracle, *records: dict[str, Any]) -> None:
    fake.payloads[("bv-1", APPROVED_FAQ_KIND)] = {"schema_version": 1, "records": list(records)}


def _stored_records(fake: FakeKnowledgeOracle) -> list[dict[str, Any]]:
    payload = fake.payloads[("bv-1", APPROVED_FAQ_KIND)]
    records = payload["records"]
    assert isinstance(records, list)
    return records


# ---------------------------------------------------------------------------
# 権限の絞り込み（提示・チャット）と、存在を示さないこと
# ---------------------------------------------------------------------------


async def test_suggest_and_find_use_only_faqs_whose_target_kbs_are_all_permitted(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    _store(
        fake_oracle,
        _record("faq-a", "合成の設定 A はどこですか？", ["kb-1"]),
        _record("faq-b", "合成の設定 A はどこにありますか？", ["kb-2"]),
        _record("faq-ab", "合成の設定 A はどこで変えますか？", ["kb-1", "kb-2"]),
    )
    question = "合成の設定 A はどこですか？"

    with _kbs("kb-1"):
        only_a = await suggest_approved_faq(fake_oracle, "bv-1", question, min_score=0.0)
        assert [item.record.id for item in only_a] == ["faq-a"]
        # 選べない FAQ は「無い」と同じ（存在を示さない）。
        assert await find_approved_faq(fake_oracle, "bv-1", "faq-b") is None
        assert await find_approved_faq(fake_oracle, "bv-1", "faq-ab") is None
        assert await find_approved_faq(fake_oracle, "bv-1", "missing") is None
        found = await find_approved_faq(fake_oracle, "bv-1", "faq-a")
        assert found is not None and found.id == "faq-a"

    with _kbs("kb-1", "kb-2"):
        both = await suggest_approved_faq(fake_oracle, "bv-1", question, min_score=0.0)
        assert {item.record.id for item in both} == {"faq-a", "faq-b", "faq-ab"}
        assert await find_approved_faq(fake_oracle, "bv-1", "faq-ab") is not None

    with _kbs():
        assert await suggest_approved_faq(fake_oracle, "bv-1", question, min_score=0.0) == []


def test_suggest_api_never_returns_the_answer_of_a_non_permitted_faq(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(
        fake_oracle,
        _record("faq-a", "合成の設定 A はどこですか？", ["kb-1"]),
        _record("faq-b", "合成の設定 A はどこにありますか？", ["kb-2"]),
    )
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions(
        "kb1-user", ["menu.search"], search_answer_profile_ids=["bv-1"], knowledge_base_ids=["kb-1"]
    )
    response = client.post(
        f"{BASE}/suggest",
        json={"query": "合成の設定 A はどこにありますか？"},
        headers=login(client, "kb1-user"),
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]["suggestions"]] == ["faq-a"]
    assert "faq-b" not in response.text
    assert "どこにありますか？ の回答" not in response.text


# ---------------------------------------------------------------------------
# 対象のナレッジベースの既定・検証・移行
# ---------------------------------------------------------------------------


def test_manual_add_defaults_to_all_profile_kbs_and_validates_the_choice(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    added = client.post(BASE, json={"question": "合成の質問 1", "answer": "合成の回答 1"})
    assert added.status_code == 200, added.text
    record = added.json()["data"]["records"][0]
    assert record["target_knowledge_base_ids"] == ["kb-1", "kb-2"]
    assert record["review_reasons"] == []
    assert added.json()["data"]["knowledge_bases"] == [
        {"id": "kb-1", "name": "ナレッジベース kb-1", "in_profile": True},
        {"id": "kb-2", "name": "ナレッジベース kb-2", "in_profile": True},
    ]

    chosen = client.post(
        BASE,
        json={
            "question": "合成の質問 2",
            "answer": "合成の回答 2",
            "target_knowledge_base_ids": ["kb-2"],
        },
    )
    assert chosen.status_code == 200
    by_question = {item["question"]: item for item in chosen.json()["data"]["records"]}
    assert by_question["合成の質問 2"]["target_knowledge_base_ids"] == ["kb-2"]

    for targets in ([], ["kb-9"]):
        rejected = client.post(
            BASE,
            json={
                "question": "合成の質問 3",
                "answer": "合成の回答 3",
                "target_knowledge_base_ids": targets,
            },
        )
        assert rejected.status_code == 422, targets
    assert "合成の質問 3" not in {item["question"] for item in _stored_records(fake_oracle)}


def _excel(rows: list[tuple[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["QUESTION", "ANSWER"])
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_excel_import_uses_the_selected_kbs_or_all_profile_kbs(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    files = {"file": ("faq.xlsx", _excel([("合成の取込 1", "回答 1")]), "application/octet-stream")}
    default = client.post(f"{BASE}/import", files=files, data={"mode": "INSERT"})
    assert default.status_code == 200, default.text
    files = {"file": ("faq.xlsx", _excel([("合成の取込 2", "回答 2")]), "application/octet-stream")}
    chosen = client.post(
        f"{BASE}/import",
        files=files,
        data={"mode": "INSERT", "target_knowledge_base_ids": ["kb-1"]},
    )
    assert chosen.status_code == 200, chosen.text
    targets = {
        item["question"]: item["target_knowledge_base_ids"] for item in _stored_records(fake_oracle)
    }
    assert targets == {"合成の取込 1": ["kb-1", "kb-2"], "合成の取込 2": ["kb-1"]}


def test_existing_faqs_are_migrated_once_to_all_profile_kbs(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    _store(fake_oracle, _record("faq-legacy", "合成の既存の質問", None))

    listed = client.get(BASE)
    assert listed.status_code == 200
    assert listed.json()["data"]["records"][0]["target_knowledge_base_ids"] == ["kb-1", "kb-2"]
    # 移行は payload に書く（移行の時点の KB を残し、後でプロファイルを変えても変わらない）。
    assert _stored_records(fake_oracle)[0]["target_knowledge_base_ids"] == ["kb-1", "kb-2"]
    fake_oracle.knowledge_base_ids = ["kb-1", "kb-2", "kb-3"]
    again = client.get(BASE).json()["data"]["records"][0]
    assert again["target_knowledge_base_ids"] == ["kb-1", "kb-2"]
    assert again["review_reasons"] == []


# ---------------------------------------------------------------------------
# 根拠の変化（停止・原文の更新・確かめ直し）
# ---------------------------------------------------------------------------


def test_deleted_source_document_suspends_the_faq_until_reconfirmed(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    fake_oracle.documents["doc-1"] = FaqSourceDocumentState(
        version="v1", knowledge_base_ids=frozenset({"kb-1"})
    )
    _store(
        fake_oracle,
        _record("faq-a", "合成の設定 A はどこですか？", ["kb-1"], documents=[("doc-1", "v1")]),
    )
    query = {"query": "合成の設定 A はどこですか？"}
    assert client.get(BASE).json()["data"]["records"][0]["review_reasons"] == []
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"]

    del fake_oracle.documents["doc-1"]
    record = client.get(BASE).json()["data"]["records"][0]
    assert record["review_reasons"] == ["source_document_missing"]
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"] == []

    confirmed = client.post(f"{BASE}/faq-a/confirm", json={"target_knowledge_base_ids": ["kb-1"]})
    assert confirmed.status_code == 200, confirmed.text
    restored = confirmed.json()["data"]["records"][0]
    assert restored["review_reasons"] == []
    # 無くなった文書は根拠から外し、確かめ直した人と時刻を記録する。
    assert restored["source_document_ids"] == []
    assert restored["approved_at"]
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"]


def test_document_moved_out_of_the_target_kbs_also_suspends(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    fake_oracle.documents["doc-1"] = FaqSourceDocumentState(
        version="v1", knowledge_base_ids=frozenset({"kb-2"})
    )
    _store(fake_oracle, _record("faq-a", "合成の質問", ["kb-1"], documents=[("doc-1", "v1")]))
    record = client.get(BASE).json()["data"]["records"][0]
    assert record["review_reasons"] == ["source_document_missing"]


def test_target_kb_removed_from_profile_suspends_and_confirm_rescopes(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    _store(fake_oracle, _record("faq-a", "合成の設定 A はどこですか？", ["kb-1", "kb-2"]))
    fake_oracle.knowledge_base_ids = ["kb-1"]
    data = client.get(BASE).json()["data"]
    assert data["records"][0]["review_reasons"] == ["knowledge_base_removed"]
    # プロファイルから外れた KB も名前で出す（選択肢には出さない）。
    assert {"id": "kb-2", "name": "ナレッジベース kb-2", "in_profile": False} in data[
        "knowledge_bases"
    ]
    query = {"query": "合成の設定 A はどこですか？"}
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"] == []

    # 未指定はプロファイルの今の全 KB。
    confirmed = client.post(f"{BASE}/faq-a/confirm", json={})
    assert confirmed.status_code == 200
    record = confirmed.json()["data"]["records"][0]
    assert (record["target_knowledge_base_ids"], record["review_reasons"]) == (["kb-1"], [])
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"]


def test_confirm_rejects_unknown_faq_and_kbs_outside_the_profile(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    _store(fake_oracle, _record("faq-a", "合成の質問", ["kb-1"]))
    assert client.post(f"{BASE}/missing/confirm", json={}).status_code == 404
    rejected = client.post(f"{BASE}/faq-a/confirm", json={"target_knowledge_base_ids": ["kb-9"]})
    assert rejected.status_code == 422
    assert _stored_records(fake_oracle)[0]["target_knowledge_base_ids"] == ["kb-1"]


def test_updated_source_document_keeps_the_faq_in_use_and_flags_it(
    fake_oracle: FakeKnowledgeOracle,
) -> None:
    fake_oracle.documents["doc-1"] = FaqSourceDocumentState(
        version="v2", knowledge_base_ids=frozenset({"kb-1"})
    )
    _store(
        fake_oracle,
        _record("faq-a", "合成の設定 A はどこですか？", ["kb-1"], documents=[("doc-1", "v1")]),
    )
    record = client.get(BASE).json()["data"]["records"][0]
    assert (record["review_reasons"], record["source_updated"]) == ([], True)
    query = {"query": "合成の設定 A はどこですか？"}
    assert client.post(f"{BASE}/suggest", json=query).json()["data"]["suggestions"]

    # 確かめ直すと今の版を記録し、印が消える。
    confirmed = client.post(f"{BASE}/faq-a/confirm", json={"target_knowledge_base_ids": ["kb-1"]})
    assert confirmed.json()["data"]["records"][0]["source_updated"] is False
    assert _stored_records(fake_oracle)[0]["source_documents"] == [
        {"document_id": "doc-1", "version": "v2"}
    ]


def test_evaluate_scope_without_targets_is_never_usable() -> None:
    from rag_engine.knowledge.approved_faq import ApprovedFaqRecord

    record = ApprovedFaqRecord(id="faq", question="q", approved_answer="a", status="approved")
    state = evaluate_approved_faq_scope(record, [], {})
    assert state.review_reasons == ("no_target_knowledge_bases",)


def test_document_version_changes_with_served_version_and_superseding_document() -> None:
    first = datetime(2026, 1, 1, tzinfo=UTC)
    assert document_version(first, None) != document_version(datetime(2026, 1, 2, tzinfo=UTC), None)
    assert document_version(first, None) != document_version(first, "doc-new")
    assert document_version(first, None) == document_version(first, None)


# ---------------------------------------------------------------------------
# 承認者
# ---------------------------------------------------------------------------


def test_approver_and_time_are_recorded_and_shown(
    fake_oracle: FakeKnowledgeOracle, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions(
        "faq-admin",
        ["menu.search_answer_profiles"],
        search_answer_profile_ids=["bv-1"],
        knowledge_base_ids=["kb-1", "kb-2"],
    )
    headers = login(client, "faq-admin")
    added = client.post(
        BASE, json={"question": "合成の質問", "answer": "合成の回答"}, headers=headers
    )
    assert added.status_code == 200, added.text
    record = added.json()["data"]["records"][0]
    assert record["approved_by"] == "faq-admin"
    assert datetime.fromisoformat(record["approved_at"]).tzinfo is not None
    # 保存するのは利用者の UUID（表示名は一覧で引く）。
    approval = _stored_records(fake_oracle)[0]["approval"]
    assert approval["approved_by"] != "faq-admin" and approval["approved_by"]


# ---------------------------------------------------------------------------
# フィードバックからの昇格
# ---------------------------------------------------------------------------


def test_promotion_fills_target_kbs_and_documents_from_the_answer_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePromotionClient(detail=_detail())
    fake.documents = {
        "doc-1": FaqSourceDocumentState(version="v1", knowledge_base_ids=frozenset({"kb-2"})),
        # プロファイルに無い KB は対象にしない。
        "doc-2": FaqSourceDocumentState(
            version="v7", knowledge_base_ids=frozenset({"kb-2", "kb-9"})
        ),
    }
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    response = client.post("/api/feedback/feedback-1/approved-faq")

    assert response.status_code == 200, response.text
    records = fake.knowledge["bv-1"]["records"]
    assert isinstance(records, list)
    record = records[0]
    assert record["target_knowledge_base_ids"] == ["kb-2"]
    assert record["source_documents"] == [
        {"document_id": "doc-1", "version": "v1"},
        {"document_id": "doc-2", "version": "v7"},
    ]
    # 引用の chunk も出典に残る（以前は answer_trace に入らず空だった）。
    assert record["source"]["evidence_item_ids"] == ["doc-1:c1", "doc-1:c2", "doc-2:c1"]
    assert record["approval"]["approved_at"]


def test_promotion_without_citations_targets_all_profile_kbs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePromotionClient(detail=_detail(citations=[]))
    monkeypatch.setattr(feedback_route, "OracleClient", lambda: fake)

    assert client.post("/api/feedback/feedback-1/approved-faq").status_code == 200
    records = fake.knowledge["bv-1"]["records"]
    assert isinstance(records, list)
    assert records[0]["target_knowledge_base_ids"] == ["kb-1", "kb-2", "kb-3"]
    assert records[0]["source_documents"] == []


# ---------------------------------------------------------------------------
# 文書の状態の SQL
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_source_document_states_read_visible_documents_and_their_kbs() -> None:
    indexed_at = datetime(2026, 1, 1, tzinfo=UTC)
    pool = FakeOraclePool(
        execute_results=[
            [{"document_id": "doc-1", "indexed_at": indexed_at, "superseded_by_document_id": None}],
            [{"document_id": "doc-1", "knowledge_base_id": "kb-1", "name": "ナレッジベース A"}],
        ]
    )
    oracle = OracleClient(
        settings=Settings.model_construct(), pool=pool, db_call_runner=_run_inline
    )
    with _kbs("kb-1"):
        states = await oracle.approved_faq_source_document_states(["doc-1", "doc-missing"])

    assert states == {
        "doc-1": FaqSourceDocumentState(
            version=document_version(indexed_at, None), knowledge_base_ids=frozenset({"kb-1"})
        )
    }
    documents_call, kb_call = pool.connection.calls
    assert "d.document_id IN (:document_id_0, :document_id_1)" in documents_call.statement
    # 利用者から見える文書・KB だけを読む。
    assert ":access_knowledge_base_id_0" in documents_call.statement
    assert ":access_knowledge_base_id_0" in kb_call.statement
