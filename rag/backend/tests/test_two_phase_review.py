"""2 段階ファイル処理(parse → 人がプレビュー確認 → index)の API テスト。"""

import asyncio
from typing import Any, cast

import pytest
from pytest import MonkeyPatch

from app.api.routes import documents as documents_route
from app.clients.oracle import reset_local_store
from app.config import get_settings
from app.main import app
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

# 実 Oracle 26ai + OCI を用いる統合テスト（DB 未到達環境では自動 skip）。
pytestmark = pytest.mark.usefixtures("oracle_db")


def setup_function() -> None:
    """テストごとにローカル Oracle ストアを初期化する。"""
    reset_local_store()


def _enable_review_gate(monkeypatch: MonkeyPatch) -> None:
    """REVIEW ゲート(2 段階処理)を有効化する。"""
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", True)


def _upload_sample(text: str = "社内規程: 経費申請\n部門長の承認後、経理部が確認します。") -> str:
    upload_resp = client.post(
        "/api/documents/upload",
        files={"file": ("two-phase-policy.txt", text.encode(), "text/plain")},
    )
    assert upload_resp.status_code == 200
    return cast(str, upload_resp.json()["data"]["id"])


def _run_job(job_id: str) -> None:
    asyncio.run(documents_route._run_ingestion_job(job_id))


def _enqueue_extract(document_id: str) -> dict[str, Any]:
    response = client.post(f"/api/documents/{document_id}/ingest")
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["data"])


def _extract_to_review(document_id: str) -> None:
    """EXTRACT フェーズを走らせ、REVIEW で停止させる。"""
    job = _enqueue_extract(document_id)
    assert job["phase"] == "EXTRACT"
    _run_job(cast(str, job["id"]))


def _get_document(document_id: str) -> dict[str, Any]:
    response = client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["data"])


def _search(query: str) -> dict[str, Any]:
    response = client.post(
        "/api/search",
        json={"query": query, "top_k": 5, "rerank_top_n": 3},
    )
    assert response.status_code == 200
    return cast(dict[str, Any], response.json()["data"])


def test_review_gate_stops_at_review_and_excludes_from_search(monkeypatch: MonkeyPatch) -> None:
    """EXTRACT 後は REVIEW で停止し、抽出は保持されるが検索対象外。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()

    _extract_to_review(document_id)

    detail = _get_document(document_id)
    assert detail["status"] == "REVIEW"
    # 抽出本文はプレビュー用に保持される。
    assert detail["extraction"]["raw_text"]
    # まだ索引していないので chunk は無い。
    chunks_resp = client.get(f"/api/documents/{document_id}/chunks")
    assert chunks_resp.status_code == 200
    assert chunks_resp.json()["data"] == []
    # REVIEW 文書は検索対象に入らない。
    search = _search("経費申請の承認者は？")
    assert all(
        citation["document_id"] != document_id for citation in search["citations"]
    )


def test_approve_indexes_and_makes_searchable(monkeypatch: MonkeyPatch) -> None:
    """承認すると INDEX フェーズが走り、INDEXED・検索可能になる。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    index_job = approve_resp.json()["data"]
    assert index_job["phase"] == "INDEX"
    assert index_job["status"] == "QUEUED"

    _run_job(cast(str, index_job["id"]))

    detail = _get_document(document_id)
    assert detail["status"] == "INDEXED"
    chunks_resp = client.get(f"/api/documents/{document_id}/chunks")
    assert chunks_resp.json()["data"]

    search = _search("経費申請の承認者は？")
    assert any(
        citation["document_id"] == document_id for citation in search["citations"]
    )


def test_reject_returns_document_to_uploaded(monkeypatch: MonkeyPatch) -> None:
    """却下すると UPLOADED へ戻り、検索対象に入らない。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    reject_resp = client.post(f"/api/documents/{document_id}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["data"]["status"] == "UPLOADED"

    search = _search("経費申請の承認者は？")
    assert all(
        citation["document_id"] != document_id for citation in search["citations"]
    )


def test_approve_requires_review_status(monkeypatch: MonkeyPatch) -> None:
    """REVIEW でない文書の承認は 409。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()

    # まだ UPLOADED。
    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 409


def test_reject_requires_review_status(monkeypatch: MonkeyPatch) -> None:
    """REVIEW でない文書の却下は 409。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()

    reject_resp = client.post(f"/api/documents/{document_id}/reject")
    assert reject_resp.status_code == 409


def test_double_approve_after_index_conflicts(monkeypatch: MonkeyPatch) -> None:
    """INDEXED 済み文書の再承認は 409。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    second = client.post(f"/api/documents/{document_id}/approve")
    assert second.status_code == 409


def test_approve_with_text_edits_indexes_edited_content(monkeypatch: MonkeyPatch) -> None:
    """承認時の人手テキスト修正が抽出へ反映され、検索対象になる。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    detail = _get_document(document_id)
    elements = detail["extraction"]["elements"]
    target = next(el for el in elements if el.get("element_id"))
    edited_text = "編集後マーカー ZZZ 経費の最終承認は役員会です。"

    approve_resp = client.post(
        f"/api/documents/{document_id}/approve",
        json={
            "element_edits": [{"element_id": target["element_id"], "text": edited_text}],
        },
    )
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))

    indexed = _get_document(document_id)
    assert indexed["status"] == "INDEXED"
    edited_element = next(
        el
        for el in indexed["extraction"]["elements"]
        if el.get("element_id") == target["element_id"]
    )
    assert edited_element["text"] == edited_text

    search = _search("役員会 ZZZ")
    assert any(citation["document_id"] == document_id for citation in search["citations"])


def test_approve_with_unknown_element_id_is_rejected(monkeypatch: MonkeyPatch) -> None:
    """存在しない要素 ID の修正は 400。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    approve_resp = client.post(
        f"/api/documents/{document_id}/approve",
        json={"element_edits": [{"element_id": "does-not-exist", "text": "x"}]},
    )
    assert approve_resp.status_code == 400


def test_gate_disabled_keeps_single_pass_indexing(monkeypatch: MonkeyPatch) -> None:
    """既定(gate-off)では従来どおり 1 ジョブで INDEXED まで進む。"""
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", False)
    document_id = _upload_sample()

    job = _enqueue_extract(document_id)
    assert job["phase"] == "EXTRACT"
    _run_job(cast(str, job["id"]))

    assert _get_document(document_id)["status"] == "INDEXED"
