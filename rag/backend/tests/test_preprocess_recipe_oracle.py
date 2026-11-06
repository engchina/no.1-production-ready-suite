"""実Oracleで正式recipe artifactへ保存し、旧文書列を変更しない。"""

import hashlib

import pytest
from rag_parser_core.preprocess import SourceDerivation

from app.clients.oracle import OracleClient
from app.rag.ingestion import IngestionPipeline
from app.schemas.document import DocumentPreprocessArtifact
from tests.support import test_audit_request_context as audit_request_context


@pytest.mark.usefixtures("oracle_db")
async def test_formal_preprocess_artifact_roundtrip_keeps_document_legacy_empty() -> None:
    with audit_request_context():
        client = OracleClient()
        digest = hashlib.sha256(b"synthetic source").hexdigest()
        document = await client.create_document(
            file_name="synthetic.txt",
            object_storage_path="local://synthetic/source.txt",
            content_type="text/plain",
            content_sha256=digest,
        )
        recipe = next(
            row for row in await client.list_document_recipes(document.id) if row["slot_no"] == 1
        )
        pipeline = IngestionPipeline.__new__(IngestionPipeline)
        pipeline._oracle = client
        pipeline._recipe_id = str(recipe["recipe_id"])
        pipeline._recipe_revision = int(str(recipe["config_revision"]))
        artifact = await pipeline._save_preprocess_artifact(
            document_id=document.id,
            source_derivation=SourceDerivation(
                derivation_id="synthetic-prepared",
                preprocess_profile="passthrough",
                converted=False,
                source_content_type="text/plain",
                source_sha256=digest,
            ),
            original_file_name="synthetic.txt",
            original_object_storage_path="local://synthetic/source.txt",
            fallback_content_type="text/plain",
        )
        saved = await client.get_document_recipe(document.id, str(recipe["recipe_id"]))
        assert saved is not None and saved["status"] == "PREPROCESSED"
        assert DocumentPreprocessArtifact.model_validate(saved["preprocess_artifact"]) == artifact
        reloaded = await client.get_document(document.id)
        assert (
            reloaded is not None
            and reloaded.preprocess_artifact == document.preprocess_artifact is None
        )
