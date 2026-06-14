"""Oracle schema artifact CLI のテスト。"""

import hashlib
import json
from pathlib import Path

from pytest import CaptureFixture

from app.rag import oracle_schema


def test_oracle_schema_sql_contains_required_rag_tables() -> None:
    """RAG 本番運用に必要な Oracle table / vector 契約を artifact に含める。"""
    sql = oracle_schema.oracle_schema_sql()

    assert "-- section: documents" in sql
    assert "CREATE TABLE rag_documents" in sql
    assert "CREATE TABLE rag_chunks" in sql
    assert "embedding       VECTOR(1536, FLOAT32)" in sql
    assert "CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx" in sql
    assert "TYPE HNSW" in sql
    assert "WITH TARGET ACCURACY 95" in sql
    assert "CREATE TABLE rag_search_audit" in sql
    assert "CREATE TABLE rag_ingestion_audit" in sql
    assert "SELECT AI" not in sql.upper()


def test_oracle_schema_manifest_is_deterministic() -> None:
    """manifest は時刻を入れず、SQL の hash / statement 数を検証できる形にする。"""
    sql = oracle_schema.oracle_schema_sql()
    manifest = oracle_schema.oracle_schema_manifest()

    assert manifest == oracle_schema.oracle_schema_manifest()
    assert "generated_at" not in manifest
    assert manifest["schema_name"] == "production-ready-rag-oracle-26ai"
    assert manifest["schema_version"] == "1"
    assert manifest["vector_contract"] == "VECTOR(1536, FLOAT32)"
    assert manifest["vector_index"] == {
        "distance": "COSINE",
        "efconstruction": 500,
        "neighbors": 32,
        "target_accuracy": 95,
        "type": "HNSW",
    }
    assert manifest["sha256"] == hashlib.sha256(sql.encode("utf-8")).hexdigest()
    assert manifest["statement_count"] == len(oracle_schema.split_sql_statements(sql))
    assert [section["name"] for section in manifest["sections"]] == [
        "documents",
        "chunks",
        "search_audit",
        "ingestion_audit",
    ]
    assert all(section["statement_count"] > 0 for section in manifest["sections"])


def test_oracle_schema_cli_writes_sql_and_manifest(tmp_path: Path) -> None:
    """CLI は SQL と manifest を staging artifact として保存できる。"""
    sql_output = tmp_path / "artifacts" / "oracle-schema.sql"
    manifest_output = tmp_path / "artifacts" / "oracle-schema.manifest.json"

    exit_code = oracle_schema.main(
        [
            "--output",
            str(sql_output),
            "--manifest-output",
            str(manifest_output),
        ]
    )

    assert exit_code == 0
    assert "CREATE TABLE rag_documents" in sql_output.read_text(encoding="utf-8")
    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert (
        manifest["sha256"]
        == hashlib.sha256(sql_output.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    )


def test_oracle_schema_cli_manifest_only_prints_json(
    capsys: CaptureFixture[str],
) -> None:
    """--manifest-only は SQL を出さず manifest JSON だけを stdout に出す。"""
    exit_code = oracle_schema.main(["--manifest-only"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "CREATE TABLE" not in captured.out
    manifest = json.loads(captured.out)
    assert manifest["vector_contract"] == "VECTOR(1536, FLOAT32)"
