"""設定 API のテスト。"""

import json
import stat
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

from pytest import MonkeyPatch

from app.api.routes import settings as settings_routes
from app.clients.oracle import OracleConnectionTimeoutError, OracleWalletPasswordRequiredError
from app.config import Settings, get_settings, load_persisted_model_settings
from app.main import app
from app.rag import parser_adapter_readiness
from app.schemas.settings import EnterpriseAiModelSettings
from tests.support import AsgiTestClient

client = AsgiTestClient(app)
LLM_TEMPLATE = '{"input":"${user_message}"}'
VLM_TEMPLATE = '{"input":"${data_base64}"}'


async def _run_inline(operation: Any) -> Any:
    """テスト用 fake SDK 呼び出しをスレッドへ逃がさず同期実行する。"""
    return operation()


def test_model_settings_vision_test_image_is_valid_jpeg() -> None:
    """Vision モデルの接続テストには provider が受理できる JPEG を使う。"""
    data = settings_routes.MODEL_TEST_IMAGE_BYTES

    assert data.startswith(b"\xff\xd8")
    assert len(data) > 1024


def test_parser_adapter_settings_reports_flags_and_package_status(
    monkeypatch: MonkeyPatch,
) -> None:
    """adapter readiness は flag と Python package 有無を非機密に返す。"""
    settings = get_settings()
    # 旧 runtime 値 auto は利用者向けには返さず、明示値 local へ寄せる。
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "auto")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unlimited_ocr_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_mineru_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_dots_ocr_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_glm_ocr_enabled", False)

    def package_info(
        import_name: str,
        _distribution_names: Sequence[str],
    ) -> tuple[bool, str | None, str | None]:
        return (
            import_name == "docling",
            "1.2.3" if import_name == "docling" else None,
            "docling" if import_name == "docling" else None,
        )

    monkeypatch.setattr(parser_adapter_readiness, "_package_info", package_info)

    resp = client.get("/api/settings/parser-adapters")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["adapter_backend"] == "local"
    assert body["effective_order"] == []
    by_backend = {adapter["backend"]: adapter for adapter in body["adapters"]}
    assert by_backend["docling"]["status"] == "ignored"
    assert by_backend["docling"]["import_name"] == "docling"
    assert by_backend["docling"]["distribution_name"] == "docling"
    assert by_backend["docling"]["install_package"] == "docling==2.103.0"
    assert by_backend["docling"]["version"] == "1.2.3"
    assert by_backend["marker"]["status"] == "ignored"
    assert by_backend["marker"]["install_package"] == "marker-pdf[full]==1.10.2"
    assert by_backend["marker"]["warning_code"] == "adapter_flag_ignored_by_backend"
    assert by_backend["unstructured"]["install_package"] == "unstructured[all-docs]==0.23.1"
    assert by_backend["unstructured"]["status"] == "disabled"
    assert by_backend["unlimited_ocr"]["install_package"].startswith("transformers")
    assert by_backend["unlimited_ocr"]["status"] == "disabled"
    assert by_backend["mineru"]["install_package"] == "mineru[core]==3.4.0"
    assert by_backend["mineru"]["status"] == "disabled"
    assert by_backend["dots_ocr"]["status"] == "disabled"
    assert by_backend["glm_ocr"]["status"] == "disabled"
    assert body["scorecard"]["selected_backend"] == "local"
    assert body["scorecard"]["recommended_backend"] == "local"
    score_by_backend = {entry["backend"]: entry for entry in body["scorecard"]["entries"]}
    assert score_by_backend["local"]["recommended"] is True
    assert score_by_backend["marker"]["warning_codes"] == ["adapter_flag_ignored_by_backend"]
    route_by_kind = {route["source_kind"]: route for route in body["source_routes"]}
    assert route_by_kind["pdf"]["candidate_order"] == [
        "docling",
        "marker",
        "unstructured",
        "unlimited_ocr",
        "mineru",
        "glm_ocr",
    ]
    assert route_by_kind["pdf"]["attempted_order"] == []
    assert route_by_kind["pdf"]["selected_backend"] == "local"
    assert route_by_kind["email"]["selected_backend"] == "local"
    assert "unstructured_adapter_feature_flag_disabled" in route_by_kind["email"]["warning_codes"]
    matrix = body["backend_source_kind_matrix"]
    assert matrix["evidence_source"] == "runtime_routes"
    assert "pdf" in matrix["backend_source_kinds"]["local"]
    assert "email" in matrix["backend_source_kinds"]["local"]
    assert "audio" in matrix["backend_source_kinds"]["local"]
    assert matrix["missing_source_kinds"] == []
    assert "raw_text" not in str(matrix)


def test_parser_adapter_settings_reports_service_backends(
    monkeypatch: MonkeyPatch,
) -> None:
    """service 系 backend(VLM 明示 / OCI Document Understanding)の選択・可用性を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "enterprise_ai_vlm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://ea.example.com")
    monkeypatch.setattr(settings, "oci_compartment_id", "")
    monkeypatch.setattr(settings, "oci_document_understanding_compartment_id", "")
    monkeypatch.setattr(settings, "oci_document_understanding_namespace", "")
    monkeypatch.setattr(settings, "oci_document_understanding_input_bucket", "")
    monkeypatch.setattr(settings, "object_storage_namespace", "")
    monkeypatch.setattr(settings, "object_storage_bucket", "")

    resp = client.get("/api/settings/parser-adapters")

    assert resp.status_code == 200
    body = resp.json()["data"]
    # 旧称 enterprise_ai_vlm を保存値にしても、canonical な oci_genai_vision service
    # backend が選択状態として返る(後方互換エイリアス)。
    assert body["adapter_backend"] == "enterprise_ai_vlm"
    service = {item["backend"]: item for item in body["service_backends"]}
    assert service["oci_genai_vision"]["selected"] is True
    assert service["oci_genai_vision"]["configured"] is True
    assert service["oci_document_understanding"]["selected"] is False
    assert service["oci_document_understanding"]["configured"] is False
    assert (
        service["oci_document_understanding"]["warning_code"]
        == "oci_document_understanding_unconfigured"
    )


def test_parser_adapter_contract_endpoint_reports_non_sensitive_matrix(
    monkeypatch: MonkeyPatch,
) -> None:
    """adapter contract endpoint は fixture 本文なしで実行証跡を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "local")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )

    resp = client.get("/api/settings/parser-adapters/contract")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["passed"] is True
    assert body["case_count"] == len(body["cases"])
    assert body["blocking_failure_count"] == 0
    assert body["summary"]["case_count"] == body["case_count"]
    assert body["summary"]["blocking_failure_count"] == 0
    assert body["summary"]["backend_source_status"]["docling"]["pdf"] == "disabled"
    assert body["summary"]["source_kind_status_counts"]["pdf"]["disabled"] > 0
    assert body["summary"]["missing_source_kinds"]
    assert body["summary"]["blocking_failure_source_kinds"] == []
    assert body["summary"]["blocking_failure_backends"] == []
    assert body["summary"]["reason_code_counts"]["adapter_disabled"] > 0
    assert body["summary"]["warning_code_counts"] == {}
    assert body["summary"]["blocking_failure_reason_counts"] == {}
    assert all(case["blocking"] is False for case in body["cases"])
    assert body["fixture_root"].startswith("fixture_root:")
    assert all(":" in case["fixture_name"] for case in body["cases"])
    assert body["config_source"] == "runtime"
    assert "raw_text" not in resp.text
    assert "policy-ja.pdf" not in resp.text
    assert "file-processing-fixtures" not in resp.text


def test_parser_adapter_contract_endpoint_blocks_enabled_missing_adapter(
    monkeypatch: MonkeyPatch,
) -> None:
    """有効化された adapter package がない場合は contract summary で失敗を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "docling")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )

    resp = client.get("/api/settings/parser-adapters/contract")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["passed"] is False
    assert body["blocking_failure_count"] > 0
    assert body["summary"]["blocking_failure_count"] == body["blocking_failure_count"]
    assert body["summary"]["backend_status_counts"]["docling"]["missing"] > 0
    assert "pdf" in body["summary"]["blocking_failure_source_kinds"]
    assert body["summary"]["blocking_failure_backends"] == ["docling"]
    assert body["summary"]["warning_code_counts"]["adapter_package_missing"] > 0
    assert body["summary"]["blocking_failure_reason_counts"]["adapter_missing"] > 0
    assert any(
        failure["backend"] == "docling" and failure["status"] == "missing"
        for failure in body["summary"]["blocking_failures"]
    )
    assert "adapter_package_missing" in resp.text


def test_parser_adapter_settings_reports_marker_distribution_name(
    monkeypatch: MonkeyPatch,
) -> None:
    """Marker は import 名 marker と配布 package marker-pdf を分けて表示する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "marker")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)

    def package_info(
        import_name: str,
        distribution_names: Sequence[str],
    ) -> tuple[bool, str | None, str | None]:
        if import_name != "marker":
            return False, None, None
        assert import_name == "marker"
        assert tuple(distribution_names) == ("marker-pdf", "marker")
        return True, "5.0.0", "marker-pdf"

    monkeypatch.setattr(parser_adapter_readiness, "_package_info", package_info)

    resp = client.get("/api/settings/parser-adapters")

    assert resp.status_code == 200
    body = resp.json()["data"]
    marker = {adapter["backend"]: adapter for adapter in body["adapters"]}["marker"]
    assert marker["status"] == "active"
    assert marker["package_name"] == "marker"
    assert marker["import_name"] == "marker"
    assert marker["distribution_name"] == "marker-pdf"
    assert marker["install_package"] == "marker-pdf[full]==1.10.2"
    assert marker["version"] == "5.0.0"


def test_parser_adapter_settings_explicit_backend_requires_feature_flag(
    monkeypatch: MonkeyPatch,
) -> None:
    """backend を明示しても flag が false なら実行順から外し警告する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "marker")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )

    resp = client.get("/api/settings/parser-adapters")

    assert resp.status_code == 200
    body = resp.json()["data"]
    by_backend = {adapter["backend"]: adapter for adapter in body["adapters"]}
    assert body["adapter_backend"] == "marker"
    assert body["effective_order"] == []
    assert by_backend["marker"]["selected"] is True
    assert by_backend["marker"]["enabled"] is False
    assert by_backend["marker"]["status"] == "disabled"
    assert by_backend["marker"]["warning_code"] == "adapter_feature_flag_disabled"
    assert by_backend["docling"]["status"] == "ignored"


def test_update_parser_adapter_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """parser adapter 設定は .env と現在プロセスの ingestion 設定へ反映する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "local")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# Parser adapters",
                "RAG_PARSER_ADAPTER_BACKEND=local",
                "RAG_PARSER_DOCLING_ENABLED=false",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "auto",
            "docling_enabled": True,
            "marker_enabled": False,
            "unstructured_enabled": True,
        },
    )

    assert resp.status_code == 422
    assert settings.rag_parser_adapter_backend == "local"
    assert settings.rag_parser_docling_enabled is False
    assert settings.rag_parser_marker_enabled is False
    assert settings.rag_parser_unstructured_enabled is False
    persisted = env_file.read_text(encoding="utf-8")
    assert "RAG_PARSER_ADAPTER_BACKEND=local" in persisted
    assert "RAG_PARSER_DOCLING_ENABLED=false" in persisted
    assert "RAG_PARSER_UNSTRUCTURED_ENABLED=true" not in persisted


def test_update_parser_adapter_settings_persists_gpu_flags_and_preserves_omitted_flags(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """GPU parser flags も保存し、省略された既存 flag は維持する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "local")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unlimited_ocr_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_mineru_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_dots_ocr_enabled", True)
    monkeypatch.setattr(settings, "rag_parser_glm_ocr_enabled", False)
    monkeypatch.setattr(
        parser_adapter_readiness,
        "_package_info",
        lambda *_args: (False, None, None),
    )
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# Parser adapters",
                "RAG_PARSER_ADAPTER_BACKEND=local",
                "RAG_PARSER_DOCLING_ENABLED=true",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "mineru",
            "unlimited_ocr_enabled": True,
            "mineru_enabled": True,
            "dots_ocr_enabled": False,
            "glm_ocr_enabled": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["adapter_backend"] == "mineru"
    assert body["effective_order"] == ["mineru"]
    by_backend = {adapter["backend"]: adapter for adapter in body["adapters"]}
    assert by_backend["docling"]["enabled"] is True
    assert by_backend["unlimited_ocr"]["enabled"] is True
    assert by_backend["mineru"]["enabled"] is True
    assert by_backend["dots_ocr"]["enabled"] is False
    assert by_backend["glm_ocr"]["enabled"] is True
    assert settings.rag_parser_adapter_backend == "mineru"
    assert settings.rag_parser_docling_enabled is True
    assert settings.rag_parser_unlimited_ocr_enabled is True
    assert settings.rag_parser_mineru_enabled is True
    assert settings.rag_parser_dots_ocr_enabled is False
    assert settings.rag_parser_glm_ocr_enabled is True
    persisted = env_file.read_text(encoding="utf-8")
    assert "RAG_PARSER_ADAPTER_BACKEND=mineru" in persisted
    assert "RAG_PARSER_DOCLING_ENABLED=true" in persisted
    assert "RAG_PARSER_UNLIMITED_OCR_ENABLED=true" in persisted
    assert "RAG_PARSER_MINERU_ENABLED=true" in persisted
    assert "RAG_PARSER_DOTS_OCR_ENABLED=false" in persisted
    assert "RAG_PARSER_GLM_OCR_ENABLED=true" in persisted


def test_update_parser_adapter_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """parser adapter 設定保存に失敗したら runtime を変更しない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_parser_adapter_backend", "local")
    monkeypatch.setattr(settings, "rag_parser_docling_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_marker_enabled", False)
    monkeypatch.setattr(settings, "rag_parser_unstructured_enabled", False)
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "docling",
            "docling_enabled": True,
            "marker_enabled": True,
            "unstructured_enabled": True,
        },
    )

    assert resp.status_code == 500
    assert settings.rag_parser_adapter_backend == "local"
    assert settings.rag_parser_docling_enabled is False
    assert settings.rag_parser_marker_enabled is False
    assert settings.rag_parser_unstructured_enabled is False


def test_update_parser_adapter_settings_rejects_unknown_backend() -> None:
    resp = client.patch(
        "/api/settings/parser-adapters",
        json={
            "adapter_backend": "llama_parse",
            "docling_enabled": True,
            "marker_enabled": False,
            "unstructured_enabled": False,
        },
    )

    assert resp.status_code == 422


def test_preprocess_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    """前処理設定 API は選択プロファイル・サービス状態・プロファイル一覧を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_preprocess_profile", "office_to_pdf")
    monkeypatch.setattr(settings, "rag_preprocess_enabled", False)

    resp = client.get("/api/settings/preprocess")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "office_to_pdf"
    assert body["service_enabled"] is False
    assert body["config_source"] == "runtime"
    names = [item["name"] for item in body["profiles"]]
    assert names == [
        "passthrough",
        "office_to_pdf",
        "pdf_to_page_images",
        "csv_to_json",
        "excel_to_json",
        "url_to_markdown",
        "image_enhance",
        "pii_redact",
    ]
    by_name = {item["name"]: item for item in body["profiles"]}
    assert by_name["office_to_pdf"]["available"] is False
    selected = [item["name"] for item in body["profiles"] if item["selected"]]
    assert selected == ["office_to_pdf"]


def test_update_preprocess_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_preprocess_profile", "passthrough")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/preprocess", json={"profile": "office_to_pdf"})

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "office_to_pdf"
    assert settings.rag_preprocess_profile == "office_to_pdf"
    assert "RAG_PREPROCESS_PROFILE=office_to_pdf" in env_file.read_text(encoding="utf-8")


def test_update_preprocess_settings_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/preprocess", json={"profile": "ocr_magic"})
    assert resp.status_code == 422


def test_chunking_settings_reports_runtime_strategy_and_params(
    monkeypatch: MonkeyPatch,
) -> None:
    """Chunking 設定 API は選択戦略・パラメータ・戦略一覧を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_chunking_strategy", "sentence_window")
    monkeypatch.setattr(settings, "rag_chunk_size", 900)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 150)
    monkeypatch.setattr(settings, "rag_chunk_child_size", 280)
    monkeypatch.setattr(settings, "rag_chunk_sentence_window_size", 4)
    monkeypatch.setattr(settings, "rag_chunk_min_chars", 50)
    monkeypatch.setattr(settings, "rag_chunk_delimiter", "\\n---\\n")

    resp = client.get("/api/settings/chunking")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["strategy"] == "sentence_window"
    assert body["chunk_size"] == 900
    assert body["overlap"] == 150
    assert body["child_size"] == 280
    assert body["sentence_window_size"] == 4
    assert body["min_chars"] == 50
    assert body["delimiter"] == "\\n---\\n"
    assert body["config_source"] == "runtime"
    names = [item["name"] for item in body["strategies"]]
    assert names == [
        "structure_aware",
        "recursive_character",
        "sentence_window",
        "hierarchical_parent_child",
        "markdown_heading",
        "page_level",
        "fixed_size",
        "fixed_delimiter",
    ]
    selected = [item["name"] for item in body["strategies"] if item["selected"]]
    assert selected == ["sentence_window"]


def test_update_chunking_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chunking 設定は .env と現在プロセスの取込設定へ反映する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_chunking_strategy", "structure_aware")
    monkeypatch.setattr(settings, "rag_chunk_size", 800)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 120)
    monkeypatch.setattr(settings, "rag_chunk_child_size", 320)
    monkeypatch.setattr(settings, "rag_chunk_sentence_window_size", 3)
    monkeypatch.setattr(settings, "rag_chunk_min_chars", 0)
    monkeypatch.setattr(settings, "rag_chunk_delimiter", "\\n\\n")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "hierarchical_parent_child",
            "chunk_size": 1000,
            "overlap": 100,
            "child_size": 300,
            "sentence_window_size": 5,
            "min_chars": 40,
            "delimiter": "---",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["strategy"] == "hierarchical_parent_child"
    assert body["chunk_size"] == 1000
    assert settings.rag_chunking_strategy == "hierarchical_parent_child"
    assert settings.rag_chunk_size == 1000
    assert settings.rag_chunk_child_size == 300
    assert settings.rag_chunk_min_chars == 40
    assert settings.rag_chunk_delimiter == "---"
    persisted = env_file.read_text(encoding="utf-8")
    assert "RAG_CHUNKING_STRATEGY=hierarchical_parent_child" in persisted
    assert "RAG_CHUNK_SIZE=1000" in persisted
    assert "RAG_CHUNK_CHILD_SIZE=300" in persisted
    assert "RAG_CHUNK_MIN_CHARS=40" in persisted
    assert "RAG_CHUNK_DELIMITER=---" in persisted


def test_update_chunking_settings_rejects_unknown_strategy() -> None:
    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "semantic_double_pass",
            "chunk_size": 800,
            "overlap": 120,
            "child_size": 320,
            "sentence_window_size": 3,
            "min_chars": 0,
        },
    )

    assert resp.status_code == 422


def test_update_chunking_settings_rejects_overlap_not_smaller_than_size() -> None:
    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "structure_aware",
            "chunk_size": 400,
            "overlap": 400,
            "child_size": 320,
            "sentence_window_size": 3,
            "min_chars": 0,
        },
    )

    assert resp.status_code == 422


def test_update_chunking_settings_ignores_non_applicable_bounds_for_fixed_size(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """固定長は child_size / min_chars の cross-field bounds を使わない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_chunking_strategy", "structure_aware")
    monkeypatch.setattr(settings, "rag_chunk_size", 800)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 120)
    monkeypatch.setattr(settings, "rag_chunk_child_size", 320)
    monkeypatch.setattr(settings, "rag_chunk_sentence_window_size", 3)
    monkeypatch.setattr(settings, "rag_chunk_min_chars", 0)
    monkeypatch.setattr(settings, "rag_chunk_delimiter", "\\n\\n")
    _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "fixed_size",
            "chunk_size": 400,
            "overlap": 120,
            "child_size": 400,
            "sentence_window_size": 3,
            "min_chars": 400,
            "delimiter": "\\n\\n",
        },
    )

    assert resp.status_code == 200
    assert settings.rag_chunking_strategy == "fixed_size"


def test_update_chunking_settings_rejects_child_size_for_parent_child() -> None:
    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "hierarchical_parent_child",
            "chunk_size": 400,
            "overlap": 120,
            "child_size": 400,
            "sentence_window_size": 3,
            "min_chars": 0,
            "delimiter": "\\n\\n",
        },
    )

    assert resp.status_code == 422


def test_update_chunking_settings_allows_fixed_delimiter_without_chunk_bounds(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_chunking_strategy", "structure_aware")
    monkeypatch.setattr(settings, "rag_chunk_size", 800)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 120)
    monkeypatch.setattr(settings, "rag_chunk_child_size", 320)
    monkeypatch.setattr(settings, "rag_chunk_sentence_window_size", 3)
    monkeypatch.setattr(settings, "rag_chunk_min_chars", 0)
    monkeypatch.setattr(settings, "rag_chunk_delimiter", "\\n\\n")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/chunking",
        json={
            "strategy": "fixed_delimiter",
            "chunk_size": 400,
            "overlap": 400,
            "child_size": 400,
            "sentence_window_size": 3,
            "min_chars": 400,
            "delimiter": "---",
        },
    )

    assert resp.status_code == 200
    assert settings.rag_chunking_strategy == "fixed_delimiter"
    assert "RAG_CHUNK_DELIMITER=---" in env_file.read_text(encoding="utf-8")


def test_retrieval_settings_reports_runtime_strategy(monkeypatch: MonkeyPatch) -> None:
    """Retrieval 設定 API は選択戦略・解決手法・戦略一覧を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_retrieval_strategy", "business_context_strict")

    resp = client.get("/api/settings/retrieval")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["strategy"] == "business_context_strict"
    assert body["gap_stop"] is True
    assert body["business_fit_weighting"] is True
    names = [item["name"] for item in body["strategies"]]
    assert names[0] == "hybrid_rrf"
    assert "corrective_multi_query" in names
    selected = [item["name"] for item in body["strategies"] if item["selected"]]
    assert selected == ["business_context_strict"]


def test_update_retrieval_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_retrieval_strategy", "hybrid_rrf")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/retrieval", json={"strategy": "corrective_multi_query"})

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["strategy"] == "corrective_multi_query"
    assert body["corrective_retrieval"] is True
    assert settings.rag_retrieval_strategy == "corrective_multi_query"
    assert "RAG_RETRIEVAL_STRATEGY=corrective_multi_query" in env_file.read_text(encoding="utf-8")


def test_update_retrieval_settings_rejects_unknown_strategy() -> None:
    resp = client.patch("/api/settings/retrieval", json={"strategy": "hyde_fusion"})
    assert resp.status_code == 422


def test_grounding_settings_reports_runtime_pipeline(monkeypatch: MonkeyPatch) -> None:
    """Grounding 設定 API は選択プリセット・解決段・プリセット一覧を返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_post_retrieval_pipeline", "full_governed")

    resp = client.get("/api/settings/grounding")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["pipeline"] == "full_governed"
    assert body["dependency_promotion_enabled"] is True
    assert body["expansion_mode"] == "adaptive"
    assert body["compression_enabled"] is True
    names = [item["name"] for item in body["pipelines"]]
    assert names[0] == "custom"
    selected = [item["name"] for item in body["pipelines"] if item["selected"]]
    assert selected == ["full_governed"]


def test_update_grounding_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_post_retrieval_pipeline", "custom")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/grounding", json={"pipeline": "verified_context"})

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["pipeline"] == "verified_context"
    assert body["diversity_enabled"] is True
    assert settings.rag_post_retrieval_pipeline == "verified_context"
    assert "RAG_POST_RETRIEVAL_PIPELINE=verified_context" in env_file.read_text(encoding="utf-8")


def test_update_grounding_settings_rejects_unknown_pipeline() -> None:
    resp = client.patch("/api/settings/grounding", json={"pipeline": "agentic_loop"})
    assert resp.status_code == 422


def test_generation_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_generation_profile", "structured_json")

    resp = client.get("/api/settings/generation")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "structured_json"
    assert body["structured_output"] is True
    names = [item["name"] for item in body["profiles"]]
    assert names[0] == "grounded_concise"
    selected = [item["name"] for item in body["profiles"] if item["selected"]]
    assert selected == ["structured_json"]


def test_update_generation_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_generation_profile", "grounded_concise")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/generation", json={"profile": "detailed_cited"})

    assert resp.status_code == 200
    assert resp.json()["data"]["profile"] == "detailed_cited"
    assert settings.rag_generation_profile == "detailed_cited"
    assert "RAG_GENERATION_PROFILE=detailed_cited" in env_file.read_text(encoding="utf-8")


def test_update_generation_settings_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/generation", json={"profile": "chain_of_thought"})
    assert resp.status_code == 422


def test_guardrail_settings_reports_runtime_policy(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_guardrail_policy", "strict")

    resp = client.get("/api/settings/guardrail")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["policy"] == "strict"
    assert body["grounding_min_overlap"] > 3
    names = [item["name"] for item in body["policies"]]
    assert names[0] == "standard"
    selected = [item["name"] for item in body["policies"] if item["selected"]]
    assert selected == ["strict"]


def test_update_guardrail_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_guardrail_policy", "standard")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/guardrail", json={"policy": "regulated"})

    assert resp.status_code == 200
    assert resp.json()["data"]["policy"] == "regulated"
    assert resp.json()["data"]["audit_emphasis"] is True
    assert settings.rag_guardrail_policy == "regulated"
    assert "RAG_GUARDRAIL_POLICY=regulated" in env_file.read_text(encoding="utf-8")


def test_update_guardrail_settings_rejects_unknown_policy() -> None:
    resp = client.patch("/api/settings/guardrail", json={"policy": "paranoid"})
    assert resp.status_code == 422


def test_vector_index_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_vector_index_profile", "accurate")
    monkeypatch.setattr(settings, "oracle_vector_target_accuracy", 95)

    resp = client.get("/api/settings/vector-index")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "accurate"
    assert body["target_accuracy"] == 98
    assert body["requires_reprovision"] is True
    names = [item["name"] for item in body["profiles"]]
    assert names[0] == "balanced"
    selected = [item["name"] for item in body["profiles"] if item["selected"]]
    assert selected == ["accurate"]


def test_vector_index_balanced_reports_existing_accuracy(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_vector_index_profile", "balanced")
    monkeypatch.setattr(settings, "oracle_vector_target_accuracy", 92)

    resp = client.get("/api/settings/vector-index")

    body = resp.json()["data"]
    assert body["profile"] == "balanced"
    assert body["target_accuracy"] == 92
    assert body["requires_reprovision"] is False


def test_update_vector_index_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_vector_index_profile", "balanced")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/vector-index", json={"profile": "fast"})

    assert resp.status_code == 200
    assert resp.json()["data"]["profile"] == "fast"
    assert resp.json()["data"]["target_accuracy"] == 85
    assert settings.rag_vector_index_profile == "fast"
    assert "RAG_VECTOR_INDEX_PROFILE=fast" in env_file.read_text(encoding="utf-8")


def test_update_vector_index_settings_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/vector-index", json={"profile": "ivf_flat"})
    assert resp.status_code == 422


def test_evaluation_settings_reports_runtime_suite(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_evaluation_suite", "balanced")

    resp = client.get("/api/settings/evaluation-suite")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["suite"] == "balanced"
    assert body["thresholds"]["groundedness_pass_rate"] == 0.9
    assert "groundedness_pass_rate" in body["focus_metrics"]
    names = [item["name"] for item in body["suites"]]
    assert names[0] == "request_only"
    selected = [item["name"] for item in body["suites"] if item["selected"]]
    assert selected == ["balanced"]


def test_evaluation_settings_request_only_has_no_thresholds(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_evaluation_suite", "request_only")

    resp = client.get("/api/settings/evaluation-suite")

    body = resp.json()["data"]
    assert body["suite"] == "request_only"
    assert body["thresholds"] == {}


def test_update_evaluation_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_evaluation_suite", "request_only")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/evaluation-suite", json={"suite": "strict_ci"})

    assert resp.status_code == 200
    assert resp.json()["data"]["suite"] == "strict_ci"
    assert settings.rag_evaluation_suite == "strict_ci"
    assert "RAG_EVALUATION_SUITE=strict_ci" in env_file.read_text(encoding="utf-8")


def test_update_evaluation_settings_rejects_unknown_suite() -> None:
    resp = client.patch("/api/settings/evaluation-suite", json={"suite": "autorag_tuner"})
    assert resp.status_code == 422


def test_graph_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_graph_profile", "entities")
    monkeypatch.setattr(settings, "rag_graph_enabled", False)

    resp = client.get("/api/settings/graph")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "entities"
    assert body["enabled"] is True
    assert body["build_claims"] is False
    assert body["build_community_summaries"] is False
    names = [item["name"] for item in body["profiles"]]
    assert names == ["off", "entities", "full"]
    selected = [item["name"] for item in body["profiles"] if item["selected"]]
    assert selected == ["entities"]


def test_graph_settings_off_disables_build(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_graph_profile", "off")
    monkeypatch.setattr(settings, "rag_graph_enabled", False)

    resp = client.get("/api/settings/graph")

    body = resp.json()["data"]
    assert body["profile"] == "off"
    assert body["enabled"] is False


def test_update_graph_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_graph_profile", "off")
    monkeypatch.setattr(settings, "rag_graph_enabled", False)
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/graph", json={"profile": "full"})

    assert resp.status_code == 200
    assert resp.json()["data"]["profile"] == "full"
    assert resp.json()["data"]["build_community_summaries"] is True
    assert settings.rag_graph_profile == "full"
    assert "RAG_GRAPH_PROFILE=full" in env_file.read_text(encoding="utf-8")


def test_update_graph_settings_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/graph", json={"profile": "neo4j"})
    assert resp.status_code == 422


def test_agentic_settings_reports_runtime_profile(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_agentic_profile", "decompose")
    monkeypatch.setattr(settings, "rag_agentic_max_subqueries", 4)

    resp = client.get("/api/settings/agentic")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["profile"] == "decompose"
    assert body["enabled"] is True
    assert body["decompose"] is True
    assert body["multi_hop"] is False
    assert body["max_subqueries"] == 4
    names = [item["name"] for item in body["profiles"]]
    assert names == ["off", "smart_routing", "query_rewrite", "hyde", "decompose", "multi_hop"]
    selected = [item["name"] for item in body["profiles"] if item["selected"]]
    assert selected == ["decompose"]


def test_agentic_settings_off_disables_planning(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_agentic_profile", "off")

    resp = client.get("/api/settings/agentic")

    body = resp.json()["data"]
    assert body["profile"] == "off"
    assert body["enabled"] is False


def test_update_agentic_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rag_agentic_profile", "off")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch("/api/settings/agentic", json={"profile": "multi_hop"})

    assert resp.status_code == 200
    assert resp.json()["data"]["profile"] == "multi_hop"
    assert resp.json()["data"]["multi_hop"] is True
    assert settings.rag_agentic_profile == "multi_hop"
    assert "RAG_AGENTIC_PROFILE=multi_hop" in env_file.read_text(encoding="utf-8")


def test_update_agentic_settings_rejects_unknown_profile() -> None:
    resp = client.patch("/api/settings/agentic", json={"profile": "react_agent"})
    assert resp.status_code == 422


def test_get_model_settings_returns_runtime_values(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://enterprise-ai.example")
    monkeypatch.setattr(
        settings,
        "oci_enterprise_ai_project_ocid",
        "ocid1.generativeaiproject.oc1..example",
    )
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-runtime-secret")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_model", "enterprise-llm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_model", "enterprise-vlm")
    monkeypatch.setattr(settings, "oci_enterprise_ai_models", [])
    monkeypatch.setattr(settings, "oci_enterprise_ai_default_model", "")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_path", "/responses")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_path", "/responses")
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_payload_template", LLM_TEMPLATE)
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_payload_template", VLM_TEMPLATE)
    monkeypatch.setattr(settings, "oci_enterprise_ai_llm_response_path", "/data/text")
    monkeypatch.setattr(settings, "oci_enterprise_ai_vlm_response_path", "/data/document")
    monkeypatch.setattr(settings, "oci_genai_embedding_model", "cohere.embed-v4.0")
    monkeypatch.setattr(settings, "oci_genai_embedding_dim", 1536)
    monkeypatch.setattr(settings, "oci_genai_rerank_model", "cohere.rerank-v4.0-fast")

    resp = client.get("/api/settings/model")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["settings"]["enterprise_ai"]["endpoint"] == "https://enterprise-ai.example"
    assert (
        body["settings"]["enterprise_ai"]["project_ocid"]
        == "ocid1.generativeaiproject.oc1..example"
    )
    assert body["settings"]["enterprise_ai"]["api_key"] == ""
    assert body["settings"]["enterprise_ai"]["has_api_key"] is True
    assert body["settings"]["enterprise_ai"]["models"] == [
        {
            "model_id": "enterprise-llm",
            "display_name": "enterprise-llm",
            "vision_enabled": False,
        },
        {
            "model_id": "enterprise-vlm",
            "display_name": "enterprise-vlm",
            "vision_enabled": True,
        },
    ]
    assert body["settings"]["enterprise_ai"]["default_model_id"] == "enterprise-llm"
    assert body["settings"]["enterprise_ai"]["api_path"] == "/responses"
    assert body["settings"]["enterprise_ai"]["vlm_input_mode"] == "files_api"
    assert body["settings"]["enterprise_ai"]["text_payload_template"] == LLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["vision_payload_template"] == VLM_TEMPLATE
    assert body["settings"]["enterprise_ai"]["text_response_path"] == "/data/text"
    assert body["settings"]["enterprise_ai"]["vision_response_path"] == "/data/document"
    assert body["settings"]["enterprise_ai"]["llm_max_output_tokens"] == 1200
    assert body["settings"]["enterprise_ai"]["vlm_max_output_tokens"] == 65536
    assert body["settings"]["generative_ai"]["embedding_dim"] == 1536
    assert "sk-runtime-secret" not in resp.text
    assert body["checks"] == {
        "enterprise_ai": "ok",
        "generative_ai": "ok",
        "embedding_dim": "ok",
    }


def test_update_model_settings_mutates_runtime_settings() -> None:
    payload = _payload()

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "ok"
    assert body["model_settings_file"] == get_settings().model_settings_file
    settings = get_settings()
    assert settings.oci_enterprise_ai_endpoint == "https://enterprise-ai.example"
    assert settings.oci_enterprise_ai_project_ocid == "ocid1.generativeaiproject.oc1..example"
    assert settings.oci_enterprise_ai_api_key == "sk-update-secret"
    assert settings.oci_enterprise_ai_llm_model == "enterprise-llm"
    assert settings.oci_enterprise_ai_vlm_model == "enterprise-vlm"
    assert [model.model_id for model in settings.oci_enterprise_ai_models] == [
        "enterprise-llm",
        "enterprise-vlm",
    ]
    assert settings.oci_enterprise_ai_default_model == "enterprise-llm"
    assert settings.oci_enterprise_ai_llm_path == "/responses"
    assert settings.oci_enterprise_ai_vlm_path == "/responses"
    assert settings.oci_enterprise_ai_vlm_input_mode == "files_api"
    assert settings.oci_enterprise_ai_llm_payload_template == LLM_TEMPLATE
    assert settings.oci_enterprise_ai_vlm_payload_template == VLM_TEMPLATE
    assert settings.oci_enterprise_ai_llm_response_path == "/data/text"
    assert settings.oci_enterprise_ai_vlm_response_path == "/data/document"
    assert settings.oci_enterprise_ai_llm_max_output_tokens == 1600
    assert settings.oci_enterprise_ai_vlm_max_output_tokens == 64000
    assert settings.oci_genai_embedding_model == "cohere.embed-v4.0"
    assert settings.oci_genai_embedding_dim == 1536
    assert settings.oci_genai_rerank_model == "cohere.rerank-v4.0-fast"
    assert "sk-update-secret" not in resp.text


def test_update_model_settings_persists_private_json(tmp_path: Path) -> None:
    settings = get_settings()
    settings.model_settings_file = str(tmp_path / "config" / "model-settings.json")
    payload = _payload()

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    settings_file = Path(settings.model_settings_file)
    assert settings_file.is_file()
    assert stat.S_IMODE(settings_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(settings_file.stat().st_mode) == 0o600
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["version"] == 1
    assert persisted["enterprise_ai"]["api_key"] == "sk-update-secret"
    assert persisted["enterprise_ai"]["models"] == [
        {
            "model_id": "enterprise-llm",
            "display_name": "標準 LLM",
            "vision_enabled": False,
        },
        {
            "model_id": "enterprise-vlm",
            "display_name": "Vision LLM",
            "vision_enabled": True,
        },
    ]
    assert persisted["enterprise_ai"]["default_model_id"] == "enterprise-llm"
    assert persisted["enterprise_ai"]["vlm_input_mode"] == "files_api"
    assert persisted["enterprise_ai"]["llm_max_output_tokens"] == 1600
    assert persisted["enterprise_ai"]["vlm_max_output_tokens"] == 64000
    assert persisted["generative_ai"]["embedding_dim"] == 1536
    assert "sk-update-secret" not in resp.text


def test_load_persisted_model_settings_applies_saved_model_catalog(tmp_path: Path) -> None:
    settings_file = tmp_path / "model-settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "enterprise_ai": {
                    "endpoint": "https://persisted-enterprise.example",
                    "project_ocid": "ocid1.generativeaiproject.oc1..persisted",
                    "api_key": "sk-persisted-secret",
                    "models": [
                        {
                            "model_id": "persisted-text",
                            "display_name": "永続 Text",
                            "vision_enabled": False,
                        },
                        {
                            "model_id": "persisted-vision",
                            "display_name": "永続 Vision",
                            "vision_enabled": True,
                        },
                    ],
                    "default_model_id": "persisted-text",
                    "api_path": "/responses",
                    "vlm_input_mode": "inline_image",
                    "text_payload_template": LLM_TEMPLATE,
                    "vision_payload_template": VLM_TEMPLATE,
                    "text_response_path": "/payload/text",
                    "vision_response_path": "/payload/document",
                    "timeout_seconds": 42,
                    "max_retries": 1,
                    "llm_max_output_tokens": 1700,
                    "vlm_max_output_tokens": 63000,
                },
                "generative_ai": {
                    "embedding_model": "cohere.embed-v4.0",
                    "embedding_dim": 1536,
                    "rerank_model": "cohere.rerank-v4.0-fast",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(model_settings_file=str(settings_file))
    load_persisted_model_settings(settings)

    assert settings.oci_enterprise_ai_endpoint == "https://persisted-enterprise.example"
    assert settings.oci_enterprise_ai_project_ocid == "ocid1.generativeaiproject.oc1..persisted"
    assert settings.oci_enterprise_ai_api_key == "sk-persisted-secret"
    assert [model.model_id for model in settings.oci_enterprise_ai_models] == [
        "persisted-text",
        "persisted-vision",
    ]
    assert settings.oci_enterprise_ai_default_model == "persisted-text"
    assert settings.oci_enterprise_ai_llm_model == "persisted-text"
    assert settings.oci_enterprise_ai_vlm_model == "persisted-vision"
    assert settings.oci_enterprise_ai_vlm_input_mode == "inline_image"
    assert settings.oci_enterprise_ai_llm_response_path == "/payload/text"
    assert settings.oci_enterprise_ai_vlm_response_path == "/payload/document"
    assert settings.oci_enterprise_ai_timeout_seconds == 42
    assert settings.oci_enterprise_ai_max_retries == 1
    assert settings.oci_enterprise_ai_llm_max_output_tokens == 1700
    assert settings.oci_enterprise_ai_vlm_max_output_tokens == 63000


def test_update_model_settings_does_not_mutate_runtime_when_persist_fails(
    tmp_path: Path,
) -> None:
    settings = get_settings()
    settings.model_settings_file = str(tmp_path)
    settings.oci_enterprise_ai_endpoint = "https://old-enterprise.example"
    settings.oci_enterprise_ai_api_key = "sk-old-secret"

    resp = client.patch("/api/settings/model", json=_payload())

    assert resp.status_code == 500
    assert settings.oci_enterprise_ai_endpoint == "https://old-enterprise.example"
    assert settings.oci_enterprise_ai_api_key == "sk-old-secret"


def test_check_model_settings_does_not_mutate_runtime_settings(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "")

    resp = client.post("/api/settings/model/check", json=_payload())

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "ok"
    assert settings.oci_enterprise_ai_endpoint == ""


def test_check_model_settings_masks_candidate_api_key() -> None:
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = "sk-check-secret"
    payload["enterprise_ai"]["has_api_key"] = False

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "ok"
    assert body["settings"]["enterprise_ai"]["api_key"] == ""
    assert body["settings"]["enterprise_ai"]["has_api_key"] is True
    assert "sk-check-secret" not in resp.text


def test_model_settings_test_enterprise_text_uses_candidate_without_mutating_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_endpoint", "https://runtime.example")
    observed_settings: list[Settings] = []

    class FakeEnterpriseAiClient:
        def __init__(self, settings: Settings) -> None:
            observed_settings.append(settings)

        async def generate(self, prompt: str, context: str) -> str:
            assert prompt
            assert context
            return "接続テスト応答"

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FakeEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_text",
            "model_id": "enterprise-llm",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["target_type"] == "enterprise_text"
    assert body["details"]["surface"] == "llm"
    assert observed_settings[0].oci_enterprise_ai_llm_model == "enterprise-llm"
    assert observed_settings[0].oci_enterprise_ai_api_key == "sk-update-secret"
    assert settings.oci_enterprise_ai_endpoint == "https://runtime.example"
    assert "sk-update-secret" not in resp.text


def test_model_settings_test_enterprise_vision_uses_smoke_image_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    observed: list[tuple[Settings, bytes, str, str]] = []

    class FakeEnterpriseAiClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def generate_from_image(
            self,
            image_bytes: bytes,
            prompt: str,
            *,
            mime_type: str,
        ) -> str:
            observed.append((self.settings, image_bytes, prompt, mime_type))
            return "画像を確認しました。"

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FakeEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_vision",
            "model_id": "google.gemini-2.5-flash",
            "vision_enabled": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["details"]["surface"] == "vision"
    assert body["details"]["response_chars"] == len("画像を確認しました。")
    assert observed[0][0].oci_enterprise_ai_vlm_model == "google.gemini-2.5-flash"
    assert observed[0][1] == settings_routes.MODEL_TEST_IMAGE_BYTES
    assert observed[0][2]
    assert observed[0][3] == "image/jpeg"


def test_model_settings_test_embedding_uses_candidate_model(
    monkeypatch: MonkeyPatch,
) -> None:
    observed_settings: list[Settings] = []

    class FakeGenAiClient:
        def __init__(self, settings: Settings) -> None:
            observed_settings.append(settings)

        async def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
            assert texts == ["モデル接続テスト"]
            assert input_type == "SEARCH_QUERY"
            return [[0.0] * 1536]

    monkeypatch.setattr(settings_routes, "OciGenAiClient", FakeGenAiClient)
    payload = _payload()
    payload["generative_ai"]["embedding_model"] = "cohere.embed-custom"

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "embedding",
            "model_id": "cohere.embed-custom",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["details"]["vector_dim"] == 1536
    assert observed_settings[0].oci_genai_embedding_model == "cohere.embed-custom"


def test_model_settings_test_returns_real_error_with_troubleshooting_and_masks_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    class FailingEnterpriseAiClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def generate(self, prompt: str, context: str) -> str:
            raise RuntimeError(
                f"401 Unauthorized: bearer {self.settings.oci_enterprise_ai_api_key}"
            )

    monkeypatch.setattr(settings_routes, "OciEnterpriseAiClient", FailingEnterpriseAiClient)
    payload = _payload()

    resp = client.post(
        "/api/settings/model/test",
        json={
            "settings": payload,
            "target_type": "enterprise_text",
            "model_id": "enterprise-llm",
            "vision_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "RuntimeError"
    assert "401 Unauthorized" in body["raw_error"]
    assert "<secret>" in body["raw_error"]
    assert body["troubleshooting"]
    assert "sk-update-secret" not in resp.text


def test_model_settings_missing_values_are_reported() -> None:
    payload = _payload()
    payload["enterprise_ai"]["endpoint"] = ""
    payload["generative_ai"]["rerank_model"] = ""

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "missing"
    assert body["checks"]["generative_ai"] == "missing"
    assert body["checks"]["embedding_dim"] == "ok"


def test_update_model_settings_allows_invalid_readiness_fields() -> None:
    payload = _payload()
    payload["enterprise_ai"]["endpoint"] = "enterprise-ai.example"
    payload["enterprise_ai"]["project_ocid"] = "not-an-ocid"
    payload["enterprise_ai"]["api_path"] = "responses"

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["checks"]["enterprise_ai"] == "invalid"
    settings = get_settings()
    assert settings.oci_enterprise_ai_endpoint == "enterprise-ai.example"
    assert settings.oci_enterprise_ai_project_ocid == "not-an-ocid"
    assert settings.oci_enterprise_ai_llm_path == "responses"


def test_model_settings_requires_enterprise_ai_api_key() -> None:
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = False

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_model_settings_requires_enterprise_ai_model_catalog() -> None:
    payload = _payload()
    payload["enterprise_ai"]["models"] = []
    payload["enterprise_ai"]["default_model_id"] = ""

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "missing"


def test_enterprise_ai_model_settings_defaults_max_retries_to_three() -> None:
    """設定 API スキーマの最大リトライ回数既定値は 3。"""
    assert EnterpriseAiModelSettings().max_retries == 3
    assert EnterpriseAiModelSettings().vlm_input_mode == "files_api"
    assert EnterpriseAiModelSettings().llm_max_output_tokens == 1200
    assert EnterpriseAiModelSettings().vlm_max_output_tokens == 65536


def test_model_settings_reports_invalid_default_model() -> None:
    payload = _payload()
    payload["enterprise_ai"]["default_model_id"] = "missing-model"

    resp = client.post("/api/settings/model/check", json=payload)

    assert resp.status_code == 200
    assert resp.json()["data"]["checks"]["enterprise_ai"] == "invalid"


def test_model_settings_rejects_invalid_payload_template() -> None:
    payload = _payload()
    payload["enterprise_ai"]["text_payload_template"] = "[1, 2, 3]"

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 422
    assert resp.json()["error_messages"]


def test_model_settings_keeps_existing_api_key_when_secret_input_is_blank(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-existing-secret")
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = True

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    assert settings.oci_enterprise_ai_api_key == "sk-existing-secret"
    assert resp.json()["data"]["settings"]["enterprise_ai"]["has_api_key"] is True
    assert "sk-existing-secret" not in resp.text


def test_model_settings_clears_existing_api_key(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_enterprise_ai_api_key", "sk-existing-secret")
    payload = _payload()
    payload["enterprise_ai"]["api_key"] = ""
    payload["enterprise_ai"]["has_api_key"] = True
    payload["enterprise_ai"]["clear_api_key"] = True

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 200
    assert settings.oci_enterprise_ai_api_key == ""
    assert resp.json()["data"]["settings"]["enterprise_ai"]["has_api_key"] is False
    assert "sk-existing-secret" not in resp.text


def test_model_settings_rejects_non_1536_embedding_dim() -> None:
    payload = _payload()
    payload["generative_ai"]["embedding_dim"] = 1024

    resp = client.patch("/api/settings/model", json=payload)

    assert resp.status_code == 422
    body = resp.json()
    assert body["data"] is None
    assert body["error_messages"]


def test_read_oci_config_uses_requested_profile_from_backend_path(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "tenancy=ocid1.tenancy.oc1..shared",
                "region=ap-tokyo-1",
                "key_file=/home/app/.oci/default.pem",
                "[RAG_PROD]",
                "user=ocid1.user.oc1..prod",
                "fingerprint=12:34:56:78",
                "region=ap-osaka-1",
                "compartment=ocid1.compartment.oc1..prod",
            ]
        ),
        encoding="utf-8",
    )

    resp = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(config_file), "profile": "RAG_PROD"},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {
        "profile": "RAG_PROD",
        "user": "ocid1.user.oc1..prod",
        "fingerprint": "12:34:56:78",
        "tenancy": "ocid1.tenancy.oc1..shared",
        "region": "ap-osaka-1",
        "key_file": "/home/app/.oci/default.pem",
        "applied_fields": [
            "user",
            "fingerprint",
            "tenancy",
            "region",
            "key_file",
        ],
    }
    assert "compartment" not in body


def test_get_oci_settings_returns_runtime_and_config_values(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.parent.mkdir()
    key_file.write_text(
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    config_file = tmp_path / ".oci" / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "user=ocid1.user.oc1..runtime",
                "fingerprint=12:34:56:78",
                "tenancy=ocid1.tenancy.oc1..runtime",
                "region=ap-tokyo-1",
                "key_file=/tmp/ignored.pem",
            ]
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(config_file))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")

    resp = client.get("/api/settings/oci")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {
        "config_file": str(config_file),
        "profile": "DEFAULT",
        "user": "ocid1.user.oc1..runtime",
        "fingerprint": "12:34:56:78",
        "tenancy": "ocid1.tenancy.oc1..runtime",
        "region": "ap-tokyo-1",
        "key_file": "~/.oci/oci_api_key.pem",
        "key_file_exists": True,
        "config_file_exists": True,
        "config_source": "runtime",
    }


def test_get_oci_settings_reports_missing_private_key_without_failing(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(tmp_path / ".oci" / "missing-config"))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "ap-osaka-1")

    resp = client.get("/api/settings/oci")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["user"] == ""
    assert body["fingerprint"] == ""
    assert body["tenancy"] == ""
    assert body["region"] == ""
    assert body["key_file"] == "~/.oci/oci_api_key.pem"
    assert body["key_file_exists"] is False
    assert body["config_file_exists"] is False


def test_update_oci_settings_creates_config_dir_and_file_with_private_permissions(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "OCI_REGION=us-chicago-1",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    config_dir = tmp_path / ".oci"
    config_file = config_dir / "config"
    assert config_dir.is_dir()
    assert config_file.is_file()
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    content = config_file.read_text(encoding="utf-8")
    assert "[DEFAULT]" in content
    assert "user=ocid1.user.oc1..new" in content
    assert "fingerprint=12:34:56:78:90:ab:cd:ef" in content
    assert "tenancy=ocid1.tenancy.oc1..new" in content
    assert "region=ap-osaka-1" in content
    assert "key_file=~/.oci/oci_api_key.pem" in content
    assert settings.oci_region == "ap-osaka-1"
    assert body["config_file_exists"] is True
    assert body["key_file_exists"] is False
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert "OCI_CONFIG_FILE=~/.oci/config" in persisted
    assert "OCI_CONFIG_PROFILE=DEFAULT" in persisted
    assert "OCI_REGION=ap-osaka-1" in persisted


def test_update_oci_settings_does_not_write_empty_config_defaults(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    env_file = _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "",
            "fingerprint": "",
            "tenancy": "",
            "region": "",
        },
    )

    assert resp.status_code == 200
    content = (tmp_path / ".oci" / "config").read_text(encoding="utf-8")
    assert "user=" not in content
    assert "fingerprint=" not in content
    assert "tenancy=" not in content
    assert "region=" not in content
    assert "key_file=" not in content
    assert settings.oci_region == ""
    assert "OCI_REGION" not in env_file.read_text(encoding="utf-8")


def test_update_oci_settings_preserves_existing_non_default_profile(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".oci"
    config_dir.mkdir()
    config_file = config_dir / "config"
    config_file.write_text(
        "\n".join(
            [
                "[DEFAULT]",
                "user=ocid1.user.oc1..old",
                "fingerprint=aa:bb:cc:dd",
                "[ADMIN_USER]",
                "user=ocid1.user.oc1..admin",
                "fingerprint=11:22:33:44",
                "tenancy=ocid1.tenancy.oc1..admin",
                "region=us-chicago-1",
                "key_file=keys/admin.pem",
            ]
        ),
        encoding="utf-8",
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", str(config_file))
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    content = config_file.read_text(encoding="utf-8")
    assert "[ADMIN_USER]" in content
    assert "user=ocid1.user.oc1..admin" in content
    assert "key_file=keys/admin.pem" in content
    assert "user=ocid1.user.oc1..new" in content


def test_update_oci_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    monkeypatch.setattr(settings, "oci_region", "us-chicago-1")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 500
    assert settings.oci_region == "us-chicago-1"


def test_update_oci_object_storage_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "old-namespace")
    env_file = _settings_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# OCI Object Storage",
                "OBJECT_STORAGE_REGION=ap-osaka-1",
                "OBJECT_STORAGE_NAMESPACE=old-namespace",
                "OBJECT_STORAGE_BUCKET=rag-originals",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "us-chicago-1",
            "object_storage_namespace": "mytenancynamespace",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["object_storage_region"] == "us-chicago-1"
    assert body["object_storage_namespace"] == "mytenancynamespace"
    assert settings.object_storage_region == "us-chicago-1"
    assert settings.object_storage_namespace == "mytenancynamespace"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_REGION=us-chicago-1" in persisted
    assert "OBJECT_STORAGE_NAMESPACE=mytenancynamespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_oci_object_storage_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "ap-osaka-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "old-namespace")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "us-chicago-1",
            "object_storage_namespace": "mytenancynamespace",
        },
    )

    assert resp.status_code == 500
    assert settings.object_storage_region == "ap-osaka-1"
    assert settings.object_storage_namespace == "old-namespace"


def test_update_oci_object_storage_settings_rejects_invalid_values() -> None:
    resp = client.patch(
        "/api/settings/oci/object-storage",
        json={
            "object_storage_region": "US_CHICAGO_1",
            "object_storage_namespace": "invalid namespace",
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error_messages"]


def test_test_oci_config_reports_missing_private_key_after_save(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["config_file_exists"] is True
    assert body["key_file_exists"] is False
    assert body["missing_fields"] == []
    assert body["oci_directory_mode"] == "0700"
    assert body["config_file_mode"] == "0600"
    assert body["key_file_mode"] is None
    assert "秘密鍵ファイルが見つかりません" in body["message"]


def test_test_oci_config_succeeds_with_private_key_and_permissions(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.write_text(
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    key_file.chmod(0o600)

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["key_file_exists"] is True
    assert body["permission_issues"] == []
    assert body["key_file_mode"] == "0600"


def test_test_oci_config_reports_encrypted_private_key_without_pass_phrase(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "oci_config_file", "~/.oci/config")
    monkeypatch.setattr(settings, "oci_config_profile", "DEFAULT")
    _settings_env_file(monkeypatch, tmp_path)
    client.patch(
        "/api/settings/oci",
        json={
            "user": "ocid1.user.oc1..new",
            "fingerprint": "12:34:56:78:90:ab:cd:ef",
            "tenancy": "ocid1.tenancy.oc1..new",
            "region": "ap-osaka-1",
        },
    )
    key_file = tmp_path / ".oci" / "oci_api_key.pem"
    key_file.write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    key_file.chmod(0o600)

    resp = client.post("/api/settings/oci/config/test")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["key_file_exists"] is True
    assert body["error_type"] == "OciPrivateKeyPassPhraseRequiredError"
    assert "暗号化されています" in body["message"]


def test_read_oci_config_rejects_missing_requested_profile(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text(
        "[DEFAULT]\nuser=ocid1.user.oc1..default\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/settings/oci/config/read",
        json={"config_file": str(config_file), "profile": "RAG_PROD"},
    )

    assert resp.status_code == 404
    assert "指定した OCI config profile が見つかりません。" in resp.text


def test_read_object_storage_namespace_uses_oci_sdk(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, Any] = {}

    class FakeObjectStorageClient:
        def __init__(self, config: dict[str, Any]) -> None:
            captured["config"] = config

        def get_namespace(self) -> object:
            captured["get_namespace_called"] = True
            return SimpleNamespace(data="mytenancynamespace")

    def fake_from_file(path: str, profile: str) -> dict[str, Any]:
        captured["config_path"] = path
        captured["profile"] = profile
        return {"region": "ap-tokyo-1"}

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(from_file=fake_from_file)
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=FakeObjectStorageClient)
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("app.api.routes.settings.importlib.import_module", fake_import_module)
    config_file = tmp_path / "config"

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(config_file),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["data"] == {"namespace": "mytenancynamespace"}
    assert captured["config_path"] == str(config_file)
    assert captured["profile"] == "DEFAULT"
    assert captured["config"] == {"region": "ap-osaka-1"}
    assert captured["get_namespace_called"] is True


def test_read_object_storage_namespace_reports_oci_errors(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(from_file=lambda path, profile: {"region": "ap-tokyo-1"})
        if name == "oci.object_storage":
            raise RuntimeError("sdk unavailable")
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("app.api.routes.settings.importlib.import_module", fake_import_module)

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(tmp_path / "config"),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 502
    assert "namespace を取得できませんでした" in resp.text


def test_read_object_storage_namespace_refuses_encrypted_private_key_without_prompt(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "encrypted.pem"
    key_file.write_text(
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nabc\n-----END ENCRYPTED PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    initialized = False

    class FakeObjectStorageClient:
        def __init__(self, config: dict[str, Any]) -> None:
            nonlocal initialized
            initialized = True

        def get_namespace(self) -> object:
            return SimpleNamespace(data="mytenancynamespace")

    def fake_import_module(name: str) -> object:
        if name == "oci.config":
            return SimpleNamespace(
                from_file=lambda path, profile: {"key_file": str(key_file), "region": "ap-tokyo-1"}
            )
        if name == "oci.object_storage":
            return SimpleNamespace(ObjectStorageClient=FakeObjectStorageClient)
        raise AssertionError(f"unexpected module import: {name}")

    monkeypatch.setattr("app.api.routes.settings.importlib.import_module", fake_import_module)

    resp = client.post(
        "/api/settings/oci/object-storage/namespace",
        json={
            "config_file": str(tmp_path / "config"),
            "profile": "DEFAULT",
            "region": "ap-osaka-1",
        },
    )

    assert resp.status_code == 502
    assert "暗号化されています" in resp.text
    assert initialized is False


def test_upload_oci_private_key_overwrites_fixed_path(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pem = b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("new-key.pem", pem, "application/x-pem-file")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {"key_file": "~/.oci/oci_api_key.pem", "saved": True}
    target = tmp_path / ".oci" / "oci_api_key.pem"
    assert target.read_bytes() == pem
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "PRIVATE KEY" not in resp.text


def test_upload_oci_private_key_rejects_invalid_content(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("new-key.pem", b"not a pem", "application/x-pem-file")},
    )

    assert resp.status_code == 400
    assert not (tmp_path / ".oci" / "oci_api_key.pem").exists()


def test_upload_oci_private_key_rejects_encrypted_content(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    pem = (
        b"-----BEGIN ENCRYPTED PRIVATE KEY-----\n" b"abc\n" b"-----END ENCRYPTED PRIVATE KEY-----\n"
    )

    resp = client.post(
        "/api/settings/oci/key-file",
        files={"file": ("encrypted.pem", pem, "application/x-pem-file")},
    )

    assert resp.status_code == 400
    assert "pass phrase" in resp.text
    assert not (tmp_path / ".oci" / "oci_api_key.pem").exists()


def test_get_database_settings_masks_secrets(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "super-secret-password")
    monkeypatch.setattr(settings, "oracle_dsn", "adb.example.com/rag")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "wallet-secret")

    resp = client.get("/api/settings/database")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["user"] == "rag_app"
    assert body["dsn"] == "adb.example.com/rag"
    assert body["wallet_dir"] == settings.resolved_oracle_wallet_dir
    assert body["has_password"] is True
    assert body["has_wallet_password"] is True
    assert body["wallet_uploaded"] is False
    assert body["available_services"] == []
    assert body["readiness"] == "ok"
    assert body["vector_column"] == "VECTOR(1536, FLOAT32)"
    assert "super-secret-password" not in resp.text
    assert "wallet-secret" not in resp.text


def test_update_database_settings_mutates_runtime_without_echoing_secret(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "old_user")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "old-dsn")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", "/opt/oracle/instantclient_23_26")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")
    env_file = _database_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "ORACLE_USER=old_user",
                "ORACLE_DSN=old-dsn",
                "ORACLE_USER=duplicate",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "/opt/oracle/wallet",
        },
    )

    assert resp.status_code == 200
    assert settings.oracle_user == "rag_app"
    assert settings.oracle_dsn == "adb.example.com/rag"
    assert settings.oracle_wallet_dir == settings.resolved_oracle_wallet_dir
    assert settings.oracle_password == "old-secret"
    assert resp.json()["data"]["wallet_dir"] == settings.resolved_oracle_wallet_dir
    assert resp.json()["data"]["has_password"] is True
    assert "old-secret" not in resp.text
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert persisted.count("ORACLE_USER=") == 1
    assert "ORACLE_USER=rag_app" in persisted
    assert "ORACLE_PASSWORD=old-secret" in persisted
    assert "ORACLE_DSN=adb.example.com/rag" in persisted
    assert "ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient_23_26" in persisted
    assert "ORACLE_WALLET_PASSWORD=" in persisted


def test_update_database_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "old_user")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "old-dsn")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "old-wallet-secret")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "/opt/oracle/wallet",
            "password": "new-secret",
            "wallet_password": "new-wallet-secret",
        },
    )

    assert resp.status_code == 500
    assert settings.oracle_user == "old_user"
    assert settings.oracle_password == "old-secret"
    assert settings.oracle_dsn == "old-dsn"
    assert settings.oracle_wallet_password == "old-wallet-secret"


def test_update_database_settings_clears_saved_secrets(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "old-secret")
    monkeypatch.setattr(settings, "oracle_dsn", "ragdb_high")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "old-wallet-secret")
    env_file = _database_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "ORACLE_USER=rag_app",
                "ORACLE_PASSWORD=old-secret",
                "ORACLE_DSN=ragdb_high",
                "ORACLE_WALLET_PASSWORD=old-wallet-secret",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/database",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": settings.resolved_oracle_wallet_dir,
            "clear_password": True,
            "clear_wallet_password": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["has_password"] is False
    assert body["has_wallet_password"] is False
    assert settings.oracle_password == ""
    assert settings.oracle_wallet_password == ""
    persisted = env_file.read_text(encoding="utf-8")
    assert "ORACLE_PASSWORD=" in persisted
    assert "ORACLE_PASSWORD=old-secret" not in persisted
    assert "ORACLE_WALLET_PASSWORD=" in persisted
    assert "ORACLE_WALLET_PASSWORD=old-wallet-secret" not in persisted


def test_database_connection_test_uses_candidate_without_mutating_runtime(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        assert candidate.oracle_user == "rag_app"
        assert candidate.oracle_password == "candidate-secret"
        assert candidate.oracle_dsn == "adb.example.com/rag"

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "adb.example.com/rag",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "success"
    assert body["readiness"] == "ok"
    assert body["elapsed_ms"] >= 0
    assert body["details"]["timeout_seconds"] == settings.oracle_db_test_timeout_seconds
    assert settings.oracle_user == ""
    assert settings.oracle_password == ""
    assert "candidate-secret" not in resp.text


def test_database_connection_test_returns_wallet_password_guidance(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_client_lib_dir", str(tmp_path / "instantclient_23_26"))
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise OracleWalletPasswordRequiredError("Wallet パスワードを入力してください。")

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["message"] == "Wallet パスワードを入力してください。"
    assert body["error_type"] == "OracleWalletPasswordRequiredError"
    assert body["elapsed_ms"] >= 0
    assert body["troubleshooting"]


def test_database_connection_test_returns_timeout_guidance(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise OracleConnectionTimeoutError("Oracle 26ai 接続テストが 15 秒でタイムアウトしました。")

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "OracleConnectionTimeoutError"
    assert "タイムアウト" in body["message"]
    assert any("TCPS 1522" in tip for tip in body["troubleshooting"])
    assert (
        body["details"]["tcp_connect_timeout_seconds"]
        == settings.oracle_tcp_connect_timeout_seconds
    )


def test_database_connection_test_classifies_oracle_operational_error(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    class FakeOperationalError(Exception):
        """python-oracledb OperationalError 相当のテスト用例外。"""

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise FakeOperationalError(
            "ORA-01017: invalid username/password; logon denied candidate-secret"
        )

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "failed"
    assert body["error_type"] == "FakeOperationalError"
    assert "ORA-01017" in body["message"]
    assert "ユーザー名または DB パスワード" in body["message"]
    assert body["details"]["oracle_error_codes"] == "ORA-01017"
    assert any("DB パスワード" in tip for tip in body["troubleshooting"])
    assert "candidate-secret" not in resp.text


def test_database_connection_test_classifies_adb_acl_rejection(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_user", "")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    monkeypatch.setattr(settings, "oracle_wallet_password", "")

    class FakeOperationalError(Exception):
        """python-oracledb OperationalError 相当のテスト用例外。"""

    async def fake_test_oracle_connection(candidate: Settings) -> None:
        raise FakeOperationalError(
            "DPY-6005: cannot connect to database. DPY-6000: listener refused. "
            "ORA-12506: listener rejected connection based on service ACL filtering"
        )

    monkeypatch.setattr(settings_routes, "test_oracle_connection", fake_test_oracle_connection)

    resp = client.post(
        "/api/settings/database/test",
        json={
            "user": "rag_app",
            "dsn": "ragdb_high",
            "wallet_dir": "",
            "password": "candidate-secret",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert "ORA-12506" in body["message"]
    assert "アクセス制御リスト" in body["message"]
    assert body["details"]["oracle_error_codes"] == "DPY-6005, DPY-6000, ORA-12506"
    assert any("Network Access / ACL" in tip for tip in body["troubleshooting"])


def test_upload_database_wallet_zip_updates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "oracle_user", "rag_app")
    monkeypatch.setattr(settings, "oracle_password", "")
    monkeypatch.setattr(settings, "oracle_dsn", "ragdb_high")
    monkeypatch.setattr(settings, "oracle_wallet_dir", "")
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "old-wallet-file").write_text("old", encoding="utf-8")

    resp = client.post(
        "/api/settings/database/wallet",
        files={"file": ("Wallet_RAGDB.zip", _wallet_zip(), "application/zip")},
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    wallet_dir = Path(settings.oracle_wallet_dir)
    assert wallet_dir.is_dir()
    assert (wallet_dir / "tnsnames.ora").read_text(encoding="utf-8") == "ragdb_high = ..."
    assert (wallet_dir / "ewallet.pem").is_file()
    assert not (wallet_dir / "ewallet.p12").exists()
    assert not (wallet_dir / "keystore.jks").exists()
    assert not (wallet_dir / "old-wallet-file").exists()
    assert body["wallet_dir"] == str(wallet_dir)
    assert body["wallet_uploaded"] is True
    assert body["available_services"] == ["ragdb_high"]
    assert body["readiness"] == "ok"
    assert "ewallet-secret" not in resp.text


def test_get_database_settings_extracts_available_services_from_wallet_dir(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    (wallet_dir / "ewallet.p12").write_text("legacy-password-wallet", encoding="utf-8")
    (wallet_dir / "keystore.jks").write_text("legacy-java-keystore", encoding="utf-8")
    (wallet_dir / "tnsnames.ora").write_text(
        "\n".join(
            [
                "ragdb_high = (DESCRIPTION = ...)",
                "  (ADDRESS = (PROTOCOL = tcps)(HOST = example.oraclecloud.com)(PORT = 1522))",
                "ragdb_low = (DESCRIPTION = ...)",
                "ragdb_high = (DESCRIPTION = duplicate)",
            ]
        ),
        encoding="utf-8",
    )

    resp = client.get("/api/settings/database")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["wallet_uploaded"] is True
    assert body["available_services"] == ["ragdb_high", "ragdb_low"]
    assert not (wallet_dir / "ewallet.p12").exists()
    assert not (wallet_dir / "keystore.jks").exists()


def test_upload_database_wallet_zip_rejects_unsafe_member_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    wallet_dir = Path(settings.resolved_oracle_wallet_dir)
    wallet_dir.mkdir(parents=True)
    sentinel = wallet_dir / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    resp = client.post(
        "/api/settings/database/wallet",
        files={
            "file": (
                "Wallet_BAD.zip",
                _wallet_zip(
                    {
                        "../tnsnames.ora": "bad",
                        "sqlnet.ora": "...",
                        "cwallet.sso": "...",
                        "ewallet.pem": "...",
                    }
                ),
                "application/zip",
            )
        },
    )

    assert resp.status_code == 400
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (wallet_dir / "tnsnames.ora").exists()


class _FakeAdbInfo:
    """OCI SDK の AutonomousDatabase model 代替。"""

    def __init__(
        self,
        lifecycle_state: str,
        *,
        display_name: str = "RAG ADB",
        adb_id: str = "ocid1.autonomousdatabase.oc1..fake",
    ) -> None:
        self.id = adb_id
        self.display_name = display_name
        self.lifecycle_state = lifecycle_state
        self.db_name = "ragdb"
        self.cpu_core_count = 2
        self.data_storage_size_in_tbs = 1.0


class _FakeAdbResponse:
    def __init__(self, data: _FakeAdbInfo) -> None:
        self.data = data


def _make_fake_database_client(
    lifecycle_state: str,
    calls: list[str],
) -> type:
    """指定 lifecycle を返し、start/stop 呼び出しを記録する Fake DatabaseClient。"""

    class _FakeDatabaseClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append("init")

        def get_autonomous_database(self, autonomous_database_id: str) -> _FakeAdbResponse:
            calls.append(f"get:{autonomous_database_id}")
            return _FakeAdbResponse(_FakeAdbInfo(lifecycle_state))

        def start_autonomous_database(self, autonomous_database_id: str) -> None:
            calls.append(f"start:{autonomous_database_id}")

        def stop_autonomous_database(self, autonomous_database_id: str) -> None:
            calls.append(f"stop:{autonomous_database_id}")

    return _FakeDatabaseClient


def _patch_adb_client(
    monkeypatch: MonkeyPatch,
    lifecycle_state: str,
    calls: list[str],
) -> None:
    from app.clients.oci_database import OciDatabaseClient

    fake_client = _make_fake_database_client(lifecycle_state, calls)()

    def _factory(settings: Settings | None = None) -> OciDatabaseClient:
        return OciDatabaseClient(
            settings=settings,
            database_client=fake_client,
            sdk_call_runner=_run_inline,
        )

    monkeypatch.setattr(settings_routes, "OciDatabaseClient", _factory)


def test_get_adb_info_returns_not_configured_without_ocid(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "oracle_adb_ocid", "")

    resp = client.get("/api/settings/database/adb")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "not_configured"
    assert data["lifecycle_state"] is None


def test_get_adb_info_returns_lifecycle_from_oci(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.get("/api/settings/database/adb")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"
    assert data["lifecycle_state"] == "AVAILABLE"
    assert data["display_name"] == "RAG ADB"
    assert any(call.startswith("get:") for call in calls)


def test_update_adb_settings_persists_ocid_and_region(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "")
    env_file = _database_env_file(monkeypatch, tmp_path)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post(
        "/api/settings/database/adb/settings",
        json={"adb_ocid": "ocid1.autonomousdatabase.oc1..saved", "region": "ap-tokyo-1"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["region"] == "ap-tokyo-1"
    assert settings.oracle_adb_ocid == "ocid1.autonomousdatabase.oc1..saved"
    assert settings.oracle_adb_region == "ap-tokyo-1"
    persisted = env_file.read_text(encoding="utf-8")
    assert "ORACLE_ADB_OCID=ocid1.autonomousdatabase.oc1..saved" in persisted
    assert "ORACLE_ADB_REGION=ap-tokyo-1" in persisted


def test_start_adb_sends_start_when_stopped(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    ocid = "ocid1.autonomousdatabase.oc1..fake"
    monkeypatch.setattr(settings, "oracle_adb_ocid", ocid)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["lifecycle_state"] == "STARTING"
    assert f"start:{ocid}" in calls


def test_start_adb_reports_already_available(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "already_available"
    assert not any(call.startswith("start:") for call in calls)


def test_stop_adb_sends_stop_when_available(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    ocid = "ocid1.autonomousdatabase.oc1..fake"
    monkeypatch.setattr(settings, "oracle_adb_ocid", ocid)
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "AVAILABLE", calls)

    resp = client.post("/api/settings/database/adb/stop")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "accepted"
    assert data["lifecycle_state"] == "STOPPING"
    assert f"stop:{ocid}" in calls


def test_stop_adb_reports_already_stopped(monkeypatch: MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "oracle_adb_ocid", "ocid1.autonomousdatabase.oc1..fake")
    calls: list[str] = []
    _patch_adb_client(monkeypatch, "STOPPED", calls)

    resp = client.post("/api/settings/database/adb/stop")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "already_stopped"
    assert not any(call.startswith("stop:") for call in calls)


def test_start_adb_without_ocid_returns_not_configured(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "oracle_adb_ocid", "")

    resp = client.post("/api/settings/database/adb/start")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "not_configured"


def test_get_upload_storage_settings_returns_runtime_values(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "object_storage_region", "us-chicago-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "example-namespace")
    monkeypatch.setattr(settings, "object_storage_bucket", "rag-originals")
    monkeypatch.setattr(settings, "max_upload_bytes", 12345)

    resp = client.get("/api/settings/upload-storage")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "local"
    assert body["local_storage_dir"] == str(tmp_path / "uploads")
    assert body["object_storage_region"] == "us-chicago-1"
    assert body["object_storage_namespace"] == "example-namespace"
    assert body["object_storage_bucket"] == "rag-originals"
    assert body["readiness"] == "ok"
    assert body["max_upload_bytes"] == 12345
    assert body["config_source"] == "runtime"


def test_update_upload_storage_settings_persists_env_and_mutates_runtime(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_region", "us-chicago-1")
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    env_file = _upload_storage_env_file(
        monkeypatch,
        tmp_path,
        "\n".join(
            [
                "# 既存設定",
                "UPLOAD_STORAGE_BACKEND=local",
                "LOCAL_STORAGE_DIR=/old/uploads",
                "UPLOAD_STORAGE_BACKEND=duplicate",
                "",
            ]
        ),
    )

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "ok"
    assert settings.upload_storage_backend == "oci"
    assert settings.local_storage_dir == "/u01/production-ready-rag"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "# 既存設定" in persisted
    assert persisted.count("UPLOAD_STORAGE_BACKEND=") == 1
    assert "UPLOAD_STORAGE_BACKEND=oci" in persisted
    assert "LOCAL_STORAGE_DIR=/u01/production-ready-rag" in persisted
    assert "OBJECT_STORAGE_REGION=us-chicago-1" in persisted
    assert "OBJECT_STORAGE_NAMESPACE=global-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_upload_storage_settings_can_apply_namespace_from_oci_settings_draft(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_namespace": "oci-page-namespace",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    assert settings.object_storage_namespace == "oci-page-namespace"
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=oci-page-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def test_update_upload_storage_settings_does_not_mutate_runtime_when_env_write_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", "/old/uploads")
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    monkeypatch.setattr(settings, "object_storage_bucket", "old-bucket")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 500
    assert settings.upload_storage_backend == "local"
    assert settings.local_storage_dir == "/old/uploads"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == "old-bucket"


def test_update_upload_storage_settings_allows_missing_selected_backend_fields(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "global-namespace")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "missing"
    assert settings.upload_storage_backend == "oci"
    assert settings.object_storage_namespace == "global-namespace"
    assert settings.object_storage_bucket == ""
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=global-namespace" in persisted
    assert "OBJECT_STORAGE_BUCKET=" in persisted


def test_update_upload_storage_settings_allows_missing_global_namespace(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_namespace", "")
    env_file = _upload_storage_env_file(monkeypatch, tmp_path)

    resp = client.patch(
        "/api/settings/upload-storage",
        json={
            "backend": "oci",
            "local_storage_dir": "/u01/production-ready-rag",
            "object_storage_bucket": "rag-originals",
        },
    )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["backend"] == "oci"
    assert body["readiness"] == "missing"
    assert settings.object_storage_namespace == ""
    assert settings.object_storage_bucket == "rag-originals"
    persisted = env_file.read_text(encoding="utf-8")
    assert "OBJECT_STORAGE_NAMESPACE=" in persisted
    assert "OBJECT_STORAGE_BUCKET=rag-originals" in persisted


def _database_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    return _settings_env_file(monkeypatch, tmp_path, content)


def _upload_storage_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    return _settings_env_file(monkeypatch, tmp_path, content)


def _settings_env_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    content: str = "",
) -> Path:
    env_file = tmp_path / ".env"
    if content:
        env_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(settings_routes, "BACKEND_ENV_FILE", env_file)
    return env_file


def _payload() -> dict[str, Any]:
    return {
        "enterprise_ai": {
            "endpoint": "https://enterprise-ai.example",
            "project_ocid": "ocid1.generativeaiproject.oc1..example",
            "api_key": "sk-update-secret",
            "has_api_key": False,
            "clear_api_key": False,
            "models": [
                {
                    "model_id": "enterprise-llm",
                    "display_name": "標準 LLM",
                    "vision_enabled": False,
                },
                {
                    "model_id": "enterprise-vlm",
                    "display_name": "Vision LLM",
                    "vision_enabled": True,
                },
            ],
            "default_model_id": "enterprise-llm",
            "api_path": "/responses",
            "vlm_input_mode": "files_api",
            "text_payload_template": LLM_TEMPLATE,
            "vision_payload_template": VLM_TEMPLATE,
            "text_response_path": "/data/text",
            "vision_response_path": "/data/document",
            "timeout_seconds": 60.0,
            "max_retries": 2,
            "llm_max_output_tokens": 1600,
            "vlm_max_output_tokens": 64000,
        },
        "generative_ai": {
            "embedding_model": "cohere.embed-v4.0",
            "embedding_dim": 1536,
            "rerank_model": "cohere.rerank-v4.0-fast",
        },
    }


def _wallet_zip(entries: dict[str, str] | None = None) -> bytes:
    wallet_entries = entries or {
        "tnsnames.ora": "ragdb_high = ...",
        "sqlnet.ora": "WALLET_LOCATION = ...",
        "cwallet.sso": "ewallet-secret",
        "ewallet.pem": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
        "ewallet.p12": "password-wallet",
        "keystore.jks": "java-keystore",
    }
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in wallet_entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()
