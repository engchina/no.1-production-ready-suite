"""解析後の本文で見る文書の重複（#5313）。

実 Oracle での SQL の確認は ``test_extracted_text_duplicate_oracle.py``。ここは hash・確認の
組み立て・取込の工程での呼ぶ位置・job の結果の扱い・SQL の条件を fake で確かめる。
fixture の本文はすべて合成。
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, cast

import pytest

from app.api.routes import documents as documents_route
from app.clients.object_storage import ObjectStorageClient
from app.clients.oracle import OracleClient
from app.config import Settings
from app.rag.extracted_text_duplicate import (
    DUPLICATE_CONTENT_SKIP_REASON,
    ExtractedTextDuplicateError,
    extracted_text_duplicate_gate,
    extracted_text_sha256,
)
from app.rag.ingestion import IngestionPipeline
from app.schemas.document import DocumentDetail, FileStatus, IngestionJobStatus
from tests.language_release_support import install_language_release_stub
from tests.test_document_recipes import _extract_job
from tests.test_ingestion_strategy import (
    CapturingVlm,
    FakeEmbeddingClient,
    FakeObjectStorage,
    FakeOracle,
    _formal_extraction_recipe,
    _pdf_source_profile,
    _stub_parser_service,  # noqa: F401  (fixture)
    isolated_preprocess_save,  # noqa: F401  (fixture)
)
from tests.test_oracle_adapter import FakeOraclePool, _oci_settings, _run_inline
from tests.test_recipe_activation_baseline import _LeaseOracle

_RECORDS = [
    {"id": "item-1", "title": "合成の見出し A", "body": "合成の本文の一行目。\n二行目。"},
    {"id": "item-2", "title": "Synthetic heading B", "values": [1, 2, 3], "flag": True},
]


# --- hash -----------------------------------------------------------------------------------


def test_same_json_in_one_line_and_pretty_printed_has_the_same_hash() -> None:
    one_line = json.dumps(_RECORDS, ensure_ascii=False, separators=(",", ":"))
    pretty = json.dumps(_RECORDS, ensure_ascii=False, indent=2)
    assert one_line != pretty
    assert extracted_text_sha256(one_line) == extracted_text_sha256(pretty)


def test_same_body_with_different_line_breaks_has_the_same_hash() -> None:
    """PDF の抽出(行の途中の改行・ページの空行)と、同じ本文のテキストを同じにする。"""
    plain_text = "第一節 合成の見出し\n合成の本文はここから始まり、ここで終わる。\n"
    pdf_like = "第一節 合成の見出し\n\n合成の本文はここから\n始まり、ここで終わる。\n\n\n"
    assert extracted_text_sha256(plain_text) == extracted_text_sha256(pdf_like)


def test_composed_and_decomposed_characters_have_the_same_hash() -> None:
    composed = "合成のデータ"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    assert extracted_text_sha256(composed) == extracted_text_sha256(decomposed)


@pytest.mark.parametrize(
    "changed",
    [
        json.dumps(_RECORDS, ensure_ascii=False).replace("item-2", "item-3"),
        json.dumps(_RECORDS, ensure_ascii=False).replace("一行目", "一行日"),
        json.dumps(_RECORDS, ensure_ascii=False).replace("[1, 2, 3]", "[1, 2, 3, 4]"),
        json.dumps(_RECORDS, ensure_ascii=False) + "。",
    ],
    ids=["id", "one_kanji", "added_value", "added_period"],
)
def test_body_that_differs_by_one_character_has_another_hash(changed: str) -> None:
    original = json.dumps(_RECORDS, ensure_ascii=False)
    assert extracted_text_sha256(original) != extracted_text_sha256(changed)


# --- 確認の組み立て -------------------------------------------------------------------------


class _HashStore:
    def __init__(self, canonical: str | None = None) -> None:
        self.canonical = canonical
        self.calls: list[tuple[str, str, bool]] = []

    async def record_extracted_text_hash(
        self, document_id: str, extracted_text_sha256: str, *, judge_duplicate: bool
    ) -> str | None:
        self.calls.append((document_id, extracted_text_sha256, judge_duplicate))
        return self.canonical if judge_duplicate else None


@pytest.mark.parametrize(
    ("recipe", "duplicate_of"),
    [
        (None, None),
        ({"slot_no": 2, "active_chunk_set_id": None}, None),
        ({"slot_no": 1, "active_chunk_set_id": None}, "doc-source"),
    ],
    ids=["no_recipe", "other_recipe", "already_duplicate"],
)
def test_gate_is_not_built_for_other_recipes_or_duplicates(
    recipe: dict[str, object] | None, duplicate_of: str | None
) -> None:
    gate = extracted_text_duplicate_gate(
        _HashStore("doc-source"),
        document_id="doc-1",
        recipe=recipe,
        duplicate_of_document_id=duplicate_of,
    )
    assert gate is None


async def test_gate_marks_first_ingestion_as_duplicate() -> None:
    store = _HashStore("doc-source")
    gate = extracted_text_duplicate_gate(
        store,
        document_id="doc-1",
        recipe={"slot_no": 1, "active_chunk_set_id": None},
        duplicate_of_document_id=None,
    )
    assert gate is not None

    with pytest.raises(ExtractedTextDuplicateError) as caught:
        await gate("合成の本文")

    assert caught.value.duplicate_of_document_id == "doc-source"
    assert store.calls == [("doc-1", extracted_text_sha256("合成の本文"), True)]


async def test_gate_only_records_hash_when_document_already_has_an_index() -> None:
    """索引のある文書の再処理は、後から重複にしない(hash の記録だけ新しくする)。"""
    store = _HashStore("doc-source")
    gate = extracted_text_duplicate_gate(
        store,
        document_id="doc-1",
        recipe={"slot_no": 1, "active_chunk_set_id": "chunk-set-1"},
        duplicate_of_document_id=None,
    )
    assert gate is not None

    await gate("合成の本文")

    assert store.calls == [("doc-1", extracted_text_sha256("合成の本文"), False)]


# --- 取込の工程での呼ぶ位置 -----------------------------------------------------------------


def _pipeline(oracle: FakeOracle, storage: FakeObjectStorage) -> IngestionPipeline:
    return IngestionPipeline(
        vlm=CapturingVlm(),
        genai=FakeEmbeddingClient(),
        oracle=cast(Any, oracle),
        recipe_id=_formal_extraction_recipe(oracle, "doc-text-duplicate"),
        recipe_revision=1,
        object_storage=cast(Any, storage),
        settings=Settings(
            rag_field_extraction_enabled=False,
            rag_parser_adapter_backend="local",
            rag_review_gate_enabled=False,
            rag_auto_parse_after_preprocess_enabled=True,
        ),
    )


@pytest.mark.usefixtures("isolated_preprocess_save", "_stub_parser_service")
async def test_duplicate_gate_stops_before_saving_extraction_and_index() -> None:
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    seen: list[str] = []

    async def gate(text: str) -> None:
        seen.append(text)
        raise ExtractedTextDuplicateError("doc-source")

    with pytest.raises(ExtractedTextDuplicateError):
        await _pipeline(oracle, storage).ingest(
            "doc-text-duplicate",
            b"pdfdata",
            content_type="application/pdf",
            source_profile=_pdf_source_profile(file_size_bytes=7),
            extracted_text_gate=gate,
        )

    assert len(seen) == 1 and seen[0].strip()
    # 抽出の正本の保存・分割・索引へ進まず、文書を ERROR にしない。
    assert oracle.saved_extraction is None
    assert oracle.atomic_index_save_count == 0
    assert oracle.saved_chunk_count == 0
    assert FileStatus.ERROR not in oracle.statuses
    # 解析の段は成功のまま(重複を無視して取り込むときに使い直せる)。
    assert all(segment.status == "SUCCEEDED" for segment in oracle.segments.values())


@pytest.mark.usefixtures("isolated_preprocess_save", "_stub_parser_service")
async def test_passing_gate_lets_ingestion_continue_to_index() -> None:
    oracle = FakeOracle()
    storage = FakeObjectStorage()
    seen: list[str] = []

    async def gate(text: str) -> None:
        seen.append(text)

    detail = await _pipeline(oracle, storage).ingest(
        "doc-text-duplicate",
        b"pdfdata",
        content_type="application/pdf",
        source_profile=_pdf_source_profile(file_size_bytes=7),
        extracted_text_gate=gate,
    )

    assert detail.status == FileStatus.INDEXED
    assert len(seen) == 1
    assert oracle.saved_extraction is not None


# --- job の結果の扱い -----------------------------------------------------------------------


class _DuplicateOracle(_LeaseOracle):
    """既定のレシピに有効な版が無い(初めての取込)文書と、本文の hash の記録を持つ fake。"""

    def __init__(self, *, canonical: str | None, duplicate_of: str | None = None) -> None:
        super().__init__(active=None)
        self.canonical = canonical
        self.duplicate_of = duplicate_of
        self.hash_calls: list[tuple[str, str, bool]] = []

    async def get_document(self, document_id: str) -> DocumentDetail:
        detail = await super().get_document(document_id)
        return detail.model_copy(update={"duplicate_of_document_id": self.duplicate_of})

    async def get_document_recipe(self, document_id: str, recipe_id: str) -> dict[str, object]:
        row = await super().get_document_recipe(document_id, recipe_id)
        return {**row, "slot_no": 1}

    async def record_extracted_text_hash(
        self, document_id: str, extracted_text_sha256: str, *, judge_duplicate: bool
    ) -> str | None:
        self.hash_calls.append((document_id, extracted_text_sha256, judge_duplicate))
        if self.canonical is not None and judge_duplicate:
            self.duplicate_of = self.canonical
            return self.canonical
        return None


class _GatePipeline:
    """抽出の後の確認を呼び、通ればレシピを確認待ち(REVIEW)にする fake の取込。"""

    gates: list[object] = []

    def __init__(self, *, oracle: _DuplicateOracle, **_kwargs: object) -> None:
        self._oracle = oracle
        self.deferred_success_outcome: object | None = None

    async def ingest(self, *args: object, **kwargs: object) -> None:
        _ = args
        gate = kwargs.get("extracted_text_gate")
        _GatePipeline.gates.append(gate)
        if callable(gate):
            await gate("合成の本文")
        self._oracle.recipe_status = FileStatus.REVIEW


async def _run_extract_job(
    monkeypatch: pytest.MonkeyPatch, oracle: _DuplicateOracle
) -> list[object]:
    install_language_release_stub(monkeypatch)
    await ObjectStorageClient().put("prepared/policy.pdf", b"prepared pdf bytes", "application/pdf")
    _GatePipeline.gates = []
    monkeypatch.setattr(documents_route, "IngestionPipeline", _GatePipeline)
    monkeypatch.setattr(documents_route, "get_settings", lambda: Settings())
    monkeypatch.setattr(documents_route, "OracleClient", lambda: oracle)
    monkeypatch.setattr(documents_route, "_dispatch_ingestion_job", lambda *a, **k: None)
    oracle.jobs["job-1"] = _extract_job("job-1")
    # 失敗ではないので、propagate_errors でも例外にしない。
    await documents_route._run_ingestion_job("job-1", propagate_errors=True, lease_owner="worker-a")
    return _GatePipeline.gates


async def test_duplicate_extracted_text_skips_job_like_upload_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oracle = _DuplicateOracle(canonical="doc-source")

    await _run_extract_job(monkeypatch, oracle)

    job = oracle.jobs["job-1"]
    assert job.status == IngestionJobStatus.SKIPPED
    assert job.skip_reason == DUPLICATE_CONTENT_SKIP_REASON
    assert job.error_message is None
    assert oracle.hash_calls == [("doc-1", extracted_text_sha256("合成の本文"), True)]
    assert oracle.duplicate_of == "doc-source"
    # 索引を作らず、レシピは解析の前(処理後ファイルのある状態)に戻る。
    assert oracle.activated == []
    assert oracle.recipe_statuses[-1] == FileStatus.PREPROCESSED


async def test_unique_extracted_text_continues_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = _DuplicateOracle(canonical=None)

    await _run_extract_job(monkeypatch, oracle)

    assert oracle.jobs["job-1"].status == IngestionJobStatus.SUCCEEDED
    assert oracle.jobs["job-1"].skip_reason is None
    assert len(oracle.hash_calls) == 1
    assert oracle.duplicate_of is None


async def test_document_already_marked_duplicate_is_not_checked_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重複を無視して取り込む操作(force)は、本文の確認で止めない。"""
    oracle = _DuplicateOracle(canonical="doc-other", duplicate_of="doc-source")

    gates = await _run_extract_job(monkeypatch, oracle)

    assert gates == [None]
    assert oracle.hash_calls == []
    assert oracle.jobs["job-1"].status == IngestionJobStatus.SUCCEEDED


# --- SQL の条件 -----------------------------------------------------------------------------


def _client(pool: FakeOraclePool) -> OracleClient:
    return OracleClient(settings=_oci_settings(), pool=pool, db_call_runner=_run_inline)


async def test_record_without_judgement_only_writes_the_hash() -> None:
    pool = FakeOraclePool(execute_results=[[{"document_id": "doc-1"}]])

    assert (
        await _client(pool).record_extracted_text_hash("doc-1", "b" * 64, judge_duplicate=False)
        is None
    )

    statements = [" ".join(call.statement.split()) for call in pool.connection.calls]
    assert len(statements) == 2
    assert statements[0].startswith("SELECT document_id, duplicate_of_document_id")
    assert statements[0].endswith("FOR UPDATE")
    assert statements[1].startswith(
        "UPDATE rag_documents SET extracted_text_sha256 = :extracted_text_sha256"
    )
    assert pool.connection.calls[1].parameters["extracted_text_sha256"] == "b" * 64


async def test_record_with_judgement_points_to_earlier_canonical_in_shared_kb() -> None:
    pool = FakeOraclePool(
        execute_results=[[{"document_id": "doc-1"}], [{"document_id": "doc-source"}]]
    )

    canonical = await _client(pool).record_extracted_text_hash(
        "doc-1", "b" * 64, judge_duplicate=True
    )

    assert canonical == "doc-source"
    statements = [" ".join(call.statement.split()) for call in pool.connection.calls]
    assert len(statements) == 4
    query = statements[2]
    assert "canonical.extracted_text_sha256 = :extracted_text_sha256" in query
    assert "canonical.duplicate_of_document_id IS NULL" in query
    assert "canonical.superseded_by_document_id IS NULL" in query
    assert "canonical.uploaded_at < target.uploaded_at" in query
    assert "target_kb.knowledge_base_id = canonical_kb.knowledge_base_id" in query
    assert "FETCH FIRST 1 ROWS ONLY" in query
    assert statements[3].startswith(
        "UPDATE rag_documents SET duplicate_of_document_id = :duplicate_of_document_id"
    )
    assert "duplicate_of_document_id IS NULL" in statements[3]
    assert pool.connection.calls[3].parameters["duplicate_of_document_id"] == "doc-source"


async def test_record_does_not_judge_document_already_marked_duplicate() -> None:
    pool = FakeOraclePool(
        execute_results=[[{"document_id": "doc-1", "duplicate_of_document_id": "doc-source"}]]
    )

    assert (
        await _client(pool).record_extracted_text_hash("doc-1", "b" * 64, judge_duplicate=True)
        is None
    )
    assert len(pool.connection.calls) == 2


async def test_record_for_missing_document_writes_nothing() -> None:
    pool = FakeOraclePool(execute_results=[[]])

    assert (
        await _client(pool).record_extracted_text_hash("doc-1", "b" * 64, judge_duplicate=True)
        is None
    )
    assert len(pool.connection.calls) == 1


async def test_same_extracted_text_documents_groups_other_documents_by_hash() -> None:
    """品質評価の同一視（#5325）: hash のある文書の、同じ hash の別の文書を登録の順に返す。"""
    pool = FakeOraclePool(
        execute_results=[
            [
                {"document_id": "doc-1", "same_text_document_id": "doc-2"},
                {"document_id": "doc-1", "same_text_document_id": "doc-3"},
            ]
        ]
    )

    found = await _client(pool).same_extracted_text_document_ids(["doc-1", " ", "doc-1", "doc-4"])

    assert found == {"doc-1": ["doc-2", "doc-3"]}
    assert len(pool.connection.calls) == 1
    query = " ".join(pool.connection.calls[0].statement.split())
    assert "ON other.extracted_text_sha256 = base.extracted_text_sha256" in query
    assert "other.document_id <> base.document_id" in query
    assert "base.extracted_text_sha256 IS NOT NULL" in query
    assert "ORDER BY base.document_id, other.uploaded_at, other.document_id" in query
    assert {
        key: value
        for key, value in pool.connection.calls[0].parameters.items()
        if key.startswith("same_text_document_id")
    } == {"same_text_document_id_0": "doc-1", "same_text_document_id_1": "doc-4"}


async def test_same_extracted_text_documents_without_ids_does_not_query() -> None:
    pool = FakeOraclePool(execute_results=[])

    assert await _client(pool).same_extracted_text_document_ids([]) == {}
    assert pool.connection.calls == []
