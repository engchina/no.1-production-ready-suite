"""2 段階ファイル処理(parse → 人がプレビュー確認 → index)の API テスト。"""

import asyncio
from typing import Any, cast

import pytest
from pytest import MonkeyPatch

from app.api.routes import documents as documents_route
from app.clients.oracle import OracleClient, reset_local_store
from app.config import get_settings
from app.main import app
from app.rag import ingestion as ingestion_module
from app.rag.audit import record_rag_ingestion_audit
from app.rag.variant_planner import plan_document_materializations
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
    assert all(citation["document_id"] != document_id for citation in search["citations"])


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
    assert any(citation["document_id"] == document_id for citation in search["citations"])


def test_approve_records_chunk_set_and_kb_binding(monkeypatch: MonkeyPatch) -> None:
    """索引後に reconcile が chunk_set を記録し所属 KB を binding する(planner 駆動の基盤)。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    oracle = OracleClient()
    chunk_set_ids = asyncio.run(oracle.list_document_chunk_set_ids(document_id))
    # 単一 materialization なので chunk_set は 1 つ。
    assert len(chunk_set_ids) == 1

    chunk_set = asyncio.run(oracle.get_chunk_set(chunk_set_ids[0]))
    assert chunk_set is not None
    assert chunk_set["status"] == "INDEXED"
    # chunk がタグ付け・計数されている。
    assert chunk_set["chunk_count"]

    # 既定ナレッジベースがこの chunk_set を参照(refcount=1)。
    assert asyncio.run(oracle.chunk_set_refcount(chunk_set_ids[0])) == 1


def test_publish_binding_failure_marks_document_error(monkeypatch: MonkeyPatch) -> None:
    """chunk/vector 保存後でも KB binding に失敗したら INDEXED 成功扱いにしない。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    async def _fail_binding(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("binding failed")

    monkeypatch.setattr(OracleClient, "upsert_chunk_set_binding", _fail_binding)

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    index_job = approve_resp.json()["data"]
    _run_job(cast(str, index_job["id"]))

    job_resp = client.get(f"/api/documents/ingestion-jobs/{index_job['id']}")
    assert job_resp.status_code == 200
    job = job_resp.json()["data"]
    assert job["status"] == "FAILED"
    assert "公開設定" in job["error_message"]

    detail = _get_document(document_id)
    assert detail["status"] == "ERROR"
    assert "公開設定" in detail["error_message"]
    search = _search("経費申請の承認者は？")
    assert all(citation["document_id"] != document_id for citation in search["citations"])


def test_kb_scoped_search_finds_serving_chunk_set(monkeypatch: MonkeyPatch) -> None:
    """KB スコープ検索でも、配信中 chunk_set はフィルタで除外されない(回帰なし)。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)
    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))

    detail = _get_document(document_id)
    assert detail["status"] == "INDEXED"
    knowledge_base_id = detail["knowledge_bases"][0]["id"]

    response = client.post(
        "/api/search",
        json={
            "query": "経費申請の承認者は？",
            "knowledge_base_ids": [knowledge_base_id],
            "top_k": 5,
            "rerank_top_n": 3,
        },
    )
    assert response.status_code == 200
    citations = response.json()["data"]["citations"]
    assert any(citation["document_id"] == document_id for citation in citations)


def test_plan_yields_the_single_materialized_chunk_set(monkeypatch: MonkeyPatch) -> None:
    """plan_document_materializations が、実際に materialize した単一 chunk_set と一致する。

    Stage 1: per-recipe ループの入力(KB configs → plan)が live の materialization と一致する
    ことを保証する(同一設定なら 1 chunk_set)。取込挙動は変えない。
    """
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)
    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))

    detail = _get_document(document_id)
    assert detail["status"] == "INDEXED"

    oracle = OracleClient()
    materialized = asyncio.run(oracle.list_document_chunk_set_ids(document_id))
    assert len(materialized) == 1

    configs = dict(asyncio.run(oracle.list_document_knowledge_base_configs(document_id)))
    plan = plan_document_materializations(
        cast(str, detail["content_sha256"]), get_settings(), configs
    )
    # plan が出す chunk_set 集合 == 実際に materialize した chunk_set 集合(単一・一致)。
    assert set(plan.chunk_sets) == set(materialized)


def test_multiple_kb_configs_materialize_separate_chunk_sets(monkeypatch: MonkeyPatch) -> None:
    """取込設定が分岐する 2 KB に属する文書は、2 つの chunk_set を共存 materialize する。

    Stage 2: per-recipe ループ。default(chunk_size=800)と別 KB(chunk_size=3500)で別 chunk_set
    になり、各 KB が自分の chunk_set を refcount=1 で参照する。
    """
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    # 別 chunk_size の 2 つ目の KB を作り、文書を両方に所属させる。
    kb_resp = client.post(
        "/api/knowledge-bases",
        json={"name": "高chunk-KB", "adapter_config": {"ingestion": {"chunk_size": 3500}}},
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assign_resp = client.post(
        f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
    )
    assert assign_resp.status_code == 200

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 200
    _run_job(cast(str, approve_resp.json()["data"]["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    oracle = OracleClient()
    materialized = asyncio.run(oracle.list_document_chunk_set_ids(document_id))
    # 2 つの取込設定 → 2 chunk_set が共存。
    assert len(materialized) == 2
    detail = _get_document(document_id)
    configs = dict(asyncio.run(oracle.list_document_knowledge_base_configs(document_id)))
    plan = plan_document_materializations(
        cast(str, detail["content_sha256"]), get_settings(), configs
    )
    # chunking だけの差分なので extraction は 1 つを共有する。
    assert len(plan.extraction_recipes) == 1
    # 各 chunk_set はちょうど 1 KB に bind(materialization が KB ごとに分裂)。
    for chunk_set_id in materialized:
        assert asyncio.run(oracle.chunk_set_refcount(chunk_set_id)) == 1


def test_approve_rejects_review_only_extraction_recipe_split(
    monkeypatch: MonkeyPatch,
) -> None:
    """REVIEW 後に前処理/Parser が分岐した場合、保存済み抽出の静かな再利用は 409 で止める。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    kb_resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "正規化KB",
            "adapter_config": {"ingestion": {"preprocess_profile": "text_normalize"}},
        },
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assert (
        client.post(
            f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
        ).status_code
        == 200
    )

    approve_resp = client.post(f"/api/documents/{document_id}/approve")
    assert approve_resp.status_code == 409
    assert "再取込" in approve_resp.json()["error_messages"][0]


def test_reject_returns_document_to_uploaded(monkeypatch: MonkeyPatch) -> None:
    """却下すると UPLOADED へ戻り、検索対象に入らない。"""
    _enable_review_gate(monkeypatch)
    document_id = _upload_sample()
    _extract_to_review(document_id)

    reject_resp = client.post(f"/api/documents/{document_id}/reject")
    assert reject_resp.status_code == 200
    assert reject_resp.json()["data"]["status"] == "UPLOADED"

    search = _search("経費申請の承認者は？")
    assert all(citation["document_id"] != document_id for citation in search["citations"])


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


def test_gate_disabled_multiple_kb_configs_materialize_separate_chunk_sets(
    monkeypatch: MonkeyPatch,
) -> None:
    """gate-off(ingest 経路)でも、取込設定が分岐する 2 KB は 2 chunk_set を共存 materialize する。

    _ingest_existing_document の plan 駆動化(先頭 chunk_set=ingest で抽出保存、残りは
    index_reviewed で抽出再利用)を検証。INDEX 経路の同名テストと対になる。
    """
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", False)
    document_id = _upload_sample()

    # default(chunk_size=800)に加え、別 chunk_size の 2 つ目の KB を作り両方へ所属させる。
    kb_resp = client.post(
        "/api/knowledge-bases",
        json={"name": "高chunk-KB-ingest", "adapter_config": {"ingestion": {"chunk_size": 3500}}},
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assign_resp = client.post(
        f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
    )
    assert assign_resp.status_code == 200

    job = _enqueue_extract(document_id)
    _run_job(cast(str, job["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    oracle = OracleClient()
    materialized = asyncio.run(oracle.list_document_chunk_set_ids(document_id))
    # 2 つの取込設定 → 2 chunk_set が共存。
    assert len(materialized) == 2
    # 各 chunk_set はちょうど 1 KB に bind(materialization が KB ごとに分裂)。
    for chunk_set_id in materialized:
        assert asyncio.run(oracle.chunk_set_refcount(chunk_set_id)) == 1


def test_gate_disabled_extraction_recipe_split_reextracts_from_source(
    monkeypatch: MonkeyPatch,
) -> None:
    """原文 bytes がある取込経路では、前処理差分ごとに extract+index を実行する。"""
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", False)
    document_id = _upload_sample()

    kb_resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "正規化KB-ingest",
            "adapter_config": {"ingestion": {"preprocess_profile": "text_normalize"}},
        },
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assert (
        client.post(
            f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
        ).status_code
        == 200
    )

    job = _enqueue_extract(document_id)
    _run_job(cast(str, job["id"]))
    detail = _get_document(document_id)
    assert detail["status"] == "INDEXED"

    oracle = OracleClient()
    materialized = asyncio.run(oracle.list_document_chunk_set_ids(document_id))
    assert len(materialized) == 2
    configs = dict(asyncio.run(oracle.list_document_knowledge_base_configs(document_id)))
    plan = plan_document_materializations(
        cast(str, detail["content_sha256"]), get_settings(), configs
    )
    assert len(plan.extraction_recipes) == 2
    assert len(plan.chunk_sets_by_extraction_recipe()) == 2


def test_multi_chunk_set_records_single_success_audit(monkeypatch: MonkeyPatch) -> None:
    """複数 chunk_set を materialize しても成功 audit は 1 回(1 文書 1 論理取込に集約)。

    record_outcome=最後のみ により、N chunk_set でも成功 audit/metric が N→1 になることを検証。
    """
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", False)

    success_audit_docs: list[str] = []

    def _spy(**kwargs: Any) -> None:
        if kwargs.get("outcome") == "success":
            success_audit_docs.append(cast(str, kwargs.get("document_id")))
        record_rag_ingestion_audit(**kwargs)

    monkeypatch.setattr(ingestion_module, "record_rag_ingestion_audit", _spy)

    document_id = _upload_sample()
    kb_resp = client.post(
        "/api/knowledge-bases",
        json={"name": "高chunk-KB-audit", "adapter_config": {"ingestion": {"chunk_size": 3500}}},
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assign_resp = client.post(
        f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
    )
    assert assign_resp.status_code == 200

    job = _enqueue_extract(document_id)
    _run_job(cast(str, job["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    oracle = OracleClient()
    # 2 chunk_set が materialize されている。
    assert len(asyncio.run(oracle.list_document_chunk_set_ids(document_id))) == 2
    # それでも成功 audit はこの文書につき 1 回だけ(複数化前は chunk_set 数だけ出ていた)。
    assert success_audit_docs.count(document_id) == 1


def test_document_chunk_sets_endpoint_lists_variants(monkeypatch: MonkeyPatch) -> None:
    """/chunk-sets が文書の複数 chunk_set(variant)を状態/件数/配信 KB つきで返す。"""
    monkeypatch.setattr(get_settings(), "rag_review_gate_enabled", False)
    document_id = _upload_sample(
        "# 第1章 概要\n\n"
        "社内規程の概要を説明します。\n\n"
        "## 1.1 経費申請\n\n"
        "部門長の承認後、経理部が確認します。\n"
    )
    kb_resp = client.post(
        "/api/knowledge-bases",
        json={
            "name": "高chunk-KB-csapi",
            "adapter_config": {
                "ingestion": {
                    "chunk_size": 3500,
                    "graph_profile": "entities",
                    "field_extraction_enabled": True,
                    "navigation_summary_enabled": True,
                }
            },
        },
    )
    assert kb_resp.status_code == 200
    kb_b = kb_resp.json()["data"]["id"]
    assert (
        client.post(
            f"/api/knowledge-bases/{kb_b}/documents", json={"document_ids": [document_id]}
        ).status_code
        == 200
    )

    job = _enqueue_extract(document_id)
    _run_job(cast(str, job["id"]))
    assert _get_document(document_id)["status"] == "INDEXED"

    resp = client.get(f"/api/documents/{document_id}/chunk-sets")
    assert resp.status_code == 200
    chunk_sets = cast(list[dict[str, Any]], resp.json()["data"])
    # default + 高chunk = 2 variant。
    assert len(chunk_sets) == 2
    for chunk_set in chunk_sets:
        assert chunk_set["status"] == "INDEXED"
        assert chunk_set["extraction_recipe_id"].startswith("er_")
        assert chunk_set["extraction_status"] == "materialized"
        assert chunk_set["chunk_count"] > 0
        # 各 variant はいずれかの KB に配信 binding される。
        assert chunk_set["serving_knowledge_base_ids"]
        assert set(chunk_set["layer_statuses"]) == {"metadata", "graph", "navigation"}
    # 全 binding KB の和集合に追加した kb_b が含まれる。
    all_kbs = {kb for chunk_set in chunk_sets for kb in chunk_set["knowledge_base_ids"]}
    assert kb_b in all_kbs
    planned_layers = [
        (name, status)
        for chunk_set in chunk_sets
        for name, status in chunk_set["layer_statuses"].items()
        if status["requested"]
    ]
    assert planned_layers
    assert all(status["layer_id"] for _name, status in planned_layers)
    assert any(
        name == "navigation" and status["status"] == "materialized"
        for name, status in planned_layers
    )
    assert any(
        name == "graph" and status["status"] == "planned_only"
        for name, status in planned_layers
    )

    metadata_owner = next(
        chunk_set
        for chunk_set in chunk_sets
        if chunk_set["layer_statuses"]["metadata"]["requested"]
    )
    metadata_status = metadata_owner["layer_statuses"]["metadata"]
    oracle = OracleClient()
    asyncio.run(
        oracle.upsert_artifact_layer(
            layer_id=metadata_status["layer_id"],
            layer_kind="metadata",
            parent_chunk_set_id=metadata_owner["chunk_set_id"],
            document_id=document_id,
            requested=True,
            status="materialized",
            reason="項目抽出は保存済み抽出 artifact から実体化済みです。",
        )
    )
    materialized_resp = client.get(f"/api/documents/{document_id}/chunk-sets")
    assert materialized_resp.status_code == 200
    materialized_sets = cast(list[dict[str, Any]], materialized_resp.json()["data"])
    assert any(
        chunk_set["layer_statuses"]["metadata"]["status"] == "materialized"
        for chunk_set in materialized_sets
    )
