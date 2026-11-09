"""形式別の既定（ファイル準備・解析エンジン・分割方式。#4732）。

全体の設定の層で、解決の順は「文書のレシピ > 形式別の既定 > 全体の既定」。アップロードのときに
レシピへ写さず、取り込み済みの文書は再処理するまで変わらない（再処理が要ることは drift で見える）。
"""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from rag_parser_core.structured_records import JSONL_RECORD_BOUNDARY

from app import config as app_config
from app.api.routes import documents as documents_route
from app.config import (
    FILE_TYPE_DEFAULTS_STORE,
    FileTypeDefaultEntry,
    Settings,
    get_settings,
)
from app.main import app
from app.rag.bulk_ingestion import _SettingsCache
from app.rag.file_type_defaults import (
    FILE_TYPE_EXTENSIONS,
    classify_file_type,
    file_type_default_issues,
    file_type_parser_options,
    file_type_preprocess_options,
)
from app.rag.kb_adapter_config import KbAdapterConfigError
from app.rag.source_profile import ACCEPTED_UPLOAD_EXTENSIONS
from app.rag.variant_keys import compute_chunk_set_id, compute_extraction_recipe_id
from app.schemas.document import DocumentProcessingConfig
from app.security.permissions import ROUTE_PERMISSIONS
from tests.security_support import enable_production_auth, login
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
PATH = "/api/settings/file-type-defaults"
HASH = "a" * 64


def _set_defaults(settings: Settings, **entries: dict[str, str | None]) -> None:
    settings._file_type_defaults = {
        name: FileTypeDefaultEntry.model_validate(value) for name, value in entries.items()
    }


@pytest.fixture(autouse=True)
def _clean_defaults() -> Iterator[None]:
    settings = get_settings()
    settings._file_type_defaults = {}
    FILE_TYPE_DEFAULTS_STORE.reset()
    yield
    settings._file_type_defaults = {}
    FILE_TYPE_DEFAULTS_STORE.reset()


@pytest.mark.parametrize(
    ("file_name", "content_type", "expected"),
    [
        ("a.PDF", "application/pdf", "pdf"),
        ("a.docx", None, "word"),
        ("a.doc", "application/octet-stream", "word"),
        ("a.pptx", None, "powerpoint"),
        ("a.ppt", None, "powerpoint"),
        ("a.xlsx", None, "excel"),
        ("a.xls", None, "excel"),
        ("a.csv", "text/csv", "csv"),
        ("a.md", None, "text"),
        ("a.markdown", None, "text"),
        ("a.txt", None, "text"),
        ("a.json", "application/json", "text"),
        ("a.htm", "text/html", "html"),
        ("a.jpeg", None, "image"),
        ("a.png", "image/png", "image"),
        # 拡張子の無い原本は content type で決める（パラメータ付きでも同じ）。
        ("noext", "application/pdf; charset=binary", "pdf"),
        (
            "noext",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "excel",
        ),
        ("noext", "text/csv", "csv"),
        ("noext", "text/plain", "text"),
        # 一覧に無い形式は全体の既定に従う。
        ("a.tsv", "text/tab-separated-values", None),
        ("a.docm", "application/vnd.ms-word.document.macroEnabled.12", None),
        ("a.log", "text/plain", None),
        ("noext", "application/octet-stream", None),
        ("noext", None, None),
    ],
)
def test_classify_file_type(file_name: str, content_type: str | None, expected: str | None) -> None:
    assert classify_file_type(file_name, content_type) == expected


def test_every_accepted_extension_belongs_to_exactly_one_file_type() -> None:
    listed = [ext for extensions in FILE_TYPE_EXTENSIONS.values() for ext in extensions]
    assert len(listed) == len(set(listed))
    assert set(listed) == ACCEPTED_UPLOAD_EXTENSIONS


def test_resolution_order_recipe_over_file_type_over_global() -> None:
    settings = get_settings()
    settings.rag_parser_adapter_backend = "docling"
    settings.rag_chunking_strategy = "small_to_big"
    settings.rag_preprocess_profile = "passthrough"
    _set_defaults(
        settings,
        word={
            "preprocess_profile": "office_to_pdf",
            "parser_adapter_backend": "unstructured",
        },
    )

    resolved, effective = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(parser_adapter_backend="docling"), file_type="word"
    )

    # レシピの明示 > 形式別の既定。
    assert resolved.rag_parser_adapter_backend == "docling"
    assert effective.parser_adapter_backend == "docling"
    # レシピが未設定なら形式別の既定。
    assert resolved.rag_preprocess_profile == "office_to_pdf"
    assert effective.preprocess_profile == "office_to_pdf"
    # 形式別の既定も未設定なら全体の既定。
    assert resolved.rag_chunking_strategy == "small_to_big"
    assert effective.chunking_strategy == "small_to_big"


def test_file_type_without_defaults_and_unlisted_format_follow_global() -> None:
    settings = get_settings()
    settings.rag_parser_adapter_backend = "docling"
    _set_defaults(settings, word={"parser_adapter_backend": "unstructured"})

    for file_type in ("excel", None):
        resolved, effective = documents_route._merge_document_processing_config(
            DocumentProcessingConfig(), file_type=file_type
        )
        assert resolved is settings
        assert effective.parser_adapter_backend == "docling"


def test_chunk_set_and_extraction_id_follow_the_file_type_layer() -> None:
    settings = get_settings()
    settings.rag_parser_adapter_backend = "docling"
    settings.rag_chunking_strategy = "small_to_big"

    before, _ = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(), file_type="pdf"
    )
    before_chunk_set_id = compute_chunk_set_id(HASH, before)
    before_extraction_id = compute_extraction_recipe_id(HASH, before)

    _set_defaults(
        settings,
        pdf={"parser_adapter_backend": "unstructured", "chunking_strategy": "page_level"},
    )
    after, effective = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(), file_type="pdf"
    )

    assert compute_chunk_set_id(HASH, after) != before_chunk_set_id
    assert compute_extraction_recipe_id(HASH, after) != before_extraction_id
    # snapshot の実効値にも新しい層が刻まれ、レシピの明示値は空のまま。
    snapshot = documents_route._processing_recipe_snapshot(DocumentProcessingConfig(), effective)
    assert snapshot["processing_config"] == {}
    effective_snapshot = snapshot["effective_processing_config"]
    assert isinstance(effective_snapshot, dict)
    assert effective_snapshot["parser_adapter_backend"] == "unstructured"
    assert effective_snapshot["chunking_strategy"] == "page_level"


def test_structured_records_row_uses_the_record_boundary_delimiter() -> None:
    settings = get_settings()
    settings.rag_chunk_delimiter = "\\n\\n"
    _set_defaults(
        settings,
        excel={
            "preprocess_profile": "excel_to_jsonl",
            "parser_adapter_backend": "structured_records_jsonl",
            "chunking_strategy": "fixed_delimiter",
        },
    )

    resolved, effective = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(), file_type="excel"
    )
    assert resolved.rag_chunk_delimiter == JSONL_RECORD_BOUNDARY
    assert effective.chunk_delimiter == JSONL_RECORD_BOUNDARY

    # レシピが分割符を決めていれば、それを優先する。
    explicit, _ = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(chunk_delimiter="---"), file_type="excel"
    )
    assert explicit.rag_chunk_delimiter == "---"


def test_external_parser_flag_is_enabled_when_the_row_selects_it() -> None:
    settings = get_settings()
    settings.rag_parser_unstructured_enabled = False
    _set_defaults(settings, html={"parser_adapter_backend": "unstructured"})

    resolved, _ = documents_route._merge_document_processing_config(
        DocumentProcessingConfig(), file_type="html"
    )
    assert resolved.rag_parser_unstructured_enabled is True


def test_options_only_list_what_the_type_supports() -> None:
    settings = get_settings()
    settings.rag_preprocess_enabled = True
    assert file_type_preprocess_options("excel", settings)[0] == ["passthrough", "excel_to_jsonl"]
    assert "office_to_pdf" not in file_type_preprocess_options("excel", settings)[0]
    assert file_type_preprocess_options("pdf", settings)[0] == ["passthrough"]

    assert file_type_parser_options("word", "word_to_jsonl", settings)[0] == [
        "structured_records_jsonl"
    ]
    word_passthrough = file_type_parser_options("word", "passthrough", settings)[0]
    assert "unstructured" in word_passthrough
    assert "docling" not in word_passthrough
    word_pdf = file_type_parser_options("word", "office_to_pdf", settings)[0]
    assert "docling" in word_pdf
    # テキストの一部の形式（.json）しか読めないエンジンはテキストの行に出さない。
    assert "xml_tree_json" not in file_type_parser_options("text", "passthrough", settings)[0]
    # 準備中のエンジンはどの行にも出さない。
    for file_type in FILE_TYPE_EXTENSIONS:
        options, _ = file_type_parser_options(file_type, "passthrough", settings)
        assert "mineru" not in options
        assert "oci_document_understanding" not in options


def test_preprocess_needing_the_service_is_reported_as_unavailable() -> None:
    settings = get_settings()
    settings.rag_preprocess_enabled = False
    options, unavailable = file_type_preprocess_options("word", settings)
    assert options == ["passthrough"]
    assert unavailable == ["office_to_pdf", "word_to_jsonl"]


@pytest.mark.parametrize(
    ("file_type", "entry", "needle"),
    [
        ("excel", {"preprocess_profile": "office_to_pdf"}, "Excel→JSONL"),
        ("pdf", {"preprocess_profile": "csv_to_jsonl"}, "ファイル準備"),
        (
            "excel",
            {"preprocess_profile": "excel_to_jsonl", "parser_adapter_backend": "docling"},
            "構造化レコード JSONL",
        ),
        ("csv", {"preprocess_profile": "csv_to_jsonl"}, "構造化レコード JSONL"),
        ("word", {"parser_adapter_backend": "docling"}, "Office→PDF"),
        ("html", {"parser_adapter_backend": "docling"}, "HTML"),
        ("text", {"parser_adapter_backend": "xml_tree_json"}, "テキスト"),
        ("pdf", {"parser_adapter_backend": "mineru"}, "MinerU"),
    ],
)
def test_invalid_combination_is_rejected_with_a_fix(
    file_type: str, entry: dict[str, str], needle: str
) -> None:
    resp = client.patch(PATH, json={"entries": {file_type: entry}})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    message = body.get("detail") or " ".join(body.get("error_messages") or [])
    assert needle in str(message)


def test_mineru_license_is_checked_independently_of_the_roadmap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import file_type_defaults as module

    monkeypatch.setattr(module, "is_roadmap_parser_adapter_backend", lambda _value: False)
    monkeypatch.setattr(module, "mineru_license_acknowledged", lambda _settings: False)
    issues = file_type_default_issues(
        "pdf",
        FileTypeDefaultEntry(parser_adapter_backend="mineru"),
        get_settings(),
    )
    assert any("ライセンス" in issue for issue in issues)


def test_valid_save_persists_atomically_and_reloads(tmp_path: Path) -> None:
    settings = get_settings()
    settings.rag_file_type_defaults_file = str(tmp_path / "file-type-defaults.json")

    resp = client.patch(
        PATH,
        json={
            "entries": {
                "word": {
                    "preprocess_profile": "office_to_pdf",
                    "parser_adapter_backend": "docling",
                },
                "excel": {
                    "preprocess_profile": "excel_to_jsonl",
                    "parser_adapter_backend": "structured_records_jsonl",
                    "chunking_strategy": "fixed_delimiter",
                },
                # すべて未設定の行は保存しない。
                "image": {},
            }
        },
    )
    assert resp.status_code == 200, resp.text
    path = tmp_path / "file-type-defaults.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document["entries"]) == {"word", "excel"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".*tmp*")) == []

    data = resp.json()["data"]
    assert [row["file_type"] for row in data["rows"]] == list(FILE_TYPE_EXTENSIONS)
    by_type = {row["file_type"]: row for row in data["rows"]}
    assert by_type["word"]["preprocess_profile"] == "office_to_pdf"
    assert by_type["image"]["parser_adapter_backend"] is None

    # 別の worker: メモリの値を消しても、mtime の変化で読み直す。
    settings._file_type_defaults = {}
    FILE_TYPE_DEFAULTS_STORE.reset()
    FILE_TYPE_DEFAULTS_STORE.reload_if_changed(settings)
    assert settings._file_type_defaults["word"].parser_adapter_backend == "docling"

    # 外から書き換えられたら次の読み取りで反映する。
    document["entries"]["word"]["parser_adapter_backend"] = "unstructured"
    document["entries"]["word"].pop("preprocess_profile")
    path.write_text(json.dumps(document), encoding="utf-8")
    FILE_TYPE_DEFAULTS_STORE.reset()
    assert client.get(PATH).json()["data"]["rows"][1]["parser_adapter_backend"] == "unstructured"

    # 空にすると全体の既定へ戻る。
    assert client.patch(PATH, json={"entries": {}}).status_code == 200
    assert settings._file_type_defaults == {}
    assert json.loads(path.read_text(encoding="utf-8"))["entries"] == {}


def test_broken_row_in_the_file_is_skipped(tmp_path: Path) -> None:
    settings = get_settings()
    path = tmp_path / "file-type-defaults.json"
    path.write_text(
        json.dumps(
            {
                "entries": {
                    "word": {"parser_adapter_backend": "removed_engine"},
                    "unknown_type": {"chunking_strategy": "page_level"},
                    "pdf": {"chunking_strategy": "page_level"},
                }
            }
        ),
        encoding="utf-8",
    )
    settings.rag_file_type_defaults_file = str(path)
    FILE_TYPE_DEFAULTS_STORE.load(settings)
    assert list(settings._file_type_defaults) == ["pdf"]


def test_get_reports_rows_and_global_defaults() -> None:
    settings = get_settings()
    settings.rag_preprocess_enabled = True
    settings.rag_parser_adapter_backend = "docling"
    settings.rag_preprocess_profile = "passthrough"
    settings.rag_chunking_strategy = "small_to_big"

    data = client.get(PATH).json()["data"]

    assert data["global_defaults"] == {
        "preprocess_profile": "passthrough",
        "parser_adapter_backend": "docling",
        "chunking_strategy": "small_to_big",
    }
    assert "fixed_delimiter" in data["chunking_options"]
    excel = next(row for row in data["rows"] if row["file_type"] == "excel")
    assert excel["extensions"] == [".xlsx", ".xls"]
    assert excel["preprocess_options"] == ["passthrough", "excel_to_jsonl"]
    assert excel["parser_options"]["excel_to_jsonl"] == ["structured_records_jsonl"]
    assert "unstructured" in excel["parser_options"]["passthrough"]


def test_settings_route_requires_its_own_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ROUTE_PERMISSIONS[("GET", "/settings/file-type-defaults")] == frozenset(
        {"menu.settings_file_type_defaults"}
    )
    assert ROUTE_PERMISSIONS[("PATCH", "/settings/file-type-defaults")] == frozenset(
        {"menu.settings_file_type_defaults"}
    )

    auth = enable_production_auth(monkeypatch)
    auth.user_with_permissions("allowed", ["menu.settings_file_type_defaults"])
    auth.user_with_permissions("denied", ["menu.settings_chunking"])
    allowed = login(client, "allowed")
    denied = login(client, "denied")

    assert client.get(PATH, headers=allowed).status_code == 200
    assert client.patch(PATH, json={"entries": {}}, headers=allowed).status_code == 200
    assert client.get(PATH, headers=denied).status_code == 403
    assert client.patch(PATH, json={"entries": {}}, headers=denied).status_code == 403


def _recipe_row(**overrides: Any) -> dict[str, object]:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    return {
        "recipe_id": "recipe-a",
        "document_id": "doc-a",
        "document_file_name": "synthetic.docx",
        "document_content_type": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        "slot_no": 1,
        "processing_config": {},
        "status": "INDEXED",
        "config_revision": 1,
        "materialized_revision": 1,
        "active_chunk_set_id": "chunk-set-a",
        "chunk_set_status": "INDEXED",
        "chunk_count": 3,
        "vector_count": 3,
        "created_at": now,
        "updated_at": now,
        **overrides,
    }


def _view(row: dict[str, object]) -> Any:
    class Unused:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"oracle.{name} は呼ばない")

    return asyncio.run(
        documents_route._document_recipe_view(Unused(), row, document_jobs=[], queue_positions={})  # type: ignore[arg-type]
    )


def _snapshot(**effective: object) -> dict[str, object]:
    return {
        "processing_config": {},
        "effective_processing_config": {
            "preprocess_profile": "passthrough",
            "parser_adapter_backend": "docling",
            "chunking_strategy": "small_to_big",
            **effective,
        },
    }


def test_recipe_view_uses_file_type_layer_and_shows_drift_after_change() -> None:
    settings = get_settings()
    settings.rag_parser_adapter_backend = "docling"
    settings.rag_preprocess_profile = "passthrough"
    settings.rag_chunking_strategy = "small_to_big"

    # 同じ設定で作った文書は再処理不要。
    row = _recipe_row(active_recipe_subset=_snapshot())
    assert _view(row).needs_reprocessing is False

    # 形式別の既定を変えると、実効値が変わり、再処理が必要なことが見える。
    _set_defaults(settings, word={"parser_adapter_backend": "unstructured"})
    view = _view(row)
    assert view.effective_processing_config.parser_adapter_backend == "unstructured"
    assert view.processing_config.parser_adapter_backend is None
    assert view.needs_reprocessing is True

    # 別の種類の文書には影響しない。
    other = _recipe_row(
        document_file_name="synthetic.pdf",
        document_content_type="application/pdf",
        active_recipe_subset=_snapshot(),
    )
    assert _view(other).needs_reprocessing is False


def test_recipe_view_without_snapshot_field_is_not_flagged() -> None:
    settings = get_settings()
    _set_defaults(settings, word={"parser_adapter_backend": "unstructured"})

    # 取り込み前（active の chunk_set が無い）は再処理の対象ではない。
    assert (
        _view(_recipe_row(active_chunk_set_id=None, chunk_set_status=None)).needs_reprocessing
        is False
    )
    # 実効値を持たない旧い snapshot は比べない（誤って再処理を促さない）。
    assert _view(_recipe_row(active_recipe_subset={})).needs_reprocessing is False


def test_recipe_revision_mismatch_still_flags_reprocessing() -> None:
    assert _view(_recipe_row(materialized_revision=2, config_revision=3)).needs_reprocessing is True


def test_explicit_recipe_value_ignores_file_type_change() -> None:
    settings = get_settings()
    settings.rag_parser_adapter_backend = "docling"
    row = _recipe_row(
        processing_config={"parser_adapter_backend": "docling"},
        active_recipe_subset=_snapshot(),
    )
    _set_defaults(settings, word={"parser_adapter_backend": "unstructured"})

    assert _view(row).needs_reprocessing is False


def test_bulk_settings_cache_separates_file_types() -> None:
    calls: list[tuple[object, object]] = []
    settings = get_settings()

    def resolve(config: DocumentProcessingConfig, file_type: Any) -> Settings:
        calls.append((config, file_type))
        return settings

    cache = _SettingsCache(resolve)
    cache.get({}, "word")
    cache.get({}, "word")
    cache.get({}, "excel")
    cache.get({}, None)

    assert [file_type for _config, file_type in calls] == ["word", "excel", None]


def test_settings_file_defaults_next_to_the_backend_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_config, "BACKEND_ENV_FILE", tmp_path / "backend" / ".env")
    assert (
        app_config.resolve_file_type_defaults_file("file-type-defaults.json")
        == (tmp_path / "backend" / "file-type-defaults.json").resolve()
    )


def test_inconsistent_chunking_in_recipe_is_still_rejected() -> None:
    """種類の層を足しても、レシピの chunk パラメータの検証は変えない。"""
    with pytest.raises(KbAdapterConfigError):
        documents_route._merge_document_processing_config(
            DocumentProcessingConfig(chunk_size=500, chunk_overlap=500),
            file_type="pdf",
        )
