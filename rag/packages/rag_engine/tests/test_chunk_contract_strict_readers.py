"""旧チャンク本文の拒否と、削除専用identityの境界を合成保存runで確認する。"""

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_engine.adapters.oracle.store import document_id_for_chunk_run
from rag_engine.chunking import storage
from rag_engine.chunking.service import ChunkingService
from rag_engine.models.contracts import ChunkRequest, ParsedDocument
from rag_engine.models.layout import LayoutRecord
from rag_engine.workflows.uploaded_files import delete_uploaded_file, list_uploaded_files


def _save(root: Path, run_id: str, *, legacy: bool = False, content: bytes = b"synthetic source", sha_less: bool = False):
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / "source.pdf").write_bytes(content)
    (folder / "viewer-data.json").write_text(json.dumps({"pdf_name": "synthetic.pdf", "source_page_count": 1}))
    record = LayoutRecord("element-a", "synthetic", 1, 1, [0, 0, 10, 10], "image_top_left", 10, 10, "Text", "合成の原文です。")
    result = storage.persist_batch(
        ChunkingService().chunk(ChunkRequest(ParsedDocument(run_id, "synthetic.pdf", (record,), page_count=1))), output_dir=root
    )
    if sha_less:
        result = replace(result, source_file_sha256="")
    payload = storage.chunk_payload(result)
    if legacy:
        contracts = {
            **payload["contracts"],
            "inquiry_chunk_metadata_schema_version": 1,
            "inquiry_profile_contract_hash": "synthetic-retired",
        }
        contracts.pop("fingerprint")
        contracts["fingerprint"] = hashlib.sha256(
            json.dumps(contracts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        payload["contracts"] = contracts
    Path(result.json_path).write_text(json.dumps(payload, ensure_ascii=False))
    return result


def _source(root: Path, content: bytes = b"synthetic source") -> Path:
    path = root / "synthetic.pdf"
    path.write_bytes(content)
    return path


@pytest.mark.parametrize("reader", ["id", "latest"])
def test_current_contract_still_reads(tmp_path: Path, reader: str) -> None:
    result = _save(tmp_path, "aaaaaa01")
    loaded = (
        storage.load_chunk_run_by_id(tmp_path, result.chunk_run_id)
        if reader == "id"
        else storage.load_latest_chunk_run(tmp_path, "aaaaaa01")
    )
    assert loaded is not None and loaded.chunk_run_id == result.chunk_run_id


@pytest.mark.parametrize("reader", ["id", "latest", "source"])
def test_legacy_semantic_contract_requires_rechunk(tmp_path: Path, reader: str) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True)
    with pytest.raises(ValueError, match="再チャンキング"):
        if reader == "id":
            storage.load_chunk_run_by_id(tmp_path, result.chunk_run_id)
        elif reader == "latest":
            storage.load_latest_chunk_run(tmp_path, "aaaaaa01")
        else:
            storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1)


def test_source_prefers_current_even_when_legacy_same_source_exists(tmp_path: Path) -> None:
    _save(tmp_path, "aaaaaa01", legacy=True)
    current = _save(tmp_path, "aaaaaa02")
    result = storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1)
    assert result is not None and result.chunk_run_id == current.chunk_run_id


@pytest.mark.parametrize("pointer_identity", [False, True])
def test_unrelated_legacy_does_not_block_source(tmp_path: Path, pointer_identity: bool) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True, content=b"other synthetic source")
    if pointer_identity:
        path = Path(result.latest_path)
        pointer = json.loads(path.read_text())
        pointer.update(source_file_sha256=result.source_file_sha256, source_page_count=1, source_file_name="synthetic.pdf")
        path.write_text(json.dumps(pointer))
    assert storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1) is None


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("sha_less", [False, True])
def test_deletion_uses_stable_identity_without_restoring_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool, sha_less: bool
) -> None:
    monkeypatch.setattr("rag_engine.workflows.uploaded_files.tempfile.gettempdir", lambda: str(tmp_path))
    result = _save(tmp_path / "runs", "aaaaaa01", legacy=legacy, sha_less=sha_less)
    uploaded = list_uploaded_files(tmp_path / "runs")[0]
    calls = []

    def delete(_settings, **kwargs):
        calls.append(kwargs)
        return 1

    removed = delete_uploaded_file(tmp_path / "runs", uploaded, SimpleNamespace(adb_settings=object()), delete_documents=delete)
    assert removed.database_deleted == 1
    assert calls[0]["document_ids"] == [document_id_for_chunk_run(result)]
    assert not (tmp_path / "runs" / "aaaaaa01").exists()


@pytest.mark.parametrize("change", ["source_name", "source_hash", "missing_identity", "wrong_run", "duplicate"])
def test_deletion_invalid_identity_keeps_local_and_never_calls_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    monkeypatch.setattr("rag_engine.workflows.uploaded_files.tempfile.gettempdir", lambda: str(tmp_path))
    root = tmp_path / "runs"
    result = _save(root, "aaaaaa01", legacy=True, sha_less=True)
    path = Path(result.json_path)
    payload = json.loads(path.read_text())
    if change == "source_name":
        payload["source_file_name"] = "other.pdf"
    elif change == "source_hash":
        payload["source_file_sha256"] = "f" * 64
    elif change == "missing_identity":
        payload["source_document_id"] = ""
    elif change == "wrong_run":
        payload["source_run_id"] = "bbbbbb01"
    else:
        target = root / "bbbbbb01" / "chunks" / result.chunk_run_id
        target.mkdir(parents=True)
        shutil.copyfile(path, target / "chunks.json")
    path.write_text(json.dumps(payload))
    uploaded = list_uploaded_files(root)[0]
    calls = []
    with pytest.raises(ValueError, match="識別|一致|複数"):
        delete_uploaded_file(
            root, uploaded, SimpleNamespace(adb_settings=object()), delete_documents=lambda *args, **kwargs: calls.append(kwargs)
        )
    assert not calls
    assert (root / "aaaaaa01" / "source.pdf").is_file()


def test_inactive_legacy_is_not_a_current_source_candidate(tmp_path: Path) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True)
    path = Path(result.json_path)
    payload = json.loads(path.read_text())
    payload["active"] = False
    path.write_text(json.dumps(payload))
    assert storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1) is None


def test_unknown_source_pointer_does_not_guess_by_name(tmp_path: Path) -> None:
    _save(tmp_path, "aaaaaa01", legacy=True, sha_less=True)
    assert storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1) is None


@pytest.mark.parametrize(
    "field,value", [("source_page_count", True), ("source_file_sha256", None), ("source_document_id", None), ("active", "false")]
)
def test_deletion_identity_types_are_not_coerced(tmp_path: Path, field: str, value: object) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True, sha_less=True)
    path = Path(result.json_path)
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="識別"):
        storage.load_chunk_run_source_identity(tmp_path, result.chunk_run_id)


def test_deletion_missing_saved_identity_does_not_guess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rag_engine.workflows.uploaded_files.tempfile.gettempdir", lambda: str(tmp_path))
    root = tmp_path / "runs"
    result = _save(root, "aaaaaa01", legacy=True, sha_less=True)
    Path(result.json_path).unlink()
    uploaded = list_uploaded_files(root)[0]
    calls = []
    with pytest.raises(ValueError, match="識別"):
        delete_uploaded_file(
            root, uploaded, SimpleNamespace(adb_settings=object()), delete_documents=lambda *args, **kwargs: calls.append(kwargs)
        )
    assert not calls
    assert (root / "aaaaaa01" / "source.pdf").is_file()


def test_partial_delete_retry_same_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rag_engine.workflows.uploaded_files.tempfile.gettempdir", lambda: str(tmp_path))
    root = tmp_path / "runs"
    _save(root, "aaaaaa01", legacy=True, sha_less=True)
    _save(root, "aaaaaa02", legacy=True, sha_less=True)
    uploaded = list_uploaded_files(root)[0]
    first, second = uploaded.run_ids
    original = shutil.rmtree
    failed = False

    def partial(path, **kwargs):
        nonlocal failed
        if Path(path) == root / second and not failed:
            failed = True
            raise OSError("合成のローカル削除失敗")
        return original(path, **kwargs)

    calls = []

    def delete(_settings, **kwargs):
        calls.append(kwargs)
        return 2 if len(calls) == 1 else 0

    monkeypatch.setattr("rag_engine.workflows.uploaded_files.shutil.rmtree", partial)
    with pytest.raises(OSError, match="合成"):
        delete_uploaded_file(root, uploaded, SimpleNamespace(adb_settings=object()), delete_documents=delete)
    assert not (root / first).exists() and (root / second).exists()
    result = delete_uploaded_file(root, uploaded, SimpleNamespace(adb_settings=object()), delete_documents=delete)
    assert result.run_count == 1 and result.database_deleted == 0
    assert not (root / second).exists()
    assert len(calls) == 2
    assert set(calls[1]["document_ids"]).issubset(calls[0]["document_ids"])


@pytest.mark.parametrize("version", [True, 1.0])
def test_identity_schema_version_is_an_integer(tmp_path: Path, version: object) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True)
    path = Path(result.json_path)
    payload = json.loads(path.read_text())
    # 値が一致するfloatでも識別契約の整数ではない。
    payload["schema_version"] = float(payload["schema_version"]) if isinstance(version, float) else version
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="識別"):
        storage.load_chunk_run_source_identity(tmp_path, result.chunk_run_id)


def test_sha_less_blank_document_identity_is_missing(tmp_path: Path) -> None:
    result = _save(tmp_path, "aaaaaa01", legacy=True, sha_less=True)
    path = Path(result.json_path)
    payload = json.loads(path.read_text())
    payload["source_document_id"] = "   "
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="識別"):
        storage.load_chunk_run_source_identity(tmp_path, result.chunk_run_id)


def test_cross_run_latest_pointer_is_not_a_source_binding(tmp_path: Path) -> None:
    first = _save(tmp_path, "aaaaaa01", legacy=True, content=b"unrelated source")
    other = _save(tmp_path, "bbbbbb01", legacy=True)
    pointer = json.loads(Path(first.latest_path).read_text())
    pointer["chunk_run_id"] = other.chunk_run_id
    Path(first.latest_path).write_text(json.dumps(pointer))
    target = Path(first.json_path).parent.parent / other.chunk_run_id
    target.mkdir()
    shutil.copyfile(other.json_path, target / "chunks.json")
    Path(other.json_path).unlink()
    assert storage.load_latest_chunk_run_for_source(tmp_path, _source(tmp_path), 1) is None


def test_same_name_other_content_is_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rag_engine.workflows.uploaded_files.tempfile.gettempdir", lambda: str(tmp_path))
    root = tmp_path / "runs"
    chosen = _save(root, "aaaaaa01", legacy=True, sha_less=True)
    other = _save(root, "bbbbbb01", content=b"different synthetic source")
    uploaded = next(item for item in list_uploaded_files(root) if "aaaaaa01" in item.run_ids)
    calls = []

    def delete(_settings, **kwargs):
        calls.append(kwargs)
        return 1

    delete_uploaded_file(root, uploaded, SimpleNamespace(adb_settings=object()), delete_documents=delete)
    assert calls[0]["document_ids"] == [document_id_for_chunk_run(chosen)]
    assert calls[0]["source_file_sha256"] != other.source_file_sha256
    assert (root / "bbbbbb01" / "source.pdf").is_file()
