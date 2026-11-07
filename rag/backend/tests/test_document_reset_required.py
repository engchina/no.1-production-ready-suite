"""文書resetの正式async portを必須にし、状態だけの成功へ縮退しない。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.api.routes import documents as routes
from app.api.routes.documents import _reset_document_outputs_for_extract
from app.clients.oracle import OracleClient
from app.rag.staging_recipes import StagingRecipeOracle
from app.schemas.document import DocumentPreprocessArtifact, FileStatus, IngestionJobPhase


class _Base:
    def __init__(self) -> None:
        self.extraction: object | None = object()
        self.chunks = ["synthetic-chunk"]
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def update_document_status(self, document_id: str, status: FileStatus) -> None:
        self.calls.append(("status_only", {"document_id": document_id, "status": status}))


class _Complete(_Base):
    async def reset_document_ingestion_outputs(self, document_id: str, **kwargs: Any) -> None:
        self.calls.append(("reset", {"document_id": document_id, **kwargs}))
        self.extraction = None
        self.chunks = []


class _Missing(_Base):
    pass


class _NonCallable(_Base):
    reset_document_ingestion_outputs = None


class _Sync(_Base):
    def reset_document_ingestion_outputs(self, document_id: str, **kwargs: Any) -> None:
        self.calls.append(("sync", {"document_id": document_id, **kwargs}))


class _Raising(_Base):
    async def reset_document_ingestion_outputs(self, document_id: str, **kwargs: Any) -> None:
        self.calls.append(("raising", {"document_id": document_id, **kwargs}))
        raise RuntimeError("synthetic-reset-failure")


@pytest.mark.parametrize(
    ("adapter_type", "failure"),
    [
        (_Complete, None),
        (_Missing, AttributeError),
        (_NonCallable, TypeError),
        (_Sync, TypeError),
        (_Raising, RuntimeError),
    ],
)
async def test_reset_requires_async_port_without_status_only_success(
    adapter_type: type[_Base], failure: type[Exception] | None
) -> None:
    adapter = adapter_type()
    old_extraction = adapter.extraction
    if failure is None:
        await _reset_document_outputs_for_extract(cast(OracleClient, adapter), "synthetic-doc")
        assert adapter.extraction is None and adapter.chunks == []
        assert adapter.calls == [
            ("reset", {"document_id": "synthetic-doc", "status": FileStatus.UPLOADED})
        ]
    else:
        with pytest.raises(failure):
            await _reset_document_outputs_for_extract(cast(OracleClient, adapter), "synthetic-doc")
        assert adapter.extraction is old_extraction
        assert adapter.chunks == ["synthetic-chunk"]
    assert not any(name == "status_only" for name, _ in adapter.calls)


async def test_formal_oracle_wrapper_forwards_the_same_reset_transaction() -> None:
    sentinel = object()
    calls: list[dict[str, Any]] = []

    class Transaction:
        async def _reset_document_ingestion_outputs_with_oracle(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return sentinel

    returned = await OracleClient.reset_document_ingestion_outputs(
        cast(OracleClient, Transaction()),
        "synthetic-doc",
        status=FileStatus.UPLOADED,
    )
    assert returned is sentinel
    assert calls == [
        {
            "document_id": "synthetic-doc",
            "status": FileStatus.UPLOADED,
            "error_message": None,
        }
    ]


def test_staging_adapter_inherits_the_formal_reset_port() -> None:
    assert (
        StagingRecipeOracle.reset_document_ingestion_outputs
        is OracleClient.reset_document_ingestion_outputs
    )


@pytest.mark.parametrize("served", [False, True])
@pytest.mark.parametrize("phase", [IngestionJobPhase.PREPROCESS, IngestionJobPhase.EXTRACT])
async def test_real_enqueue_preserves_phase_and_served_output(
    monkeypatch: pytest.MonkeyPatch, served: bool, phase: IngestionJobPhase
) -> None:
    artifact = DocumentPreprocessArtifact(
        derivation_id="synthetic",
        profile="none",
        converted=False,
        object_storage_path="synthetic-original",
        file_name="synthetic.txt",
        content_type="text/plain",
    )

    class Oracle(_Complete):
        async def get_document(self, document_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                object_storage_path="synthetic-original",
                status=FileStatus.UPLOADED,
                duplicate_of_document_id=None,
            )

        async def ensure_default_document_recipe(self, document_id: str) -> dict[str, Any]:
            return {"recipe_id": "synthetic-recipe", "preprocess_artifact": artifact.model_dump()}

        async def delete_recipe_segment_checkpoints(self, document_id: str, recipe_id: str) -> None:
            self.calls.append(("checkpoint", {"document_id": document_id, "recipe_id": recipe_id}))

    oracle = Oracle()
    original = oracle.extraction, list(oracle.chunks)

    async def noop(*args: Any, **kwargs: Any) -> None:
        pass

    async def settings(*args: Any) -> object:
        return object()

    async def has_served(*args: Any) -> bool:
        return served

    async def create(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["phase"] == phase
        return SimpleNamespace(id="synthetic-job")

    dispatched: list[tuple[str, bool]] = []
    monkeypatch.setattr(routes, "OracleClient", lambda: oracle)
    monkeypatch.setattr(routes, "_raise_if_default_recipe_job_active", noop)
    monkeypatch.setattr(
        routes,
        "_source_profile_for_detail",
        lambda _: SimpleNamespace(
            parser_profile="synthetic",
            quality_warnings=[],
            unsupported_reason=None,
        ),
    )
    monkeypatch.setattr(routes, "_default_recipe_settings", settings)
    monkeypatch.setattr(routes, "_raise_if_parser_source_blocked", noop)
    monkeypatch.setattr(routes, "_has_served_output", has_served)
    monkeypatch.setattr(routes, "_create_ingestion_job_record", create)
    monkeypatch.setattr(
        routes,
        "_dispatch_ingestion_job",
        lambda job_id, *, force: dispatched.append((job_id, force)),
    )
    result = await routes._enqueue_ingestion_job_for_document(
        "synthetic-doc", force=True, phase=phase
    )
    assert result.id == "synthetic-job" and dispatched == [("synthetic-job", True)]
    if served:
        assert (oracle.extraction, oracle.chunks) == original
        assert oracle.calls == [
            ("checkpoint", {"document_id": "synthetic-doc", "recipe_id": "synthetic-recipe"})
        ]
    else:
        # PREPROCESS も EXTRACT も同じ初期化を呼ぶ（処理後のファイルはレシピの層にある）。
        assert oracle.extraction is None and not oracle.chunks
        assert oracle.calls == [
            ("reset", {"document_id": "synthetic-doc", "status": FileStatus.UPLOADED})
        ]
