"""根拠の版は、その版が解析したファイルを読む（#4166）。DB を使わない切り分けと回帰。

再処理はレシピの行の ``preprocess_artifact`` を新しいファイルへ差し替える。切替までは旧版が検索に
出ているので、旧版の bbox・要素の位置を新しいファイルに当ててはいけない。
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pr_backend_core.mcp import McpToolError
from rag_parser_core.preprocess import SourceDerivation

from app import main as app_main
from app.api.errors import DocumentFileMissingError, SourceVersionStaleError
from app.api.routes import documents as documents_route
from app.clients import object_storage as object_storage_module
from app.clients.object_storage import ObjectStorageClient
from app.config import Settings, get_settings
from app.mcp import tools as mcp_tools
from app.rag import answer_engine as engine_module
from app.rag.document_crop import (
    DocumentSourceNotFoundError,
    VersionSourceUnknownError,
    load_parsed_source,
    prepared_artifact_from_extraction,
)
from app.rag.ingestion import (
    IngestionPipeline,
    IngestionUserError,
    _extraction_with_source_derivation,
)
from app.schemas.document import IngestionJob, IngestionJobPhase, IngestionJobStatus
from app.schemas.extraction import StructuredExtraction
from app.schemas.search import RetrievedChunk
from tests.support import AsgiTestClient
from tests.test_json_source_window import fixture

_ORIGINAL = b"original-bytes"
_FILE_A = b"%PDF-prepared-version-A"
_FILE_B = b"%PDF-prepared-version-B"


def _derivation(name: str, path: str | None, *, converted: bool = True) -> SourceDerivation:
    return SourceDerivation(
        derivation_id=name,
        preprocess_profile="office_to_pdf" if converted else "passthrough",
        converted=converted,
        converter_name="stub" if converted else "passthrough",
        source_content_type="application/octet-stream",
        source_sha256="source-sha",
        derived_object_path=path,
        derived_content_type="application/pdf" if converted else None,
        derived_sha256=f"sha-{name}" if converted else None,
        page_map={"1": 2} if converted else {},
    )


class _Oracle:
    """レシピの行と、chunk_set ごとの抽出の系譜を持つ fake。"""

    def __init__(self, original_path: str) -> None:
        self.recipe: dict[str, object] = {"recipe_id": "r1", "preprocess_artifact": None}
        self.derivations: dict[str, object] = {}
        self.detail = SimpleNamespace(
            id="d1",
            file_name="synthetic.docx",
            content_type="application/octet-stream",
            object_storage_path=original_path,
            preprocess_artifact=None,
        )

    async def update_document_recipe_status(self, **kwargs: Any) -> None:
        artifact = kwargs.get("preprocess_artifact")
        if artifact is not None:
            self.recipe["preprocess_artifact"] = artifact.model_dump(mode="json")

    async def get_document(self, document_id: str) -> Any:
        return self.detail if document_id == "d1" else None

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> Any:
        return self.recipe if (document_id, recipe_id) == ("d1", "r1") else None

    async def chunk_set_source_derivation(self, *, document_id: str, chunk_set_id: str) -> Any:
        assert document_id == "d1"
        return self.derivations.get(chunk_set_id)


@pytest.fixture
async def reprocessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_Oracle, dict[str, str]]:
    """版 A を切替で出している間に、再処理が版 B のファイルをレシピの行へ保存した状態。"""
    settings = get_settings().model_copy(
        update={"upload_storage_backend": "local", "local_storage_dir": str(tmp_path)}
    )
    monkeypatch.setattr(object_storage_module, "get_settings", lambda: settings)
    storage = ObjectStorageClient(settings)
    paths = {
        "original": await storage.put("uploads/d1/original.docx", _ORIGINAL, "x"),
        # 取込の成果物の key は trace ごとなので、版 A のファイルは上書きされない。
        "a": await storage.put("artifacts/canonical/d1/trace-a/canonical.pdf", _FILE_A, "x"),
        "b": await storage.put("artifacts/canonical/d1/trace-b/canonical.pdf", _FILE_B, "x"),
    }
    oracle = _Oracle(paths["original"])
    pipeline = IngestionPipeline(
        oracle=cast(Any, oracle), settings=settings, recipe_id="r1", recipe_revision=1
    )
    for name, path in (("a", paths["a"]), ("b", paths["b"])):
        await pipeline._save_preprocess_artifact(
            document_id="d1",
            source_derivation=_derivation(name, path),
            original_file_name="synthetic.docx",
            original_object_storage_path=paths["original"],
            fallback_content_type="application/octet-stream",
        )
    oracle.derivations = {
        "cs-old": _derivation("a", paths["a"]).model_dump(mode="json"),
        "cs-new": _derivation("b", paths["b"]).model_dump(mode="json"),
    }
    return oracle, paths


async def test_each_version_reads_the_file_it_parsed(
    reprocessed: tuple[_Oracle, dict[str, str]],
) -> None:
    oracle, _paths = reprocessed
    read = cast(Any, oracle)
    assert await load_parsed_source(read, "d1", "r1", chunk_set_id="cs-old") == _FILE_A
    assert await load_parsed_source(read, "d1", "r1", chunk_set_id="cs-new") == _FILE_B
    # 版を指定しない読み取りは、今のレシピのファイルで代用しない。
    with pytest.raises(VersionSourceUnknownError):
        await load_parsed_source(read, "d1", "r1")


async def test_preview_document_binds_to_the_evidence_version(
    reprocessed: tuple[_Oracle, dict[str, str]],
) -> None:
    oracle, paths = reprocessed
    read = cast(Any, oracle)
    _detail, old = await mcp_tools.preview_document(read, "d1", "r1", chunk_set_id="cs-old")
    assert old is not None
    assert old.object_storage_path == paths["a"]
    assert old.page_map == {"1": 2}
    assert old.file_name == "synthetic__prepared.pdf"
    _detail, current = await mcp_tools.preview_document(read, "d1", "r1", chunk_set_id="cs-new")
    assert current is not None
    assert current.object_storage_path == paths["b"]


async def test_version_without_conversion_reads_the_original(
    reprocessed: tuple[_Oracle, dict[str, str]],
) -> None:
    """旧版が変換なしなら、新版が変換しても旧版は原本を読む。"""
    oracle, _paths = reprocessed
    oracle.derivations["cs-plain"] = _derivation("plain", None, converted=False).model_dump(
        mode="json"
    )
    read = cast(Any, oracle)
    assert await load_parsed_source(read, "d1", "r1", chunk_set_id="cs-plain") == _ORIGINAL


@pytest.mark.parametrize("derivation", [None, {}, {"converted": "not-a-derivation"}])
async def test_version_without_usable_lineage_is_an_error_not_the_recipe_file(
    reprocessed: tuple[_Oracle, dict[str, str]], derivation: object
) -> None:
    """抽出の行か系譜が無い版は、別のファイルで代用せずエラーにする。"""
    oracle, _paths = reprocessed
    oracle.derivations["cs-unknown"] = derivation
    with pytest.raises(VersionSourceUnknownError):
        await load_parsed_source(cast(Any, oracle), "d1", "r1", chunk_set_id="cs-unknown")


async def test_missing_old_file_is_reported_not_replaced_with_the_new_file(
    reprocessed: tuple[_Oracle, dict[str, str]],
) -> None:
    oracle, _paths = reprocessed
    oracle.derivations["cs-gone"] = _derivation(
        "gone", "artifacts/canonical/d1/trace-gone/canonical.pdf"
    ).model_dump(mode="json")
    with pytest.raises(DocumentSourceNotFoundError):
        await load_parsed_source(cast(Any, oracle), "d1", "r1", chunk_set_id="cs-gone")


def test_saved_extraction_payload_carries_the_file_lineage() -> None:
    """抽出の payload の系譜だけで、列を足さずに版のファイルへ辿れる（確認の保存でも残る）。"""
    detail = SimpleNamespace(
        file_name="synthetic.docx", content_type=None, object_storage_path="docs/original.docx"
    )
    extraction = _extraction_with_source_derivation(
        StructuredExtraction(raw_text="合成の本文"), _derivation("a", "artifacts/a.pdf")
    )
    payload = extraction.to_document_payload()
    artifact = prepared_artifact_from_extraction(detail, payload)
    assert artifact is not None
    assert artifact.object_storage_path == "artifacts/a.pdf"
    assert artifact.derivation_id == "a"
    reloaded = StructuredExtraction.model_validate(payload).to_document_payload()
    assert prepared_artifact_from_extraction(detail, reloaded) == artifact
    with pytest.raises(VersionSourceUnknownError):
        prepared_artifact_from_extraction(detail, {"raw_text": "系譜なし"})


def _source_map_oracle(detail: Any, chunk: RetrievedChunk, payload: dict[str, Any]) -> Any:
    return SimpleNamespace(
        readable_version_chunk=AsyncMock(return_value=chunk),
        accessible_document_exists=AsyncMock(return_value=True),
        get_document=AsyncMock(return_value=detail),
        get_document_recipe=AsyncMock(
            return_value={
                "active_extraction_recipe_id": "ex-old",
                "preprocess_artifact": {
                    "derivation_id": "new",
                    "profile": "office_to_pdf",
                    "converted": True,
                    "object_storage_path": "artifacts/new.pdf",
                    "file_name": "new.pdf",
                },
            }
        ),
        chunk_set_extraction_recipe_ids=AsyncMock(return_value={"cs-old": "ex-old"}),
        get_document_extraction_artifact=AsyncMock(return_value={"extraction_json": payload}),
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/d1/source-map?chunk_id=c1&expected_revision=cs-old&element_id=e1",
        "/api/documents/d1/recipes/r1/source-map?element_id=e1&expected_revision=ex-old",
    ],
)
def test_source_map_routes_read_the_file_of_the_extraction(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """原 JSON の窓は、レシピの今のファイルではなく、その抽出が解析したファイルを読む。"""
    detail = SimpleNamespace(
        file_name="synthetic.json",
        content_type="application/json",
        object_storage_path="docs/original.json",
        preprocess_artifact=None,
    )
    chunk = RetrievedChunk(
        document_id="d1",
        chunk_id="c1",
        text="合成",
        score=0,
        metadata={"chunk_set_id": "cs-old", "recipe_id": "r1", "element_ids": "e1"},
    )
    raw, source_payload = fixture()
    payload = dict(source_payload)
    payload["parser_artifacts"] = {
        **dict(payload.get("parser_artifacts") or {}),
        "source_derivation": _derivation("old", "artifacts/old.pdf").model_dump(mode="json"),
    }
    monkeypatch.setattr(
        documents_route, "OracleClient", lambda: _source_map_oracle(detail, chunk, payload)
    )
    seen: list[str | None] = []

    async def load(_detail: Any, *, variant: str, preprocess_artifact: Any) -> Any:
        _ = variant
        seen.append(preprocess_artifact.object_storage_path if preprocess_artifact else None)
        return raw, "synthetic.json", "application/json"

    monkeypatch.setattr(documents_route, "load_document_content", load)
    assert AsgiTestClient(app_main.app).get(path).status_code == 200
    assert set(seen) == {"artifacts/old.pdf"}


async def test_answer_figure_crop_reads_the_file_of_each_chunk_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答の図の切り出しは、文書ではなく根拠の版ごとにファイルを読む。"""
    calls: list[tuple[str, object, object]] = []

    async def fake_source(
        oracle: object, document_id: str, recipe_id: object = None, *, chunk_set_id: object = None
    ) -> bytes:
        _ = oracle
        calls.append((document_id, recipe_id, chunk_set_id))
        return f"{chunk_set_id}".encode()

    monkeypatch.setattr(engine_module, "load_parsed_source", fake_source)
    engine = engine_module.AnswerEngine(Settings(), oracle=cast(Any, object()), genai=cast(Any, 1))
    state = engine_module._SearchState()

    def chunk(chunk_set_id: str, recipe_id: str) -> RetrievedChunk:
        return RetrievedChunk(
            document_id="d1",
            chunk_id=f"c-{chunk_set_id}",
            text="合成",
            score=0,
            metadata={"chunk_set_id": chunk_set_id, "recipe_id": recipe_id},
        )

    assert await engine._parsed_source(chunk("cs-1", "r1"), state) == b"cs-1"
    assert await engine._parsed_source(chunk("cs-2", "r2"), state) == b"cs-2"
    assert await engine._parsed_source(chunk("cs-1", "r1"), state) == b"cs-1"
    assert calls == [("d1", "r1", "cs-1"), ("d1", "r2", "cs-2")]


async def test_job_without_recipe_stops_with_user_error_and_touches_nothing() -> None:
    """recipe_id の無い job は文書単位の経路へ進まない（旧 active を消す経路を残さない）。"""
    job = IngestionJob(
        id="job-no-recipe",
        document_id="d1",
        status=IngestionJobStatus.RUNNING,
        phase=IngestionJobPhase.PREPROCESS,
        parser_profile="local_text_structure",
        queued_at=datetime.now(UTC),
    )
    with pytest.raises(IngestionUserError, match="文書の処理レシピから再実行"):
        await documents_route._materialize_experiment_candidate(cast(Any, object()), job)


def _version_oracle(*, version_exists: bool, derivation: dict[str, Any] | None) -> Any:
    detail = SimpleNamespace(
        file_name="synthetic.docx",
        content_type="application/octet-stream",
        object_storage_path="docs/original.docx",
        preprocess_artifact=None,
    )
    return SimpleNamespace(
        get_document=AsyncMock(return_value=detail),
        get_document_recipe=AsyncMock(
            return_value={
                "preprocess_artifact": {
                    "derivation_id": "new",
                    "profile": "office_to_pdf",
                    "converted": True,
                    "object_storage_path": "artifacts/new.pdf",
                    "file_name": "new.pdf",
                }
            }
        ),
        recipe_version_exists=AsyncMock(return_value=version_exists),
        chunk_set_source_derivation=AsyncMock(return_value=derivation),
    )


async def test_recipe_file_is_the_new_file_without_a_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ファイル準備の確認の画面（版を渡さない）は今までどおり新しいファイルを見る。"""
    oracle = _version_oracle(version_exists=True, derivation=None)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)

    _, artifact = await documents_route._recipe_detail_and_artifact("d1", "r1")

    assert artifact is not None and artifact.object_storage_path == "artifacts/new.pdf"
    oracle.recipe_version_exists.assert_not_awaited()


async def test_recipe_file_is_the_file_of_the_cited_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    derivation = _derivation("old", "artifacts/old.pdf").model_dump(mode="json")
    oracle = _version_oracle(version_exists=True, derivation=derivation)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)

    _, artifact = await documents_route._recipe_detail_and_artifact("d1", "r1", "cs-old")

    assert artifact is not None and artifact.object_storage_path == "artifacts/old.pdf"
    oracle.recipe_version_exists.assert_awaited_once_with(
        document_id="d1", recipe_id="r1", chunk_set_id="cs-old"
    )


async def test_recipe_file_of_a_deleted_version_is_stale_not_the_new_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = _version_oracle(version_exists=False, derivation=None)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)

    with pytest.raises(SourceVersionStaleError):
        await documents_route._recipe_detail_and_artifact("d1", "r1", "cs-gone")


async def test_recipe_file_without_lineage_is_a_missing_file_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = _version_oracle(version_exists=True, derivation=None)
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)

    with pytest.raises(DocumentFileMissingError):
        await documents_route._recipe_detail_and_artifact("d1", "r1", "cs-old")


async def test_extraction_without_lineage_is_a_missing_file_error() -> None:
    """抽出の窓（引用の原 JSON・確認の窓）も、記録の無いファイルを別のファイルで代用しない。"""
    with pytest.raises(DocumentFileMissingError):
        documents_route._extraction_file_artifact(SimpleNamespace(), {"raw_text": "系譜なし"})


async def test_mcp_preview_of_a_version_without_lineage_is_a_missing_file_error() -> None:
    detail = SimpleNamespace(
        file_name="synthetic.docx",
        content_type="application/octet-stream",
        object_storage_path="docs/original.docx",
        preprocess_artifact=None,
    )
    oracle = SimpleNamespace(
        get_document=AsyncMock(return_value=detail),
        chunk_set_source_derivation=AsyncMock(return_value=None),
    )

    with pytest.raises(McpToolError) as error:
        await mcp_tools.preview_document(cast(Any, oracle), "d1", "r1", chunk_set_id="cs-old")

    assert error.value.code == mcp_tools.PREVIEW_FILE_MISSING_CODE
