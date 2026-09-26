"""設定 API。"""

import asyncio
import base64
import logging
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pr_system_settings.database import build_database_router
from pr_system_settings.model import build_model_router, model_payload
from pr_system_settings.oci import build_oci_router
from pr_system_settings.oci import test_oci_config as _test_oci_config
from pr_system_settings.upload_storage import (
    build_upload_storage_router,
)
from rag_parser_core.capabilities import ADAPTER_CAPABILITIES, supported_modalities
from rag_pipeline_core.retrieval import decompose_retrieval_strategy

from app.auth import AuthSession
from app.clients.external_parser import (
    ENGINE_SPECS,
    ExternalParserBackend,
    ExternalParserClient,
    external_parser_connection,
)
from app.clients.oci_document_understanding import OciDocumentUnderstandingClient
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.clients.oracle import (
    CustomPromptNotConfiguredError,
    GenerationSettingsRevisionConflictError,
    OracleClient,
    StoredGenerationSettings,
    StoredPromptVersion,
    close_oracle_pool,
    test_oracle_connection,
)
from app.config import (
    MODEL_SETTINGS_STORE,
    Settings,
    get_settings,
)
from app.rag.agentic_adapter import (
    agentic_adapter_runtime_settings,
    normalize_agentic_profile,
)
from app.rag.chunking_strategy import (
    chunking_runtime_settings,
    normalize_chunking_strategy,
)
from app.rag.docrag_prompts import (
    EDITABLE_PROMPT_KEYS,
    readonly_prompt_stages,
)
from app.rag.docrag_prompts import default_prompt as default_docrag_prompt
from app.rag.docrag_prompts import required_placeholders as docrag_required_placeholders
from app.rag.docrag_prompts import validate_prompt as validate_docrag_prompt
from app.rag.evaluation_adapter import (
    evaluation_adapter_runtime_settings,
    normalize_evaluation_suite,
)
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    load_field_schema,
    save_field_schema,
)
from app.rag.generation_adapter import (
    generation_adapter_runtime_settings,
    normalize_generation_profile,
)
from app.rag.graph_adapter import (
    graph_adapter_runtime_settings,
    normalize_graph_profile,
)
from app.rag.grounding_adapter import (
    grounding_adapter_runtime_settings,
    normalize_post_retrieval_pipeline,
)
from app.rag.guardrail_adapter import (
    guardrail_adapter_runtime_settings,
    normalize_guardrail_policy,
)
from app.rag.oracle_schema import vector_index_reindex_sql
from app.rag.parser_adapter_contract import (
    parser_adapter_contract_artifact_payload,
    run_parser_adapter_compatibility_matrix,
)
from app.rag.parser_adapter_readiness import parser_adapter_runtime_settings
from app.rag.parser_adapter_scorecard import (
    ParserAdapterSourceRoute,
    build_parser_adapter_scorecard,
    build_parser_adapter_source_routes,
)
from app.rag.preprocess_strategy import (
    normalize_preprocess_profile,
    preprocess_runtime_settings,
)
from app.rag.retrieval_adapter import (
    RetrievalStrategyStatus,
    retrieval_adapter_runtime_settings,
)
from app.rag.system_schema import (
    SystemSchemaError,
    oracle_error_code,
    system_schema_manager,
)
from app.rag.system_schema_runtime import system_schema_runtime
from app.rag.vector_index_adapter import (
    normalize_vector_index_profile,
    vector_index_adapter_runtime_settings,
)
from app.schemas.common import ApiResponse
from app.schemas.evaluation import EvaluationThresholds
from app.schemas.settings import (
    AgenticProfileStatusData,
    AgenticSettingsData,
    AgenticSettingsUpdate,
    AnswerRecordSettingsData,
    AnswerRecordSettingsUpdate,
    ChunkingSettingsData,
    ChunkingSettingsUpdate,
    ChunkingStrategyStatusData,
    DocragPromptsData,
    DocragPromptUpdate,
    DocragPromptView,
    EvaluationSettingsData,
    EvaluationSettingsUpdate,
    EvaluationSuiteStatusData,
    ExternalParserConnectionData,
    ExternalParserConnectionStatusData,
    ExtractionFieldsSettingsData,
    ExtractionFieldsSettingsUpdate,
    FieldDefinitionData,
    GenerationProfileStatusData,
    GenerationSettingsData,
    GenerationSettingsUpdate,
    GraphProfileStatusData,
    GraphSettingsData,
    GraphSettingsUpdate,
    GroundingPipelineStatusData,
    GroundingSettingsData,
    GroundingSettingsUpdate,
    GuardrailPolicyStatusData,
    GuardrailSettingsData,
    GuardrailSettingsUpdate,
    HuggingFaceSettingsData,
    HuggingFaceSettingsUpdate,
    ModelSettingsTestRequest,
    OciConfigField,
    ParserAdapterBackendSourceMatrixData,
    ParserAdapterContractCaseData,
    ParserAdapterContractData,
    ParserAdapterContractSummaryData,
    ParserAdapterScorecardData,
    ParserAdapterScorecardEntryData,
    ParserAdapterSettingsData,
    ParserAdapterSettingsUpdate,
    ParserAdapterSourceRouteData,
    ParserAdapterStatusData,
    ParserBackendCapabilityData,
    ParserServiceBackendData,
    PreprocessProfileStatusData,
    PreprocessSettingsData,
    PreprocessSettingsUpdate,
    PromptVersionCreate,
    PromptVersionData,
    PromptVersionsData,
    RetrievalSettingsData,
    RetrievalSettingsUpdate,
    RetrievalStrategyStatusData,
    SystemTablesInitializeRequest,
    SystemTablesOperationData,
    SystemTablesStatusData,
    VectorIndexProfileStatusData,
    VectorIndexSettingsData,
    VectorIndexSettingsUpdate,
)

router = APIRouter()
logger = logging.getLogger(__name__)
OCI_DIRECTORY_MODE = 0o700
BACKEND_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"

# アップロード保存先は3製品共通の実装（platform の pr_system_settings。#97）。
# テストで get_settings / BACKEND_ENV_FILE を差し替えられるよう、呼出時に module の値を参照する。
router.include_router(
    build_upload_storage_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: BACKEND_ENV_FILE,
    )
)
# OCI 認証も3製品共通の実装（pr_system_settings.oci。#100）。
router.include_router(
    build_oci_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: BACKEND_ENV_FILE,
    )
)
# モデル設定も3製品共通の実装（pr_system_settings.model。#103）。
router.include_router(
    build_model_router(
        get_settings=lambda: get_settings(),
        store=MODEL_SETTINGS_STORE,
        run_model_test=lambda settings, request: _run_model_settings_test(settings, request),
    )
)
# データベース設定も3製品共通の実装（pr_system_settings.database。#108）。
# 接続そのものと接続 pool の後始末は RAG のものを渡す。Walletless TLS と
# パスワードの表示は、RAG の接続処理と権限が対応していないため有効にしない。
router.include_router(
    build_database_router(
        get_settings=lambda: get_settings(),
        env_file=lambda: BACKEND_ENV_FILE,
        test_connection=lambda candidate: test_oracle_connection(candidate),
        on_saved=lambda _settings: close_oracle_pool(),
    )
)
ENV_FILE_MODE = 0o600
ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
OCI_CONFIG_KEYS: tuple[OciConfigField, ...] = (
    "user",
    "fingerprint",
    "tenancy",
    "region",
    "key_file",
)
MODEL_TEST_IMAGE_BYTES = base64.b64decode(
    "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"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAD//2Q=="
)


@router.get(
    "/database/system-tables",
    response_model=ApiResponse[SystemTablesStatusData],
)
async def get_system_tables_status() -> ApiResponse[SystemTablesStatusData]:
    """RAG system table の状態を DDL なしで取得する。"""

    try:
        data = await asyncio.to_thread(system_schema_manager.status)
    except Exception as exc:
        code = oracle_error_code(exc)
        safe_code = code if code.startswith("ORA-") else "SCHEMA_STATUS_UNAVAILABLE"
        raise HTTPException(
            status_code=503,
            detail=f"システムテーブルの状態を取得できませんでした ({safe_code})。",
        ) from exc
    return ApiResponse(data=SystemTablesStatusData.model_validate(data))


@router.post(
    "/database/system-tables/initialize",
    response_model=ApiResponse[SystemTablesOperationData],
)
async def initialize_system_tables(
    payload: SystemTablesInitializeRequest,
    request: Request,
) -> ApiResponse[SystemTablesOperationData] | JSONResponse:
    """管理者の明示操作として作成・更新または全再作成する。"""

    session = getattr(request.state, "auth_session", None)
    if not isinstance(session, AuthSession) or session.role not in {"ADMIN", "LOCAL"}:
        raise HTTPException(
            status_code=403,
            detail="システムテーブル操作には管理者権限が必要です。",
        )
    try:
        data = await asyncio.to_thread(
            system_schema_manager.initialize,
            recreate=payload.recreate,
            confirmation=payload.confirmation,
        )
    except SystemSchemaError as exc:
        headers = {"Retry-After": "5"} if exc.code == "ORA-00054" else None
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={
                "data": None,
                "error_messages": [exc.public_message],
                "warning_messages": [],
                "error_code": exc.code,
            },
        )
    system_schema_runtime.invalidate()
    return ApiResponse(data=SystemTablesOperationData.model_validate(data))


@router.get("/huggingface", response_model=ApiResponse[HuggingFaceSettingsData])
async def get_huggingface_settings() -> ApiResponse[HuggingFaceSettingsData]:
    """HuggingFace モデルダウンロード設定を返す。token 実値は返さない。"""
    return ApiResponse(data=_huggingface_settings_data(get_settings()))


@router.patch("/huggingface", response_model=ApiResponse[HuggingFaceSettingsData])
async def update_huggingface_settings(
    payload: HuggingFaceSettingsUpdate,
) -> ApiResponse[HuggingFaceSettingsData]:
    """HuggingFace 設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = _huggingface_settings_candidate(settings, payload)
    _persist_huggingface_settings(candidate)
    _apply_huggingface_settings(settings, candidate)
    return ApiResponse(data=_huggingface_settings_data(settings))


@router.get("/parser-adapters", response_model=ApiResponse[ParserAdapterSettingsData])
async def get_parser_adapter_settings() -> ApiResponse[ParserAdapterSettingsData]:
    """任意 parser adapter の feature flag と package readiness を返す。"""
    return ApiResponse(data=_parser_adapter_settings_data(get_settings()))


@router.get(
    "/parser-adapters/contract",
    response_model=ApiResponse[ParserAdapterContractData],
)
async def get_parser_adapter_contract() -> ApiResponse[ParserAdapterContractData]:
    """任意 parser adapter の schema remap compatibility matrix を返す。"""
    return ApiResponse(data=_parser_adapter_contract_data(get_settings()))


@router.get(
    "/parser-adapters/{backend}/status",
    response_model=ApiResponse[ExternalParserConnectionStatusData],
)
async def get_external_parser_status(
    backend: ExternalParserBackend,
) -> ApiResponse[ExternalParserConnectionStatusData]:
    """外部 GPU parser の native health/models endpoint を確認する。"""
    result = await asyncio.to_thread(ExternalParserClient(get_settings()).status, backend)
    return ApiResponse(
        data=ExternalParserConnectionStatusData(
            backend=result.backend,
            status=result.status,
            version=result.version,
            warning_code=result.warning_code,
        )
    )


@router.patch("/parser-adapters", response_model=ApiResponse[ParserAdapterSettingsData])
async def update_parser_adapter_settings(
    payload: ParserAdapterSettingsUpdate,
) -> ApiResponse[ParserAdapterSettingsData]:
    """任意 parser adapter の backend/feature flag を共有設定と runtime へ反映する。"""
    settings = get_settings()
    # モデル設定と同じ model-settings.json を、同じロックの下で書き換える（#103）。
    # API key（モデル・parser）は JSON に書かず .env に保存し、旧 JSON に残っていれば移す（#106）。
    try:
        with MODEL_SETTINGS_STORE.lock(settings):
            MODEL_SETTINGS_STORE.reload_if_changed(settings)
            candidate = _parser_adapter_settings_candidate(settings, payload)
            api_key = settings.oci_enterprise_ai_api_key
            MODEL_SETTINGS_STORE.save(candidate, model_payload(settings), api_key=api_key)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="設定を共有永続化ファイルへ保存できませんでした。",
        ) from exc
    # 保存したファイルから読み直し、runtime を保存した状態にそろえる。
    MODEL_SETTINGS_STORE.load(settings, refresh_secret=True)
    return ApiResponse(data=_parser_adapter_settings_data(settings))


@router.get("/preprocess", response_model=ApiResponse[PreprocessSettingsData])
async def get_preprocess_settings() -> ApiResponse[PreprocessSettingsData]:
    """前処理(Preprocess)アダプター(parse 前の原本変換)の選択と利用可否を返す。"""
    return ApiResponse(data=_preprocess_settings_data(get_settings()))


@router.patch("/preprocess", response_model=ApiResponse[PreprocessSettingsData])
async def update_preprocess_settings(
    payload: PreprocessSettingsUpdate,
) -> ApiResponse[PreprocessSettingsData]:
    """ファイル準備設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = _preprocess_settings_candidate(settings, payload)
    _persist_preprocess_settings(candidate)
    _apply_preprocess_settings(settings, candidate)
    return ApiResponse(data=_preprocess_settings_data(settings))


@router.get("/chunking", response_model=ApiResponse[ChunkingSettingsData])
async def get_chunking_settings() -> ApiResponse[ChunkingSettingsData]:
    """Chunking アダプター(分割戦略)の選択と多様化パラメータを返す。"""
    return ApiResponse(data=_chunking_settings_data(get_settings()))


@router.patch("/chunking", response_model=ApiResponse[ChunkingSettingsData])
async def update_chunking_settings(
    payload: ChunkingSettingsUpdate,
) -> ApiResponse[ChunkingSettingsData]:
    """文書分割設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = _chunking_settings_candidate(settings, payload)
    _persist_chunking_settings(candidate)
    _apply_chunking_settings(settings, candidate)
    return ApiResponse(data=_chunking_settings_data(settings))


@router.get("/retrieval", response_model=ApiResponse[RetrievalSettingsData])
async def get_retrieval_settings() -> ApiResponse[RetrievalSettingsData]:
    """Retrieval アダプター(検索戦略)の選択と解決内容を返す。"""
    return ApiResponse(data=_retrieval_settings_data(get_settings()))


@router.patch("/retrieval", response_model=ApiResponse[RetrievalSettingsData])
async def update_retrieval_settings(
    payload: RetrievalSettingsUpdate,
) -> ApiResponse[RetrievalSettingsData]:
    """検索方法設定を backend/.env と現在プロセスへ反映する。

    保存は常に新形式(検索モード + トグル)。.env に残る legacy 複合値は、
    この保存を通るときモード + トグルへ正規化される。
    """
    settings = get_settings()
    candidate = settings.model_copy(update=_retrieval_settings_updates(settings, payload))
    _persist_retrieval_settings(candidate)
    _apply_retrieval_settings(settings, candidate)
    return ApiResponse(data=_retrieval_settings_data(settings))


@router.get("/grounding", response_model=ApiResponse[GroundingSettingsData])
async def get_grounding_settings() -> ApiResponse[GroundingSettingsData]:
    """Grounding アダプター(検索後処理)の選択と解決内容を返す。"""
    return ApiResponse(data=_grounding_settings_data(get_settings()))


@router.patch("/grounding", response_model=ApiResponse[GroundingSettingsData])
async def update_grounding_settings(
    payload: GroundingSettingsUpdate,
) -> ApiResponse[GroundingSettingsData]:
    """根拠確認設定(処理方式 + CRAG 閾値)を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    updates: dict[str, object] = {}
    if payload.pipeline is not None:
        updates["rag_post_retrieval_pipeline"] = normalize_post_retrieval_pipeline(payload.pipeline)
    if payload.crag_low_confidence_threshold is not None:
        updates["rag_grounding_crag_confidence_threshold"] = payload.crag_low_confidence_threshold
    if payload.crag_high_confidence_threshold is not None:
        updates["rag_crag_high_confidence_threshold"] = payload.crag_high_confidence_threshold
    if payload.crag_max_hops is not None:
        updates["rag_crag_max_hops"] = payload.crag_max_hops
    if payload.crag_low_evidence_abstain is not None:
        updates["rag_crag_low_evidence_abstain_enabled"] = payload.crag_low_evidence_abstain
    candidate = settings.model_copy(update=updates)
    _persist_grounding_settings(candidate)
    _apply_grounding_settings(settings, candidate)
    return ApiResponse(data=_grounding_settings_data(settings))


@router.get("/generation", response_model=ApiResponse[GenerationSettingsData])
async def get_generation_settings() -> ApiResponse[GenerationSettingsData]:
    """Generation アダプター(回答生成プロファイル)の選択と解決内容を返す。"""
    stored = await OracleClient().get_generation_settings()
    return ApiResponse(data=_generation_settings_data(get_settings(), stored))


@router.patch("/generation", response_model=ApiResponse[GenerationSettingsData])
async def update_generation_settings(
    payload: GenerationSettingsUpdate,
) -> ApiResponse[GenerationSettingsData]:
    """回答スタイル設定を Oracle GLOBAL 行へ revision 付きで保存する。"""
    client = OracleClient()
    try:
        stored = await client.update_generation_settings(
            profile=normalize_generation_profile(payload.profile),
            expected_revision=payload.expected_revision,
        )
    except (GenerationSettingsRevisionConflictError, CustomPromptNotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ApiResponse(data=_generation_settings_data(get_settings(), stored))


def _prompt_versions_data(
    settings: StoredGenerationSettings,
    versions: list[StoredPromptVersion],
) -> PromptVersionsData:
    """Oracle prompt 版 store を互換 API 形へ変換する。"""
    return PromptVersionsData(
        active_version_id=settings.active_prompt_version_id,
        settings_revision=settings.revision,
        versions=[
            PromptVersionData(
                version_id=version.version_id,
                name=version.name,
                system_prompt=version.system_prompt,
                note=version.note,
                created_at=version.created_at,
                created_by=version.created_by_hash or "",
                active=version.version_id == settings.active_prompt_version_id,
            )
            for version in versions
        ],
    )


@router.get("/answer-records", response_model=ApiResponse[AnswerRecordSettingsData])
async def get_answer_record_settings() -> ApiResponse[AnswerRecordSettingsData]:
    """DocRAG 回答記録の保持日数を返す。"""
    return ApiResponse(
        data=AnswerRecordSettingsData(
            retention_days=get_settings().rag_answer_record_retention_days
        )
    )


@router.patch("/answer-records", response_model=ApiResponse[AnswerRecordSettingsData])
async def update_answer_record_settings(
    payload: AnswerRecordSettingsUpdate,
) -> ApiResponse[AnswerRecordSettingsData]:
    """保持日数を backend/.env と現在プロセスへ反映し、期限切れの記録を削除する。"""
    settings = get_settings()
    _write_env_values(
        BACKEND_ENV_FILE,
        {"RAG_ANSWER_RECORD_RETENTION_DAYS": str(payload.retention_days)},
        section_comment="# DocRAG 回答記録",
        error_detail="回答記録の保持設定を backend/.env へ保存できませんでした。",
    )
    settings.rag_answer_record_retention_days = payload.retention_days
    if payload.retention_days > 0:
        try:
            await OracleClient().purge_answer_records(payload.retention_days)
        except Exception as exc:  # 次の回答保存時にも削除するため、設定保存は止めない。
            logger.warning("answer record purge failed", extra={"error": str(exc)})
    return ApiResponse(data=AnswerRecordSettingsData(retention_days=payload.retention_days))


@router.get("/docrag-prompts", response_model=ApiResponse[DocragPromptsData])
async def get_docrag_prompts() -> ApiResponse[DocragPromptsData]:
    """編集できる DocRAG プロンプトと、回答フローの各段の読み取り専用プロンプトを返す。"""
    saved = await OracleClient().list_docrag_prompts()
    return ApiResponse(data=_docrag_prompts_data(saved))


@router.put("/docrag-prompts/{key}", response_model=ApiResponse[DocragPromptsData])
async def put_docrag_prompt(
    key: str, payload: DocragPromptUpdate
) -> ApiResponse[DocragPromptsData]:
    """DocRAG プロンプトを保存する(次の回答・次の解析から使う)。"""
    try:
        validate_docrag_prompt(key, payload.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="プロンプトが見つかりません。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    oracle = OracleClient()
    await oracle.save_docrag_prompt(key, payload.content)
    return ApiResponse(data=_docrag_prompts_data(await oracle.list_docrag_prompts()))


@router.delete("/docrag-prompts/{key}", response_model=ApiResponse[DocragPromptsData])
async def reset_docrag_prompt(key: str) -> ApiResponse[DocragPromptsData]:
    """保存した DocRAG プロンプトを消して既定値へ戻す。"""
    if key not in EDITABLE_PROMPT_KEYS:
        raise HTTPException(status_code=404, detail="プロンプトが見つかりません。")
    oracle = OracleClient()
    await oracle.delete_docrag_prompt(key)
    return ApiResponse(data=_docrag_prompts_data(await oracle.list_docrag_prompts()))


def _docrag_prompts_data(saved: dict[str, dict[str, object]]) -> DocragPromptsData:
    prompts = []
    for key in EDITABLE_PROMPT_KEYS:
        row = saved.get(key)
        prompts.append(
            DocragPromptView.model_validate(
                {
                    "key": key,
                    "content": row["content"] if row else default_docrag_prompt(key),
                    "default_content": default_docrag_prompt(key),
                    "customized": row is not None,
                    "required_placeholders": list(docrag_required_placeholders(key)),
                    "updated_at": row["updated_at"] if row else None,
                }
            )
        )
    return DocragPromptsData.model_validate(
        {"prompts": prompts, "stages": readonly_prompt_stages()}
    )


@router.get("/prompts", response_model=ApiResponse[PromptVersionsData])
async def get_prompt_versions() -> ApiResponse[PromptVersionsData]:
    """回答生成 system prompt の版一覧と有効版を返す(custom profile が使用)。"""
    settings, versions = await OracleClient().list_prompt_versions()
    return ApiResponse(data=_prompt_versions_data(settings, versions))


@router.post("/prompts", response_model=ApiResponse[PromptVersionsData])
async def create_prompt_version_endpoint(
    payload: PromptVersionCreate,
) -> ApiResponse[PromptVersionsData]:
    """新しい prompt 版を作成する(activate=true で即時有効化)。"""
    settings, versions = await OracleClient().create_prompt_version(
        name=payload.name,
        system_prompt=payload.system_prompt,
        note=payload.note,
        activate=payload.activate,
    )
    return ApiResponse(data=_prompt_versions_data(settings, versions))


@router.post("/prompts/{version_id}/activate", response_model=ApiResponse[PromptVersionsData])
async def activate_prompt_version_endpoint(
    version_id: str,
) -> ApiResponse[PromptVersionsData]:
    """指定 prompt 版を有効化する(rollback = 旧版を再有効化)。"""
    try:
        settings, versions = await OracleClient().activate_prompt_version(version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="指定の prompt 版が見つかりません。") from exc
    return ApiResponse(data=_prompt_versions_data(settings, versions))


def _extraction_fields_data() -> ExtractionFieldsSettingsData:
    """field schema 定義を非機密 API 形へ変換する。"""
    store = load_field_schema()
    return ExtractionFieldsSettingsData(
        fields=[
            FieldDefinitionData(
                name=field.name,
                description=field.description,
                value_type=field.value_type,
            )
            for field in store.fields
        ]
    )


@router.get("/extraction-fields", response_model=ApiResponse[ExtractionFieldsSettingsData])
async def get_extraction_fields_settings() -> ApiResponse[ExtractionFieldsSettingsData]:
    """field 抽出 schema 定義(抽出対象 field の一覧)を返す。"""
    return ApiResponse(data=_extraction_fields_data())


@router.patch("/extraction-fields", response_model=ApiResponse[ExtractionFieldsSettingsData])
async def update_extraction_fields_settings(
    payload: ExtractionFieldsSettingsUpdate,
) -> ApiResponse[ExtractionFieldsSettingsData]:
    """field 抽出 schema 定義を保存する(name 重複は 422)。"""
    definitions = [
        FieldDefinition(name=field.name, description=field.description, value_type=field.value_type)
        for field in payload.fields
    ]
    try:
        save_field_schema(definitions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=_extraction_fields_data())


@router.get("/guardrail", response_model=ApiResponse[GuardrailSettingsData])
async def get_guardrail_settings() -> ApiResponse[GuardrailSettingsData]:
    """Guardrail アダプター(安全ポリシー)の選択と解決内容を返す。"""
    return ApiResponse(data=_guardrail_settings_data(get_settings()))


@router.patch("/guardrail", response_model=ApiResponse[GuardrailSettingsData])
async def update_guardrail_settings(
    payload: GuardrailSettingsUpdate,
) -> ApiResponse[GuardrailSettingsData]:
    """安全チェック設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    update: dict[str, object] = {"rag_guardrail_policy": normalize_guardrail_policy(payload.policy)}
    if payload.backend is not None:
        update["rag_guardrail_backend"] = payload.backend
    candidate = settings.model_copy(update=update)
    readiness_issue = _oci_guardrails_warning_code(candidate)
    if candidate.rag_guardrail_backend == "oci_guardrails" and readiness_issue:
        raise HTTPException(
            status_code=422,
            detail=(
                "OCI Guardrails を選択する前に、compartment と OCI API キー認証を"
                "システム設定 > OCI 認証で設定してください。"
            ),
        )
    _persist_guardrail_settings(candidate)
    settings.rag_guardrail_policy = candidate.rag_guardrail_policy
    settings.rag_guardrail_backend = candidate.rag_guardrail_backend
    return ApiResponse(data=_guardrail_settings_data(settings))


@router.get("/vector-index", response_model=ApiResponse[VectorIndexSettingsData])
async def get_vector_index_settings() -> ApiResponse[VectorIndexSettingsData]:
    """Vector Index アダプター(索引/検索精度)の選択と解決内容を返す。"""
    return ApiResponse(data=_vector_index_settings_data(get_settings()))


@router.patch("/vector-index", response_model=ApiResponse[VectorIndexSettingsData])
async def update_vector_index_settings(
    payload: VectorIndexSettingsUpdate,
) -> ApiResponse[VectorIndexSettingsData]:
    """検索インデックス設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = settings.model_copy(
        update={"rag_vector_index_profile": normalize_vector_index_profile(payload.profile)}
    )
    _persist_vector_index_settings(candidate)
    settings.rag_vector_index_profile = candidate.rag_vector_index_profile
    return ApiResponse(data=_vector_index_settings_data(settings))


@router.get("/evaluation-suite", response_model=ApiResponse[EvaluationSettingsData])
async def get_evaluation_settings() -> ApiResponse[EvaluationSettingsData]:
    """Evaluation アダプター(評価スイート/閾値)の選択と解決内容を返す。"""
    return ApiResponse(data=_evaluation_settings_data(get_settings()))


@router.patch("/evaluation-suite", response_model=ApiResponse[EvaluationSettingsData])
async def update_evaluation_settings(
    payload: EvaluationSettingsUpdate,
) -> ApiResponse[EvaluationSettingsData]:
    """品質評価設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = settings.model_copy(
        update={"rag_evaluation_suite": normalize_evaluation_suite(payload.suite)}
    )
    _persist_evaluation_settings(candidate)
    settings.rag_evaluation_suite = candidate.rag_evaluation_suite
    return ApiResponse(data=_evaluation_settings_data(settings))


@router.get("/graph", response_model=ApiResponse[GraphSettingsData])
async def get_graph_settings() -> ApiResponse[GraphSettingsData]:
    """GraphRAG アダプター(知識グラフ構築)の選択と解決内容を返す。"""
    return ApiResponse(data=_graph_settings_data(get_settings()))


@router.patch("/graph", response_model=ApiResponse[GraphSettingsData])
async def update_graph_settings(
    payload: GraphSettingsUpdate,
) -> ApiResponse[GraphSettingsData]:
    """関係情報設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = settings.model_copy(
        update={
            "rag_graph_profile": normalize_graph_profile(payload.profile),
            # UI で明示保存したら新 profile を正本にし、legacy の full 強制上書きを退役させる。
            "rag_graph_enabled": False,
        }
    )
    _persist_graph_settings(candidate)
    settings.rag_graph_profile = candidate.rag_graph_profile
    settings.rag_graph_enabled = candidate.rag_graph_enabled
    return ApiResponse(data=_graph_settings_data(settings))


@router.get("/agentic", response_model=ApiResponse[AgenticSettingsData])
async def get_agentic_settings() -> ApiResponse[AgenticSettingsData]:
    """Agentic アダプター(クエリ計画)の選択と解決内容を返す。"""
    return ApiResponse(data=_agentic_settings_data(get_settings()))


@router.patch("/agentic", response_model=ApiResponse[AgenticSettingsData])
async def update_agentic_settings(
    payload: AgenticSettingsUpdate,
) -> ApiResponse[AgenticSettingsData]:
    """高度な検索設定を backend/.env と現在プロセスへ反映する。"""
    settings = get_settings()
    candidate = settings.model_copy(
        update={
            "rag_agentic_profile": normalize_agentic_profile(payload.profile),
            "rag_agentic_max_subqueries": payload.max_subqueries,
        }
    )
    _persist_agentic_settings(candidate)
    settings.rag_agentic_profile = candidate.rag_agentic_profile
    settings.rag_agentic_max_subqueries = candidate.rag_agentic_max_subqueries
    return ApiResponse(data=_agentic_settings_data(settings))


async def _run_model_settings_test(
    settings: Settings,
    request: ModelSettingsTestRequest,
) -> dict[str, str | int | float | bool | None]:
    """対象モデルの実 API 呼び出しを行い、表示用 details を返す（共有 router の hook）。"""
    if request.target_type == "enterprise_text":
        text = await OciEnterpriseAiClient(settings=settings).generate(
            "モデル接続テストです。短く応答してください。",
            "これは Production Ready RAG のモデル接続テスト用コンテキストです。",
        )
        return {"response_chars": len(text), "surface": "llm"}
    if request.target_type == "enterprise_vision":
        text = await OciEnterpriseAiClient(settings=settings).generate_from_image(
            MODEL_TEST_IMAGE_BYTES,
            "白い背景にある大きな図形の色を日本語で1語だけ返してください。",
            mime_type="image/jpeg",
        )
        return {
            "surface": "vision",
            "response_chars": len(text),
        }
    if request.target_type == "embedding":
        vectors = await OciGenAiClient(settings=settings).embed(
            ["モデル接続テスト"],
            input_type="SEARCH_QUERY",
        )
        vector = vectors[0] if vectors else []
        return {"vector_dim": len(vector), "input_count": len(vectors)}
    ranks = await OciGenAiClient(settings=settings).rerank(
        "モデル接続テスト",
        [
            "これはモデル接続テストに関する候補文書です。",
            "別の業務文書に関する候補文書です。",
        ],
        top_n=1,
    )
    top_score = ranks[0][1] if ranks else None
    return {"ranked_count": len(ranks), "top_score": top_score}


def _huggingface_settings_data(settings: Settings) -> HuggingFaceSettingsData:
    """Settings から HuggingFace 設定の表示用データを作る(token 実値は返さない)。"""
    return HuggingFaceSettingsData(
        endpoint=settings.huggingface_endpoint,
        token_configured=bool(settings.huggingface_token.strip()),
        config_source="runtime",
    )


def _huggingface_settings_candidate(
    base: Settings,
    payload: HuggingFaceSettingsUpdate,
) -> Settings:
    """更新 payload を適用した一時 Settings を作る。"""
    updates = {
        "huggingface_endpoint": payload.endpoint,
        "huggingface_token": _secret_value(
            current=base.huggingface_token,
            update=payload.token,
            clear=payload.clear_token,
        ),
    }
    return base.model_copy(update=updates)


def _apply_huggingface_settings(target: Settings, source: Settings) -> None:
    """HuggingFace 関連設定だけ現在プロセスへ反映する。"""
    target.huggingface_endpoint = source.huggingface_endpoint
    target.huggingface_token = source.huggingface_token


def _persist_huggingface_settings(settings: Settings) -> None:
    """HuggingFace 設定を backend/.env へ永続化する(env キーは標準名)。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "HF_TOKEN": settings.huggingface_token,
            "HF_ENDPOINT": settings.huggingface_endpoint,
        },
        section_comment="# HuggingFace モデルダウンロード",
        error_detail="HuggingFace 設定を backend/.env へ保存できませんでした。",
    )


def _parser_service_backends_data(settings: Settings) -> list[ParserServiceBackendData]:
    """service 系 parser backend の選択状態と設定可用性を作る。"""
    selected = str(getattr(settings, "rag_parser_adapter_backend", "local"))
    vlm_configured = bool(settings.oci_enterprise_ai_endpoint.strip())
    du_configured = OciDocumentUnderstandingClient(settings=settings).is_configured()
    return [
        ParserServiceBackendData(
            backend="oci_genai_vision",
            # 旧称 enterprise_ai_vlm も選択値として受理(後方互換エイリアス)。
            selected=selected in ("oci_genai_vision", "enterprise_ai_vlm"),
            configured=vlm_configured,
            warning_code=(None if vlm_configured else "enterprise_ai_endpoint_unconfigured"),
        ),
        ParserServiceBackendData(
            backend="oci_document_understanding",
            selected=selected == "oci_document_understanding",
            configured=du_configured,
            warning_code=(None if du_configured else "oci_document_understanding_unconfigured"),
        ),
    ]


def _parser_adapter_settings_data(settings: Settings) -> ParserAdapterSettingsData:
    """Settings から parser adapter readiness の表示用データを作る。"""
    runtime = parser_adapter_runtime_settings(settings)
    scorecard = build_parser_adapter_scorecard(runtime)
    source_routes = build_parser_adapter_source_routes(runtime)
    route_data = [_parser_adapter_source_route_data(route) for route in source_routes]
    return ParserAdapterSettingsData(
        adapter_backend=runtime.adapter_backend,
        effective_order=list(runtime.effective_order),
        docling_vision_enabled=settings.rag_parser_docling_vision_enabled,
        service_backends=_parser_service_backends_data(settings),
        adapters=[
            ParserAdapterStatusData(
                backend=adapter.backend,
                package_name=adapter.package_name,
                import_name=adapter.import_name,
                distribution_name=adapter.distribution_name,
                install_package=adapter.install_package,
                enabled=adapter.enabled,
                selected=adapter.selected,
                installed=adapter.installed,
                status=adapter.status,
                version=adapter.version,
                warning_code=adapter.warning_code,
            )
            for adapter in runtime.adapters
        ],
        connections=[
            ExternalParserConnectionData(
                backend=backend,
                protocol=connection.protocol,
                endpoint=connection.endpoint,
                model=connection.model,
                api_key_configured=bool(connection.api_key),
                configured=connection.configured,
            )
            for backend in ENGINE_SPECS
            for connection in [external_parser_connection(settings, backend)]
        ],
        scorecard=ParserAdapterScorecardData(
            selected_backend=scorecard.selected_backend,
            recommended_backend=scorecard.recommended_backend,
            metrics_source=scorecard.metrics_source,
            metrics_applied_to=scorecard.metrics_applied_to,
            entries=[
                ParserAdapterScorecardEntryData(
                    backend=entry.backend,
                    rank=entry.rank,
                    score=entry.score,
                    status=entry.status,
                    recommended=entry.recommended,
                    executable=entry.executable,
                    selected=entry.selected,
                    enabled=entry.enabled,
                    installed=entry.installed,
                    metric_source=entry.metric_source,
                    metric_count=entry.metric_count,
                    signals=dict(entry.signals),
                    reason_codes=list(entry.reason_codes),
                    warning_codes=list(entry.warning_codes),
                )
                for entry in scorecard.entries
            ],
        ),
        source_routes=route_data,
        backend_source_kind_matrix=_parser_adapter_backend_source_matrix(route_data),
        capabilities=_parser_backend_capabilities_data(),
        config_source="runtime",
    )


def _parser_backend_capabilities_data() -> list[ParserBackendCapabilityData]:
    """capabilities 正本から backend ごとの対応形式宣言を作る(別名は除外)。"""
    return [
        ParserBackendCapabilityData(
            backend=backend,
            modalities=[m.value for m in supported_modalities(backend)],
            extensions=sorted(capability.extensions),
        )
        for backend, capability in ADAPTER_CAPABILITIES.items()
        if backend != "enterprise_ai_vlm"
    ]


def _parser_adapter_contract_data(settings: Settings) -> ParserAdapterContractData:
    """Settings から parser adapter compatibility matrix の表示用データを作る。"""
    matrix = run_parser_adapter_compatibility_matrix(settings)
    payload = parser_adapter_contract_artifact_payload(matrix)
    raw_summary = payload.get("summary")
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    raw_cases = payload.get("cases")
    artifact_cases = (
        [case for case in raw_cases if isinstance(case, dict)]
        if isinstance(raw_cases, list | tuple)
        else []
    )
    return ParserAdapterContractData(
        passed=matrix.passed,
        fixture_root=str(payload["fixture_root"]),
        source_kinds=list(matrix.source_kinds),
        backends=list(matrix.backends),
        case_count=matrix.case_count,
        blocking_failure_count=matrix.blocking_failure_count,
        cases=[
            ParserAdapterContractCaseData(
                backend=case["backend"],
                source_kind=str(case["source_kind"]),
                fixture_name=str(case["fixture_name"]),
                content_type=str(case["content_type"]),
                status=case["status"],
                blocking=bool(case["blocking"]),
                parser_backend=(str(case["parser_backend"]) if "parser_backend" in case else None),
                parser_version=(str(case["parser_version"]) if "parser_version" in case else None),
                adapter_import_name=(
                    str(case["adapter_import_name"]) if "adapter_import_name" in case else None
                ),
                adapter_distribution_name=(
                    str(case["adapter_distribution_name"])
                    if "adapter_distribution_name" in case
                    else None
                ),
                adapter_package_version=(
                    str(case["adapter_package_version"])
                    if "adapter_package_version" in case
                    else None
                ),
                template=str(case["template"]) if "template" in case else None,
                element_count=_int_value(case.get("element_count")),
                page_count=_int_value(case.get("page_count")),
                table_count=_int_value(case.get("table_count")),
                table_cell_count=_int_value(case.get("table_cell_count")),
                asset_count=_int_value(case.get("asset_count")),
                bbox_count=_int_value(case.get("bbox_count")),
                warning_codes=_string_list(case.get("warning_codes")),
                reason_codes=_string_list(case.get("reason_codes")),
            )
            for case in artifact_cases
        ],
        summary=ParserAdapterContractSummaryData.model_validate(summary),
        config_source="runtime",
    )


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple | set | frozenset):
        return []
    return [item for item in value if isinstance(item, str)]


def _parser_adapter_source_route_data(
    route: ParserAdapterSourceRoute,
) -> ParserAdapterSourceRouteData:
    return ParserAdapterSourceRouteData(
        source_kind=route.source_kind,
        candidate_order=list(route.candidate_order),
        attempted_order=list(route.attempted_order),
        active_order=list(route.active_order),
        selected_backend=route.selected_backend,
        reason_codes=list(route.reason_codes),
        warning_codes=list(route.warning_codes),
    )


def _parser_adapter_backend_source_matrix(
    routes: list[ParserAdapterSourceRouteData],
) -> ParserAdapterBackendSourceMatrixData:
    backend_source_kinds: dict[str, list[str]] = {}
    covered_source_kinds: list[str] = []
    for route in routes:
        backend_source_kinds.setdefault(route.selected_backend, []).append(route.source_kind)
        covered_source_kinds.append(route.source_kind)
    normalized_backend_source_kinds = {
        backend: sorted(set(source_kinds))
        for backend, source_kinds in sorted(backend_source_kinds.items())
    }
    required_source_kinds = sorted({route.source_kind for route in routes})
    covered = sorted(set(covered_source_kinds))
    return ParserAdapterBackendSourceMatrixData(
        evidence_source="runtime_routes",
        required_source_kinds=required_source_kinds,
        covered_source_kinds=covered,
        missing_source_kinds=sorted(set(required_source_kinds) - set(covered)),
        backend_source_kinds=normalized_backend_source_kinds,
        route_evidence=routes,
    )


def _parser_adapter_settings_candidate(
    base: Settings,
    payload: ParserAdapterSettingsUpdate,
) -> Settings:
    """parser adapter 更新 payload から保存候補 settings を作る。"""
    updates: dict[str, object] = {
        "rag_parser_adapter_backend": payload.adapter_backend,
        "rag_parser_docling_enabled": _optional_bool(
            payload.docling_enabled,
            base.rag_parser_docling_enabled,
        ),
        "rag_parser_docling_vision_enabled": _optional_bool(
            payload.docling_vision_enabled,
            base.rag_parser_docling_vision_enabled,
        ),
        "rag_parser_marker_enabled": _optional_bool(
            payload.marker_enabled,
            base.rag_parser_marker_enabled,
        ),
        "rag_parser_unstructured_enabled": _optional_bool(
            payload.unstructured_enabled,
            base.rag_parser_unstructured_enabled,
        ),
        "rag_parser_unlimited_ocr_enabled": _optional_bool(
            payload.unlimited_ocr_enabled,
            base.rag_parser_unlimited_ocr_enabled,
        ),
        "rag_parser_mineru_enabled": _optional_bool(
            payload.mineru_enabled,
            base.rag_parser_mineru_enabled,
        ),
        "rag_parser_dots_ocr_enabled": _optional_bool(
            payload.dots_ocr_enabled,
            base.rag_parser_dots_ocr_enabled,
        ),
        "rag_parser_glm_ocr_enabled": _optional_bool(
            payload.glm_ocr_enabled,
            base.rag_parser_glm_ocr_enabled,
        ),
    }
    for connection in payload.connections:
        spec = ENGINE_SPECS[connection.backend]
        if "endpoint" in connection.model_fields_set:
            updates[spec.endpoint_field] = connection.endpoint or ""
        if spec.model_field and "model" in connection.model_fields_set:
            updates[spec.model_field] = connection.model or ""
        updates[spec.api_key_field] = _secret_value(
            current=str(getattr(base, spec.api_key_field, "") or ""),
            update=connection.api_key,
            clear=connection.clear_api_key,
        )
    return base.model_copy(update=updates)


def _optional_bool(value: bool | None, fallback: bool) -> bool:
    return fallback if value is None else value


def _graph_settings_data(settings: Settings) -> GraphSettingsData:
    """Settings から関係情報設定の表示用データを作る。"""
    runtime = graph_adapter_runtime_settings(settings)
    return GraphSettingsData(
        profile=runtime.profile,
        enabled=runtime.enabled,
        build_claims=runtime.build_claims,
        build_community_summaries=runtime.build_community_summaries,
        profiles=[
            GraphProfileStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                enabled=status.enabled,
                build_claims=status.build_claims,
                build_community_summaries=status.build_community_summaries,
            )
            for status in runtime.profiles
        ],
        config_source="runtime",
    )


def _persist_graph_settings(settings: Settings) -> None:
    """関係情報設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_GRAPH_PROFILE": settings.rag_graph_profile,
            # legacy フラグも併記して、profile を正本に固定する(off/entities を選べる状態にする)。
            "RAG_GRAPH_ENABLED": str(settings.rag_graph_enabled).lower(),
        },
        section_comment="# GraphRAG アダプター",
        error_detail="関係情報設定を backend/.env へ保存できませんでした。",
    )


def _agentic_settings_data(settings: Settings) -> AgenticSettingsData:
    """Settings から高度な検索設定の表示用データを作る。"""
    runtime = agentic_adapter_runtime_settings(settings)
    return AgenticSettingsData(
        profile=runtime.profile,
        enabled=runtime.enabled,
        rewrite=runtime.rewrite,
        decompose=runtime.decompose,
        multi_hop=runtime.multi_hop,
        max_subqueries=runtime.max_subqueries,
        profiles=[
            AgenticProfileStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                enabled=status.enabled,
                rewrite=status.rewrite,
                decompose=status.decompose,
                multi_hop=status.multi_hop,
                hyde=status.hyde,
            )
            for status in runtime.profiles
        ],
        config_source="runtime",
    )


def _persist_agentic_settings(settings: Settings) -> None:
    """高度な検索設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_AGENTIC_PROFILE": settings.rag_agentic_profile,
            "RAG_AGENTIC_MAX_SUBQUERIES": str(settings.rag_agentic_max_subqueries),
        },
        section_comment="# Agentic アダプター",
        error_detail="高度な検索設定を backend/.env へ保存できませんでした。",
    )


def _evaluation_settings_data(settings: Settings) -> EvaluationSettingsData:
    """Settings から品質評価設定の表示用データを作る。"""
    runtime = evaluation_adapter_runtime_settings(settings)

    def _thresholds_dict(thresholds: EvaluationThresholds | None) -> dict[str, float]:
        if thresholds is None:
            return {}
        return {
            key: float(value) for key, value in thresholds.model_dump(exclude_none=True).items()
        }

    return EvaluationSettingsData(
        suite=runtime.suite,
        thresholds=_thresholds_dict(runtime.thresholds),
        suites=[
            EvaluationSuiteStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                thresholds=_thresholds_dict(status.thresholds),
            )
            for status in runtime.suites
        ],
        config_source="runtime",
    )


def _persist_evaluation_settings(settings: Settings) -> None:
    """品質評価設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {"RAG_EVALUATION_SUITE": settings.rag_evaluation_suite},
        section_comment="# Evaluation アダプター",
        error_detail="品質評価設定を backend/.env へ保存できませんでした。",
    )


def _vector_index_settings_data(settings: Settings) -> VectorIndexSettingsData:
    """Settings から検索インデックス設定の表示用データを作る。"""
    runtime = vector_index_adapter_runtime_settings(settings)
    return VectorIndexSettingsData(
        profile=runtime.profile,
        target_accuracy=runtime.target_accuracy,
        neighbors=runtime.neighbors,
        efconstruction=runtime.efconstruction,
        distance=runtime.distance,
        requires_reprovision=runtime.requires_reprovision,
        profiles=[
            VectorIndexProfileStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                target_accuracy=status.target_accuracy,
                neighbors=status.neighbors,
                efconstruction=status.efconstruction,
                distance=status.distance,
            )
            for status in runtime.profiles
        ],
        reindex_sql=vector_index_reindex_sql(
            target_accuracy=runtime.target_accuracy,
            neighbors=runtime.neighbors,
            efconstruction=runtime.efconstruction,
            distance=runtime.distance,
        ),
        config_source="runtime",
    )


def _persist_vector_index_settings(settings: Settings) -> None:
    """検索インデックス設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {"RAG_VECTOR_INDEX_PROFILE": settings.rag_vector_index_profile},
        section_comment="# Vector Index アダプター",
        error_detail="検索インデックス設定を backend/.env へ保存できませんでした。",
    )


def _generation_settings_data(
    settings: Settings,
    stored: StoredGenerationSettings,
) -> GenerationSettingsData:
    """Oracle GLOBAL 行から回答スタイル設定の表示用データを作る。"""
    runtime = generation_adapter_runtime_settings(
        settings.model_copy(update={"rag_generation_profile": stored.profile})
    )
    return GenerationSettingsData(
        profile=runtime.profile,
        structured_output=runtime.structured_output,
        profiles=[
            GenerationProfileStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                structured_output=status.structured_output,
                contract_mode=status.contract_mode,
                repair_enabled=status.repair_enabled,
            )
            for status in runtime.profiles
        ],
        config_source="oracle",
        revision=stored.revision,
        updated_at=stored.updated_at,
        active_prompt_version_id=stored.active_prompt_version_id,
        custom_prompt_configured=stored.active_prompt_version_id is not None,
    )


def _guardrail_settings_data(settings: Settings) -> GuardrailSettingsData:
    """Settings から安全チェック設定の表示用データを作る。"""
    runtime = guardrail_adapter_runtime_settings(settings)
    return GuardrailSettingsData(
        policy=runtime.policy,
        block_prompt_injection=runtime.block_prompt_injection,
        mask_sensitive_identifiers=runtime.mask_sensitive_identifiers,
        max_query_chars=runtime.max_query_chars,
        grounding_min_overlap=runtime.grounding_min_overlap,
        grounding_min_ratio=runtime.grounding_min_ratio,
        audit_emphasis=runtime.audit_emphasis,
        policies=[
            GuardrailPolicyStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                grounding_min_overlap=status.grounding_min_overlap,
                grounding_min_ratio=status.grounding_min_ratio,
                audit_emphasis=status.audit_emphasis,
            )
            for status in runtime.policies
        ],
        backend=settings.rag_guardrail_backend,
        oci_configured=_oci_guardrails_configured(settings),
        oci_warning_code=_oci_guardrails_warning_code(settings),
        config_source="runtime",
    )


def _oci_guardrails_configured(settings: Settings) -> bool:
    """OCI Guardrails の compartment と API キー認証を静的に確認する。"""
    compartment_configured = bool(
        str(getattr(settings, "oci_guardrails_compartment_id", "") or "").strip()
        or str(getattr(settings, "oci_compartment_id", "") or "").strip()
    )
    return (
        compartment_configured
        and _test_oci_config(settings, verify_with_oci=False).status == "success"
    )


def _oci_guardrails_warning_code(settings: Settings) -> str | None:
    """oci_guardrails 選択時の静的 readiness warning code を返す。"""
    if settings.rag_guardrail_backend != "oci_guardrails":
        return None
    compartment_configured = bool(
        str(getattr(settings, "oci_guardrails_compartment_id", "") or "").strip()
        or str(getattr(settings, "oci_compartment_id", "") or "").strip()
    )
    if not compartment_configured:
        return "oci_guardrails_compartment_missing"
    if _test_oci_config(settings, verify_with_oci=False).status != "success":
        return "oci_guardrails_credentials_invalid"
    return None


def _persist_guardrail_settings(settings: Settings) -> None:
    """安全チェック設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_GUARDRAIL_POLICY": settings.rag_guardrail_policy,
            "RAG_GUARDRAIL_BACKEND": settings.rag_guardrail_backend,
        },
        section_comment="# Guardrail アダプター",
        error_detail="安全チェック設定を backend/.env へ保存できませんでした。",
    )


def _retrieval_status_data(
    statuses: Iterable[RetrievalStrategyStatus],
) -> list[RetrievalStrategyStatusData]:
    return [
        RetrievalStrategyStatusData(
            name=status.name,
            origin=status.origin,
            recommended_for=list(status.recommended_for),
            selected=status.selected,
            gap_stop=status.gap_stop,
            corrective_retrieval=status.corrective_retrieval,
            business_fit_weighting=status.business_fit_weighting,
        )
        for status in statuses
    ]


def _retrieval_settings_data(settings: Settings) -> RetrievalSettingsData:
    """Settings から検索方法設定の表示用データを作る。"""
    runtime = retrieval_adapter_runtime_settings(settings)
    return RetrievalSettingsData(
        mode=runtime.mode,
        legacy_strategy=runtime.legacy_strategy,
        query_expansion=runtime.query_expansion,
        query_expansion_llm=settings.rag_query_expansion_llm_enabled,
        gap_stop=runtime.gap_stop,
        corrective_retrieval=runtime.corrective_retrieval,
        business_fit_weighting=runtime.business_fit_weighting,
        text_search_tokenizer=settings.rag_text_search_tokenizer,
        modes=_retrieval_status_data(runtime.modes),
        config_source="runtime",
    )


def _retrieval_settings_updates(
    settings: Settings, payload: RetrievalSettingsUpdate
) -> dict[str, object]:
    """更新 payload(新形式)から Settings 更新 dict を作る。

    .env に legacy 複合値が残っている場合も、この保存を通るとモードへ正規化される
    (legacy の強制トグルは明示 ON へ引き継ぎ、現在の有効トグルは
    _persist_retrieval_settings が書き出す)。
    """
    updates: dict[str, object] = {}
    current = decompose_retrieval_strategy(settings.rag_retrieval_strategy)
    updates["rag_retrieval_strategy"] = current.mode
    if current.forced_query_expansion:
        updates["rag_query_expansion_enabled"] = True
    if current.forced_gap_stop:
        updates["rag_retrieval_gap_stop_enabled"] = True
    if current.forced_corrective_retrieval:
        updates["rag_retrieval_corrective_enabled"] = True
    if current.forced_business_fit_weighting:
        updates["rag_retrieval_business_fit_weighting_enabled"] = True
    if payload.mode is not None:
        updates["rag_retrieval_strategy"] = payload.mode
    if payload.query_expansion is not None:
        updates["rag_query_expansion_enabled"] = payload.query_expansion
    if payload.query_expansion_llm is not None:
        updates["rag_query_expansion_llm_enabled"] = payload.query_expansion_llm
    if payload.gap_stop is not None:
        updates["rag_retrieval_gap_stop_enabled"] = payload.gap_stop
    if payload.corrective_retrieval is not None:
        updates["rag_retrieval_corrective_enabled"] = payload.corrective_retrieval
    if payload.business_fit_weighting is not None:
        updates["rag_retrieval_business_fit_weighting_enabled"] = payload.business_fit_weighting
    if payload.text_search_tokenizer is not None:
        updates["rag_text_search_tokenizer"] = payload.text_search_tokenizer
    return updates


def _apply_retrieval_settings(target: Settings, source: Settings) -> None:
    """保存済み検索方法設定を現在プロセスへ反映する。"""
    target.rag_retrieval_strategy = source.rag_retrieval_strategy
    target.rag_query_expansion_enabled = source.rag_query_expansion_enabled
    target.rag_query_expansion_llm_enabled = source.rag_query_expansion_llm_enabled
    target.rag_retrieval_gap_stop_enabled = source.rag_retrieval_gap_stop_enabled
    target.rag_retrieval_corrective_enabled = source.rag_retrieval_corrective_enabled
    target.rag_retrieval_business_fit_weighting_enabled = (
        source.rag_retrieval_business_fit_weighting_enabled
    )
    target.rag_text_search_tokenizer = source.rag_text_search_tokenizer


def _persist_retrieval_settings(settings: Settings) -> None:
    """検索方法設定(モード + トグル)を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_RETRIEVAL_STRATEGY": settings.rag_retrieval_strategy,
            "RAG_QUERY_EXPANSION_ENABLED": _format_env_bool(settings.rag_query_expansion_enabled),
            "RAG_QUERY_EXPANSION_LLM_ENABLED": _format_env_bool(
                settings.rag_query_expansion_llm_enabled
            ),
            "RAG_RETRIEVAL_GAP_STOP_ENABLED": _format_env_bool(
                settings.rag_retrieval_gap_stop_enabled
            ),
            "RAG_RETRIEVAL_CORRECTIVE_ENABLED": _format_env_bool(
                settings.rag_retrieval_corrective_enabled
            ),
            "RAG_RETRIEVAL_BUSINESS_FIT_WEIGHTING_ENABLED": _format_env_bool(
                settings.rag_retrieval_business_fit_weighting_enabled
            ),
            "RAG_TEXT_SEARCH_TOKENIZER": settings.rag_text_search_tokenizer,
        },
        section_comment="# Retrieval アダプター",
        error_detail="検索方法設定を backend/.env へ保存できませんでした。",
    )


def _grounding_settings_data(settings: Settings) -> GroundingSettingsData:
    """Settings から根拠確認設定の表示用データを作る。"""
    runtime = grounding_adapter_runtime_settings(settings)
    return GroundingSettingsData(
        pipeline=runtime.pipeline,
        dependency_promotion_enabled=runtime.dependency_promotion_enabled,
        diversity_enabled=runtime.diversity_enabled,
        expansion_mode=runtime.expansion_mode,
        compression_enabled=runtime.compression_enabled,
        crag_low_confidence_threshold=settings.rag_grounding_crag_confidence_threshold,
        crag_high_confidence_threshold=settings.rag_crag_high_confidence_threshold,
        crag_max_hops=settings.rag_crag_max_hops,
        crag_low_evidence_abstain=settings.rag_crag_low_evidence_abstain_enabled,
        pipelines=[
            GroundingPipelineStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                dependency_promotion=status.dependency_promotion,
                diversity=status.diversity,
                expansion_mode=status.expansion_mode,
                compression=status.compression,
                corrective=status.corrective,
            )
            for status in runtime.pipelines
        ],
        config_source="runtime",
    )


def _apply_grounding_settings(target: Settings, source: Settings) -> None:
    """保存済み根拠確認設定を現在プロセスへ反映する。"""
    target.rag_post_retrieval_pipeline = source.rag_post_retrieval_pipeline
    target.rag_grounding_crag_confidence_threshold = source.rag_grounding_crag_confidence_threshold
    target.rag_crag_high_confidence_threshold = source.rag_crag_high_confidence_threshold
    target.rag_crag_max_hops = source.rag_crag_max_hops
    target.rag_crag_low_evidence_abstain_enabled = source.rag_crag_low_evidence_abstain_enabled


def _persist_grounding_settings(settings: Settings) -> None:
    """根拠確認設定(処理方式 + CRAG 閾値)を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_POST_RETRIEVAL_PIPELINE": settings.rag_post_retrieval_pipeline,
            "RAG_GROUNDING_CRAG_CONFIDENCE_THRESHOLD": str(
                settings.rag_grounding_crag_confidence_threshold
            ),
            "RAG_CRAG_HIGH_CONFIDENCE_THRESHOLD": str(settings.rag_crag_high_confidence_threshold),
            "RAG_CRAG_MAX_HOPS": str(settings.rag_crag_max_hops),
            "RAG_CRAG_LOW_EVIDENCE_ABSTAIN_ENABLED": _format_env_bool(
                settings.rag_crag_low_evidence_abstain_enabled
            ),
        },
        section_comment="# Grounding アダプター",
        error_detail="根拠確認設定を backend/.env へ保存できませんでした。",
    )


def _preprocess_settings_data(settings: Settings) -> PreprocessSettingsData:
    """Settings からファイル準備設定の表示用データを作る。"""
    runtime = preprocess_runtime_settings(settings)
    return PreprocessSettingsData(
        profile=runtime.profile,
        service_enabled=runtime.service_enabled,
        service_url=runtime.service_url,
        canonical_artifact_prefix=runtime.canonical_artifact_prefix,
        profiles=[
            PreprocessProfileStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                in_process=status.in_process,
                requires_service=status.requires_service,
                available=status.available,
            )
            for status in runtime.profiles
        ],
        config_source="runtime",
    )


def _preprocess_settings_candidate(
    base: Settings,
    payload: PreprocessSettingsUpdate,
) -> Settings:
    """前処理アダプター更新 payload から保存候補 settings を作る。"""
    return base.model_copy(
        update={"rag_preprocess_profile": normalize_preprocess_profile(payload.profile)}
    )


def _apply_preprocess_settings(target: Settings, source: Settings) -> None:
    """保存済みファイル準備設定を現在プロセスへ反映する。"""
    target.rag_preprocess_profile = source.rag_preprocess_profile


def _persist_preprocess_settings(settings: Settings) -> None:
    """ファイル準備設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {"RAG_PREPROCESS_PROFILE": settings.rag_preprocess_profile},
        section_comment="# 前処理(Preprocess)アダプター",
        error_detail="ファイル準備設定を backend/.env へ保存できませんでした。",
    )


def _chunking_settings_data(settings: Settings) -> ChunkingSettingsData:
    """Settings から文書分割設定の表示用データを作る。"""
    runtime = chunking_runtime_settings(settings)
    return ChunkingSettingsData(
        strategy=runtime.strategy,
        chunk_size=runtime.chunk_size,
        overlap=runtime.overlap,
        child_size=runtime.child_size,
        min_chars=runtime.min_chars,
        delimiter=runtime.delimiter,
        context_header_enabled=settings.rag_chunk_context_header_enabled,
        strategies=[
            ChunkingStrategyStatusData(
                name=status.name,
                origin=status.origin,
                recommended_for=list(status.recommended_for),
                selected=status.selected,
                uses_child_size=status.uses_child_size,
            )
            for status in runtime.strategies
        ],
        config_source="runtime",
    )


def _chunking_settings_candidate(
    base: Settings,
    payload: ChunkingSettingsUpdate,
) -> Settings:
    """Chunking アダプター更新 payload から保存候補 settings を作る。"""
    return base.model_copy(
        update={
            "rag_chunking_strategy": normalize_chunking_strategy(payload.strategy),
            "rag_chunk_size": payload.chunk_size,
            "rag_chunk_overlap": payload.overlap,
            "rag_chunk_child_size": payload.child_size,
            "rag_chunk_min_chars": payload.min_chars,
            "rag_chunk_delimiter": payload.delimiter,
            "rag_chunk_context_header_enabled": payload.context_header_enabled,
        }
    )


def _apply_chunking_settings(target: Settings, source: Settings) -> None:
    """保存済み文書分割設定を現在プロセスへ反映する。"""
    target.rag_chunking_strategy = source.rag_chunking_strategy
    target.rag_chunk_size = source.rag_chunk_size
    target.rag_chunk_overlap = source.rag_chunk_overlap
    target.rag_chunk_child_size = source.rag_chunk_child_size
    target.rag_chunk_min_chars = source.rag_chunk_min_chars
    target.rag_chunk_delimiter = source.rag_chunk_delimiter
    target.rag_chunk_context_header_enabled = source.rag_chunk_context_header_enabled


def _persist_chunking_settings(settings: Settings) -> None:
    """文書分割設定を backend/.env へ永続化する。"""
    _write_env_values(
        BACKEND_ENV_FILE,
        {
            "RAG_CHUNKING_STRATEGY": settings.rag_chunking_strategy,
            "RAG_CHUNK_SIZE": str(settings.rag_chunk_size),
            "RAG_CHUNK_OVERLAP": str(settings.rag_chunk_overlap),
            "RAG_CHUNK_CHILD_SIZE": str(settings.rag_chunk_child_size),
            "RAG_CHUNK_MIN_CHARS": str(settings.rag_chunk_min_chars),
            "RAG_CHUNK_DELIMITER": settings.rag_chunk_delimiter,
            "RAG_CHUNK_CONTEXT_HEADER_ENABLED": _format_env_bool(
                settings.rag_chunk_context_header_enabled
            ),
        },
        section_comment="# Chunking アダプター",
        error_detail="文書分割設定を backend/.env へ保存できませんでした。",
    )


def _format_env_bool(value: bool) -> str:
    return "true" if value else "false"


def _write_env_values(
    path: Path,
    values: Mapping[str, str | None],
    *,
    section_comment: str,
    error_detail: str,
) -> None:
    """既存 .env のコメントや無関係な値を保ったまま指定 key だけ更新する。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        next_lines: list[str] = []
        written: set[str] = set()
        for line in lines:
            key = _env_assignment_key(line)
            if key is None or key not in values:
                next_lines.append(line)
                continue
            if key in written:
                continue
            value = values[key]
            if value is None:
                written.add(key)
                continue
            next_lines.append(f"{key}={_format_env_value(value)}")
            written.add(key)

        missing = [key for key, value in values.items() if key not in written and value is not None]
        if missing:
            if next_lines and next_lines[-1].strip():
                next_lines.append("")
            next_lines.append(section_comment)
            for key in missing:
                value = values[key]
                if value is not None:
                    next_lines.append(f"{key}={_format_env_value(value)}")

        content = "\n".join(next_lines).rstrip() + "\n"
        _replace_env_file(path, content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=error_detail) from exc


def _env_assignment_key(line: str) -> str | None:
    """通常の .env 代入行から key を取り出す。コメント行は対象外。"""
    if line.lstrip().startswith("#"):
        return None
    match = ENV_ASSIGNMENT_RE.match(line)
    return match.group(1) if match else None


def _format_env_value(value: str) -> str:
    """python-dotenv と shell の両方で読みやすい .env value へ整形する。"""
    normalized = value.strip()
    if not normalized:
        return ""
    if re.search(r"[\s#\"']", normalized):
        return '"' + normalized.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return normalized


def _replace_env_file(path: Path, content: str) -> None:
    """同一ディレクトリ内の一時ファイルから atomic replace する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else ENV_FILE_MODE
    tmp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.chmod(mode)
        tmp_path.replace(path)
        path.chmod(mode)
    finally:
        tmp_path.unlink(missing_ok=True)


def _secret_value(*, current: str, update: str | None, clear: bool) -> str:
    """secret の保持・更新・削除を判定する。"""
    if clear:
        return ""
    if update is not None and update != "":
        return update
    return current
