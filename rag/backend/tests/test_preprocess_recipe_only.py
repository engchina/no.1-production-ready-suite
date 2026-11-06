"""正式recipe保存を保ち、旧文書列への保存と静かな未保存を拒否する。"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from rag_parser_core.preprocess import SourceDerivation

from app.clients import oracle as om
from app.clients.oracle import OracleClient, StoredDocument
from app.rag.ingestion import IngestionPipeline
from app.rag.request_context import (
    AuditRequestContext,
    reset_audit_request_context,
    set_audit_request_context,
)
from app.schemas.document import FileStatus

source = SourceDerivation(
    derivation_id="synthetic-derivation",
    preprocess_profile="office_to_pdf",
    converted=True,
    source_content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    source_sha256="a" * 64,
    derived_object_path="local://synthetic/canonical.pdf",
    derived_content_type="application/pdf",
    derived_sha256="b" * 64,
)


async def one(mode: str) -> dict[str, Any]:
    state: dict[str, Any] = {"legacy": None, "recipe": None, "sql": [], "commits": 0, "reads": 0}
    client = OracleClient.__new__(OracleClient)

    def document() -> StoredDocument:
        return StoredDocument(
            id="synthetic-doc",
            file_name="synthetic.docx",
            status=FileStatus.PREPROCESSING,
            uploaded_at=datetime(2026, 1, 1, tzinfo=UTC),
            tenant_id_hash="synthetic-tenant",
            preprocess_artifact=state["legacy"],
        )

    def execute(_connection: Any, sql: str, binds: Any = None, **_kw: Any) -> None:
        state["sql"].append(" ".join(sql.split()))
        if mode == "write_error":
            raise RuntimeError("synthetic write failure")
        if "SET preprocess_artifact = :preprocess_artifact" in sql and mode != "discard":
            state["legacy"] = (
                json.loads(binds["preprocess_artifact"]) if binds["preprocess_artifact"] else None
            )
        if "UPDATE rag_document_recipes" in sql:
            state["recipe"] = "synthetic-recipe"

    async def transaction(op: Any) -> Any:
        result = op(object())
        state["commits"] += 1
        return result

    async def get_document(docid: str) -> Any:
        state["reads"] += 1
        return None if mode == "read_missing" else om._to_document_detail(document())

    oracle: Any = client
    if mode == "no_writer":
        oracle = SimpleNamespace()
    if mode == "no_getter":

        async def save(docid: str, artifact: Any) -> None:
            state["legacy"] = artifact.model_dump(mode="json")

        oracle = SimpleNamespace(save_preprocess_artifact=save)
    helper = IngestionPipeline.__new__(IngestionPipeline)
    helper._oracle = oracle
    helper._recipe_id = "synthetic-recipe" if mode == "formal_recipe" else None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(client, "_run_transaction", transaction)
        mp.setattr(client, "get_document", get_document)
        mp.setattr(om, "_execute", execute)
        mp.setattr(om, "_select_document", lambda *_: document())
        mp.setattr(om, "_select_document_knowledge_base_refs", lambda *_: [])
        try:
            artifact = await helper._save_preprocess_artifact(
                document_id="synthetic-doc",
                source_derivation=source,
                original_file_name="synthetic.docx",
                original_object_storage_path="local://synthetic/original.docx",
                fallback_content_type="application/pdf",
            )
            outcome: dict[str, Any] = {
                "returned": True,
                "artifact_path_present": bool(artifact.object_storage_path),
                "error": None,
            }
        except Exception as err:
            outcome = {"returned": False, "error_type": type(err).__name__, "error": str(err)}
    return {
        "mode": mode,
        "recipe_id_supplied": helper._recipe_id is not None,
        "legacy_written": state["legacy"] is not None,
        "recipe_written": state["recipe"] is not None,
        "sql": state["sql"],
        "fake_commits": state["commits"],
        "readback_calls": state["reads"],
        **outcome,
    }


@pytest.mark.parametrize(
    "mode",
    [
        "formal_recipe",
        "no_recipe",
        "discard",
        "read_missing",
        "write_error",
        "no_writer",
        "no_getter",
    ],
)
async def test_preprocess_persistence_requires_formal_recipe(mode: str) -> None:
    token = set_audit_request_context(
        AuditRequestContext(
            request_id="synthetic",
            tenant_id_hash="synthetic-tenant",
            allowed_document_ids=frozenset({"synthetic-doc"}),
        )
    )
    try:
        result = await one(mode)
    finally:
        reset_audit_request_context(token)
    if mode == "formal_recipe":
        assert result["returned"] is True
        assert result["recipe_written"] is True
        assert result["legacy_written"] is False
        assert result["fake_commits"] == 1
    else:
        assert result["returned"] is False
        assert result["error_type"] == "IngestionUserError"
        assert "レシピ" in result["error"]
        assert result["sql"] == []
        assert result["fake_commits"] == 0
    assert result["readback_calls"] == 0
