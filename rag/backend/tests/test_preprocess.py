"""前処理(Preprocess)アダプター + 固定長 chunking + 派生系譜のテスト。"""

from rag_parser_core.preprocess import (
    ConvertOutcome,
    ConvertResponse,
    SourceDerivation,
    normalize_preprocess_profile,
)

from app.clients.preprocess_service import PreprocessServiceClient, normalize_text_bytes
from app.config import Settings
from app.rag.chunking import CHUNKING_STRATEGIES, chunk_extraction_with_strategy
from app.rag.chunking_strategy import (
    CHUNKING_STRATEGY_ORDER,
    CHUNKING_STRATEGY_SPECS,
)
from app.rag.kb_adapter_config import (
    KnowledgeBaseAdapterConfig,
    resolve_effective_settings,
)
from app.rag.preprocess_strategy import (
    PREPROCESS_PROFILE_ORDER,
    preprocess_runtime_settings,
    resolve_preprocess_profile,
)
from app.schemas.document import SourceModality, SourceProfile
from app.schemas.extraction import StructuredExtraction


def _text_profile(content_type: str = "text/plain") -> SourceProfile:
    return SourceProfile(
        original_file_name="note.txt",
        sanitized_file_name="note.txt",
        content_type=content_type,
        file_size_bytes=16,
        content_sha256="abc",
        modality=SourceModality.TEXT,
        parser_profile="text",
    )


# --- 契約(rag_parser_core)---


def test_convert_response_base64_roundtrip() -> None:
    outcome = ConvertOutcome(
        converted=True,
        converter_name="libreoffice",
        converter_version="v1",
        derived_bytes=b"%PDF-1.7 body",
        derived_content_type="application/pdf",
        page_map={"1": 1},
    )
    response = ConvertResponse.from_outcome(outcome)
    assert response.converted is True
    assert response.derived_bytes() == b"%PDF-1.7 body"
    assert response.page_map == {"1": 1}


def test_convert_response_passthrough_has_no_payload() -> None:
    response = ConvertResponse.from_outcome(ConvertOutcome.passthrough(reason="x"))
    assert response.converted is False
    assert response.derived_bytes() is None


def test_normalize_preprocess_profile_falls_back() -> None:
    assert normalize_preprocess_profile("OFFICE_TO_PDF") == "office_to_pdf"
    assert normalize_preprocess_profile("nope") == "passthrough"


def test_source_derivation_defaults() -> None:
    derivation = SourceDerivation(derivation_id="d1", preprocess_profile="text_normalize")
    assert derivation.converted is False
    assert derivation.created_at  # ISO timestamp


# --- readiness snapshot ---


def test_preprocess_runtime_snapshot_defaults_to_passthrough() -> None:
    snapshot = preprocess_runtime_settings(Settings())
    assert snapshot.profile == "passthrough"
    assert snapshot.service_enabled is False
    names = tuple(status.name for status in snapshot.profiles)
    assert names == PREPROCESS_PROFILE_ORDER
    selected = [status for status in snapshot.profiles if status.selected]
    assert [status.name for status in selected] == ["passthrough"]


def test_service_profiles_unavailable_without_service() -> None:
    snapshot = preprocess_runtime_settings(Settings(rag_preprocess_enabled=False))
    by_name = {status.name: status for status in snapshot.profiles}
    assert by_name["office_to_pdf"].available is False
    assert by_name["text_normalize"].available is True


def test_resolve_preprocess_profile_normalizes() -> None:
    assert resolve_preprocess_profile(Settings(rag_preprocess_profile="text_normalize")) == (
        "text_normalize"
    )


# --- in-process text_normalize ---


def test_normalize_text_bytes_nfkc_and_whitespace() -> None:
    derived, warnings = normalize_text_bytes(
        "ＡＢＣ　ｔｅｓｔ\r\n\r\n\r\n\r\n末尾  ".encode(),
        "text/plain",
    )
    text = derived.decode("utf-8")
    assert text.startswith("ABC test")  # 全角→半角(NFKC)
    assert "\r" not in text
    assert "\n\n\n\n" not in text  # 連続空行は 2 行(改行 3 連)までに圧縮
    assert text.endswith("末尾")  # 行末空白を除去
    assert isinstance(warnings, list)


def test_client_text_normalize_in_process() -> None:
    client = PreprocessServiceClient(Settings(rag_preprocess_profile="text_normalize"))
    outcome = client.convert(
        "ＡＢＣ".encode(),
        content_type="text/plain",
        source_profile=_text_profile(),
        profile="text_normalize",
    )
    assert outcome.converted is True
    assert outcome.converter_name == "text_normalize"
    assert outcome.derived_bytes == b"ABC"


def test_client_passthrough_for_binary_text_normalize() -> None:
    client = PreprocessServiceClient(Settings())
    pdf_profile = SourceProfile(
        original_file_name="a.pdf",
        sanitized_file_name="a.pdf",
        content_type="application/pdf",
        file_size_bytes=4,
        content_sha256="x",
        modality=SourceModality.PDF,
        parser_profile="pdf",
    )
    outcome = client.convert(
        b"%PDF",
        content_type="application/pdf",
        source_profile=pdf_profile,
        profile="text_normalize",
    )
    assert outcome.converted is False


def test_client_office_profile_degrades_without_service() -> None:
    client = PreprocessServiceClient(Settings(rag_preprocess_enabled=False))
    outcome = client.convert(
        b"PK\x03\x04",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_profile=None,
        profile="office_to_pdf",
    )
    assert outcome.converted is False
    assert "preprocess_service_disabled" in outcome.warnings


def test_client_service_unreachable_degrades_to_passthrough() -> None:
    # 到達不能 URL を指定 → HTTP 失敗 → passthrough へ安全に縮退する。
    client = PreprocessServiceClient(
        Settings(
            rag_preprocess_enabled=True,
            rag_preprocess_office_to_pdf_service_url="http://127.0.0.1:9/",
            rag_preprocess_service_timeout_seconds=0.2,
        )
    )
    outcome = client.convert(
        b"PK\x03\x04",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_profile=None,
        profile="office_to_pdf",
    )
    assert outcome.converted is False
    assert "preprocess_service_unreachable" in outcome.warnings


def test_csv_to_json_profile_routes_to_dedicated_service() -> None:
    # csv_to_json は専用サービス URL へルーティングされ、未達時は passthrough へ縮退する。
    client = PreprocessServiceClient(
        Settings(
            rag_preprocess_enabled=True,
            rag_preprocess_csv_to_json_service_url="http://127.0.0.1:9/",
            rag_preprocess_service_timeout_seconds=0.2,
        )
    )
    outcome = client.convert(
        b"a,b\n1,2\n",
        content_type="text/csv",
        source_profile=None,
        profile="csv_to_json",
    )
    assert outcome.converted is False
    assert "preprocess_service_unreachable" in outcome.warnings


def test_csv_to_json_profile_unconfigured_degrades() -> None:
    # サービス URL が空 → 未設定として安全に passthrough。
    client = PreprocessServiceClient(
        Settings(rag_preprocess_enabled=True, rag_preprocess_csv_to_json_service_url="")
    )
    outcome = client.convert(
        b"a,b\n1,2\n",
        content_type="text/csv",
        source_profile=None,
        profile="csv_to_json",
    )
    assert outcome.converted is False
    assert "preprocess_service_unconfigured" in outcome.warnings


def test_excel_to_json_profile_routes_to_dedicated_service() -> None:
    # excel_to_json は専用サービス URL へルーティングされ、未達時は passthrough へ縮退する。
    client = PreprocessServiceClient(
        Settings(
            rag_preprocess_enabled=True,
            rag_preprocess_excel_to_json_service_url="http://127.0.0.1:9/",
            rag_preprocess_service_timeout_seconds=0.2,
        )
    )
    outcome = client.convert(
        b"PK\x03\x04",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        source_profile=None,
        profile="excel_to_json",
    )
    assert outcome.converted is False
    assert "preprocess_service_unreachable" in outcome.warnings


# --- 固定長 chunking + KB 上書き ---


def test_fixed_size_strategy_registered() -> None:
    assert "fixed_size" in CHUNKING_STRATEGIES
    assert "fixed_size" in CHUNKING_STRATEGY_ORDER
    assert CHUNKING_STRATEGY_SPECS["fixed_size"].origin == "ragflow_general_fixed"


def test_fixed_size_chunking_is_deterministic_fixed_length() -> None:
    extraction = StructuredExtraction(
        raw_text="あいうえお。" * 200,
        document_type="ドキュメント",
        summary="x",
        elements=[],
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy="fixed_size", chunk_size=300, overlap=50
    )
    assert len(chunks) > 1
    # 末尾以外は chunk_size ちょうどの固定長。
    assert all(len(chunk.text) == 300 for chunk in chunks[:-1])
    assert chunks[0].metadata.get("chunk_strategy") == "fixed_size"
    assert chunks[0].metadata.get("chunk_fixed_size") is True


def test_fixed_size_kb_override() -> None:
    settings = Settings()
    config = KnowledgeBaseAdapterConfig.model_validate(
        {"ingestion": {"chunking_strategy": "fixed_size", "chunk_size": 512, "chunk_overlap": 64}}
    )
    effective = resolve_effective_settings(settings, config, scope="ingestion")
    assert effective.rag_chunking_strategy == "fixed_size"
    assert effective.rag_chunk_size == 512
    assert effective.rag_chunk_overlap == 64


def test_preprocess_profile_kb_override() -> None:
    settings = Settings()
    config = KnowledgeBaseAdapterConfig.model_validate(
        {"ingestion": {"preprocess_profile": "office_to_pdf"}}
    )
    effective = resolve_effective_settings(settings, config, scope="ingestion")
    assert effective.rag_preprocess_profile == "office_to_pdf"


# --- 派生系譜(溯源)の貫通 ---


def test_source_derivation_threads_into_extraction_and_chunks() -> None:
    from app.rag.chunking import Chunk
    from app.rag.ingestion import (
        _chunks_with_source_derivation,
        _extraction_with_source_derivation,
        _passthrough_derivation,
        _source_derivation_id,
    )

    derivation = _passthrough_derivation(
        profile="text_normalize", source_sha="deadbeef", content_type="text/plain"
    )
    extraction = StructuredExtraction(raw_text="x", document_type="ドキュメント", summary="s")
    enriched = _extraction_with_source_derivation(extraction, derivation)
    stored = enriched.parser_artifacts["source_derivation"]
    assert isinstance(stored, dict)
    assert stored["derivation_id"] == derivation.derivation_id
    assert _source_derivation_id(enriched) == derivation.derivation_id

    chunks = [Chunk(text="a", index=0, start_offset=0, end_offset=1, metadata={})]
    stamped = _chunks_with_source_derivation(chunks, _source_derivation_id(enriched))
    assert stamped[0].metadata["source_derivation_id"] == derivation.derivation_id


def test_source_derivation_id_absent_returns_none() -> None:
    from app.rag.ingestion import _source_derivation_id

    extraction = StructuredExtraction(raw_text="x", document_type="ドキュメント", summary="s")
    assert _source_derivation_id(extraction) is None
