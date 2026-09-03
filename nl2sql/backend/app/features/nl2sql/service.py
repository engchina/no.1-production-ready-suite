"""NL2SQL application service.

この実装は local / CI で外部 Oracle・OCI に依存せずに動く deterministic adapter を持つ。
実運用では `SelectAiAdapter` / `SelectAiAgentAdapter` / `EnterpriseAiDirectAdapter`
の generate 部分を Oracle / OCI 呼び出しに差し替える。
"""

from __future__ import annotations

import base64
import binascii
import copy
import csv
import hashlib
import importlib
import io
import json
import logging
import math
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

from charset_normalizer import from_bytes
from dotenv import dotenv_values
from pydantic import BaseModel, ValidationError
from pydantic import Field as PydanticField

from app.security.request_actor import actor_scope, current_actor_is_system_admin
from app.settings import get_settings

from .embedding_client import (
    EmbeddingClientError,
    FeedbackEmbeddingClient,
    OciGenAiEmbeddingClient,
)
from .enterprise_ai_client import (
    EnterpriseAiDirectClient,
    EnterpriseAiDirectError,
    OciEnterpriseAiDirectClient,
)
from .incremental_observability import (
    SCHEMA_CHANGED_OBJECTS,
    SCHEMA_REFRESH_ERRORS,
    SCHEMA_REFRESH_PENDING_AGE_SECONDS,
    SCHEMA_REFRESH_PHASE_SECONDS,
    observe_schema_refresh,
    record_persistence_failure,
    record_persistence_recovery,
    record_token_lag,
    set_persistence_circuit_state,
)
from .incremental_store import (
    PROFILE_NAMESPACE,
    SCHEMA_NAMESPACE,
    STATE_NAMESPACE,
    IncrementalNl2SqlRepository,
    IncrementalVersionConflict,
    MemoryIncrementalNl2SqlRepository,
    OracleIncrementalNl2SqlRepository,
    VersionedTtlCache,
    _decode_cursor,
    _encode_cursor,
    _profile_matches_query,
    _profile_search_key,
)
from .logical_steps import (
    LabelResolver,
    build_business_explanation,
    build_logical_steps,
    build_logical_structure_items,
)
from .models import (
    AdminFeedbackReviewData,
    AdminFeedbackReviewRequest,
    AgentConversationCreateData,
    AgentConversationCreateRequest,
    AgentConversationItem,
    AgentConversationsData,
    AgentPrivilegeCheckData,
    AgentTeamRunData,
    AgentTeamRunRequest,
    AgentToolRunRequest,
    AllowedObjects,
    AnalyzeData,
    AnnotationApplyData,
    AnnotationApplyItem,
    AnnotationApplyRequest,
    AnnotationApplyStatement,
    AnnotationSuggestion,
    AnnotationSuggestionData,
    AssetCleanupData,
    AssetCleanupRequest,
    AssetRefreshData,
    ClassifierFeedbackImportData,
    ClassifierFeedbackImportRequest,
    ClassifierFeedbackImportResult,
    ClassifierImportData,
    ClassifierModelImportData,
    ClassifierModelInfo,
    ClassifierPredictionCandidate,
    ClassifierPredictionData,
    ClassifierPredictRequest,
    ClassifierStatusData,
    ClassifierTrainingCandidate,
    ClassifierTrainingCandidatesData,
    ClassifierTrainingDataData,
    ClassifierTrainingExample,
    ClassifierTrainingExampleUpdateRequest,
    ClassifierTrainRequest,
    CommentApplyData,
    CommentApplyItem,
    CommentApplyRequest,
    CommentApplyStatement,
    CommentSuggestion,
    CommentSuggestionData,
    CommentSuggestionRequest,
    CsvImportColumn,
    DbAdminAiAnalysisData,
    DbAdminAiAnalysisRequest,
    DbAdminCsvUploadData,
    DbAdminCsvUploadRequest,
    DbAdminDataPreviewData,
    DbAdminDataPreviewRequest,
    DbAdminDropTableRequest,
    DbAdminDropViewRequest,
    DbAdminExecuteData,
    DbAdminExecuteRequest,
    DbAdminImportTabularData,
    DbAdminImportTabularRequest,
    DbAdminJoinWhereData,
    DbAdminJoinWhereRequest,
    DbAdminObjectDetail,
    DbAdminObjectPage,
    DbAdminObjectsData,
    DbAdminObjectSummary,
    DbAdminStatementResult,
    DbAdminStatementsRequest,
    DbAdminTruncateTableRequest,
    DemoLearningData,
    DiagnosticCheck,
    DiagnosticConfigGuide,
    DiagnosticConfigVar,
    DiagnosticReadiness,
    DiagnosticsData,
    DiagnosticSmokeCheck,
    ExplainPlanData,
    FeedbackClearData,
    FeedbackData,
    FeedbackEntriesData,
    FeedbackIndexData,
    FeedbackIndexRequest,
    FeedbackListData,
    FeedbackRating,
    FeedbackRecord,
    FeedbackSearchConfigData,
    FeedbackSearchConfigRequest,
    FeedbackVectorEntry,
    HistoryData,
    HistoryItem,
    JobCreateData,
    JobCreateRequest,
    JobData,
    JobStatus,
    JobStepData,
    JobStepStatus,
    LegacyLearningMaterialData,
    MetadataSqlGenerateData,
    MetadataSqlGenerateRequest,
    MetadataSqlSampleData,
    MetadataSqlSampleRequest,
    Nl2SqlEngine,
    Nl2SqlInterpretationArtifact,
    Nl2SqlLogicalStep,
    Nl2SqlLogicalStructureItem,
    Nl2SqlOntologyGraphSnapshot,
    Nl2SqlProfile,
    Nl2SqlQuestionInterpretation,
    Nl2SqlResult,
    Nl2SqlShowPromptArtifact,
    Nl2SqlSqlInterpretation,
    PersistenceStatusData,
    PreviewData,
    PreviewRequest,
    ProfileDeleteData,
    ProfileLearningMaterialImportData,
    ProfileRecommendationCandidate,
    ProfileRecommendationData,
    ProfileRecommendationRequest,
    ProfileSelectAiConfig,
    ProfileSelectAiProfileRequest,
    ProfileSummary,
    ProfileSummaryPage,
    QueryResults,
    ReverseSqlData,
    ReverseSqlRequest,
    RewriteData,
    RewriteRequest,
    SafetyReport,
    SampleDataInfo,
    SampleDataMutationData,
    SampleDataMutationRequest,
    SampleDataStep,
    SchemaCatalog,
    SchemaCatalogHead,
    SchemaColumn,
    SchemaObjectDetail,
    SchemaObjectPage,
    SchemaObjectSummary,
    SchemaOwnersData,
    SchemaOwnerSummary,
    SchemaRefreshJob,
    SchemaRefreshJobStatus,
    SchemaRefreshMode,
    SchemaRefreshPhase,
    SchemaRefreshTargetObject,
    SchemaTable,
    SchemaViewDependency,
    SelectAiAgentAsset,
    SelectAiAgentAssetsData,
    SelectAiDbProfile,
    SelectAiDbProfileDetailData,
    SelectAiDbProfileMutationData,
    SelectAiDbProfileRefreshJobData,
    SelectAiDbProfileRefreshMode,
    SelectAiDbProfileRefreshPhase,
    SelectAiDbProfileRefreshStatus,
    SelectAiDbProfileRefreshTarget,
    SelectAiDbProfilesData,
    SelectAiDbProfileUpsertRequest,
    SelectAiFeedbackAddData,
    SelectAiFeedbackAddRequest,
    SelectAiFeedbackDeleteRequest,
    SelectAiFeedbackEntriesData,
    SelectAiFeedbackEntry,
    SelectAiFeedbackMutationData,
    SelectAiFeedbackVectorIndexRequest,
    SelectAiProfilesExportData,
    SelectAiProfilesImportRequest,
    SelectAiRequestOverrides,
    SimilarHistoryData,
    SimilarHistoryItem,
    SimilarHistoryPublishData,
    SimilarHistoryRequest,
    StageTiming,
    SyntheticDataGenerateRequest,
    SyntheticDataOperationData,
    SyntheticDataResultsData,
    TimingEnvelope,
)
from .object_identity import (
    OracleObjectIdentity,
    normalize_object_part,
    parse_object_identity,
    qualified_object_name,
)
from .object_visibility import (
    filter_user_visible_catalog,
    filter_user_visible_object_page,
    is_user_visible_object_name,
    is_user_visible_schema_object,
)
from .oracle_adapter import (
    OracleAdapterError,
    OracleNl2SqlAdapter,
    SelectAiCredentialMissingError,
    TabularImportValidationError,
)
from .sql_semantics import parse_oracle_sql
from .store import MemoryNl2SqlStore, Nl2SqlStore, OracleJsonNl2SqlStore
from .tabular_files import (
    WORKBOOK_SUFFIXES,
    normalize_workbook_scalar,
    read_workbook_sheet,
    read_workbook_sheets,
    select_workbook_sheet,
    validate_tabular_text_signature,
)

logger = logging.getLogger(__name__)

_SELECT_AI_DB_PROFILE_COLLECTION = "select_ai_db_profiles"
_SELECT_AI_DB_PROFILE_REFRESH_JOB_COLLECTION = "select_ai_db_profile_refresh_jobs"
_SELECT_AI_DB_PROFILE_REFRESH_META_COLLECTION = "select_ai_db_profile_refresh_meta"
_CLASSIFIER_MODEL_FORMAT = "logistic_regression_coefficients_v1"
_CLASSIFIER_VECTOR_DIMENSION = 1536
_CLASSIFIER_COUNT_METRICS = frozenset(
    {"training_examples", "source_example_count", "category_count"}
)
_CLASSIFIER_TRAINING_MAX_ROWS = 10_000
_CLASSIFIER_TRAINING_MAX_COLUMNS = 32
_CLASSIFIER_RECOMMENDATION_CONFIDENCE_THRESHOLD = 0.3


class Nl2SqlPersistenceUnavailable(RuntimeError):
    """共有状態を安全に読み書きできない場合の公開用例外。"""

    public_message = (
        "業務データを永続化するデータベースを利用できません。"
        "データベース設定と起動状態を確認してから再試行してください。"
    )

    def __init__(self, reason_code: str = "persistence_unavailable") -> None:
        super().__init__(self.public_message)
        self.reason_code = reason_code


class Nl2SqlRepositoryOperationFailed(RuntimeError):
    """DB 自体は利用可能だが repository operation が失敗した場合の公開例外。"""

    public_message = (
        "データベース処理に失敗しました。時間をおいて再試行してください。"
        "解消しない場合は管理者にお問い合わせください。"
    )

    def __init__(self, reason_code: str) -> None:
        super().__init__(self.public_message)
        self.reason_code = reason_code


class ProfileOracleCleanupFailed(RuntimeError):
    """業務 profile 削除前の Oracle asset cleanup が失敗した場合の公開例外。"""

    public_message = (
        "Oracle DBMS_CLOUD_AI Profile / Select AI Agent 関連アセットの削除に失敗しました。"
        "業務 profile は削除していません。"
    )

    def __init__(self, cleanup: list[AssetCleanupData]) -> None:
        warnings = [item.warning for item in cleanup if item.warning]
        detail = " ".join(warnings).strip()
        message = f"{self.public_message} {detail}".strip()
        super().__init__(message)
        self.cleanup = cleanup


class ProfileNameConflict(ValueError):
    """業務 profile 名が既存 profile と衝突した。"""

    code = "NL2SQL_PROFILE_NAME_CONFLICT"
    field_pointer = "/name"

    def __init__(self, profile_name: str) -> None:
        normalized = profile_name.strip().upper()
        super().__init__(f"業務 profile 名「{normalized}」は既に使用されています。")
        self.profile_name = normalized


class ProfileNotFoundError(KeyError, ValueError):
    """業務 profile が存在しない、または通常利用できない。"""

    def __init__(self, profile_id: str | None = None) -> None:
        self.profile_id = profile_id or "default"
        super().__init__(self.profile_id)

    def __str__(self) -> str:
        return "指定された profile が見つからないか、利用できません。"


class DbAdminOperationFailed(RuntimeError):
    """DB 管理画面向けに、復旧可能な情報を保持する公開例外。"""

    def __init__(
        self,
        *,
        error_code: str,
        summary: str,
        cause: str,
        actions: list[str],
        target_name: str = "",
        target_type: str = "",
        operation: str = "",
        raw_message: str = "",
    ) -> None:
        super().__init__(summary)
        self.error_code = error_code
        self.summary = summary
        self.cause = cause
        self.actions = actions
        self.target_name = target_name
        self.target_type = target_type
        self.operation = operation
        self.raw_message = raw_message


@dataclass(frozen=True)
class SchemaRefreshMutationSync:
    """DDL/metadata mutation 後に UI へ返す schema 同期状態。"""

    job_id: str = ""
    required: bool = False
    reason_code: str = ""


class SchemaRefreshFullRequired(RuntimeError):
    """Targeted schema sync が安全に確定できず full refresh が必要な状態。"""

    def __init__(self, reason_code: str = "schema_refresh_full_required") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _schema_refresh_required_warning(reason_code: str) -> str:
    if reason_code == "schema_refresh_target_unresolved":
        return "DB 構造の差分同期対象を安全に特定できませんでした。DB 構造を再取得してください。"
    return "DB 構造の差分同期で不整合を検出しました。DB 構造を再取得してください。"


@dataclass(frozen=True)
class SelectAiDbProfileListRefreshSync:
    """DBMS_CLOUD_AI profile 一覧 mutation 後に UI へ返す同期状態。"""

    job_id: str = ""
    required: bool = False
    reason_code: str = ""


class SelectAiDbProfileListRefreshFullRequired(RuntimeError):
    """Targeted DB profile list sync が安全に確定できず full refresh が必要な状態。"""

    def __init__(self, reason_code: str = "profile_list_refresh_full_required") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _profile_list_refresh_required_warning(reason_code: str) -> str:
    if reason_code == "profile_list_refresh_target_unresolved":
        return (
            "DB Profile 一覧の差分同期対象を安全に特定できませんでした。"
            "DB Profile 一覧を再取得してください。"
        )
    if reason_code == "profile_list_refresh_submit_failed":
        return (
            "DB Profile 一覧の差分同期を開始できませんでした。DB Profile 一覧を再取得してください。"
        )
    return "DB Profile 一覧の差分同期で不整合を検出しました。DB Profile 一覧を再取得してください。"


_ORACLE_ERROR_CODE_RE = re.compile(r"\b(?:ORA|DPY|DPI)-\d{4,5}\b", re.IGNORECASE)
_ORACLE_CONNECTION_CODES = frozenset(
    {
        "ORA-01012",
        "ORA-01017",
        "ORA-03113",
        "ORA-03114",
        "ORA-03135",
        "ORA-12154",
        "ORA-12505",
        "ORA-12506",
        "ORA-12514",
        "ORA-12541",
        "DPY-4011",
        "DPY-6000",
        "DPY-6005",
        "DPI-1047",
        "DPI-1072",
    }
)
_ORACLE_SCHEMA_COMPATIBILITY_CODES = frozenset({"ORA-00904", "ORA-00942"})


def _safe_oracle_error_code(exc: Exception) -> str:
    match = _ORACLE_ERROR_CODE_RE.search(str(exc))
    return match.group(0).upper() if match else ""


_TEMPLATE_XLSX_UPLOAD_MESSAGE = (
    "ダウンロードした .xlsx テンプレートファイルをアップロードしてください。"
)
_EXCEL_ILLEGAL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")


def _require_xlsx_template_upload(filename: str) -> None:
    """Excel 出力と対になるテンプレート取込は .xlsx のみ受け付ける。"""

    if Path(filename).suffix.lower() != ".xlsx":
        raise ValueError(_TEMPLATE_XLSX_UPLOAD_MESSAGE)


def _excel_safe_text(value: Any) -> str:
    """Excel 書き出し時に式評価と不正制御文字を避ける文字列へ寄せる。"""

    return _EXCEL_ILLEGAL_CHARACTERS_RE.sub("", str(value if value is not None else ""))


def _append_excel_text_row(sheet: Any, values: Sequence[Any]) -> None:
    sheet.append([_excel_safe_text(value) for value in values])
    for cell in sheet[sheet.max_row]:
        cell.data_type = "s"


def _db_admin_error(
    exc: Exception,
    *,
    target_name: str = "",
    target_type: str = "",
    operation: str = "",
) -> DbAdminOperationFailed:
    """Oracle 例外を、DB 管理で共通利用する日本語の復旧情報へ変換する。"""
    code = _safe_oracle_error_code(exc) or "oracle_operation_failed"
    raw_message = str(exc)
    known: dict[str, tuple[str, str, list[str]]] = {
        "ORA-01031": (
            "権限が不足しています。",
            "この操作を実行する Oracle 権限がありません。",
            [
                "実行ユーザーの CREATE / ALTER / DROP または対象表への権限を"
                "管理者に確認してください。"
            ],
        ),
        "ORA-00942": (
            "対象の表またはビューが見つかりません。",
            "対象が存在しないか、参照権限がありません。",
            ["対象名、schema owner、実行ユーザーの権限を確認してください。"],
        ),
        "ORA-00904": (
            "列名または識別子が無効です。",
            "存在しない列名、または Oracle で解釈できない識別子が指定されています。",
            ["SQL と表定義の列名・引用符・alias を確認してください。"],
        ),
        "ORA-00922": (
            "Oracle SQL の構文が無効です。",
            "句、括弧、カンマ、または複数 SQL の区切りが Oracle 構文に合っていません。",
            ["SQL プレビューを確認し、文ごとに実行してください。"],
        ),
        "ORA-01722": (
            "数値形式が正しくありません。",
            "数値列へ文字列を渡した、または数値変換できない値が含まれています。",
            ["対象列のデータ型とファイルまたは SQL の値を確認してください。"],
        ),
        "ORA-01843": (
            "日付の月が正しくありません。",
            "指定した日付値を Oracle が解釈できません。",
            ["日付を YYYY-MM-DD など表定義に合う形式へ修正してください。"],
        ),
        "ORA-01861": (
            "日付または時刻の形式が一致しません。",
            "値の書式が対象列または変換書式と一致していません。",
            ["日付・時刻の書式と対象列の型を確認してください。"],
        ),
        "ORA-12899": (
            "値が列の最大長を超えています。",
            "文字列またはバイト数が対象列の上限を超えています。",
            ["値を短くするか、対象列の長さを拡張してください。"],
        ),
        "ORA-00001": (
            "一意制約に違反しています。",
            "同じ主キーまたは一意キーの値が既に存在します。",
            ["重複する値を修正するか、既存データを確認してください。"],
        ),
        "ORA-01400": (
            "必須列に値がありません。",
            "NOT NULL の列へ NULL または空の値を登録しようとしました。",
            ["ファイルまたは SQL に必須列の値を設定してください。"],
        ),
        "ORA-02291": (
            "参照先データが存在しません。",
            "外部キーが参照する親データが未登録です。",
            ["親テーブルのデータを先に登録し、外部キー値を確認してください。"],
        ),
        "ORA-02292": (
            "参照されているデータは削除できません。",
            "子テーブルのデータが対象行を参照しています。",
            ["参照データを確認し、必要なら子データを先に処理してください。"],
        ),
        "ORA-00054": (
            "対象が他の処理で使用中です。",
            "ロック競合のため操作を完了できませんでした。",
            ["他の更新処理の完了後に再試行してください。"],
        ),
    }
    if code == "ORA-00955":
        object_description = (
            f"既存の{target_type}" if target_type else "同名のデータベースオブジェクト"
        )
        name = f"「{target_name}」" if target_name else "指定した名前"
        return DbAdminOperationFailed(
            error_code=code,
            summary=f"{name} は既に存在するため、新規作成できません。",
            cause=f"{object_description} と名前が重複しています。",
            actions=[
                "表名を変更して再実行してください。",
                "一覧へ戻り、同名の既存オブジェクトを確認してください。",
            ],
            target_name=target_name,
            target_type=target_type,
            operation=operation,
            raw_message=raw_message,
        )
    if code in _ORACLE_CONNECTION_CODES or _is_oracle_connection_failure(exc):
        return DbAdminOperationFailed(
            error_code=code,
            summary="Oracle データベースに接続できません。",
            cause="接続設定、ネットワーク、またはデータベースの稼働状態に問題がある可能性があります。",
            actions=[
                "データベース接続状態を確認してから再試行してください。",
                "解消しない場合は管理者に接続設定を確認してください。",
            ],
            target_name=target_name,
            target_type=target_type,
            operation=operation,
            raw_message=raw_message,
        )
    if code in known:
        summary, cause, actions = known[code]
    else:
        summary = f"データベース処理に失敗しました（{code}）。"
        cause = "Oracle 側で処理を完了できませんでした。"
        actions = ["対象 SQL または取込データ、実行権限、データベース状態を確認してください。"]
    return DbAdminOperationFailed(
        error_code=code,
        summary=summary,
        cause=cause,
        actions=actions,
        target_name=target_name,
        target_type=target_type,
        operation=operation,
        raw_message=raw_message,
    )


def _is_oracle_connection_failure(exc: Exception) -> bool:
    code = _safe_oracle_error_code(exc)
    if code in _ORACLE_CONNECTION_CODES:
        return True
    if code.startswith(("ORA-121", "ORA-122", "ORA-125", "ORA-126", "DPY-4", "DPY-6")):
        return True
    exception_type = type(exc).__name__.lower()
    detail = str(exc).lower()
    return (
        exception_type in {"operationalerror", "interfaceerror"}
        or "timed out" in detail
        or "timeout" in detail
    )


_JoinWherePromptProfile = Literal["sql_structure"]

_SAMPLE_PROFILE_ID = "sql_assist_sample"
_SAMPLE_CONFIRMATION = "SQL_ASSIST_SAMPLE"
_LEGACY_LEARNING_MATERIAL_SINGLETON = "legacy_learning_material"
# 推薦信頼度の平滑化定数。strength = s/(s+K) で、確かな 2 ヒット(≒score 3)が
# strength≈0.5 になるよう K=3 とする（散在していた除数 6 を置換する唯一の定数）。
_RECOMMEND_CONFIDENCE_SMOOTHING = 3.0
_PROFILE_RECOMMEND_CURRENT_PROFILE_BIAS = 0.2
_PROFILE_RECOMMEND_DEFAULT_PROFILE_BIAS = 0.5
_PROFILE_RECOMMENDATION_STOPWORDS = {
    "ある",
    "あり",
    "いた",
    "一覧",
    "確認",
    "検索",
    "する",
    "たい",
    "です",
    "ます",
    "見た",
    "プロファイル",
    "profile",
}
_SAMPLE_OBJECTS = [
    "DEPARTMENT",
    "EMPLOYEE",
    "PROJECT",
    "V_EMP_DEPT",
    "V_DEPT_PROJECT",
]
_SAMPLE_IMPORT_IDEMPOTENT_ERROR_CODES = frozenset({"ORA-00955", "ORA-00001"})
_SAMPLE_DELETE_IDEMPOTENT_ERROR_CODES = frozenset({"ORA-00942"})
_SAMPLE_EXECUTED_STATUSES = frozenset({"success", "skipped"})
_SYNTHETIC_DATA_UNSUPPORTED_DATA_TYPES = {
    "BFILE",
    "BLOB",
    "CLOB",
    "JSON",
    "LONG",
    "LONG RAW",
    "NCLOB",
    "RAW",
    "SDO_GEOMETRY",
    "VECTOR",
    "XMLTYPE",
}
_SAMPLE_TABLES = ["DEPARTMENT", "EMPLOYEE", "PROJECT"]
_SAMPLE_VIEWS = ["V_EMP_DEPT", "V_DEPT_PROJECT"]
_SCHEMA_EMPTY_MESSAGE = (
    "Schema catalog が空です。Oracle schema を refresh するか、"
    "Data Tools から sample data を明示的に import してください。"
)
SCHEMA_CATALOG_EMPTY_ERROR_CODE = "SCHEMA_CATALOG_EMPTY"
JOB_CANCELLED_ERROR_CODE = "JOB_CANCELLED"
# プロセス内 job/history の保持上限(terminal な古いものから捨てる)。
# incremental repository 有効時は DB 側が正本で、これはメモリ肥大の安全弁。
_JOB_RETENTION_LIMIT = 200
_HISTORY_RETENTION_LIMIT = 1000
# GET /nl2sql/history の 1 ページ件数(既定 / 上限)。旧実装は 50 件固定で続きが取れなかった。
_HISTORY_PAGE_DEFAULT_LIMIT = 50
_HISTORY_PAGE_MAX_LIMIT = 200
# 類似履歴 / few-shot の母集団上限(管理者 GOOD の履歴を新しい順にこの件数まで読む)。
_SIMILAR_HISTORY_POOL_LIMIT = 1000
_FEEDBACK_INDEX_OPERATION_LOCK = threading.RLock()
# 別プロセス(gunicorn worker)からのキャンセル要求を伝える repository 上の専用 collection。
# owner の _persist_job が job 本体を丸ごと上書きしても失われない別ドキュメントにする。
_JOB_CANCEL_COLLECTION = "job_cancel_requests"
_IN_FLIGHT_JOB_STATUSES = frozenset({JobStatus.PENDING, JobStatus.RUNNING})
JOB_INTERRUPTED_ERROR_CODE = "JOB_INTERRUPTED"
_JOB_INTERRUPTED_MESSAGE = "サーバ再起動前に完了しなかったため、ジョブを終了扱いにしました。"


class JobCancelledError(RuntimeError):
    """利用者要求による job の協調キャンセル(stage 境界で検出)。"""

    def __init__(self) -> None:
        super().__init__("利用者の要求によりジョブをキャンセルしました。")


class SchemaCatalogEmptyError(ValueError):
    """schema catalog 未整備で SQL 生成できない状態。error_code で機械判定させる。"""

    def __init__(self, message: str = _SCHEMA_EMPTY_MESSAGE) -> None:
        super().__init__(message)


_PROFILE_SCHEMA_SCOPE_EMPTY_MESSAGE = (
    "業務 Profile の許可表が schema catalog に見つかりません。"
    "DB 構造を再取得するか、業務 Profile の許可表を確認してください。"
)
_EMPTY_FILTER_SLOT_WARNING = "抽出条件が空欄のため条件追加を抑止しました。"
_EMPTY_FILTER_GENERATION_INSTRUCTION = (
    "抽出条件が空欄です。WHERE 句、HAVING 句、QUALIFY 句などの抽出条件を追加しないでください。"
    "表名・列名・コメント・schema 説明から値条件を推測しないでください。"
)
_EMPTY_FILTER_BLOCK_REASON = (
    "抽出条件が空欄の質問に対して WHERE 条件が生成されたため、SQL を実行しません。"
)
_QUESTION_SLOT_LABELS = (
    "対象テーブル",
    "対象テーブル（複数可）",
    "テーブル間の関連",
    "抽出項目",
    "抽出条件",
    "条件",
    "WHERE条件",
    "WHERE 条件",
    "検索条件",
    "集計内容（件数・合計・平均など）",
    "集計単位（グループ化）",
    "並び替え（項目と昇順／降順）",
    "表示件数（上位N件）",
)
_QUESTION_FILTER_SLOT_LABELS = (
    "抽出条件",
    "条件",
    "WHERE条件",
    "WHERE 条件",
    "検索条件",
)
_QUESTION_TARGET_TABLE_SLOT_LABELS = (
    "対象テーブル",
    "対象テーブル（複数可）",
)
_QUESTION_SLOT_PATTERN = re.compile(r"^\s*([^：:\n]{1,80})\s*[：:]\s*(.*)$")


def _normalize_question_slot_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s　（）()・/／]", "", normalized)


_QUESTION_SLOT_LABEL_KEYS = {
    _normalize_question_slot_label(label): label for label in _QUESTION_SLOT_LABELS
}
_QUESTION_FILTER_SLOT_LABEL_KEYS = {
    _normalize_question_slot_label(label) for label in _QUESTION_FILTER_SLOT_LABELS
}
_QUESTION_TARGET_TABLE_SLOT_LABEL_KEYS = {
    _normalize_question_slot_label(label) for label in _QUESTION_TARGET_TABLE_SLOT_LABELS
}


@dataclass(frozen=True)
class _StructuredQuestionSlots:
    slots: dict[str, str] = field(default_factory=dict)
    has_template: bool = False

    @property
    def has_empty_filter_slot(self) -> bool:
        return any(
            _normalize_question_slot_label(label) in _QUESTION_FILTER_SLOT_LABEL_KEYS
            and not value.strip()
            for label, value in self.slots.items()
        )


def _parse_structured_question_slots(question: str) -> _StructuredQuestionSlots:
    slots: dict[str, str] = {}
    current_label = ""
    has_template = False
    for raw_line in str(question or "").splitlines():
        match = _QUESTION_SLOT_PATTERN.match(raw_line)
        if match:
            label_key = _normalize_question_slot_label(match.group(1))
            label = _QUESTION_SLOT_LABEL_KEYS.get(label_key)
            if label:
                has_template = True
                current_label = label
                current_value = slots.get(label, "")
                line_value = match.group(2)
                slots[label] = f"{current_value}\n{line_value}" if current_value else line_value
                continue
        if current_label:
            current_value = slots.get(current_label, "")
            slots[current_label] = f"{current_value}\n{raw_line}" if current_value else raw_line
    return _StructuredQuestionSlots(
        slots={label: value.strip() for label, value in slots.items()},
        has_template=has_template,
    )


def _question_has_empty_filter_slot(question: str) -> bool:
    return _parse_structured_question_slots(question).has_empty_filter_slot


def _structured_question_values(question: str, label_keys: set[str]) -> list[str]:
    slots = _parse_structured_question_slots(question)
    if not slots.has_template:
        return []
    values: list[str] = []
    for label, value in slots.slots.items():
        if _normalize_question_slot_label(label) not in label_keys:
            continue
        values.extend(line.strip() for line in value.splitlines() if line.strip())
    return values


def _structured_question_filter_values(question: str) -> list[str]:
    return _structured_question_values(question, _QUESTION_FILTER_SLOT_LABEL_KEYS)


def _structured_question_target_table_values(question: str) -> list[str]:
    return _structured_question_values(question, _QUESTION_TARGET_TABLE_SLOT_LABEL_KEYS)


def _question_with_empty_filter_guard(question: str) -> str:
    cleaned = str(question or "").strip()
    if not cleaned or not _question_has_empty_filter_slot(cleaned):
        return cleaned
    if _EMPTY_FILTER_GENERATION_INSTRUCTION in cleaned:
        return cleaned
    return f"{cleaned}\n\n=== NL2SQL Guard ===\n{_EMPTY_FILTER_GENERATION_INSTRUCTION}"


_SYSTEM_OBJECT_BLOCKED_MESSAGE = (
    "NL2SQL_ で始まる表/VIEW は NL2SQL システム object です。"
    "システムテーブル管理からのみ管理できます。"
)

_FORBIDDEN_PREFIXES = (
    "insert",
    "update",
    "delete",
    "merge",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "begin",
    "declare",
    "call",
)
_DANGEROUS_TOKENS = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|begin|declare|call)\b",
    re.IGNORECASE,
)
_SQL_OBJECT_REF = r'(?:"[^"]+"|[a-zA-Z_][\w$#]*)(?:\s*\.\s*(?:"[^"]+"|[a-zA-Z_][\w$#]*))?'
_FROM_JOIN_TABLE = re.compile(rf"\b(?:from|join)\s+({_SQL_OBJECT_REF})", re.IGNORECASE)
_FROM_JOIN_WITH_ALIAS = re.compile(
    rf"\b(?:from|join)\s+({_SQL_OBJECT_REF})(?:\s+(?:as\s+)?([a-zA-Z_][\w$#]*))?",
    re.IGNORECASE,
)
_SYSTEM_OBJECT_TOKEN = re.compile(
    r'(?<![A-Z0-9_$#])"?NL2SQL_[A-Z0-9_$#]*"?',
    re.IGNORECASE,
)
_SELECT_TOKEN = re.compile(r"\bselect\b", re.IGNORECASE)
_SQL_IDENTIFIER = re.compile(r"[a-zA-Z_][\w$#]*")
_STRICT_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_EXISTING_ORACLE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
_QUALIFIED_COLUMN = re.compile(r"([a-zA-Z_][\w$#]*)\s*\.\s*([a-zA-Z_*][\w$#*]*)", re.IGNORECASE)
_COMMENT_TARGET = re.compile(
    r"^comment\s+on\s+([a-zA-Z_]+(?:\s+[a-zA-Z_]+)?(?:\s+[a-zA-Z_]+)?)\b",
    re.IGNORECASE,
)
_SCHEMA_MUTATION_STATEMENT_TYPES = frozenset(
    {"CREATE", "ALTER", "DROP", "COMMENT", "RENAME", "FLASHBACK", "PURGE"}
)
_IMPLICIT_COMMIT_STATEMENT_TYPES = frozenset(
    {"CREATE", "ALTER", "DROP", "TRUNCATE", "COMMENT", "RENAME", "FLASHBACK", "PURGE"}
)
_ROLLBACKABLE_DML_STATEMENT_TYPES = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})
_SQL_RESERVED_OR_FUNCTIONS = {
    "AS",
    "CASE",
    "CAST",
    "COALESCE",
    "COUNT",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "DATE",
    "DECODE",
    "DISTINCT",
    "ELSE",
    "END",
    "EXTRACT",
    "FROM",
    "LOWER",
    "MAX",
    "MIN",
    "NVL",
    "NULL",
    "NULLIF",
    "NUMBER",
    "OVER",
    "RANK",
    "ROW_NUMBER",
    "SELECT",
    "SUM",
    "THEN",
    "TO_CHAR",
    "TO_DATE",
    "TRUNC",
    "UPPER",
    "WHEN",
}
_SQL_STRUCTURE_SYSTEM_PROMPT = (
    "You are a SQL parser. Output ONLY the requested markdown format. No explanations."
)
_SQL_STRUCTURE_ANALYSIS_PROMPT = (
    "Analyze the SQL query and extract its COMPLETE structure in Markdown format.\n"
    "GOAL: Output must contain 100% of SQL information to enable exact SQL reconstruction.\n"
    "Output ONLY the markdown text below (no code blocks, no explanations):\n\n"
    "## SQL構造分析\n\n"
    "### SELECT句\n"
    "- [DISTINCT] (if present)\n"
    "- schema.table(alias).column1 [AS alias1]\n"
    "- aggregate_function(schema.table(alias).column) [AS alias]\n"
    "- expression [AS alias]\n"
    "- (サブクエリ-N) AS alias\n"
    "- * (if SELECT *)\n\n"
    "### FROM句\n"
    "- schema.table_name [alias]\n"
    "- (サブクエリ-N) AS alias (if inline view)\n\n"
    "### JOIN句\n"
    "- **[JOIN_TYPE]**: schema.table1(alias1) JOIN schema.table2(alias2)\n"
    "  - ON: condition1\n"
    "  - ON: condition2 (if multiple conditions)\n"
    "  - USING: (column_name) (if USING clause)\n\n"
    "### WHERE句\n"
    "- schema.table(alias).column operator value\n"
    "- AND/OR schema.table(alias).column operator value\n"
    "- AND/OR schema.table(alias).column IN (サブクエリ-N)\n"
    "- AND/OR EXISTS (サブクエリ-N)\n"
    "- AND/OR schema.table(alias).column BETWEEN value1 AND value2\n"
    "- AND/OR schema.table(alias).column LIKE 'pattern'\n"
    "- AND/OR schema.table(alias).column IS [NOT] NULL\n\n"
    "### GROUP BY句\n"
    "- schema.table(alias).column1\n"
    "- schema.table(alias).column2\n\n"
    "### HAVING句\n"
    "- aggregate_function(schema.table(alias).column) operator value\n\n"
    "### ORDER BY句\n"
    "- schema.table(alias).column1 ASC/DESC [NULLS FIRST/LAST]\n\n"
    "### WITH句(CTE)\n"
    "- **cte_name**:\n"
    "  - SELECT: columns and expressions\n"
    "  - FROM: schema.table_name(alias)\n"
    "  - JOIN: **[JOIN_TYPE]** schema.table(alias) ON condition\n"
    "  - WHERE: condition1 AND/OR condition2\n\n"
    "### サブクエリ\n"
    "- **サブクエリ-1** [Location: SELECT/FROM/WHERE/HAVING in main/CTE]:\n"
    "  - SELECT: columns/expressions\n"
    "  - FROM: schema.table_name(alias)\n"
    "  - JOIN: **[JOIN_TYPE]** schema.table(alias) ON condition\n"
    "  - WHERE: conditions\n\n"
    "Rules for 100% SQL Reconstruction:\n"
    "- MUST output ALL columns in SELECT with exact order, aliases, and expressions\n"
    "- MUST preserve ALL literal values, operators, and functions exactly as written\n"
    "- MUST include schema prefix when present in original SQL\n"
    "- Format: schema.table_name(alias).column when alias exists\n"
    "- JOIN_TYPE: INNER JOIN, LEFT [OUTER] JOIN, RIGHT [OUTER] JOIN, "
    "FULL [OUTER] JOIN, CROSS JOIN, NATURAL JOIN\n"
    "- For implicit JOIN (FROM t1, t2 WHERE t1.id=t2.id), "
    "list in FROM and show condition in WHERE\n"
    "- For compound JOIN conditions, list each ON condition separately\n"
    "- Preserve ALL operators: =, >, <, >=, <=, <>, !=, LIKE, NOT LIKE, IN, "
    "NOT IN, BETWEEN, IS NULL, IS NOT NULL, EXISTS, NOT EXISTS\n"
    "- Preserve ALL string literals with quotes, numeric values, date literals\n"
    "- Preserve AND/OR/NOT logical structure exactly\n"
    "- Do NOT merge JOIN ON conditions into WHERE\n"
    "- WITH句(CTE): Expand EACH CTE completely\n"
    "- サブクエリ: Number sequentially and expand completely\n"
    "- If section is empty/not present, omit that section entirely\n\n"
    "SQL:\n```sql\n{sql}\n```"
)


class _SqlAnalysisLlmPayload(BaseModel):
    """Enterprise AI structured payload for optional SQL deep analysis."""

    explanation: str = ""
    structure_summary: str = ""
    risk_level: str = "low"
    statement_type: str = ""
    object_names: list[str] = PydanticField(default_factory=list)
    column_names: list[str] = PydanticField(default_factory=list)
    conditions: list[str] = PydanticField(default_factory=list)
    group_by: list[str] = PydanticField(default_factory=list)
    order_by: list[str] = PydanticField(default_factory=list)
    joins: list[str] = PydanticField(default_factory=list)
    aggregations: list[str] = PydanticField(default_factory=list)
    risk_findings: list[str] = PydanticField(default_factory=list)
    repair_candidates: list[str] = PydanticField(default_factory=list)
    natural_language_question: str = ""
    logical_steps: list[str] = PydanticField(default_factory=list)


_SQL_WORD_TOKEN = re.compile(r"[A-Za-z_][\w$#]*")
_CREATE_PLSQL_BLOCK_START = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:(?:editionable|noneditionable)\s+)?"
    r"(?:procedure|function|package(?:\s+body)?|trigger|type(?:\s+body)?)\b",
    re.IGNORECASE,
)


def _statement_slice_has_sql(text: str) -> bool:
    return bool(_strip_leading_sql_comments(text).strip())


def _is_sqlplus_slash_terminator(masked_sql: str, index: int) -> bool:
    if masked_sql[index] != "/":
        return False
    line_start = masked_sql.rfind("\n", 0, index) + 1
    line_end = masked_sql.find("\n", index + 1)
    if line_end < 0:
        line_end = len(masked_sql)
    return (
        masked_sql[line_start:index].strip() == ""
        and masked_sql[index + 1 : line_end].strip() == ""
    )


def _end_clause_keyword(masked_sql: str, index: int) -> str:
    length = len(masked_sql)
    cursor = index
    while cursor < length and masked_sql[cursor].isspace():
        cursor += 1
    match = _SQL_WORD_TOKEN.match(masked_sql, cursor)
    if not match:
        return ""
    after_word = match.end()
    cursor = after_word
    while cursor < length and masked_sql[cursor].isspace():
        cursor += 1
    if cursor < length and masked_sql[cursor] == ";":
        return match.group(0).lower()
    return ""


def _split_sql_statements(sql: str) -> list[str]:
    """SQL を線形走査で分割し、literal/comment と PL/SQL block を壊さない。"""

    text = _normalize_oracle_sql_text(sql)
    masked = _mask_sql_literals_and_comments(text)
    statements: list[str] = []
    statement_start = 0
    statement_has_code = False
    block_mode: Literal["anonymous", "slash"] | None = None
    plsql_depth = 0
    case_depth = 0
    pending_declare_begins = 0
    anonymous_close_pending = False
    index = 0
    length = len(masked)

    def reset_state(next_start: int) -> None:
        nonlocal statement_start
        nonlocal statement_has_code
        nonlocal block_mode
        nonlocal plsql_depth
        nonlocal case_depth
        nonlocal pending_declare_begins
        nonlocal anonymous_close_pending
        statement_start = next_start
        statement_has_code = False
        block_mode = None
        plsql_depth = 0
        case_depth = 0
        pending_declare_begins = 0
        anonymous_close_pending = False

    def append_statement(end: int) -> None:
        statement = text[statement_start:end].strip()
        if statement and _statement_slice_has_sql(statement):
            statements.append(statement)

    while index < length:
        char = masked[index]
        if _is_sqlplus_slash_terminator(masked, index):
            append_statement(index)
            reset_state(index + 1)
            index += 1
            continue
        if char.isspace():
            index += 1
            continue
        match = _SQL_WORD_TOKEN.match(masked, index)
        if match:
            token = match.group(0)
            lowered = token.lower()
            if not statement_has_code:
                if lowered in {"begin", "declare"}:
                    block_mode = "anonymous"
                elif _CREATE_PLSQL_BLOCK_START.match(masked, index):
                    block_mode = "slash"
            statement_has_code = True
            if block_mode == "anonymous":
                if lowered == "declare":
                    plsql_depth += 1
                    pending_declare_begins += 1
                    anonymous_close_pending = False
                elif lowered == "begin":
                    if pending_declare_begins:
                        pending_declare_begins -= 1
                    else:
                        plsql_depth += 1
                    anonymous_close_pending = False
                elif lowered == "case":
                    case_depth += 1
                    anonymous_close_pending = False
                elif lowered == "end":
                    end_kind = _end_clause_keyword(masked, match.end())
                    if case_depth > 0 or end_kind == "case":
                        case_depth = max(case_depth - 1, 0)
                        anonymous_close_pending = False
                    elif end_kind in {"if", "loop"}:
                        anonymous_close_pending = False
                    else:
                        plsql_depth = max(plsql_depth - 1, 0)
                        anonymous_close_pending = plsql_depth == 0
                elif anonymous_close_pending and lowered not in {"if", "loop", "case"}:
                    # END label; の label は終端の一部なので close_pending を維持する。
                    pass
                else:
                    anonymous_close_pending = False
            index = match.end()
            continue
        statement_has_code = True
        if char == ";":
            if block_mode == "slash":
                index += 1
                continue
            if block_mode == "anonymous":
                if anonymous_close_pending and plsql_depth == 0 and case_depth == 0:
                    append_statement(index + 1)
                    reset_state(index + 1)
                index += 1
                continue
            append_statement(index)
            reset_state(index + 1)
            index += 1
            continue
        if anonymous_close_pending and not char.isspace():
            anonymous_close_pending = False
        index += 1

    append_statement(length)
    return statements


def _strip_leading_sql_comments(sql: str) -> str:
    text = str(sql or "").lstrip()
    while True:
        if text.startswith("--"):
            newline = text.find("\n")
            text = "" if newline < 0 else text[newline + 1 :].lstrip()
            continue
        if text.startswith("/*"):
            end = text.find("*/")
            text = "" if end < 0 else text[end + 2 :].lstrip()
            continue
        return text


_Q_QUOTE_CLOSERS = {"[": "]", "(": ")", "{": "}", "<": ">"}
_ORACLE_SQL_INVISIBLE_CHARS = frozenset({"\ufeff", "\u200b", "\u200c", "\u200d", "\u2060"})
_ORACLE_SQL_ASCII_WHITESPACE = frozenset({" ", "\t", "\n", "\f"})


def _normalize_oracle_sql_plain_char(char: str) -> str:
    if char in _ORACLE_SQL_INVISIBLE_CHARS:
        return ""
    if char == "\r":
        return "\n"
    if char in _ORACLE_SQL_ASCII_WHITESPACE:
        return char
    if char.isspace() or unicodedata.category(char) == "Zs":
        return " "
    return char


def _normalize_oracle_sql_text(sql: str) -> str:
    """貼り付け SQL の構文空白だけを Oracle が解釈できる ASCII 空白へ寄せる。"""

    text = str(sql or "")
    length = len(text)
    out: list[str] = []
    index = 0
    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""
        if char == "-" and next_char == "-":
            end = text.find("\n", index)
            end = length if end < 0 else end
            out.append(text[index:end])
            index = end
            continue
        if char == "/" and next_char == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                out.append(text[index:])
                break
            end += 2
            out.append(text[index:end])
            index = end
            continue
        previous = text[index - 1] if index > 0 else ""
        if (
            char in {"q", "Q"}
            and next_char == "'"
            and index + 2 < length
            and not (previous.isalnum() or previous in {"_", "$", "#"})
        ):
            opener = text[index + 2]
            closer = _Q_QUOTE_CLOSERS.get(opener, opener)
            end = text.find(f"{closer}'", index + 3)
            if end < 0:
                out.append(text[index:])
                break
            end += 2
            out.append(text[index:end])
            index = end
            continue
        if char in {"'", '"'}:
            end = index + 1
            closed = False
            while end < length:
                if text[end] != char:
                    end += 1
                    continue
                if char == "'" and end + 1 < length and text[end + 1] == "'":
                    end += 2
                    continue
                closed = True
                break
            if not closed:
                out.append(text[index:])
                break
            end += 1
            out.append(text[index:end])
            index = end
            continue
        out.append(_normalize_oracle_sql_plain_char(char))
        index += 1
    return "".join(out)


def _mask_sql_literals_and_comments(sql: str) -> str:
    """文字列リテラル・引用識別子・コメントの中身を空白へ置き換える。

    危険語 / `;` の判定を SQL の構造だけに向けるための前処理。業務データの値
    (`'delete'` 等)や列コメントに含まれる語で正当な SELECT を弾かないようにする。
    文字数は保つ(位置を参照する呼び出しがあっても壊れない)。

    閉じ引用符 / `*/` が見つからない(構文的に壊れた)場合は残りをマスクせず
    そのまま返し、後段の判定が保守的(拒否側)に働くようにする。
    """
    text = str(sql or "")
    length = len(text)
    out: list[str] = []
    index = 0
    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""
        if char == "-" and next_char == "-":
            end = text.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
            continue
        if char == "/" and next_char == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                out.append(text[index:])
                break
            end += 2
            out.append(" " * (end - index))
            index = end
            continue
        previous = text[index - 1] if index > 0 else ""
        if (
            char in {"q", "Q"}
            and next_char == "'"
            and index + 2 < length
            and not (previous.isalnum() or previous in {"_", "$", "#"})
        ):
            opener = text[index + 2]
            closer = _Q_QUOTE_CLOSERS.get(opener, opener)
            end = text.find(f"{closer}'", index + 3)
            if end < 0:
                out.append(text[index:])
                break
            end += 2
            out.append(" " * (end - index))
            index = end
            continue
        if char in {"'", '"'}:
            end = index + 1
            closed = False
            while end < length:
                if text[end] != char:
                    end += 1
                    continue
                if char == "'" and end + 1 < length and text[end + 1] == "'":
                    end += 2
                    continue
                closed = True
                break
            if not closed:
                out.append(text[index:])
                break
            end += 1
            out.append(" " * (end - index))
            index = end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _admin_statement_type(sql: str) -> str:
    stripped = _strip_leading_sql_comments(sql).strip()
    if _COMMENT_TARGET.match(stripped):
        return "COMMENT"
    if re.match(r"^(select|with)\b", stripped, flags=re.IGNORECASE):
        return "SELECT"
    if re.match(r"^(begin|declare|exec|execute)\b", stripped, flags=re.IGNORECASE):
        return "PLSQL"
    for keyword in (
        "insert",
        "update",
        "delete",
        "merge",
        "create",
        "drop",
        "alter",
        "truncate",
        "rename",
        "flashback",
        "purge",
        "grant",
        "revoke",
    ):
        if re.match(rf"^{keyword}\b", stripped, flags=re.IGNORECASE):
            return keyword.upper()
    return "UNKNOWN"


def _statements_change_schema(statements: Sequence[str]) -> bool:
    """Read model の再取得が必要な構造・metadata 変更だけを判定する。"""

    return any(
        _admin_statement_type(statement) in _SCHEMA_MUTATION_STATEMENT_TYPES
        for statement in statements
    )


def _split_object_ref_parts(value: str) -> list[str]:
    """SQL object ref を dot 区切りにする（二重引用符内の dot は保持）。"""

    parts: list[str] = []
    buffer: list[str] = []
    in_double = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == '"':
            buffer.append(char)
            if in_double and index + 1 < len(value) and value[index + 1] == '"':
                buffer.append(value[index + 1])
                index += 2
                continue
            in_double = not in_double
            index += 1
            continue
        if char == "." and not in_double:
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_object_ref(value: str) -> str:
    return ".".join(_split_object_ref_parts(value))


def _schema_refresh_target_from_ref(
    value: str,
    *,
    current_owner: str,
    object_type: Literal["table", "view", "materialized_view", "unknown"],
    expected_state: Literal["present", "absent", "unknown"],
) -> SchemaRefreshTargetObject | None:
    try:
        identity = parse_object_identity(_normalize_object_ref(value), default_owner=current_owner)
    except ValueError:
        return None
    return SchemaRefreshTargetObject(
        owner=identity.owner,
        object_name=identity.object_name,
        object_type=object_type,
        expected_state=expected_state,
    )


def _schema_refresh_target_from_column_ref(
    value: str,
    *,
    current_owner: str,
) -> SchemaRefreshTargetObject | None:
    parts = _split_object_ref_parts(value)
    if len(parts) not in {2, 3}:
        return None
    return _schema_refresh_target_from_ref(
        ".".join(parts[:-1]),
        current_owner=current_owner,
        object_type="table",
        expected_state="present",
    )


def _schema_refresh_target_for_statement(
    statement: str,
    *,
    current_owner: str,
) -> SchemaRefreshTargetObject | None:
    stripped = _strip_leading_sql_comments(statement).strip().rstrip(";")
    object_ref = f"({_SQL_OBJECT_REF})"
    object_end = r"(?=\s|\(|$)"
    patterns: tuple[
        tuple[str, Literal["table", "view"], Literal["present", "absent"]],
        ...,
    ] = (
        (
            rf"^create\s+(?:global\s+temporary\s+)?table\s+{object_ref}{object_end}",
            "table",
            "present",
        ),
        (
            rf"^create\s+(?:or\s+replace\s+)?(?:force\s+)?(?:editionable\s+)?view\s+"
            rf"{object_ref}{object_end}",
            "view",
            "present",
        ),
        (rf"^drop\s+table\s+{object_ref}{object_end}", "table", "absent"),
        (rf"^drop\s+view\s+{object_ref}{object_end}", "view", "absent"),
        (rf"^alter\s+table\s+{object_ref}{object_end}", "table", "present"),
        (rf"^alter\s+view\s+{object_ref}{object_end}", "view", "present"),
        (rf"^comment\s+on\s+table\s+{object_ref}\s+is\b", "table", "present"),
    )
    for pattern, object_type, expected_state in patterns:
        match = re.match(pattern, stripped, flags=re.IGNORECASE)
        if match:
            return _schema_refresh_target_from_ref(
                match.group(1),
                current_owner=current_owner,
                object_type=object_type,
                expected_state=expected_state,
            )
    materialized = re.match(
        rf"^comment\s+on\s+materialized\s+view\s+{object_ref}\s+is\b",
        stripped,
        flags=re.IGNORECASE,
    )
    if materialized:
        return _schema_refresh_target_from_ref(
            materialized.group(1),
            current_owner=current_owner,
            object_type="materialized_view",
            expected_state="present",
        )
    materialized_alter = re.match(
        rf"^alter\s+materialized\s+view\s+{object_ref}{object_end}",
        stripped,
        flags=re.IGNORECASE,
    )
    if materialized_alter:
        return _schema_refresh_target_from_ref(
            materialized_alter.group(1),
            current_owner=current_owner,
            object_type="materialized_view",
            expected_state="present",
        )
    column = re.match(
        r"^comment\s+on\s+column\s+(.+?)\s+is\b",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if column:
        return _schema_refresh_target_from_column_ref(
            column.group(1),
            current_owner=current_owner,
        )
    return None


def _dedupe_schema_refresh_targets(
    targets: Iterable[SchemaRefreshTargetObject],
) -> list[SchemaRefreshTargetObject]:
    merged: dict[tuple[str, str], SchemaRefreshTargetObject] = {}
    for target in targets:
        key = (target.owner.upper(), target.object_name.upper())
        current = merged.get(key)
        if current is None:
            merged[key] = target.model_copy(update={"owner": key[0], "object_name": key[1]})
            continue
        expected_state = current.expected_state
        if target.expected_state != "unknown":
            expected_state = target.expected_state
        object_type = current.object_type
        if target.object_type != "unknown":
            object_type = target.object_type
        merged[key] = current.model_copy(
            update={"object_type": object_type, "expected_state": expected_state}
        )
    return list(merged.values())


def _schema_refresh_targets_for_statements(
    statements: Sequence[str],
    *,
    current_owner: str,
) -> list[SchemaRefreshTargetObject] | None:
    targets: list[SchemaRefreshTargetObject] = []
    for statement in statements:
        if _admin_statement_type(statement) not in _SCHEMA_MUTATION_STATEMENT_TYPES:
            continue
        target = _schema_refresh_target_for_statement(
            statement,
            current_owner=current_owner,
        )
        if target is None:
            return None
        targets.append(target)
    return _dedupe_schema_refresh_targets(targets)


def _system_object_blocked_message(object_names: Sequence[str] | None = None) -> str:
    names = sorted({name for name in (object_names or []) if name})
    if not names:
        return _SYSTEM_OBJECT_BLOCKED_MESSAGE
    return f"{', '.join(names)}: {_SYSTEM_OBJECT_BLOCKED_MESSAGE}"


def _hidden_schema_object_names(values: Sequence[str], *, current_owner: str) -> list[str]:
    hidden: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            identity = parse_object_identity(
                _normalize_object_ref(value),
                default_owner=current_owner,
            )
        except ValueError:
            object_name = _normalize_identifier(value)
            qualified = object_name
            visible = is_user_visible_object_name(object_name)
        else:
            qualified = identity.qualified_name
            visible = is_user_visible_schema_object(identity.owner, identity.object_name)
        if visible or qualified in seen:
            continue
        seen.add(qualified)
        hidden.append(qualified)
    return hidden


def _dml_target_refs(statement: str) -> list[str]:
    stripped = _strip_leading_sql_comments(statement).strip().rstrip(";")
    object_ref = f"({_SQL_OBJECT_REF})"
    object_end = r"(?=\s|\(|$)"
    patterns = (
        rf"^insert\s+into\s+{object_ref}{object_end}",
        rf"^update\s+{object_ref}{object_end}",
        rf"^delete\s+from\s+{object_ref}{object_end}",
        rf"^merge\s+into\s+{object_ref}{object_end}",
        rf"^truncate\s+table\s+{object_ref}{object_end}",
    )
    refs: list[str] = []
    for pattern in patterns:
        match = re.match(pattern, stripped, flags=re.IGNORECASE)
        if match:
            refs.append(match.group(1))
    return refs


def _admin_statement_hidden_object_names(
    statement: str,
    *,
    current_owner: str,
) -> list[str]:
    statement_type = _admin_statement_type(statement)
    refs: list[str] = []
    if statement_type == "SELECT":
        refs.extend(_extract_referenced_tables(statement, current_owner=current_owner))
    elif statement_type in {"CREATE", "ALTER", "DROP", "COMMENT"}:
        target = _schema_refresh_target_for_statement(statement, current_owner=current_owner)
        if target is not None:
            refs.append(target.owner + "." + target.object_name)
        if statement_type == "CREATE":
            refs.extend(_extract_referenced_tables(statement, current_owner=current_owner))
    elif statement_type in {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}:
        refs.extend(_dml_target_refs(statement))
        refs.extend(_extract_referenced_tables(statement, current_owner=current_owner))
    elif statement_type in {"PLSQL", "UNKNOWN"} and _SYSTEM_OBJECT_TOKEN.search(statement):
        return [
            _normalize_identifier(match.group(0))
            for match in _SYSTEM_OBJECT_TOKEN.finditer(statement)
        ]
    return _hidden_schema_object_names(refs, current_owner=current_owner)


def _normalize_admin_statement(sql: str) -> str:
    stripped = _normalize_oracle_sql_text(sql).strip()
    if re.match(r"^(exec|execute)\b", stripped, flags=re.IGNORECASE):
        body = re.sub(r"^(exec|execute)\s+", "", stripped, flags=re.IGNORECASE).strip()
        return f"BEGIN {body.rstrip(';')}; END;"
    return stripped


# SQL Assist の execute_create_table / execute_create_view / execute_data_sql と同一の許可セット
_DB_ADMIN_STATEMENT_POLICIES: dict[str, tuple[re.Pattern[str], ...]] = {
    "table_ddl": (
        re.compile(r"^create\s+(global\s+temporary\s+)?table\b", re.IGNORECASE),
        re.compile(r"^comment\s+on\s+(table|column)\b", re.IGNORECASE),
        re.compile(r"^drop\s+table\b", re.IGNORECASE),
    ),
    "view_ddl": (
        re.compile(
            r"^create\s+(or\s+replace\s+)?(force\s+)?(editionable\s+)?view\b",
            re.IGNORECASE,
        ),
        re.compile(r"^comment\s+on\s+(table|column)\b", re.IGNORECASE),
        re.compile(r"^drop\s+view\b", re.IGNORECASE),
    ),
    "data_dml": (re.compile(r"^(insert|update|delete|merge|truncate)\b", re.IGNORECASE),),
    "comment_sql": (
        re.compile(r"^comment\s+on\s+(table|column|materialized\s+view)\b", re.IGNORECASE),
    ),
    "annotation_sql": (re.compile(r"^alter\s+(table|view|materialized\s+view)\b", re.IGNORECASE),),
}

_DB_ADMIN_POLICY_LABELS = {
    "table_ddl": "CREATE TABLE / COMMENT ON / DROP TABLE",
    "view_ddl": "CREATE [OR REPLACE] VIEW / COMMENT ON / DROP VIEW",
    "data_dml": "INSERT / UPDATE / DELETE / MERGE / TRUNCATE",
    "comment_sql": "COMMENT ON TABLE/COLUMN/MATERIALIZED VIEW",
    "annotation_sql": (
        "ALTER TABLE MODIFY ... ANNOTATIONS / ALTER TABLE ANNOTATIONS / "
        "ALTER VIEW ANNOTATIONS / ALTER MATERIALIZED VIEW ANNOTATIONS"
    ),
}


def _metadata_object_kind(value: str | None) -> Literal["table", "view", "materialized_view"]:
    normalized = re.sub(r"[\s_-]+", "_", str(value or "").strip().lower())
    if normalized in {"materialized_view", "materializedview", "mview"}:
        return "materialized_view"
    if "materialized" in normalized and "view" in normalized:
        return "materialized_view"
    if normalized == "view":
        return "view"
    return "table"


def _catalog_metadata_object_kind(
    table: SchemaTable | None,
    requested_type: str | None = None,
) -> Literal["table", "view", "materialized_view"]:
    if table is not None:
        return _metadata_object_kind(table.table_type)
    return _metadata_object_kind(requested_type)


def _comment_ddl_kind_for_metadata(
    kind: Literal["table", "view", "materialized_view"],
) -> str:
    return "MATERIALIZED VIEW" if kind == "materialized_view" else "TABLE"


def _annotation_ddl_kind_for_metadata(
    kind: Literal["table", "view", "materialized_view"],
) -> str:
    if kind == "materialized_view":
        return "MATERIALIZED VIEW"
    if kind == "view":
        return "VIEW"
    return "TABLE"


def _validate_oracle_metadata_literal_bytes(
    value: str,
    *,
    target: str,
    label: str,
) -> None:
    if len(value.encode("utf-8")) > 4000:
        raise ValueError(f"{target}: {label} は Oracle の 4000 バイト以内で指定してください。")


def _rewrite_comment_on_view_statement(statement: str) -> str:
    return re.sub(
        r"^(\s*)comment\s+on\s+view\b",
        r"\1COMMENT ON TABLE",
        statement,
        count=1,
        flags=re.IGNORECASE,
    )


def _admin_statement_result_succeeded(result: Mapping[str, Any] | None) -> bool:
    if not result:
        return False
    status = str(result.get("status") or "").strip().lower()
    return status in {"success", "applied", "executed", "applied_to_local_state"}


def _admin_statement_result_error_message(result: Mapping[str, Any] | None) -> str:
    if not result:
        return ""
    return str(result.get("error_message") or result.get("message") or "").strip()


def _align_admin_statement_results(
    results: Sequence[Mapping[str, Any]],
    statement_count: int,
) -> list[Mapping[str, Any] | None]:
    aligned: list[Mapping[str, Any] | None] = [None] * statement_count
    for fallback_index, result in enumerate(results):
        raw_index = result.get("index", fallback_index + 1)
        try:
            index = int(raw_index) - 1
        except (TypeError, ValueError):
            index = fallback_index
        if 0 <= index < statement_count:
            aligned[index] = result
    return aligned


def _db_admin_policy_error(statement: str, policy: str) -> str:
    """policy に反する statement なら日本語エラーを返す(許可なら空文字)。"""
    stripped = _strip_leading_sql_comments(statement).strip()
    if policy == "annotation_sql":
        return _annotation_statement_error(stripped)
    for pattern in _DB_ADMIN_STATEMENT_POLICIES[policy]:
        if pattern.match(stripped):
            return ""
    return f"禁止された操作です。{_DB_ADMIN_POLICY_LABELS[policy]} のみ実行できます。"


def _db_admin_system_object_error(statement: str, *, current_owner: str) -> str:
    hidden = _admin_statement_hidden_object_names(statement, current_owner=current_owner)
    return _system_object_blocked_message(hidden) if hidden else ""


def _annotation_statement_error(statement: str) -> str:
    norm = re.sub(r"\s+", " ", statement.strip())
    object_ref = _SQL_OBJECT_REF
    allowed_patterns = (
        rf"^alter\s+table\s+{object_ref}\s+annotations\s*\(.+\)\s*$",
        rf"^alter\s+table\s+{object_ref}\s+modify\s*\(.+\s+annotations\s*\(.+\)\s*\)\s*$",
        rf"^alter\s+table\s+{object_ref}\s+modify\s+.+\s+annotations\s*\(.+\)\s*$",
        rf"^alter\s+view\s+{object_ref}\s+annotations\s*\(.+\)\s*$",
        rf"^alter\s+materialized\s+view\s+{object_ref}\s+annotations\s*\(.+\)\s*$",
    )
    if any(re.match(pattern, norm, flags=re.IGNORECASE) for pattern in allowed_patterns):
        return _annotation_clause_error(statement)
    return f"禁止された操作です。{_DB_ADMIN_POLICY_LABELS['annotation_sql']} のみ実行できます。"


def _split_annotation_items(value: str) -> list[str]:
    """ANNOTATIONS 内を、引用符中のカンマを保ったまま項目へ分割する。"""
    items: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(value):
        char = value[index]
        next_char = value[index + 1] if index + 1 < len(value) else ""
        if in_single:
            buffer.append(char)
            if char == "'" and next_char == "'":
                buffer.append(next_char)
                index += 2
                continue
            if char == "'":
                in_single = False
        elif in_double:
            buffer.append(char)
            if char == '"' and next_char == '"':
                buffer.append(next_char)
                index += 2
                continue
            if char == '"':
                in_double = False
        elif char == "'":
            in_single = True
            buffer.append(char)
        elif char == '"':
            in_double = True
            buffer.append(char)
        elif char == ",":
            item = "".join(buffer).strip()
            if item:
                items.append(item)
            buffer = []
        else:
            buffer.append(char)
        index += 1
    item = "".join(buffer).strip()
    if item:
        items.append(item)
    return items


def _annotation_item_name(value: str) -> tuple[str, bool]:
    """操作句を除いた annotation 名と、二重引用符の有無を返す。"""
    remainder = re.sub(
        r"^\s*(?:add(?:\s+(?:if\s+not\s+exists|or\s+replace))?|"
        r"drop(?:\s+if\s+exists)?|replace)\s+",
        "",
        value,
        count=1,
        flags=re.IGNORECASE,
    ).lstrip()
    quoted = re.match(r'^"((?:[^"]|"")+)"', remainder)
    if quoted:
        return quoted.group(1).replace('""', '"'), True
    unquoted = re.match(r"^([A-Za-z_][\w$#]*)", remainder)
    return (unquoted.group(1), False) if unquoted else ("", False)


def _annotation_clause_contents(statement: str) -> list[tuple[int, int, str]]:
    """ANNOTATIONS(...) の内側位置と内容を引用符対応で抽出する。"""
    clauses: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\bannotations\s*\(", statement, flags=re.IGNORECASE):
        start = match.end()
        depth = 1
        in_single = False
        in_double = False
        index = start
        while index < len(statement):
            char = statement[index]
            next_char = statement[index + 1] if index + 1 < len(statement) else ""
            if in_single:
                if char == "'" and next_char == "'":
                    index += 2
                    continue
                if char == "'":
                    in_single = False
            elif in_double:
                if char == '"' and next_char == '"':
                    index += 2
                    continue
                if char == '"':
                    in_double = False
            elif char == "'":
                in_single = True
            elif char == '"':
                in_double = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    clauses.append((start, index, statement[start:index]))
                    break
            index += 1
    return clauses


def _annotation_clause_error(statement: str) -> str:
    clauses = _annotation_clause_contents(statement)
    if not clauses:
        return "ANNOTATIONS 句の括弧が不正です。"
    for _start, _end, content in clauses:
        items = _split_annotation_items(content)
        if not items:
            return "ANNOTATIONS 句に annotation 名を指定してください。"
        for item in items:
            name, quoted = _annotation_item_name(item)
            if not name:
                return f"ANNOTATIONS 句の annotation 名が不正です: {item}"
            if name.upper() == "COMMENT" and not quoted:
                return (
                    "ORA-11548 相当: annotation 名 COMMENT は Oracle の予約語です。"
                    "説明には UI_Display を使用するか、意図的な名前であれば "
                    '"COMMENT" と二重引用符で囲んでください。'
                )
    return ""


def _without_sample_annotations(statement: str) -> str:
    """サンプル未取得時に sample_header / sample_data だけを安全に除外する。"""
    filtered = statement
    clauses = _annotation_clause_contents(statement)
    for start, end, content in reversed(clauses):
        kept = []
        for item in _split_annotation_items(content):
            name, _quoted = _annotation_item_name(item)
            if name.lower() not in {"sample_header", "sample_data"}:
                kept.append(item)
        if not kept:
            return ""
        filtered = filtered[:start] + ", ".join(kept) + filtered[end:]
    return filtered


_ANNOTATION_ANY_OPERATION_RE = re.compile(
    r"^\s*(?:add(?:\s+(?:if\s+not\s+exists|or\s+replace))?|drop(?:\s+if\s+exists)?|replace)\s+",
    flags=re.IGNORECASE,
)


def _normalize_annotation_add_operations(statement: str) -> str:
    """ANNOTATIONS 句で操作句が省略された annotation だけを明示 ADD にする。

    Oracle の annotations_clause では操作句(ADD 等)が後続 annotation へ伝播しない。
    ``ANNOTATIONS (ADD IF NOT EXISTS UI_Display '...', data_type 'NUMBER')`` の
    data_type は操作句省略で既定の素の ADD になり、既存 annotation では
    ORA-11560 になる。操作句省略だけを ``ADD IF NOT EXISTS`` へ補い、
    ユーザーが明示した ADD / DROP / REPLACE / ADD OR REPLACE は変更しない。
    """
    normalized = statement
    for start, end, content in reversed(_annotation_clause_contents(statement)):
        rewritten: list[str] = []
        for item in _split_annotation_items(content):
            if _ANNOTATION_ANY_OPERATION_RE.match(item):
                rewritten.append(item)
            else:
                rewritten.append(f"ADD IF NOT EXISTS {item.strip()}")
        normalized = normalized[:start] + ", ".join(rewritten) + normalized[end:]
    return normalized


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _normalize_identifier(value: str) -> str:
    parts = [part.strip().strip('"') for part in value.strip().split(".")]
    return (parts[-1] if parts else "").upper()


def _synthetic_data_type_key(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().upper())
    if normalized.startswith("LONG RAW"):
        return "LONG RAW"
    if normalized.startswith("SDO_GEOMETRY"):
        return "SDO_GEOMETRY"
    return normalized.split("(", 1)[0].strip()


def _csv_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().upper()).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"C_{normalized}"
    return normalized[:128]


def _oracle_asset_key(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().upper()).strip("_")
    if not normalized:
        normalized = fallback.strip().upper() or "PROFILE"
    return normalized[:128]


def _oracle_agent_asset_name(*, prefix: str, profile_key: str, suffix: str) -> str:
    safe_prefix = _csv_identifier(prefix, "NL2SQL")
    safe_suffix = _oracle_asset_key(suffix, "ASSET")
    base = f"{safe_prefix}_{_oracle_asset_key(profile_key, 'PROFILE')}"
    max_base_length = max(1, 128 - len(safe_suffix) - 1)
    safe_base = base[:max_base_length].rstrip("_") or safe_prefix[:max_base_length].rstrip("_")
    return f"{safe_base}_{safe_suffix}"


def _existing_oracle_identifier(value: str) -> str:
    """既存の通常 Oracle identifier を変換せず、安全に検証する。"""
    normalized = value.strip().strip('"').upper()
    if not _EXISTING_ORACLE_IDENTIFIER.fullmatch(normalized):
        raise ValueError(
            "object_name は英字で始まる英数字・underscore・$・# の "
            "Oracle 識別子で指定してください。"
        )
    return normalized


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_object_identity(identity: OracleObjectIdentity) -> str:
    return f"{_quote_identifier(identity.owner)}.{_quote_identifier(identity.object_name)}"


def _qualified_display_name(owner: str, object_name: str) -> str:
    try:
        return qualified_object_name(owner, object_name)
    except ValueError:
        return object_name.strip()


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_WORKBOOK_ILLEGAL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def _clean_workbook_text(value: str) -> str:
    return _WORKBOOK_ILLEGAL_CHARACTERS.sub("", value)


def _normalize_db_admin_preview_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex().upper()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping | list | tuple):
        return _clean_workbook_text(json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, str):
        return _clean_workbook_text(value)
    return value


def _normalize_db_admin_preview_results(results: QueryResults) -> QueryResults:
    rows = [
        {column: _normalize_db_admin_preview_value(row.get(column)) for column in results.columns}
        for row in results.rows
    ]
    return results.model_copy(update={"rows": rows})


def _write_workbook_cell(sheet: Any, *, row: int, column: int, value: Any) -> None:
    cell = sheet.cell(row=row, column=column)
    normalized = _normalize_db_admin_preview_value(value)
    cell.value = normalized
    if isinstance(normalized, str):
        cell.data_type = "s"


class _CsvRow(dict[str, str | None]):
    """Oracle row error を元ファイル行へ戻すため、dict の等価性は保ったまま行番号を持つ。"""

    def __init__(self, values: Mapping[str, str | None], *, file_row_number: int) -> None:
        super().__init__(values)
        self.file_row_number = file_row_number


def _decode_tabular_text_content(content: bytes) -> tuple[str, str]:
    for encoding, label in (("utf-8-sig", "UTF-8"), ("cp932", "CP932")):
        try:
            return content.decode(encoding), label
        except UnicodeDecodeError:
            continue
    detected = from_bytes(content).best()
    if detected is None:
        raise TabularImportValidationError(
            "CSV の文字エンコーディングを判定できません。"
            "UTF-8 または CP932 の CSV として保存し直して再試行してください。"
        )
    encoding = str(detected.encoding or "unknown").upper()
    return str(detected), encoding


def _similarity_tokens(value: str) -> set[str]:
    normalized = value.upper()
    tokens = {match.group(0) for match in re.finditer(r"[A-Z0-9_]{2,}", normalized)}
    cjk = [char for char in value if "\u3040" <= char <= "\u9fff"]
    tokens.update(cjk)
    tokens.update("".join(cjk[index : index + 2]) for index in range(max(len(cjk) - 1, 0)))
    return {token for token in tokens if token.strip()}


def _profile_recommendation_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return {
        token
        for token in _similarity_tokens(normalized)
        if len(token.strip()) >= 2 and token.casefold() not in _PROFILE_RECOMMENDATION_STOPWORDS
    }


def is_select_only(sql: str) -> bool:
    """SELECT/WITH のみを許可し、DDL/DML/PLSQL と複数 statement を拒否する。

    先頭コメントは読み飛ばし、文字列リテラル・引用識別子・コメントの中身は
    危険語 / `;` の判定対象から外す(値に `'delete'` が入った SELECT は実行可)。
    """
    stripped = _strip_leading_sql_comments(sql).strip()
    if not stripped:
        return False
    masked = _mask_sql_literals_and_comments(stripped)
    head = masked.lstrip("(").lstrip().lower()
    if head.startswith(_FORBIDDEN_PREFIXES):
        return False
    if ";" in masked.rstrip().rstrip(";"):
        return False
    if _DANGEROUS_TOKENS.search(masked):
        return False
    return head.startswith("select") or head.startswith("with")


def _extract_referenced_tables(sql: str, *, current_owner: str = "") -> list[str]:
    semantic = parse_oracle_sql(sql)
    if semantic.graph is not None:
        seen: set[str] = set()
        tables: list[str] = []
        for table in semantic.graph.tables:
            if table.is_cte:
                continue
            owner = table.owner.upper() or current_owner.upper()
            name = table.name.upper()
            normalized = qualified_object_name(owner, name) if owner else name
            if normalized not in seen:
                seen.add(normalized)
                tables.append(normalized)
        return tables
    fallback_seen: set[str] = set()
    fallback_tables: list[str] = []
    for match in _FROM_JOIN_TABLE.finditer(sql):
        normalized = _normalize_identifier(match.group(1))
        if normalized and normalized not in fallback_seen:
            fallback_seen.add(normalized)
            fallback_tables.append(normalized)
    return fallback_tables


def _alias_to_table(sql: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for match in _FROM_JOIN_WITH_ALIAS.finditer(sql):
        table = _normalize_identifier(match.group(1))
        alias = (match.group(2) or "").upper()
        aliases[table] = table
        if alias and alias not in {
            "FETCH",
            "GROUP",
            "HAVING",
            "JOIN",
            "LEFT",
            "ORDER",
            "RIGHT",
            "WHERE",
        }:
            aliases[alias] = table
    return aliases


def _find_top_level_from(sql: str, start: int) -> int:
    depth = 0
    in_quote = False
    index = start
    while index < len(sql):
        char = sql[index]
        if char == "'":
            in_quote = not in_quote
        elif not in_quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(depth - 1, 0)
            elif depth == 0 and sql[index : index + 4].lower() == "from":
                before = sql[index - 1] if index > 0 else " "
                after = sql[index + 4] if index + 4 < len(sql) else " "
                if not (before.isalnum() or before in "_$#") and not (
                    after.isalnum() or after in "_$#"
                ):
                    return index
        index += 1
    return -1


def _extract_select_list(sql: str) -> str:
    candidates: list[str] = []
    for match in _SELECT_TOKEN.finditer(sql):
        start = match.end()
        from_index = _find_top_level_from(sql, start)
        if from_index > start:
            candidates.append(sql[start:from_index])
    return candidates[-1].strip() if candidates else ""


def _split_select_expressions(select_list: str) -> list[str]:
    expressions: list[str] = []
    depth = 0
    in_quote = False
    start = 0
    for index, char in enumerate(select_list):
        if char == "'":
            in_quote = not in_quote
        elif not in_quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(depth - 1, 0)
            elif char == "," and depth == 0:
                expressions.append(select_list[start:index].strip())
                start = index + 1
    tail = select_list[start:].strip()
    if tail:
        expressions.append(tail)
    return expressions


def _strip_expression_alias(expression: str) -> str:
    without_alias = re.split(r"\s+as\s+", expression, maxsplit=1, flags=re.IGNORECASE)[0]
    tokens = without_alias.strip().split()
    if len(tokens) > 1 and re.fullmatch(r"[a-zA-Z_][\w$#]*", tokens[-1]):
        return " ".join(tokens[:-1])
    return without_alias


def _extract_referenced_columns(sql: str, referenced_tables: list[str]) -> tuple[list[str], bool]:
    select_list = _extract_select_list(sql)
    if not select_list:
        return [], False
    aliases = _alias_to_table(sql)
    single_table = referenced_tables[0] if len(referenced_tables) == 1 else ""
    seen: set[str] = set()
    columns: list[str] = []
    wildcard = False
    for raw_expression in _split_select_expressions(select_list):
        expression = _strip_expression_alias(raw_expression)
        if re.search(r"(^|[^.\w$#])\*($|[^.\w$#])", expression):
            wildcard = True
        qualified_matches = list(_QUALIFIED_COLUMN.finditer(expression))
        if qualified_matches:
            for match in qualified_matches:
                table_or_alias = match.group(1).upper()
                column = match.group(2).upper()
                if column == "*":
                    wildcard = True
                    continue
                table = aliases.get(table_or_alias, table_or_alias)
                key = f"{table}.{column}"
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
            continue
        cleaned = re.sub(r"'[^']*'", " ", expression)
        for token_match in _SQL_IDENTIFIER.finditer(cleaned):
            token = token_match.group(0).upper()
            if token in _SQL_RESERVED_OR_FUNCTIONS:
                continue
            key = f"{single_table}.{token}" if single_table else token
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns, wildcard


def _scope_object_name(value: str, *, current_owner: str) -> str:
    return parse_object_identity(value, default_owner=current_owner).qualified_name


def _table_allowed(
    referenced_tables: list[str],
    allowed: AllowedObjects,
    *,
    current_owner: str,
) -> bool:
    if not allowed.table_names:
        return not allowed.enforce_table_scope or not referenced_tables
    allowed_set = {
        _scope_object_name(table, current_owner=current_owner) for table in allowed.table_names
    }
    return all(
        _scope_object_name(table, current_owner=current_owner) in allowed_set
        for table in referenced_tables
    )


def _column_allowed(
    referenced_columns: list[str],
    has_wildcard: bool,
    referenced_tables: list[str],
    allowed: AllowedObjects,
    *,
    current_owner: str,
) -> bool:
    restrictions = {
        _scope_object_name(table, current_owner=current_owner): {
            _normalize_identifier(column) for column in columns
        }
        for table, columns in allowed.columns.items()
        if columns
    }
    if not restrictions:
        return True
    restricted_referenced_tables = [
        table for table in referenced_tables if table in restrictions
    ] or list(restrictions)
    if has_wildcard and restricted_referenced_tables:
        return False
    for column_ref in referenced_columns:
        if "." in column_ref:
            table, column = column_ref.rsplit(".", 1)
            if table in restrictions and column not in restrictions[table]:
                return False
            continue
        if referenced_tables:
            allowed_somewhere = any(
                column_ref in restrictions.get(table, set()) for table in referenced_tables
            )
            if not allowed_somewhere:
                return False
    return True


def one_line_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def normalize_executable_sql(sql: str) -> str:
    """実行可能な形へ整えるだけ(前後空白と末尾セミコロンの除去)。

    行数上限を SQL 文へ裏で書き足さない。利用者や LLM が自分で書いた
    FETCH FIRST / LIMIT はそのまま保持する。行数上限は画面の「取得件数上限」
    (= 取得時の fetch 上限、OracleAdapter.execute_select の fetchmany)だけで効かせる。
    """
    return _normalize_oracle_sql_text(sql).strip().rstrip(";")


_JOB_RESULT_PERSISTENCE_WARNING = (
    "結果は生成されましたが、履歴/ジョブ保存に失敗しました。"
    "結果を確認後、必要に応じて管理者にお問い合わせください。"
)


@dataclass
class GeneratedSql:
    engine: Nl2SqlEngine
    generated_sql: str
    explanation: str
    engine_meta: dict[str, Any]
    fallback_reason: str = ""
    schema_catalog: SchemaCatalog | None = None


@dataclass(frozen=True)
class LearningExample:
    source: str
    question: str
    sql: str
    history_id: str | None = None
    score: float | None = None
    feedback: str | None = None
    reason: str = ""


@dataclass
class StoredJob:
    job_id: str
    request: JobCreateRequest
    actor_user_uuid: str = ""
    actor_is_system_admin: bool = False
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=_utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_ms: int | None = None
    result: Nl2SqlResult | None = None
    error_message: str | None = None
    error_code: str | None = None
    warning_message: str | None = None
    timing: TimingEnvelope | None = None
    steps: list[JobStepData] = field(default_factory=list)
    # 協調キャンセル要求。worker が stage 境界で検出して JobCancelledError を送出する。
    cancel_requested: bool = False
    # 自プロセスの worker スレッドが実行している job か。False は他 worker / 再起動前の
    # snapshot 由来で、in-flight の間は repository を正本として読み直す(永続化しない)。
    owned: bool = False


_NL2SQL_JOB_STAGES = (
    "prepare_context",
    "generate_sql",
    "safety_check",
    "execute_sql",
    "format_results",
)


def _new_job_steps() -> list[JobStepData]:
    return [JobStepData(stage=stage) for stage in _NL2SQL_JOB_STAGES]


def _job_failure_step_index(steps: list[JobStepData]) -> int | None:
    for expected_status in (JobStepStatus.RUNNING, JobStepStatus.PENDING):
        for index, step in enumerate(steps):
            if step.status == expected_status:
                return index
    return None


def _restore_job_steps(
    raw_steps: list[dict[str, Any]],
    *,
    status: JobStatus,
    timing: TimingEnvelope | None,
    has_result: bool,
) -> list[JobStepData]:
    """旧 snapshot の3段階 timing も現在の5段階契約へ正規化する。"""

    parsed = [JobStepData.model_validate(step) for step in raw_steps]
    by_stage = {step.stage: step for step in parsed}
    legacy_step = by_stage.get("safety_and_execute")
    timing_by_stage = (
        {item.stage: item.elapsed_ms for item in timing.stage_timings} if timing else {}
    )
    legacy_elapsed = timing_by_stage.get("safety_and_execute")
    restored: list[JobStepData] = []

    for stage in _NL2SQL_JOB_STAGES:
        step = by_stage.get(stage)
        if step is not None:
            restored.append(step)
            continue
        if stage in {"safety_check", "execute_sql"} and legacy_step is not None:
            legacy_status = legacy_step.status
            if status == JobStatus.ERROR:
                legacy_status = (
                    JobStepStatus.ERROR if stage == "safety_check" else JobStepStatus.SKIPPED
                )
            restored.append(
                legacy_step.model_copy(update={"stage": stage, "status": legacy_status})
            )
            continue
        elapsed_ms = timing_by_stage.get(stage)
        if elapsed_ms is None and stage in {"safety_check", "execute_sql"}:
            elapsed_ms = legacy_elapsed
        completed = elapsed_ms is not None or status == JobStatus.DONE
        if stage == "format_results" and has_result:
            completed = True
        restored.append(
            JobStepData(
                stage=stage,
                status=JobStepStatus.DONE if completed else JobStepStatus.PENDING,
                elapsed_ms=elapsed_ms,
            )
        )

    if status == JobStatus.ERROR and not any(
        step.status == JobStepStatus.ERROR for step in restored
    ):
        safety_index = _NL2SQL_JOB_STAGES.index("safety_check")
        execute_index = _NL2SQL_JOB_STAGES.index("execute_sql")
        if legacy_elapsed is not None or timing_by_stage.get("safety_check") is not None:
            restored[safety_index] = restored[safety_index].model_copy(
                update={"status": JobStepStatus.ERROR}
            )
            restored[execute_index] = restored[execute_index].model_copy(
                update={"status": JobStepStatus.SKIPPED}
            )
        else:
            failure_index = _job_failure_step_index(restored)
            if failure_index is not None:
                restored[failure_index] = restored[failure_index].model_copy(
                    update={"status": JobStepStatus.ERROR}
                )
    return restored


def _history_item_matches_payload_filters(item: HistoryItem, filters: Mapping[str, str]) -> bool:
    """memory 実装向けに repository の payload filter(top-level key の文字列一致)を再現する。"""

    if not filters:
        return True
    payload = item.model_dump(mode="json")
    return all(str(payload.get(key) or "") == value for key, value in filters.items())


# reverse deep で LLM から受け取る処理手順の上限(長大な配列をそのまま保持しない)。
_REVERSE_DEEP_MAX_STEPS = 20


def _reverse_deep_steps(raw: object) -> list[str]:
    """LLM 応答の logical_steps を空要素除去・件数上限付きの文字列 list に正規化する。"""

    if not isinstance(raw, list):
        return []
    steps = [str(item).strip() for item in raw if str(item).strip()]
    return steps[:_REVERSE_DEEP_MAX_STEPS]


def _reverse_deep_step_details(
    steps: Sequence[str],
    deterministic_details: Sequence[Nl2SqlLogicalStep],
) -> list[Nl2SqlLogicalStep]:
    """LLM の手順を業務行にした details を作る。

    件数が決定論版と一致するときだけ technical(技術行)と kind を対応付け、一致しない
    ときは業務行だけにする(位置のずれた技術行を並べて誤解させない)。
    """

    aligned = len(deterministic_details) == len(steps)
    return [
        Nl2SqlLogicalStep(
            kind=deterministic_details[index].kind if aligned else "llm",
            business=step,
            technical=deterministic_details[index].technical if aligned else "",
        )
        for index, step in enumerate(steps)
    ]


class Nl2SqlService:
    """NL2SQL orchestration with pluggable state store."""

    def __init__(self, store: Nl2SqlStore | None = None) -> None:
        settings = get_settings()
        self._deepsec_enabled = settings.oracle_deepsec_enabled
        self._lock = threading.RLock()
        self._catalog = self._build_default_catalog()
        self._oracle_adapter = OracleNl2SqlAdapter(settings)
        self._embedding_client: FeedbackEmbeddingClient = OciGenAiEmbeddingClient(settings)
        self._enterprise_ai_client: EnterpriseAiDirectClient = OciEnterpriseAiDirectClient(settings)
        self._store: Nl2SqlStore
        self._incremental_repository: IncrementalNl2SqlRepository | None = None
        if (
            store is None
            and settings.nl2sql_persistence_mode.strip().lower() == "oracle"
            and settings.nl2sql_state_backend.strip().lower() == "incremental"
        ):
            # Repository construction is deliberately zero-I/O. Oracle is touched only by
            # readiness or a data request, so module import time is independent of data size.
            self._incremental_repository = OracleIncrementalNl2SqlRepository(
                connection_factory=self._oracle_adapter.connection
            )
            self._store = MemoryNl2SqlStore()
        else:
            self._store = store or self._build_store(settings)
        self._persistence_mode = (
            "oracle" if self._incremental_repository is not None else self._store.mode
        )
        self._profile_cache = VersionedTtlCache(
            max_entries=settings.nl2sql_profile_cache_max_entries,
            ttl_seconds=settings.nl2sql_cache_ttl_seconds,
            name="profile",
        )
        self._schema_cache = VersionedTtlCache(
            max_entries=settings.nl2sql_schema_object_cache_max_entries,
            ttl_seconds=settings.nl2sql_cache_ttl_seconds,
            name="schema",
        )
        self._cache_token_checked_at = {
            PROFILE_NAMESPACE: 0.0,
            SCHEMA_NAMESPACE: 0.0,
        }
        self._cache_token_poll_seconds = max(0.1, settings.nl2sql_cache_ttl_seconds)
        self._profile_change_token = 0
        self._schema_change_token = 0
        self._incremental_hashes: dict[tuple[str, str], str] = {}
        self._schema_refresh_lock = threading.Lock()
        self._schema_refresh_submit_lock = threading.Lock()
        self._schema_refresh_dispatch_lock = threading.Lock()
        self._schema_refresh_dispatching_job_ids: set[str] = set()
        self._schema_refresh_worker_id = f"api:{uuid.uuid4()}"
        self._profile_list_refresh_lock = threading.Lock()
        self._profile_list_refresh_dispatch_lock = threading.Lock()
        self._profile_list_refresh_dispatching_job_ids: set[str] = set()
        self._refresh_job_repository: IncrementalNl2SqlRepository = (
            self._incremental_repository
            if self._incremental_repository is not None
            else MemoryIncrementalNl2SqlRepository(seed_default=False)
        )
        self._persistence_ready = False
        self._persistence_writable = False
        self._snapshot_loaded = False
        self._persistence_reason_code: str | None = "not_checked"
        self._persistence_checked_at = _utc_now()
        self._persistence_recovery_lock = threading.RLock()
        self._persistence_circuit_state: Literal["closed", "open", "half_open"] = "open"
        self._persistence_retry_at = 0.0
        self._persistence_retry_delay_seconds = 5.0
        self._last_persisted_snapshot: dict[str, Any] | None = None
        self._profiles: dict[str, Nl2SqlProfile] = {
            "default": Nl2SqlProfile(
                id="default",
                name="標準プロファイル",
                description="",
                allowed_tables=[],
                glossary={},
                sql_rules=[],
                default_row_limit=settings.nl2sql_default_row_limit,
                few_shot_examples=[],
            )
        }
        self._jobs: dict[str, StoredJob] = {}
        # job 毎の worker スレッドが同時に走る数の上限(Oracle セッション枯渇防止)。
        self._job_concurrency = threading.BoundedSemaphore(settings.nl2sql_job_max_concurrency)
        self._history: list[HistoryItem] = []
        self._feedback: dict[str, FeedbackRating] = {}
        self._feedback_indexed_ids: set[str] = set()
        self._feedback_index_lock = _FEEDBACK_INDEX_OPERATION_LOCK
        self._feedback_similarity_threshold = 0.0
        self._feedback_match_limit = 3
        self._classifier_examples: list[ClassifierTrainingExample] = []
        self._classifier_artifact: dict[str, Any] | None = None
        self._classifier_state_loaded = False
        self._classifier_state_token = -1
        self._classifier_state_checked_at = 0.0
        self._classifier_model_payload_cache: tuple[str, str, dict[str, Any]] | None = None
        self._classifier_import_lock = threading.RLock()
        self._asset_meta: dict[Nl2SqlEngine, AssetRefreshData] = {}
        self._admin_audit: list[dict[str, Any]] = []
        self._legacy_learning_material = LegacyLearningMaterialData()
        self._legacy_learning_material_io_lock = threading.RLock()
        self._legacy_learning_material_loaded = False
        self._legacy_learning_material_checked_at = 0.0
        self._ontology_name_index_cache: tuple[str, dict[str, dict[str, str]]] | None = None
        if self._incremental_repository is not None:
            self._persistence_ready = True
            self._persistence_writable = True
            self._persistence_reason_code = None
            self._persistence_checked_at = _utc_now()
            self._close_persistence_circuit_locked()
        elif self._load_snapshot():
            # load が成功した場合だけ初期/正規化済み snapshot を保存する。DB 障害時に
            # process-local の既定値で既存 row を上書きしない。
            self._persist_state(raise_on_error=False)

    def _build_store(self, settings: Any) -> Nl2SqlStore:
        mode = settings.nl2sql_persistence_mode.strip().lower()
        if mode == "oracle":
            return OracleJsonNl2SqlStore(
                connection_factory=self._oracle_adapter.connection,
                table_name=settings.nl2sql_oracle_state_table,
                migration_mirror_enabled=settings.nl2sql_migration_mirror_enabled,
            )
        if mode == "memory":
            return MemoryNl2SqlStore()
        raise ValueError(
            "NL2SQL_PERSISTENCE_MODE は oracle または明示的な memory のみ指定できます。"
        )

    @property
    def uses_incremental_store(self) -> bool:
        return self._incremental_repository is not None

    def check_incremental_store(self) -> tuple[bool, str]:
        """Readiness 用の bounded migration check（業務 row は読み込まない）。"""

        if self._incremental_repository is None:
            return True, "legacy_snapshot"
        return self._incremental_repository.check()

    def _raise_incremental_repository_failure(
        self,
        *,
        operation: str,
        exc: Exception,
        operation_error_code: str,
    ) -> NoReturn:
        """接続障害だけを global circuit へ反映し、SQL 実装不良を局所化する。"""

        oracle_code = _safe_oracle_error_code(exc)
        if _is_oracle_connection_failure(exc):
            category = "connection"
            reason_code = "oracle_connection_unavailable"
            record_persistence_failure(operation, category)
            logger.error(
                "nl2sql_incremental_repository_connection_failed",
                extra={
                    "operation": operation,
                    "error_code": oracle_code or "connection_error",
                    "exception_type": type(exc).__name__,
                },
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            self._mark_persistence_unavailable(reason_code)
            raise Nl2SqlPersistenceUnavailable(reason_code) from exc

        if (
            oracle_code in _ORACLE_SCHEMA_COMPATIBILITY_CODES
            and operation != "persistence_recover"
            and self._incremental_repository is not None
        ):
            try:
                migrated, _detail = self._incremental_repository.check()
            except Exception as check_exc:
                if _is_oracle_connection_failure(check_exc):
                    record_persistence_failure(operation, "connection")
                    self._mark_persistence_unavailable("oracle_connection_unavailable")
                    raise Nl2SqlPersistenceUnavailable(
                        "oracle_connection_unavailable"
                    ) from check_exc
            else:
                if not migrated:
                    record_persistence_failure(operation, "migration")
                    self._mark_persistence_unavailable("incremental_migration_required")
                    raise Nl2SqlPersistenceUnavailable("incremental_migration_required") from exc

        record_persistence_failure(operation, "operation")
        logger.error(
            "nl2sql_incremental_repository_operation_failed",
            extra={
                "operation": operation,
                "error_code": oracle_code or operation_error_code,
                "exception_type": type(exc).__name__,
            },
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        raise Nl2SqlRepositoryOperationFailed(operation_error_code) from exc

    def _load_snapshot(self, *, raise_on_error: bool = False) -> bool:
        try:
            snapshot = self._store.load_snapshot()
        except Exception as exc:  # pragma: no cover - live store defensive boundary
            logger.exception(
                "nl2sql_snapshot_load_failed",
                extra={"store_mode": self._store.mode, "exception_type": type(exc).__name__},
            )
            self._mark_persistence_unavailable("snapshot_load_failed")
            if raise_on_error:
                raise Nl2SqlPersistenceUnavailable("snapshot_load_failed") from exc
            return False
        if snapshot:
            try:
                self._restore_snapshot(snapshot, recover_interrupted_jobs=True)
            except Exception as exc:
                logger.exception(
                    "nl2sql_snapshot_restore_failed",
                    extra={
                        "store_mode": self._store.mode,
                        "exception_type": type(exc).__name__,
                    },
                )
                self._mark_persistence_unavailable("snapshot_invalid")
                if raise_on_error:
                    raise Nl2SqlPersistenceUnavailable("snapshot_invalid") from exc
                return False
        with self._lock:
            self._snapshot_loaded = True
            self._persistence_ready = True
            self._persistence_writable = True
            self._persistence_reason_code = None
            self._persistence_checked_at = _utc_now()
            self._close_persistence_circuit_locked()
            self._last_persisted_snapshot = (
                copy.deepcopy(self._snapshot_locked()) if snapshot else None
            )
        return True

    def _restore_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        recover_interrupted_jobs: bool,
    ) -> None:
        catalog = filter_user_visible_catalog(
            SchemaCatalog.model_validate(snapshot.get("catalog", self._catalog))
        )
        profile_items = (
            snapshot["profiles"]
            if "profiles" in snapshot
            else [profile.model_dump(mode="json") for profile in self._profiles.values()]
        )
        profiles = [Nl2SqlProfile.model_validate(item) for item in profile_items]
        jobs = {
            item["job_id"]: self._job_from_snapshot(item)
            for item in snapshot.get("jobs", [])
            if item.get("job_id")
        }
        history = [HistoryItem.model_validate(item) for item in snapshot.get("history", [])]
        asset_meta = {
            Nl2SqlEngine(engine): AssetRefreshData.model_validate(data)
            for engine, data in snapshot.get("asset_meta", {}).items()
        }
        feedback_indexed_ids = {str(item) for item in snapshot.get("feedback_indexed_ids", [])}
        feedback_config = snapshot.get("feedback_search_config", {})
        classifier_examples = [
            ClassifierTrainingExample.model_validate(item)
            for item in snapshot.get("classifier_examples", [])
        ]
        classifier_artifact = snapshot.get("classifier_artifact")
        if classifier_artifact is not None and not isinstance(classifier_artifact, dict):
            classifier_artifact = None
        admin_audit = [
            dict(item) for item in snapshot.get("admin_audit", []) if isinstance(item, dict)
        ]
        legacy_learning_material = LegacyLearningMaterialData.model_validate(
            snapshot.get("legacy_learning_material", {})
        )
        with self._lock:
            self._catalog = catalog
            self._profiles = {profile.id: profile for profile in profiles}
            self._jobs = jobs
            if recover_interrupted_jobs:
                self._recover_interrupted_jobs()
            self._history = history
            self._feedback = {
                item.id: item.feedback_rating
                for item in history
                if item.feedback_rating is not None
            }
            self._feedback_indexed_ids = feedback_indexed_ids
            self._feedback_similarity_threshold = float(
                feedback_config.get("similarity_threshold", 0.0)
            )
            self._feedback_match_limit = int(feedback_config.get("match_limit", 3))
            self._classifier_examples = classifier_examples
            self._classifier_artifact = classifier_artifact
            self._classifier_state_loaded = False
            self._classifier_state_token = -1
            self._classifier_state_checked_at = 0.0
            self._classifier_model_payload_cache = None
            self._asset_meta = asset_meta
            self._admin_audit = admin_audit[-200:]
            self._legacy_learning_material = legacy_learning_material
            self._legacy_learning_material_loaded = True
            self._legacy_learning_material_checked_at = time.monotonic()

    def _mark_persistence_unavailable(self, reason_code: str) -> None:
        with self._lock:
            self._persistence_ready = False
            self._persistence_writable = False
            self._persistence_reason_code = reason_code
            self._persistence_checked_at = _utc_now()
            self._persistence_circuit_state = "open"
            self._persistence_retry_at = time.monotonic() + self._persistence_retry_delay_seconds
            self._persistence_retry_delay_seconds = min(
                self._persistence_retry_delay_seconds * 2,
                30.0,
            )
            set_persistence_circuit_state("open")

    def _close_persistence_circuit_locked(self) -> None:
        self._persistence_circuit_state = "closed"
        self._persistence_retry_at = 0.0
        self._persistence_retry_delay_seconds = 5.0
        set_persistence_circuit_state("closed")

    @staticmethod
    def _persistence_reason_is_auto_recoverable(reason_code: str) -> bool:
        return reason_code in {
            "oracle_connection_unavailable",
            "incremental_store_unreachable",
            "legacy_store_unreachable",
        } or (reason_code.startswith("incremental_") and reason_code.endswith("_failed"))

    def ensure_persistence_available(self) -> None:
        """共有状態 API を開放し、期限到来後は一要求だけ軽量回復を試す。"""
        with self._lock:
            if self._persistence_ready and self._persistence_writable:
                return
            reason_code = self._persistence_reason_code or "persistence_unavailable"
            retry_at = self._persistence_retry_at
        if (
            not self._persistence_reason_is_auto_recoverable(reason_code)
            or time.monotonic() < retry_at
            or not self._persistence_recovery_lock.acquire(blocking=False)
        ):
            raise Nl2SqlPersistenceUnavailable(reason_code)
        try:
            with self._lock:
                if self._persistence_ready and self._persistence_writable:
                    return
                self._persistence_circuit_state = "half_open"
                set_persistence_circuit_state("half_open")
            self._recover_persistence_locked()
        finally:
            self._persistence_recovery_lock.release()

    def persistence_status(self) -> PersistenceStatusData:
        """secret や Oracle 例外を含まない永続化状態を返す。"""
        with self._lock:
            mode = "oracle" if self._persistence_mode == "oracle" else "memory"
            return PersistenceStatusData(
                mode=mode,
                ready=self._persistence_ready,
                durable=mode == "oracle",
                writable=self._persistence_writable,
                snapshot_loaded=self._snapshot_loaded,
                reason_code=self._persistence_reason_code,
                checked_at=self._persistence_checked_at,
                state_backend=(
                    "incremental" if self._incremental_repository is not None else "legacy_snapshot"
                ),
                circuit_state=self._persistence_circuit_state,
                retry_after_seconds=max(
                    0,
                    math.ceil(self._persistence_retry_at - time.monotonic()),
                ),
            )

    def recover_persistence(self) -> PersistenceStatusData:
        """Oracle 復旧後に接続/migration を単一実行で再確認する。"""
        if not self._persistence_recovery_lock.acquire(blocking=False):
            raise Nl2SqlPersistenceUnavailable("persistence_recovery_in_progress")
        try:
            with self._lock:
                self._persistence_circuit_state = "half_open"
                set_persistence_circuit_state("half_open")
            return self._recover_persistence_locked()
        finally:
            self._persistence_recovery_lock.release()

    def _recover_persistence_locked(self) -> PersistenceStatusData:
        if self._incremental_repository is not None:
            try:
                ready, reason = self._incremental_repository.check()
            except Exception as exc:
                record_persistence_recovery("failed")
                self._raise_incremental_repository_failure(
                    operation="persistence_recover",
                    exc=exc,
                    operation_error_code="persistence_recovery_failed",
                )
            if not ready:
                record_persistence_recovery("migration_required")
                self._mark_persistence_unavailable("incremental_migration_required")
                raise Nl2SqlPersistenceUnavailable("incremental_migration_required")
            with self._lock:
                self._profile_cache.clear()
                self._schema_cache.clear()
                self._legacy_learning_material_loaded = False
                self._legacy_learning_material_checked_at = 0.0
                self._cache_token_checked_at = {
                    PROFILE_NAMESPACE: 0.0,
                    SCHEMA_NAMESPACE: 0.0,
                }
                self._persistence_ready = True
                self._persistence_writable = True
                self._persistence_reason_code = None
                self._persistence_checked_at = _utc_now()
                self._close_persistence_circuit_locked()
            record_persistence_recovery("success")
            logger.info("nl2sql_incremental_store_recovered", extra={"detail": reason})
            return self.persistence_status()
        try:
            ready, _reason = self._store.check()
        except Exception as exc:
            record_persistence_recovery("failed")
            self._mark_persistence_unavailable("legacy_store_unreachable")
            raise Nl2SqlPersistenceUnavailable("legacy_store_unreachable") from exc
        if not ready:
            record_persistence_recovery("failed")
            self._mark_persistence_unavailable("legacy_store_unreachable")
            raise Nl2SqlPersistenceUnavailable("legacy_store_unreachable")
        # 障害中に process-local の既定値へ退避した可能性があるため、API を
        # 再開する前に durable snapshot を必ず再読込する。
        self._load_snapshot(raise_on_error=True)
        record_persistence_recovery("success")
        return self.persistence_status()

    def reset_after_system_schema_change(self) -> None:
        """Schema epoch 変更後に DB 由来 cache と process-local mirror を破棄する。"""

        with self._lock:
            self._profile_cache.clear()
            self._schema_cache.clear()
            self._cache_token_checked_at = {
                PROFILE_NAMESPACE: 0.0,
                SCHEMA_NAMESPACE: 0.0,
            }
            self._profile_change_token = 0
            self._schema_change_token = 0
            self._catalog = self._build_default_catalog()
            self._jobs.clear()
            self._history.clear()
            self._feedback.clear()
            self._feedback_indexed_ids.clear()
            self._classifier_examples.clear()
            self._classifier_artifact = None
            self._legacy_learning_material = LegacyLearningMaterialData()
            self._legacy_learning_material_loaded = False
            self._legacy_learning_material_checked_at = 0.0
            self._incremental_hashes.clear()
            self._persistence_ready = False
            self._persistence_writable = False
            self._snapshot_loaded = False
            self._persistence_reason_code = "schema_epoch_changed"
            self._persistence_checked_at = _utc_now()
            self._ontology_name_index_cache = None

    def _merge_additional_instruction_lines(
        self,
        existing: str,
        additions: Iterable[str],
    ) -> str:
        base = existing.strip()
        seen = {line.strip() for line in base.splitlines() if line.strip()}
        next_lines: list[str] = []
        for value in additions:
            line = str(value or "").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            next_lines.append(line)
        if not next_lines:
            return base
        if base:
            return "\n".join([base, *next_lines])
        return "\n".join(next_lines)

    def _profile_with_sql_rules_absorbed(
        self,
        profile: Nl2SqlProfile,
        *,
        inherited_rules: Iterable[str] | None = None,
    ) -> Nl2SqlProfile:
        config = ProfileSelectAiConfig.model_validate(profile.select_ai_config)
        rules = self._merge_unique_strings(list(inherited_rules or []), profile.sql_rules)
        if not rules:
            return profile.model_copy(update={"sql_rules": [], "select_ai_config": config})
        config = config.model_copy(
            update={
                "additional_instructions": self._merge_additional_instruction_lines(
                    config.additional_instructions,
                    rules,
                )
            }
        )
        return profile.model_copy(update={"sql_rules": [], "select_ai_config": config})

    def _recover_interrupted_jobs(self) -> None:
        for job in self._jobs.values():
            if job.status in _IN_FLIGHT_JOB_STATUSES:
                self._mark_job_interrupted(job)

    def _persist_state(
        self,
        *,
        raise_on_error: bool = True,
        collections: tuple[str, ...] | None = None,
    ) -> None:
        if self._incremental_repository is not None:
            try:
                self._persist_incremental_documents(collections=collections)
            except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed):
                if raise_on_error:
                    raise
            except Exception as exc:
                if raise_on_error:
                    self._raise_incremental_repository_failure(
                        operation="document_save",
                        exc=exc,
                        operation_error_code="incremental_document_save_failed",
                    )
                record_persistence_failure("document_save", "operation")
                logger.error(
                    "nl2sql_incremental_save_failed",
                    extra={"exception_type": type(exc).__name__},
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            return
        with self._lock:
            if not self._persistence_ready or not self._persistence_writable:
                error = Nl2SqlPersistenceUnavailable(
                    self._persistence_reason_code or "persistence_unavailable"
                )
                if raise_on_error:
                    raise error
                return
            snapshot = self._snapshot_locked()
            previous_snapshot = copy.deepcopy(self._last_persisted_snapshot)
            try:
                # RLock を保持して snapshot/save/rollback を 1 transaction として扱う。
                self._store.save_snapshot(snapshot)
            except Exception as exc:  # pragma: no cover - live store defensive boundary
                logger.exception(
                    "nl2sql_snapshot_save_failed",
                    extra={
                        "store_mode": self._store.mode,
                        "exception_type": type(exc).__name__,
                    },
                )
                if previous_snapshot is not None:
                    self._restore_snapshot(
                        previous_snapshot,
                        recover_interrupted_jobs=False,
                    )
                self._mark_persistence_unavailable("snapshot_save_failed")
                error = Nl2SqlPersistenceUnavailable("snapshot_save_failed")
                if raise_on_error:
                    raise error from exc
                return
            self._last_persisted_snapshot = copy.deepcopy(snapshot)
            self._persistence_ready = True
            self._persistence_writable = True
            self._persistence_reason_code = None
            self._persistence_checked_at = _utc_now()

    def _persist_incremental_documents(self, *, collections: tuple[str, ...] | None) -> None:
        """Legacy orchestration state を entity 単位に差分保存する移行 bridge。

        Profile と schema は専用 repository path を使う。残る legacy service の mutation
        はこの bridge で同一 payload の再書込みを避け、単一巨大 CLOB へ戻さない。
        """

        repository = self._incremental_repository
        if repository is None:
            return
        identities = {
            "jobs": "job_id",
            "history": "id",
            "classifier_examples": "id",
            "admin_audit": "id",
        }
        selected = set(collections or (*identities, "singletons"))
        with self._lock:
            payloads: dict[str, list[dict[str, Any]]] = {}
            if "jobs" in selected:
                payloads["jobs"] = [self._job_to_snapshot(job) for job in self._jobs.values()]
            if "history" in selected:
                payloads["history"] = [item.model_dump(mode="json") for item in self._history]
            if "classifier_examples" in selected:
                payloads["classifier_examples"] = [
                    item.model_dump(mode="json") for item in self._classifier_examples
                ]
            if "admin_audit" in selected:
                payloads["admin_audit"] = copy.deepcopy(self._admin_audit)
            singleton_payloads: dict[str, Any] = {}
            if "singletons" in selected:
                singleton_payloads = self._singleton_payloads_locked()
        for collection, identity_field in identities.items():
            if collection not in selected:
                continue
            for index, raw in enumerate(payloads[collection]):
                if not isinstance(raw, dict):
                    continue
                entity_id = str(raw.get(identity_field) or f"runtime-{index}")
                digest = hashlib.sha256(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                key = (collection, entity_id)
                if self._incremental_hashes.get(key) == digest:
                    continue
                repository.put_document(
                    collection,
                    entity_id,
                    raw,
                    profile_id=str(raw.get("profile_id") or ""),
                    status=self._document_status(collection, raw),
                )
                self._incremental_hashes[key] = digest
        if "singletons" in selected:
            for entity_id, value in singleton_payloads.items():
                raw = {"value": value}
                digest = hashlib.sha256(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                key = ("singletons", entity_id)
                if self._incremental_hashes.get(key) == digest:
                    continue
                repository.put_document("singletons", entity_id, raw)
                self._incremental_hashes[key] = digest
        with self._lock:
            self._persistence_ready = True
            self._persistence_writable = True
            self._persistence_reason_code = None
            self._persistence_checked_at = _utc_now()

    def _singleton_payloads_locked(self, entity_ids: Iterable[str] | None = None) -> dict[str, Any]:
        payloads: dict[str, Any] = {
            "feedback_indexed_ids": sorted(self._feedback_indexed_ids),
            "feedback_search_config": {
                "similarity_threshold": self._feedback_similarity_threshold,
                "match_limit": self._feedback_match_limit,
            },
            "classifier_artifact": copy.deepcopy(self._classifier_artifact),
            "asset_meta": {
                engine.value: data.model_dump(mode="json")
                for engine, data in self._asset_meta.items()
            },
            "legacy_learning_material": self._legacy_learning_material.model_dump(mode="json"),
        }
        if entity_ids is None:
            return payloads
        selected = set(entity_ids)
        return {entity_id: value for entity_id, value in payloads.items() if entity_id in selected}

    def _persist_singletons(self, *entity_ids: str) -> None:
        """Incremental mode では指定 singleton だけを書き、別 singleton を既定値で潰さない。"""

        repository = self._incremental_repository
        if repository is None:
            self._persist_state(collections=("singletons",))
            return
        with self._lock:
            payloads = self._singleton_payloads_locked(entity_ids or None)
        try:
            for entity_id, value in payloads.items():
                raw = {"value": value}
                digest = hashlib.sha256(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                key = ("singletons", entity_id)
                if self._incremental_hashes.get(key) == digest:
                    continue
                repository.put_document("singletons", entity_id, raw)
                self._incremental_hashes[key] = digest
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="singleton_save",
                exc=exc,
                operation_error_code="incremental_singleton_save_failed",
            )
        with self._lock:
            self._persistence_ready = True
            self._persistence_writable = True
            self._persistence_reason_code = None
            self._persistence_checked_at = _utc_now()

    def _persist_entities(
        self,
        documents: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        """変更された aggregate だけを永続化する高頻度 mutation path。"""

        repository = self._incremental_repository
        if repository is None:
            self._persist_state()
            return
        try:
            for collection, entity_id, payload in documents:
                digest = hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode()
                ).hexdigest()
                key = (collection, entity_id)
                if self._incremental_hashes.get(key) == digest:
                    continue
                repository.put_document(
                    collection,
                    entity_id,
                    payload,
                    profile_id=str(payload.get("profile_id") or ""),
                    status=self._document_status(collection, payload),
                )
                self._incremental_hashes[key] = digest
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="document_save",
                exc=exc,
                operation_error_code="incremental_document_save_failed",
            )

    def _replace_incremental_entity_collection(
        self,
        collection: str,
        documents: list[tuple[str, str, dict[str, Any]]],
    ) -> None:
        repository = self._incremental_repository
        if repository is None:
            return
        try:
            repository.replace_documents(
                collection,
                [
                    (
                        entity_id,
                        payload,
                        str(payload.get("profile_id") or ""),
                        self._document_status(collection, payload),
                    )
                    for _collection, entity_id, payload in documents
                ],
            )
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="document_replace",
                exc=exc,
                operation_error_code="incremental_document_replace_failed",
            )
        with self._lock:
            for key in [key for key in self._incremental_hashes if key[0] == collection]:
                self._incremental_hashes.pop(key, None)
            for _collection, entity_id, payload in documents:
                digest = hashlib.sha256(
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode()
                ).hexdigest()
                self._incremental_hashes[(collection, entity_id)] = digest

    def _document_status(self, collection: str, payload: dict[str, Any]) -> str:
        if collection == "history":
            return str(payload.get("feedback_rating") or "unrated")
        return str(payload.get("status") or "")

    def _persist_job(self, job_id: str) -> None:
        with self._lock:
            payload = self._job_to_snapshot(self._jobs[job_id])
        self._persist_entities([("jobs", job_id, payload)])

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "catalog": self._catalog.model_dump(mode="json"),
            "profiles": [profile.model_dump(mode="json") for profile in self._profiles.values()],
            "jobs": [self._job_to_snapshot(job) for job in self._jobs.values()],
            "history": [item.model_dump(mode="json") for item in self._history],
            "feedback_indexed_ids": sorted(self._feedback_indexed_ids),
            "feedback_search_config": {
                "similarity_threshold": self._feedback_similarity_threshold,
                "match_limit": self._feedback_match_limit,
            },
            "classifier_examples": [
                item.model_dump(mode="json") for item in self._classifier_examples
            ],
            "classifier_artifact": self._classifier_artifact,
            "asset_meta": {
                engine.value: data.model_dump(mode="json")
                for engine, data in self._asset_meta.items()
            },
            "admin_audit": self._admin_audit[-200:],
            "legacy_learning_material": self._legacy_learning_material.model_dump(mode="json"),
            "saved_at": _utc_now(),
        }

    def _job_to_snapshot(self, job: StoredJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "request": job.request.model_dump(mode="json"),
            "actor_user_uuid": job.actor_user_uuid,
            "actor_is_system_admin": job.actor_is_system_admin,
            "status": job.status.value,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "elapsed_ms": job.elapsed_ms,
            "result": job.result.model_dump(mode="json") if job.result else None,
            "error_message": job.error_message,
            "error_code": job.error_code,
            "warning_message": job.warning_message,
            "timing": job.timing.model_dump(mode="json") if job.timing else None,
            "steps": [step.model_dump(mode="json") for step in job.steps],
            # 他 worker / 再起動後の読込側が「更新が途絶えた in-flight job」を判定する基準。
            "updated_at": _utc_now(),
        }

    def _job_snapshot_is_stale(self, data: dict[str, Any]) -> bool:
        """in-flight のまま更新が途絶えた snapshot(再起動などで孤児化した job)か。"""

        status = str(data.get("status") or "")
        if status not in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
            return False
        stamp = str(
            data.get("updated_at") or data.get("started_at") or data.get("created_at") or ""
        )
        try:
            updated = datetime.fromisoformat(stamp)
        except ValueError:
            return True
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - updated).total_seconds()
        return age_seconds > get_settings().nl2sql_job_stale_after_seconds

    @staticmethod
    def _mark_job_interrupted(job: StoredJob) -> None:
        """完了せず終わった in-flight job を error として閉じる(表示文言は共通)。"""

        job.status = JobStatus.ERROR
        job.finished_at = job.finished_at or _utc_now()
        job.error_message = _JOB_INTERRUPTED_MESSAGE
        job.error_code = JOB_INTERRUPTED_ERROR_CODE
        failure_index = _job_failure_step_index(job.steps)
        if failure_index is not None:
            job.steps[failure_index] = job.steps[failure_index].model_copy(
                update={"status": JobStepStatus.ERROR}
            )

    def _load_job_record(self, job_id: str) -> StoredJob | None:
        """job をプロセス内 cache と repository から解決する。

        自プロセスが実行していない(owned=False)in-flight job は cache があっても
        repository を読み直す。gunicorn の複数 worker 構成で別 worker が進めた状態を
        取りこぼさないため。in-flight のまま更新が途絶えた snapshot は error へ
        正規化して書き戻し、永久 running を防ぐ。
        """

        with self._lock:
            job = self._jobs.get(job_id)
            refresh = job is not None and not job.owned and job.status in _IN_FLIGHT_JOB_STATUSES
        repository = self._incremental_repository
        if (job is None or refresh) and repository is not None:
            try:
                document = repository.get_document("jobs", job_id)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="job_load",
                    exc=exc,
                    operation_error_code="job_query_failed",
                )
            if document is not None:
                stale = self._job_snapshot_is_stale(document)
                job = self._job_from_snapshot(document)
                if stale:
                    self._mark_job_interrupted(job)
                with self._lock:
                    self._jobs[job_id] = job
                if stale:
                    try:
                        self._persist_job(job_id)
                    except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed):
                        logger.warning(
                            "nl2sql_interrupted_job_persist_failed",
                            extra={"job_id": job_id},
                            exc_info=True,
                        )
        return job

    def _job_from_snapshot(self, data: dict[str, Any]) -> StoredJob:
        status = JobStatus(data.get("status", JobStatus.PENDING))
        timing = TimingEnvelope.model_validate(data["timing"]) if data.get("timing") else None
        result = Nl2SqlResult.model_validate(data["result"]) if data.get("result") else None
        return StoredJob(
            job_id=str(data["job_id"]),
            request=JobCreateRequest.model_validate(data["request"]),
            actor_user_uuid=str(data.get("actor_user_uuid") or ""),
            actor_is_system_admin=_coerce_bool(data.get("actor_is_system_admin", False)),
            status=status,
            created_at=str(data.get("created_at") or _utc_now()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            elapsed_ms=data.get("elapsed_ms"),
            result=result,
            error_message=data.get("error_message"),
            error_code=data.get("error_code"),
            warning_message=data.get("warning_message"),
            timing=timing,
            steps=_restore_job_steps(
                data.get("steps", []),
                status=status,
                timing=timing,
                has_result=result is not None,
            ),
        )

    def get_catalog(self) -> SchemaCatalog:
        if self._incremental_repository is not None:
            cached = self._schema_cache.get("catalog")
            self._refresh_cache_token(
                SCHEMA_NAMESPACE,
                allow_cached_on_failure=isinstance(cached, SchemaCatalog),
            )
            cached = self._schema_cache.get("catalog")
            if isinstance(cached, SchemaCatalog):
                return filter_user_visible_catalog(cached)
            try:
                catalog = filter_user_visible_catalog(self._incremental_repository.load_catalog())
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="catalog_load",
                    exc=exc,
                    operation_error_code="catalog_query_failed",
                )
            with self._lock:
                self._catalog = catalog
            self._schema_cache.put("catalog", catalog)
            return catalog
        self._catalog = filter_user_visible_catalog(self._catalog)
        return self._catalog

    def _submit_schema_refresh_after_admin_mutation(
        self,
        *,
        target_objects: Sequence[SchemaRefreshTargetObject] | None,
        source: str,
    ) -> SchemaRefreshMutationSync:
        """DDL/metadata 更新後に targeted durable job を投入し、HTTP 内では実行しない。"""

        targets = _dedupe_schema_refresh_targets(target_objects or [])
        if not targets:
            return SchemaRefreshMutationSync(
                required=True,
                reason_code="schema_refresh_target_unresolved",
            )
        job = self.start_schema_refresh_job(
            mode=SchemaRefreshMode.TARGETED,
            source=source,
            target_objects=targets,
        )
        if job.requires_full_refresh:
            return SchemaRefreshMutationSync(
                job_id=job.job_id,
                required=True,
                reason_code=job.error_code or "schema_refresh_full_required",
            )
        return SchemaRefreshMutationSync(job_id=job.job_id)

    def _schema_refresh_target_for_object_name(
        self,
        object_name: str,
        *,
        object_type: Literal["table", "view", "materialized_view", "unknown"] = "unknown",
        expected_state: Literal["present", "absent", "unknown"] = "unknown",
    ) -> SchemaRefreshTargetObject | None:
        return _schema_refresh_target_from_ref(
            object_name,
            current_owner=self._current_schema_owner(),
            object_type=object_type,
            expected_state=expected_state,
        )

    def _manual_schema_refresh_sync(
        self,
        reason_code: str = "schema_refresh_target_unresolved",
    ) -> SchemaRefreshMutationSync:
        return SchemaRefreshMutationSync(required=True, reason_code=reason_code)

    def get_schema_owners(self) -> SchemaOwnersData:
        """当前数据库用户可访问的非 Oracle 维护 schema。"""

        if self._use_oracle_runtime():
            return self._oracle_adapter.fetch_schema_owners()
        catalog = self.get_catalog()
        current_owner = (
            catalog.current_owner.strip().upper()
            or get_settings().oracle_user.strip().upper()
            or "APP"
        )
        counts: dict[str, dict[str, int]] = {}
        for table in catalog.tables:
            owner = table.owner.strip().upper() or current_owner
            bucket = counts.setdefault(owner, {"table": 0, "view": 0})
            if table.table_type.lower() in {"view", "materialized view"}:
                bucket["view"] += 1
            else:
                bucket["table"] += 1
        owners = [
            SchemaOwnerSummary(
                owner=owner,
                is_current=owner == current_owner,
                table_count=values["table"],
                view_count=values["view"],
            )
            for owner, values in sorted(counts.items())
        ]
        return SchemaOwnersData(
            current_owner=current_owner,
            owners=owners,
            excluded_oracle_maintained_count=catalog.excluded_oracle_maintained_count,
        )

    def get_catalog_head(self) -> SchemaCatalogHead:
        repository = self._incremental_repository
        if repository is None:
            catalog = self.get_catalog()
            return SchemaCatalogHead(
                catalog_version=1 if catalog.tables else 0,
                schema_fingerprint=catalog.schema_fingerprint,
                refreshed_at=catalog.refreshed_at,
                object_count=len(catalog.tables),
                column_count=sum(len(table.columns) for table in catalog.tables),
                etag=catalog.schema_fingerprint,
            )
        cached = self._schema_cache.get("head")
        self._refresh_cache_token(
            SCHEMA_NAMESPACE,
            allow_cached_on_failure=isinstance(cached, SchemaCatalogHead),
        )
        cached = self._schema_cache.get("head")
        if isinstance(cached, SchemaCatalogHead):
            return cached
        try:
            head = repository.get_catalog_head()
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="catalog_head",
                exc=exc,
                operation_error_code="catalog_head_query_failed",
            )
        self._schema_cache.put("head", head)
        return head

    def search_schema_objects(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        owner: str,
        object_type: str,
        profile_id: str | None,
        owner_prefix: str = "",
        query_scope: str = "all",
        row_state: str = "",
        include_counts: bool = True,
    ) -> SchemaObjectPage:
        allowed_names: set[str] | None = None
        if profile_id:
            profile = self.get_profile(profile_id)
            allowed_names = {
                value.replace('"', "").strip().upper()
                for value in self.profile_allowed_object_names(profile)
            }
        repository = self._incremental_repository
        if repository is None:
            memory = MemoryIncrementalNl2SqlRepository(seed_default=False)
            catalog = self.get_catalog()
            manifest = {
                (table.owner.upper(), table.table_name.upper()): catalog.refreshed_at
                for table in catalog.tables
            }
            memory.apply_schema_refresh(
                catalog=catalog,
                manifest=manifest,
                changed_keys=set(manifest),
                deleted_keys=set(),
            )
            return filter_user_visible_object_page(
                memory.search_schema_objects(
                    cursor=cursor,
                    limit=limit,
                    query=query,
                    owner=owner,
                    owner_prefix=owner_prefix,
                    query_scope=query_scope,
                    object_type=object_type,
                    allowed_names=allowed_names,
                    row_state=row_state,
                    include_counts=include_counts,
                )
            )
        try:
            return filter_user_visible_object_page(
                repository.search_schema_objects(
                    cursor=cursor,
                    limit=limit,
                    query=query,
                    owner=owner,
                    owner_prefix=owner_prefix,
                    query_scope=query_scope,
                    object_type=object_type,
                    allowed_names=allowed_names,
                    row_state=row_state,
                    include_counts=include_counts,
                )
            )
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="schema_object_search",
                exc=exc,
                operation_error_code="schema_object_query_failed",
            )

    def get_schema_object(self, owner: str, object_name: str) -> SchemaObjectDetail | None:
        if not is_user_visible_schema_object(owner, object_name):
            return None
        cache_key = f"{owner.upper()}.{object_name.upper()}"
        cached = self._schema_cache.get(cache_key)
        if self._incremental_repository is not None:
            self._refresh_cache_token(
                SCHEMA_NAMESPACE,
                allow_cached_on_failure=isinstance(cached, SchemaObjectDetail),
            )
            cached = self._schema_cache.get(cache_key)
        if isinstance(cached, SchemaObjectDetail):
            return cached
        detail: SchemaObjectDetail | None
        repository = self._incremental_repository
        if repository is None:
            page = self.search_schema_objects(
                cursor=None,
                limit=2,
                query=object_name,
                owner=owner,
                object_type="",
                profile_id=None,
            )
            if not page.items:
                return None
            table = next(
                (
                    item
                    for item in self.get_catalog().tables
                    if item.owner.upper() == owner.upper()
                    and item.table_name.upper() == object_name.upper()
                ),
                None,
            )
            if table is None:
                return None
            detail = SchemaObjectDetail(table=table, catalog_version=page.catalog_version)
        else:
            try:
                detail = repository.get_schema_object(owner, object_name)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="schema_object_detail",
                    exc=exc,
                    operation_error_code="schema_object_detail_failed",
                )
        if detail is not None and self._use_oracle_runtime():
            sample_limit = max(get_settings().nl2sql_schema_sample_rows, 0)
            if sample_limit:
                try:
                    samples, _warnings = self._oracle_adapter.fetch_metadata_sample_values(
                        [
                            {
                                "owner": detail.table.owner,
                                "object_name": detail.table.table_name,
                                "columns": [column.column_name for column in detail.table.columns],
                            }
                        ],
                        sample_limit,
                    )
                    object_samples = samples.get(self._catalog_qualified_name(detail.table), {})
                    detail = detail.model_copy(
                        update={
                            "table": detail.table.model_copy(
                                update={
                                    "columns": [
                                        column.model_copy(
                                            update={
                                                "sample_values": object_samples.get(
                                                    column.column_name.upper(), []
                                                )
                                            }
                                        )
                                        for column in detail.table.columns
                                    ]
                                }
                            )
                        }
                    )
                except OracleAdapterError:
                    # 詳細 metadata は返し、サンプル取得失敗で object detail 全体を落とさない。
                    pass
        if detail is not None:
            self._schema_cache.put(cache_key, detail)
        return detail

    def start_schema_refresh_job(
        self,
        *,
        dispatch: bool = True,
        mode: SchemaRefreshMode | str = SchemaRefreshMode.FULL,
        source: str = "manual",
        target_objects: Sequence[SchemaRefreshTargetObject] | None = None,
    ) -> SchemaRefreshJob:
        repository = self._refresh_job_repository
        normalized_mode = SchemaRefreshMode(mode)
        targets = _dedupe_schema_refresh_targets(target_objects or [])
        if normalized_mode == SchemaRefreshMode.TARGETED and not targets:
            raise SchemaRefreshFullRequired("schema_refresh_target_unresolved")
        candidate = SchemaRefreshJob(
            job_id=str(uuid.uuid4()),
            created_at=_utc_now(),
            mode=normalized_mode,
            source=source,
            target_objects=targets,
        )
        try:
            with self._schema_refresh_submit_lock:
                job = repository.submit_refresh_job(candidate)
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="schema_refresh_submit",
                exc=exc,
                operation_error_code="schema_refresh_submit_failed",
            )
        settings = get_settings()
        worker_mode = settings.nl2sql_schema_refresh_worker_mode.strip().lower()
        created = job.job_id == candidate.job_id
        logger.info(
            "schema_refresh_job_submitted",
            extra={
                "job_id": job.job_id,
                "job_status": job.status.value,
                "refresh_mode": job.mode.value,
                "refresh_source": job.source,
                "target_object_count": len(job.target_objects),
                "coalesced": not created,
                "worker_mode": worker_mode,
            },
        )
        if dispatch:
            self._wake_schema_refresh_job_if_needed(job, settings=settings)
        if job.status == SchemaRefreshJobStatus.PENDING:
            created_at = datetime.fromisoformat(job.created_at)
            SCHEMA_REFRESH_PENDING_AGE_SECONDS.set(
                max(0.0, (datetime.now(UTC) - created_at).total_seconds())
            )
        return job

    def get_schema_refresh_job(self, job_id: str) -> SchemaRefreshJob | None:
        repository = self._refresh_job_repository
        try:
            job = repository.get_refresh_job(job_id)
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="schema_refresh_job_load",
                exc=exc,
                operation_error_code="schema_refresh_job_query_failed",
            )
        if job is not None:
            self._wake_schema_refresh_job_if_needed(job)
        return job

    def get_active_schema_refresh_job(self) -> SchemaRefreshJob | None:
        """画面移動・再読込後に追跡を再開する実行中 job を返す。"""

        repository = self._refresh_job_repository
        try:
            job = repository.find_active_refresh_job()
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="schema_refresh_active_job_load",
                exc=exc,
                operation_error_code="schema_refresh_job_query_failed",
            )
        if job is not None:
            self._wake_schema_refresh_job_if_needed(job)
        return job

    def run_next_schema_refresh_job(self) -> bool:
        """外部 worker 用。pending または lease 切れ job を 1 件だけ処理する。"""

        return self._run_schema_refresh_job(None)

    def _wake_schema_refresh_job_if_needed(
        self,
        job: SchemaRefreshJob,
        *,
        settings: Any | None = None,
    ) -> bool:
        """inprocess worker が見逃した pending / lease 切れ job を再起動する。"""

        settings = settings or get_settings()
        worker_mode = settings.nl2sql_schema_refresh_worker_mode.strip().lower()
        if not settings.nl2sql_schema_refresh_worker_enabled or worker_mode == "external":
            return False
        if job.status == SchemaRefreshJobStatus.PENDING:
            return self._dispatch_schema_refresh_job(job.job_id)
        if job.status == SchemaRefreshJobStatus.RUNNING and self._schema_refresh_lease_expired(job):
            return self._dispatch_schema_refresh_job(job.job_id)
        return False

    @staticmethod
    def _schema_refresh_lease_expired(job: SchemaRefreshJob) -> bool:
        if not job.lease_expires_at:
            return True
        try:
            expires_at = datetime.fromisoformat(job.lease_expires_at)
        except ValueError:
            return True
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC)

    def _dispatch_schema_refresh_job(self, job_id: str) -> bool:
        with self._schema_refresh_dispatch_lock:
            if job_id in self._schema_refresh_dispatching_job_ids:
                return False
            self._schema_refresh_dispatching_job_ids.add(job_id)

        def run() -> None:
            try:
                self._run_schema_refresh_job(job_id)
            finally:
                with self._schema_refresh_dispatch_lock:
                    self._schema_refresh_dispatching_job_ids.discard(job_id)

        thread = threading.Thread(
            target=run,
            daemon=True,
            name=f"schema-refresh-{job_id[:8]}",
        )
        thread.start()
        return True

    @staticmethod
    def _schema_refresh_target_keys(job: SchemaRefreshJob) -> set[tuple[str, str]]:
        return {
            (target.owner.upper(), target.object_name.upper())
            for target in job.target_objects
            if is_user_visible_schema_object(target.owner, target.object_name)
        }

    @staticmethod
    def _schema_refresh_expected_state_by_key(
        job: SchemaRefreshJob,
    ) -> dict[tuple[str, str], str]:
        return {
            (target.owner.upper(), target.object_name.upper()): target.expected_state
            for target in job.target_objects
            if is_user_visible_schema_object(target.owner, target.object_name)
        }

    @staticmethod
    def _heartbeat_schema_refresh_job(
        repository: IncrementalNl2SqlRepository,
        job: SchemaRefreshJob,
    ) -> SchemaRefreshJob:
        """長い metadata phase の境界で lease を更新し、誤った二重回収を防ぐ。"""

        now = datetime.now(UTC)
        lease_seconds = max(30.0, get_settings().nl2sql_schema_refresh_lease_seconds)
        renewed = job.model_copy(
            update={
                "heartbeat_at": now.isoformat(),
                "lease_expires_at": datetime.fromtimestamp(
                    now.timestamp() + lease_seconds, UTC
                ).isoformat(),
            }
        )
        return repository.save_refresh_job(renewed)

    def _run_schema_refresh_job(self, job_id: str | None) -> bool:
        repository = self._refresh_job_repository
        if not self._schema_refresh_lock.acquire(blocking=False):
            return False
        claimed_job_id = job_id or ""
        try:
            refresh_observation = observe_schema_refresh()
            refresh_state = refresh_observation.__enter__()
            job = repository.claim_refresh_job(
                worker_id=self._schema_refresh_worker_id,
                lease_seconds=get_settings().nl2sql_schema_refresh_lease_seconds,
                job_id=job_id,
            )
            if job is None:
                return False
            claimed_job_id = job.job_id
            phase_started = time.monotonic()
            job = repository.save_refresh_job(
                job.model_copy(update={"phase": SchemaRefreshPhase.SCANNING})
            )
            stored_manifest = repository.schema_manifest()
            target_keys = self._schema_refresh_target_keys(job)
            expected_state_by_key = self._schema_refresh_expected_state_by_key(job)
            if job.mode == SchemaRefreshMode.TARGETED and not target_keys:
                raise SchemaRefreshFullRequired("schema_refresh_target_unresolved")
            if self._use_oracle_runtime():
                incoming_manifest = self._oracle_adapter.fetch_schema_manifest(
                    target_keys if job.mode == SchemaRefreshMode.TARGETED else None
                )
            else:
                deterministic = self._build_default_catalog()
                incoming_manifest = {
                    (table.owner.upper(), table.table_name.upper()): "deterministic-v1"
                    for table in deterministic.tables
                }
                if job.mode == SchemaRefreshMode.TARGETED:
                    incoming_manifest = {
                        key: value for key, value in incoming_manifest.items() if key in target_keys
                    }
            SCHEMA_REFRESH_PHASE_SECONDS.labels(phase="scanning").observe(
                time.monotonic() - phase_started
            )
            phase_started = time.monotonic()
            refresh_total_objects = (
                len(target_keys)
                if job.mode == SchemaRefreshMode.TARGETED
                else len(incoming_manifest)
            )
            job = repository.save_refresh_job(
                self._heartbeat_schema_refresh_job(repository, job).model_copy(
                    update={
                        "phase": SchemaRefreshPhase.FETCHING,
                        "total_objects": refresh_total_objects,
                    }
                )
            )
            incoming_keys = set(incoming_manifest)
            if job.mode == SchemaRefreshMode.TARGETED:
                missing_present = {
                    key
                    for key in target_keys
                    if expected_state_by_key.get(key) == "present" and key not in incoming_keys
                }
                still_present = {
                    key
                    for key in target_keys
                    if expected_state_by_key.get(key) == "absent" and key in incoming_keys
                }
                if missing_present or still_present:
                    raise SchemaRefreshFullRequired("schema_refresh_full_required")
                changed_keys = {
                    key
                    for key, last_ddl_at in incoming_manifest.items()
                    if key in target_keys and stored_manifest.get(key) != last_ddl_at
                }
                deleted_keys = {
                    key
                    for key in target_keys
                    if key not in incoming_keys and key in stored_manifest
                }
            else:
                changed_keys = {
                    key
                    for key, last_ddl_at in incoming_manifest.items()
                    if stored_manifest.get(key) != last_ddl_at
                }
                deleted_keys = set(stored_manifest) - incoming_keys
            publish_manifest = incoming_manifest
            if job.mode == SchemaRefreshMode.TARGETED:
                publish_manifest = {
                    key: value for key, value in stored_manifest.items() if key not in deleted_keys
                }
                publish_manifest.update(incoming_manifest)
            SCHEMA_CHANGED_OBJECTS.observe(len(changed_keys))
            if not changed_keys and not deleted_keys:
                SCHEMA_REFRESH_PHASE_SECONDS.labels(phase="fetching").observe(
                    time.monotonic() - phase_started
                )
                phase_started = time.monotonic()
                job = repository.save_refresh_job(
                    job.model_copy(
                        update={
                            "phase": SchemaRefreshPhase.PERSISTING,
                            "processed_objects": refresh_total_objects,
                        }
                    )
                )
                head = repository.get_catalog_head()
                SCHEMA_REFRESH_PHASE_SECONDS.labels(phase="persisting").observe(
                    time.monotonic() - phase_started
                )
            else:
                current_catalog = repository.load_catalog()
                if self._use_oracle_runtime():
                    changed_catalog = self._oracle_adapter.fetch_catalog_objects(changed_keys)
                else:
                    changed_catalog = self._build_default_catalog()
                job = self._heartbeat_schema_refresh_job(repository, job)
                changed_or_deleted_keys = changed_keys | deleted_keys
                dependency_by_key = {
                    (
                        dependency.owner.upper(),
                        dependency.view_name.upper(),
                        dependency.referenced_owner.upper(),
                        dependency.referenced_name.upper(),
                    ): dependency
                    for dependency in current_catalog.view_dependencies
                    if (
                        dependency.owner.upper(),
                        dependency.view_name.upper(),
                    )
                    not in changed_or_deleted_keys
                    and (
                        dependency.referenced_owner.upper(),
                        dependency.referenced_name.upper(),
                    )
                    not in deleted_keys
                }
                for dependency in changed_catalog.view_dependencies:
                    dependency_by_key[
                        (
                            dependency.owner.upper(),
                            dependency.view_name.upper(),
                            dependency.referenced_owner.upper(),
                            dependency.referenced_name.upper(),
                        )
                    ] = dependency
                table_by_key = {
                    (table.owner.upper(), table.table_name.upper()): table
                    for table in current_catalog.tables
                    if (table.owner.upper(), table.table_name.upper()) not in deleted_keys
                }
                for table in changed_catalog.tables:
                    key = (table.owner.upper(), table.table_name.upper())
                    if key in incoming_keys:
                        table_by_key[key] = table
                merged = SchemaCatalog(
                    refreshed_at=_utc_now(),
                    tables=list(table_by_key.values()),
                    view_dependencies=list(dependency_by_key.values()),
                )
                if self._use_oracle_runtime():
                    merged.schema_fingerprint = self._oracle_adapter.catalog_fingerprint(merged)
                else:
                    merged.schema_fingerprint = hashlib.sha256(
                        json.dumps(
                            merged.model_dump(mode="json"),
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                SCHEMA_REFRESH_PHASE_SECONDS.labels(phase="fetching").observe(
                    time.monotonic() - phase_started
                )
                phase_started = time.monotonic()
                job = repository.save_refresh_job(
                    job.model_copy(
                        update={
                            "phase": SchemaRefreshPhase.PERSISTING,
                            "processed_objects": refresh_total_objects,
                        }
                    )
                )
                head = repository.apply_schema_refresh(
                    catalog=merged,
                    manifest=publish_manifest,
                    changed_keys=changed_keys,
                    deleted_keys=deleted_keys,
                )
                SCHEMA_REFRESH_PHASE_SECONDS.labels(phase="persisting").observe(
                    time.monotonic() - phase_started
                )
                self._schema_cache.clear()
                if self._incremental_repository is None:
                    with self._lock:
                        self._catalog = merged
            repository.save_refresh_job(
                job.model_copy(
                    update={
                        "status": SchemaRefreshJobStatus.DONE,
                        "phase": SchemaRefreshPhase.DONE,
                        "finished_at": _utc_now(),
                        "heartbeat_at": _utc_now(),
                        "lease_expires_at": None,
                        "scanned_objects": refresh_total_objects,
                        "processed_objects": refresh_total_objects,
                        "total_objects": refresh_total_objects,
                        "changed_objects": len(changed_keys),
                        "deleted_objects": len(deleted_keys),
                        "catalog_version": head.catalog_version,
                    }
                )
            )
            refresh_state["status"] = "done"
            SCHEMA_REFRESH_PENDING_AGE_SECONDS.set(0)
            logger.info(
                "schema_refresh_job_completed",
                extra={
                    "job_id": job.job_id,
                    "refresh_mode": job.mode.value,
                    "catalog_version": head.catalog_version,
                    "changed_objects": len(changed_keys),
                    "deleted_objects": len(deleted_keys),
                },
            )
            return True
        except SchemaRefreshFullRequired as exc:
            logger.warning(
                "schema_refresh_job_requires_full_refresh",
                extra={
                    "job_id": claimed_job_id,
                    "error_code": exc.reason_code,
                },
            )
            SCHEMA_REFRESH_ERRORS.labels(error_code=exc.reason_code).inc()
            failed = repository.get_refresh_job(claimed_job_id) if claimed_job_id else None
            if failed is not None:
                repository.save_refresh_job(
                    failed.model_copy(
                        update={
                            "status": SchemaRefreshJobStatus.ERROR,
                            "finished_at": _utc_now(),
                            "heartbeat_at": _utc_now(),
                            "lease_expires_at": None,
                            "error_code": exc.reason_code,
                            "requires_full_refresh": True,
                        }
                    )
                )
            return False
        except Exception as exc:
            logger.exception(
                "schema_refresh_job_failed",
                extra={
                    "job_id": claimed_job_id,
                    "exception_type": type(exc).__name__,
                },
            )
            SCHEMA_REFRESH_ERRORS.labels(error_code="schema_refresh_failed").inc()
            failed = repository.get_refresh_job(claimed_job_id) if claimed_job_id else None
            if failed is not None:
                repository.save_refresh_job(
                    failed.model_copy(
                        update={
                            "status": SchemaRefreshJobStatus.ERROR,
                            "finished_at": _utc_now(),
                            "heartbeat_at": _utc_now(),
                            "lease_expires_at": None,
                            "error_code": "schema_refresh_failed",
                        }
                    )
                )
            return False
        finally:
            observation = locals().get("refresh_observation")
            if observation is not None:
                observation.__exit__(None, None, None)
            self._schema_refresh_lock.release()

    def sample_data_info(self) -> SampleDataInfo:
        sql = self._sample_sql_sections()
        warnings: list[str] = []
        imported = self._sample_imported_objects(warnings=warnings)
        return SampleDataInfo(
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            profile_id="",
            confirmation=_SAMPLE_CONFIRMATION,
            objects=list(_SAMPLE_OBJECTS),
            imported_objects=imported,
            sql=sql,
            warnings=warnings,
        )

    def import_sample_data(self, request: SampleDataMutationRequest) -> SampleDataMutationData:
        started = time.monotonic()
        created_at = _utc_now()
        step = request.step
        sql_sections = self._sample_sql_sections()
        statements = self._sample_import_statements(step, sql_sections)
        warnings: list[str] = []
        executed = False
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        results: list[DbAdminStatementResult]
        confirmation_error = self._sample_confirmation_error(request.confirmation)
        if confirmation_error:
            warnings.append(confirmation_error)
            results = self._statement_results(
                statements,
                status="confirmation_required",
                error_message=confirmation_error,
            )
        elif self._use_oracle_runtime():
            try:
                results = [
                    DbAdminStatementResult.model_validate(item)
                    for item in self._oracle_adapter.execute_admin_statements(
                        statements,
                        atomic=False,
                        ignored_error_codes=_SAMPLE_IMPORT_IDEMPOTENT_ERROR_CODES,
                    )
                ]
            except OracleAdapterError as exc:
                warning = str(exc)
                warnings.append(warning)
                results = self._statement_results(
                    statements,
                    status="error",
                    error_message=warning,
                )
            else:
                skipped_count = sum(1 for item in results if item.status == "skipped")
                if skipped_count:
                    warnings.append(
                        "既存の sample object / data と重複した "
                        f"{skipped_count} 件の statement は skip しました。"
                    )
                for message in self._sample_statement_error_messages(results):
                    warnings.append(message)
                executed = any(item.status in _SAMPLE_EXECUTED_STATUSES for item in results)
                has_successful_statement = any(item.status == "success" for item in results)
                if executed:
                    try:
                        self._record_admin_audit(
                            operation="sample_data_import",
                            target=",".join(_SAMPLE_OBJECTS),
                            executed=True,
                            reason=request.reason or "sql-assist-sample-import",
                            detail={
                                "step": step.value,
                                "statement_count": len(statements),
                                "success_count": sum(
                                    1
                                    for item in results
                                    if item.status in _SAMPLE_EXECUTED_STATUSES
                                ),
                            },
                        )
                    except (
                        Nl2SqlPersistenceUnavailable,
                        Nl2SqlRepositoryOperationFailed,
                    ) as exc:
                        warnings.append(f"Sample data import の監査保存に失敗しました: {exc}")
                if has_successful_statement:
                    (
                        schema_refresh_job_id,
                        schema_refresh_required,
                        schema_refresh_reason_code,
                    ) = self._submit_sample_schema_refresh(
                        step=step,
                        expected_state="present",
                        source="sample_data_import",
                        warnings=warnings,
                    )
        else:
            blocker = self._sample_deterministic_import_blocker(step)
            if blocker:
                warnings.append(blocker)
                results = self._statement_results(
                    statements,
                    status="missing_sample_tables",
                    error_message=blocker,
                )
            else:
                self._apply_sample_import_to_catalog(step)
                results = self._statement_results(statements, status="applied_to_local_state")
                executed = True
                self._persist_local_catalog()
        return SampleDataMutationData(
            operation="import",
            step=step,
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            executed=executed,
            objects=list(_SAMPLE_OBJECTS),
            statements=results,
            warnings=warnings,
            profile_id="",
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            timing=self._timing(created_at, started, "sample_data_import"),
        )

    def delete_sample_data(self, request: SampleDataMutationRequest) -> SampleDataMutationData:
        started = time.monotonic()
        created_at = _utc_now()
        statements = self._sample_sql_sections()["delete"]
        warnings: list[str] = []
        executed = False
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        results: list[DbAdminStatementResult]
        confirmation_error = self._sample_confirmation_error(request.confirmation)
        if confirmation_error:
            warnings.append(confirmation_error)
            results = self._statement_results(
                statements,
                status="confirmation_required",
                error_message=confirmation_error,
            )
        elif self._use_oracle_runtime():
            try:
                results = [
                    DbAdminStatementResult.model_validate(item)
                    for item in self._oracle_adapter.execute_admin_statements(
                        statements,
                        atomic=False,
                        ignored_error_codes=_SAMPLE_DELETE_IDEMPOTENT_ERROR_CODES,
                    )
                ]
            except OracleAdapterError as exc:
                warning = str(exc)
                warnings.append(warning)
                results = self._statement_results(
                    statements,
                    status="error",
                    error_message=warning,
                )
            else:
                mapped_results: list[DbAdminStatementResult] = []
                for item in results:
                    if item.status == "skipped" or (
                        item.status == "error" and self._is_missing_object_error(item.error_message)
                    ):
                        warnings.append(f"{item.sql}: 対象が存在しないため skip しました。")
                        item = item.model_copy(update={"status": "skipped_missing_object"})
                    mapped_results.append(item)
                results = mapped_results
                for message in self._sample_statement_error_messages(results):
                    warnings.append(message)
                successful_drop = any(item.status == "success" for item in results)
                executed = bool(results) and all(
                    item.status in {"success", "skipped_missing_object"} for item in results
                )
                if successful_drop:
                    (
                        schema_refresh_job_id,
                        schema_refresh_required,
                        schema_refresh_reason_code,
                    ) = self._submit_sample_schema_refresh(
                        step=SampleDataStep.ALL,
                        expected_state="absent",
                        source="sample_data_delete",
                        warnings=warnings,
                    )
            if executed:
                self._remove_sample_from_state()
                try:
                    self._persist_local_catalog()
                except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed) as exc:
                    warnings.append(f"Sample data 削除後の catalog 保存に失敗しました: {exc}")
        else:
            self._remove_sample_from_state()
            results = self._statement_results(statements, status="applied_to_local_state")
            executed = True
            self._persist_local_catalog()
        return SampleDataMutationData(
            operation="delete",
            step=SampleDataStep.ALL,
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            executed=executed,
            objects=list(_SAMPLE_OBJECTS),
            statements=results,
            warnings=warnings,
            profile_id="",
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            timing=self._timing(created_at, started, "sample_data_delete"),
        )

    def _sample_sql_sections(self) -> dict[str, list[str]]:
        base = Path(__file__).with_name("sample_data") / "sql_assist_sample"
        return {
            name: _split_sql_statements((base / f"{name}.sql").read_text(encoding="utf-8"))
            for name in ("tables", "views", "data", "delete")
        }

    def _sample_import_statements(
        self, step: SampleDataStep, sql_sections: dict[str, list[str]]
    ) -> list[str]:
        names = ["tables", "views", "data"] if step == SampleDataStep.ALL else [step.value]
        return [statement for name in names for statement in sql_sections[name]]

    def _sample_statement_error_messages(
        self, results: Sequence[DbAdminStatementResult]
    ) -> list[str]:
        messages: list[str] = []
        seen: set[str] = set()
        for item in results:
            if item.status != "error" or not item.error_message:
                continue
            if item.error_message in seen:
                continue
            seen.add(item.error_message)
            messages.append(item.error_message)
        return messages

    def _submit_sample_schema_refresh(
        self,
        *,
        step: SampleDataStep,
        expected_state: Literal["present", "absent"],
        source: str,
        warnings: list[str],
    ) -> tuple[str, bool, str]:
        object_names: list[tuple[str, Literal["table", "view"]]] = []
        if step in {SampleDataStep.TABLES, SampleDataStep.DATA, SampleDataStep.ALL}:
            object_names.extend((name, "table") for name in _SAMPLE_TABLES)
        if step in {SampleDataStep.VIEWS, SampleDataStep.ALL}:
            object_names.extend((name, "view") for name in _SAMPLE_VIEWS)
        targets: list[SchemaRefreshTargetObject] = []
        for name, object_type in object_names:
            target = self._schema_refresh_target_for_object_name(
                name,
                object_type=object_type,
                expected_state=expected_state,
            )
            if target is not None:
                targets.append(target)
        try:
            sync = self._submit_schema_refresh_after_admin_mutation(
                target_objects=targets,
                source=source,
            )
        except Nl2SqlPersistenceUnavailable as exc:
            warnings.append(f"Sample data 後の Schema job 投入に失敗しました: {exc}")
            return "", False, ""
        if sync.required:
            warnings.append(_schema_refresh_required_warning(sync.reason_code))
        return sync.job_id, sync.required, sync.reason_code

    def _sample_deterministic_import_blocker(self, step: SampleDataStep) -> str:
        if step not in {SampleDataStep.DATA, SampleDataStep.VIEWS}:
            return ""
        existing_tables = self._sample_existing_table_names()
        missing_tables = [name for name in _SAMPLE_TABLES if name not in existing_tables]
        if not missing_tables:
            return ""
        missing_text = ", ".join(missing_tables)
        if step == SampleDataStep.DATA:
            return (
                "Sample data の data scope は sample table が存在する場合のみ実行できます。"
                f"不足 table: {missing_text}。先に tables または all を実行してください。"
            )
        return (
            "Sample data の views scope は sample table が存在する場合のみ実行できます。"
            f"不足 table: {missing_text}。先に tables または all を実行してください。"
        )

    def _sample_existing_table_names(self) -> set[str]:
        current_owner = self._current_schema_owner()
        return {
            table.table_name.upper()
            for table in self._catalog.tables
            if (table.owner or current_owner).upper() == current_owner
            and table.table_type.lower() != "view"
            and table.table_name.upper() in _SAMPLE_TABLES
        }

    def _sample_imported_objects(self, *, warnings: list[str] | None = None) -> list[str]:
        current_owner = self._current_schema_owner()
        if self._use_oracle_runtime():
            try:
                object_keys = {(current_owner, name) for name in _SAMPLE_OBJECTS}
                catalog = self._oracle_adapter.fetch_catalog(
                    include_samples=False,
                    object_keys=object_keys,
                )
                return self._sample_imported_objects_from_catalog(
                    catalog,
                    current_owner=current_owner,
                )
            except OracleAdapterError as exc:
                message = (
                    "Sample data 導入状態の Oracle 確認に失敗したため、"
                    f"ローカル状態を使用しました: {exc}"
                )
                if warnings is not None:
                    warnings.append(message)
                logger.warning("sample_data_oracle_status_check_failed", exc_info=True)
                catalog = self._sample_cached_catalog(warnings=warnings)
                current_owner = self._current_schema_owner()
                imported = self._sample_imported_objects_from_catalog(
                    catalog,
                    current_owner=current_owner,
                )
                if imported:
                    return imported
                return self._sample_imported_objects_from_profile(current_owner=current_owner)
        catalog = self._sample_cached_catalog(warnings=warnings)
        current_owner = self._current_schema_owner()
        return self._sample_imported_objects_from_catalog(
            catalog,
            current_owner=current_owner,
        )

    def _sample_cached_catalog(self, *, warnings: list[str] | None = None) -> SchemaCatalog:
        try:
            return self.get_catalog()
        except Exception as exc:
            message = (
                "Sample data 導入状態の catalog 確認に失敗したため、"
                f"メモリ上の状態を使用しました: {exc}"
            )
            if warnings is not None:
                warnings.append(message)
            logger.warning("sample_data_catalog_status_check_failed", exc_info=True)
            return self._catalog

    def _sample_imported_objects_from_catalog(
        self,
        catalog: SchemaCatalog,
        *,
        current_owner: str,
    ) -> list[str]:
        owner_key = current_owner.upper()
        existing = {
            ((table.owner or current_owner).upper(), table.table_name.upper())
            for table in catalog.tables
        }
        return [name for name in _SAMPLE_OBJECTS if (owner_key, name) in existing]

    def _sample_imported_objects_from_profile(self, *, current_owner: str) -> list[str]:
        try:
            profile = self.get_profile(_SAMPLE_PROFILE_ID, include_archived=True)
        except ValueError:
            return []
        if profile.archived:
            return []
        owner_key = current_owner.upper()
        existing: set[str] = set()
        for object_name in [*profile.allowed_tables, *profile.allowed_views]:
            try:
                identity = parse_object_identity(
                    str(object_name).replace('"', ""),
                    default_owner=current_owner,
                )
            except ValueError:
                continue
            if identity.owner == owner_key:
                existing.add(identity.object_name)
        return [name for name in _SAMPLE_OBJECTS if name in existing]

    def _sample_confirmation_error(self, confirmation: str) -> str:
        if confirmation.strip() == _SAMPLE_CONFIRMATION:
            return ""
        return f"実行するには confirmation に {_SAMPLE_CONFIRMATION} を入力してください。"

    def _statement_results(
        self,
        statements: list[str],
        *,
        status: str,
        error_message: str = "",
    ) -> list[DbAdminStatementResult]:
        return [
            DbAdminStatementResult(
                index=index,
                statement_type=_admin_statement_type(statement),
                status=status,
                sql=statement,
                error_message=error_message,
            )
            for index, statement in enumerate(statements, start=1)
        ]

    def _apply_sample_import_to_catalog(self, step: SampleDataStep) -> None:
        current_owner = self._current_schema_owner()
        current = {
            ((table.owner or current_owner).upper(), table.table_name.upper()): table
            for table in self._catalog.tables
        }
        sample = {
            ((table.owner or current_owner).upper(), table.table_name.upper()): table
            for table in self._sample_schema_tables(step)
        }
        current.update(sample)
        ordered = [
            (current_owner.upper(), name)
            for name in _SAMPLE_OBJECTS
            if (current_owner.upper(), name) in current
        ]
        ordered_set = set(ordered)
        ordered.extend(key for key in current if key not in ordered_set)
        self._catalog = self._catalog.model_copy(
            update={
                "refreshed_at": _utc_now(),
                "current_owner": current_owner,
                "tables": [current[key] for key in ordered],
                "schema_fingerprint": "",
            }
        )

    def _remove_sample_from_state(self) -> None:
        current_owner = self._current_schema_owner()
        sample_keys = {(current_owner.upper(), name) for name in _SAMPLE_OBJECTS}
        self._catalog = self._catalog.model_copy(
            update={
                "refreshed_at": _utc_now(),
                "current_owner": current_owner,
                "tables": [
                    table
                    for table in self._catalog.tables
                    if (
                        (table.owner or current_owner).upper(),
                        table.table_name.upper(),
                    )
                    not in sample_keys
                ],
                "schema_fingerprint": "",
            }
        )
        profile = self._profiles.get(_SAMPLE_PROFILE_ID)
        if self._incremental_repository is not None:
            current = self._incremental_repository.get_profile(_SAMPLE_PROFILE_ID)
            if current is not None:
                archived = self._incremental_repository.save_profile(
                    current.model_copy(update={"archived": True}),
                    expected_etag=current.etag,
                )
                self._profile_cache.discard(_SAMPLE_PROFILE_ID)
                self._profile_cache.put(_SAMPLE_PROFILE_ID, archived)
        elif profile is not None:
            self._profiles[_SAMPLE_PROFILE_ID] = profile.model_copy(update={"archived": True})

    def _persist_local_catalog(self) -> None:
        repository = self._incremental_repository
        if repository is None:
            self._persist_state()
            return
        manifest = {
            (table.owner.upper(), table.table_name.upper()): self._catalog.refreshed_at
            for table in self._catalog.tables
        }
        current_manifest = repository.schema_manifest()
        if not self._catalog.schema_fingerprint:
            self._catalog.schema_fingerprint = hashlib.sha256(
                json.dumps(
                    self._catalog.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        repository.apply_schema_refresh(
            catalog=self._catalog,
            manifest=manifest,
            changed_keys=set(manifest),
            deleted_keys=set(current_manifest) - set(manifest),
        )
        self._schema_cache.clear()

    def _sample_schema_tables(self, step: SampleDataStep) -> list[SchemaTable]:
        current_owner = self._current_schema_owner()
        tables: list[SchemaTable] = []
        if step in {SampleDataStep.TABLES, SampleDataStep.ALL}:
            tables.extend(self._sample_tables_from_ddl())
        if step in {SampleDataStep.VIEWS, SampleDataStep.ALL}:
            tables.extend(self._sample_views_from_ddl())
        if step in {SampleDataStep.DATA, SampleDataStep.ALL}:
            row_counts = self._sample_row_counts()
            if not tables:
                tables.extend(
                    table
                    for table in self._catalog.tables
                    if (table.owner or current_owner).upper() == current_owner
                    and table.table_name in _SAMPLE_TABLES
                )
            tables = [
                table.model_copy(
                    update={"row_count": row_counts.get(table.table_name, table.row_count)}
                )
                for table in tables
            ]
        return tables

    def _sample_tables_from_ddl(self) -> list[SchemaTable]:
        current_owner = self._current_schema_owner()
        result: list[SchemaTable] = []
        for statement in self._sample_sql_sections()["tables"]:
            match = re.search(
                r"CREATE\s+TABLE\s+([A-Z0-9_]+)\s*\(",
                statement,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            table_name = _normalize_identifier(match.group(1))
            body_end = statement.rfind(")")
            if body_end <= match.end():
                continue
            body = statement[match.end() : body_end]
            columns: list[SchemaColumn] = []
            constraints: list[str] = []
            for raw_line in body.splitlines():
                line = raw_line.strip().rstrip(",")
                if not line:
                    continue
                if line.upper().startswith("REFERENCES "):
                    if constraints:
                        constraints[-1] = f"{constraints[-1]} {line}"
                    else:
                        constraints.append(line)
                    continue
                if re.match(r"^(CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|CHECK)\b", line, re.I):
                    constraints.append(line)
                    continue
                parts = line.split()
                column_name = _normalize_identifier(parts[0])
                data_type = self._sample_column_type(parts[1:])
                columns.append(
                    SchemaColumn(
                        column_name=column_name,
                        logical_name=column_name,
                        data_type=data_type,
                        nullable=(
                            "NOT NULL" not in line.upper() and "PRIMARY KEY" not in line.upper()
                        ),
                    )
                )
                if "PRIMARY KEY" in line.upper():
                    constraints.append(f"PK_{table_name} P({column_name})")
            result.append(
                SchemaTable(
                    table_name=table_name,
                    logical_name=table_name,
                    owner=current_owner,
                    comment="SQL Assist sample table",
                    row_count=0,
                    constraints=constraints,
                    columns=columns,
                )
            )
        return result

    def _sample_views_from_ddl(self) -> list[SchemaTable]:
        views: list[SchemaTable] = []
        current_owner = self._current_schema_owner()
        for statement in self._sample_sql_sections()["views"]:
            match = re.search(
                r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([A-Z0-9_]+)\s+AS\s+SELECT\s+(.*?)\s+FROM\s+",
                statement,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not match:
                continue
            view_name = _normalize_identifier(match.group(1))
            columns = [
                SchemaColumn(
                    column_name=self._sample_select_column_name(token),
                    logical_name=self._sample_select_column_name(token),
                    data_type="VARCHAR2",
                )
                for token in match.group(2).split(",")
                if self._sample_select_column_name(token)
            ]
            views.append(
                SchemaTable(
                    table_name=view_name,
                    logical_name=view_name,
                    owner=current_owner,
                    table_type="view",
                    comment="SQL Assist sample view",
                    columns=columns,
                )
            )
        return views

    def _sample_row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for statement in self._sample_sql_sections()["data"]:
            match = re.match(r"INSERT\s+INTO\s+([A-Z0-9_]+)\b", statement, flags=re.I)
            if match:
                table_name = _normalize_identifier(match.group(1))
                counts[table_name] = counts.get(table_name, 0) + 1
        return counts

    def _sample_column_type(self, tokens: list[str]) -> str:
        stop_words = {"NOT", "NULL", "PRIMARY", "DEFAULT", "CONSTRAINT", "REFERENCES"}
        selected = []
        for token in tokens:
            if token.upper() in stop_words:
                break
            selected.append(token)
        return " ".join(selected) or "VARCHAR2"

    def _sample_select_column_name(self, token: str) -> str:
        cleaned = token.strip()
        alias = re.search(r"\bAS\s+([A-Z0-9_]+)$", cleaned, flags=re.I)
        if alias:
            return _normalize_identifier(alias.group(1))
        return _normalize_identifier(cleaned.rsplit(".", 1)[-1])

    def _is_missing_object_error(self, message: str) -> bool:
        normalized = message.upper()
        return any(code in normalized for code in ("ORA-00942", "ORA-04043"))

    def list_profiles(self, *, include_archived: bool = False) -> list[Nl2SqlProfile]:
        if self._incremental_repository is not None:
            try:
                profiles = self._incremental_repository.list_profiles(
                    include_archived=include_archived
                )
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="profile_list",
                    exc=exc,
                    operation_error_code="profile_list_query_failed",
                )
            profiles = [
                self._profile_scope_for_read(profile, persist_migration=True)
                for profile in profiles
            ]
            for profile in profiles:
                self._profile_cache.put(profile.id, profile)
            return profiles
        with self._lock:
            return [
                self._profile_scope_for_read(profile, persist_migration=True)
                for profile in self._profiles.values()
                if include_archived or not profile.archived
            ]

    def _current_schema_owner(self) -> str:
        catalog_current = self._catalog.current_owner.strip().upper()
        if catalog_current:
            return catalog_current
        configured = get_settings().oracle_user.strip().upper()
        catalog_owners = {table.owner.strip().upper() for table in self._catalog.tables}
        if configured and configured in catalog_owners:
            return configured
        if len(catalog_owners) == 1:
            return next(iter(catalog_owners))
        return configured or "APP"

    def _catalog_qualified_name(self, table: SchemaTable) -> str:
        return qualified_object_name(table.owner or self._current_schema_owner(), table.table_name)

    def _resolve_profile_object_name(self, value: str) -> str:
        """旧形式を含む object 名を catalog 上の一意な限定名へ解決する。"""

        raw = str(value or "").replace('"', "").strip().upper()
        if not raw:
            raise ValueError("空の schema object は profile に追加できません。")
        catalog_by_name: dict[str, list[str]] = {}
        catalog_names: set[str] = set()
        for table in self._catalog.tables:
            qualified = self._catalog_qualified_name(table)
            catalog_names.add(qualified)
            catalog_by_name.setdefault(table.table_name.upper(), []).append(qualified)
        current_owner = self._current_schema_owner()
        if "." in raw:
            qualified = parse_object_identity(raw).qualified_name
            # 既存 profile の object が後から削除/権限取消されても profile 自体は
            # 読み出せるよう限定名を保持する。Oracle 同期・実行時に不一致を明示する。
            return qualified
        object_name = _normalize_identifier(raw)
        current_qualified = qualified_object_name(current_owner, object_name)
        matches = sorted(set(catalog_by_name.get(object_name, [])))
        if current_qualified in matches:
            return current_qualified
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(
                f"{object_name}: 複数 schema に同名 object があります。"
                f"{', '.join(matches)} のいずれかを OWNER.OBJECT 形式で指定してください。"
            )
        # 旧 API は catalog refresh 前にも current schema の bare name を保存できた。
        # 互換入力は current owner に限定して保持し、Oracle profile 同期時の再読込照合で
        # 実在/権限の最終確認を行う。
        return current_qualified

    def _canonical_profile_scope(
        self,
        profile: Nl2SqlProfile,
        *,
        migrate_legacy_empty: bool,
    ) -> Nl2SqlProfile:
        allowed_tables = list(profile.allowed_tables)
        allowed_views = list(profile.allowed_views)
        if (
            migrate_legacy_empty
            and profile.object_scope_version < 2
            and not allowed_tables
            and not allowed_views
            and self._catalog.tables
        ):
            current_owner = self._current_schema_owner()
            for table in self._catalog.tables:
                if table.owner.upper() != current_owner:
                    continue
                target = allowed_views if "view" in table.table_type.lower() else allowed_tables
                target.append(self._catalog_qualified_name(table))
        canonical_tables = self._dedupe_object_names(allowed_tables)
        canonical_views = self._dedupe_object_names(allowed_views)
        scope_version = 2 if self._catalog.tables or profile.object_scope_version >= 2 else 1
        return profile.model_copy(
            update={
                "allowed_tables": canonical_tables,
                "allowed_views": canonical_views,
                "object_scope_version": scope_version,
            }
        )

    def _profile_scope_for_read(
        self,
        profile: Nl2SqlProfile,
        *,
        persist_migration: bool = False,
    ) -> Nl2SqlProfile:
        visible_profile = profile.model_copy(
            update={
                "allowed_tables": [
                    name for name in profile.allowed_tables if is_user_visible_object_name(name)
                ],
                "allowed_views": [
                    name for name in profile.allowed_views if is_user_visible_object_name(name)
                ],
            }
        )
        if visible_profile.object_scope_version >= 2:
            return visible_profile
        migrated = self._canonical_profile_scope(
            visible_profile,
            migrate_legacy_empty=True,
        )
        if migrated.object_scope_version < 2:
            # Catalog 未取得時は移行スナップショットを確定できない。同一 payload の
            # 再保存で version/ETag だけを進めず、後続の更新・削除競合を防ぐ。
            return migrated
        legacy_empty_scope = not profile.allowed_tables and not profile.allowed_views
        if not persist_migration or not legacy_empty_scope:
            return migrated
        repository = self._incremental_repository
        if repository is not None:
            try:
                stored = repository.save_profile(
                    migrated,
                    expected_etag=profile.etag,
                )
                self._profile_cache.put(stored.id, stored)
                return stored
            except IncrementalVersionConflict:
                latest = repository.get_profile(profile.id)
                if latest is not None:
                    return self._canonical_profile_scope(
                        latest,
                        migrate_legacy_empty=True,
                    )
                return migrated
            except Exception:
                logger.exception(
                    "legacy_profile_scope_migration_persist_failed",
                    extra={"profile_id": profile.id},
                )
                return migrated
        with self._lock:
            if profile.id in self._profiles:
                self._profiles[profile.id] = migrated
                # Legacy read migration must never turn an otherwise readable profile
                # endpoint into an outage when persistence has already gone read-only.
                self._persist_state(raise_on_error=False)
        return migrated

    def profile_allowed_object_names(self, profile: Nl2SqlProfile) -> list[str]:
        """Profile が検索・Select AI で参照できる table/view 名を返す。"""
        scoped = self._profile_scope_for_read(profile)
        return self._dedupe_object_names([*scoped.allowed_tables, *scoped.allowed_views])

    def build_select_ai_additional_instructions(
        self,
        profile: Nl2SqlProfile,
        request_instructions: str = "",
    ) -> str:
        """業務 profile の文脈を Select AI 用の決定論的な指示へまとめる。"""
        sections: list[str] = []

        rule_lines = [
            f"- {rule.strip()}" for rule in self._effective_sql_rules(profile) if rule.strip()
        ]
        if rule_lines:
            sections.append("## SQL 生成ルール\n" + "\n".join(rule_lines))

        persistent = profile.select_ai_config.additional_instructions.strip()
        if persistent:
            sections.append(f"## プロファイル追加指示\n{persistent}")

        request_value = request_instructions.strip()
        if request_value:
            sections.append(f"## 今回の追加指示\n{request_value}")
        return "\n\n".join(sections)

    def _select_ai_generate_attributes(
        self,
        profile: Nl2SqlProfile,
        overrides: SelectAiRequestOverrides | None,
    ) -> dict[str, str] | None:
        if overrides is None or not overrides.has_values():
            return None
        attributes: dict[str, str] = {}
        role = overrides.role.strip() or profile.select_ai_config.role.strip()
        instructions = self.build_select_ai_additional_instructions(
            profile,
            overrides.additional_instructions,
        )
        if role:
            attributes["role"] = role
        if instructions:
            attributes["additional_instructions"] = instructions
        return attributes or None

    def _select_ai_overrides_with_ontology_context(
        self,
        overrides: SelectAiRequestOverrides | None,
        ontology_context: Any | None,
    ) -> SelectAiRequestOverrides | None:
        ontology_instructions = self._ontology_generation_context_prompt(ontology_context)
        if not ontology_instructions:
            return overrides
        merged_instructions = "\n\n".join(
            part
            for part in [
                overrides.additional_instructions if overrides is not None else "",
                "## 確認済み Ontology コンテキスト",
                ontology_instructions,
            ]
            if part.strip()
        )
        return SelectAiRequestOverrides(
            role=overrides.role if overrides is not None else "",
            additional_instructions=merged_instructions,
        )

    def _redact_select_ai_context_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """監査・engine meta から業務 prompt 本文を除外する。"""
        redacted = {
            key: value
            for key, value in attributes.items()
            if key not in {"role", "additional_instructions"}
        }
        role = str(attributes.get("role") or "")
        instructions = str(attributes.get("additional_instructions") or "")
        redacted.update(
            {
                "role_applied": bool(role),
                "role_length": len(role),
                "additional_instructions_applied": bool(instructions),
                "additional_instructions_length": len(instructions),
            }
        )
        return redacted

    def build_select_ai_profile_attributes(self, profile: Nl2SqlProfile) -> dict[str, Any]:
        """業務 profile から OCI 固定の DBMS_CLOUD_AI attributes を組み立てる。"""
        settings = get_settings()
        config = profile.select_ai_config
        attributes: dict[str, Any] = {
            "provider": "oci",
            "enforce_object_list": config.enforce_object_list,
            "comments": config.comments,
            "annotations": config.annotations,
            "constraints": config.constraints,
            "max_tokens": config.max_tokens,
            "object_list": self._select_ai_object_list(self.profile_allowed_object_names(profile)),
        }
        credential_name = settings.nl2sql_select_ai_credential_name.strip()
        region = (
            config.region.strip()
            or settings.nl2sql_select_ai_region.strip()
            or settings.oci_region.strip()
        )
        model = config.model.strip() or settings.nl2sql_select_ai_model.strip()
        embedding_model = (
            config.embedding_model.strip()
            or settings.oci_genai_embed_model_id.strip()
            or "cohere.embed-v4.0"
        )
        if credential_name:
            attributes["credential_name"] = credential_name
        if settings.oci_compartment_id.strip():
            attributes["oci_compartment_id"] = settings.oci_compartment_id.strip()
        if region:
            attributes["region"] = region
        if model:
            attributes["model"] = model
        if embedding_model:
            attributes["embedding_model"] = embedding_model
        role = config.role.strip()
        if role:
            attributes["role"] = role
        additional_instructions = self.build_select_ai_additional_instructions(profile)
        if additional_instructions:
            attributes["additional_instructions"] = additional_instructions
        return attributes

    def search_profiles(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        include_archived: bool,
        allowed_profile_ids: set[str] | None = None,
    ) -> ProfileSummaryPage:
        _decode_cursor(cursor, 2)
        if allowed_profile_ids is not None:
            profiles = [
                profile
                for profile in self.list_profiles(include_archived=include_archived)
                if profile.id in allowed_profile_ids
            ]
            return self._profile_summary_page_from_profiles(
                profiles,
                cursor=cursor,
                limit=limit,
                query=query,
                include_archived=include_archived,
                change_token=self._profile_summary_change_token(profiles),
            )
        repository = self._incremental_repository
        if repository is None:
            profiles = self.list_profiles(include_archived=include_archived)
            return self._profile_summary_page_from_profiles(
                profiles,
                cursor=cursor,
                limit=limit,
                query=query,
                include_archived=include_archived,
                change_token=self._profile_summary_change_token(profiles),
            )
        try:
            return repository.search_profiles(
                cursor=cursor,
                limit=limit,
                query=query,
                include_archived=include_archived,
            )
        except ValueError:
            raise
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="profile_search",
                exc=exc,
                operation_error_code="profile_search_query_failed",
            )

    def _profile_summary_page_from_profiles(
        self,
        profiles: Sequence[Nl2SqlProfile],
        *,
        cursor: str | None,
        limit: int,
        query: str,
        include_archived: bool,
        change_token: int,
    ) -> ProfileSummaryPage:
        after = _decode_cursor(cursor, 2)
        query_key = _profile_search_key(query.strip())
        filtered = [
            profile
            for profile in profiles
            if (include_archived or not profile.archived)
            and _profile_matches_query(profile, query_key)
        ]
        filtered.sort(key=lambda profile: (_profile_search_key(profile.name), profile.id))
        total = len(filtered)
        if after:
            after_key = (_profile_search_key(after[0]), after[1])
            filtered = [
                profile
                for profile in filtered
                if (_profile_search_key(profile.name), profile.id) > after_key
            ]
        selected = filtered[: limit + 1]
        has_more = len(selected) > limit
        selected = selected[:limit]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = _encode_cursor(_profile_search_key(last.name), last.id)
        return ProfileSummaryPage(
            items=[self._profile_summary(profile) for profile in selected],
            next_cursor=next_cursor,
            total=total,
            change_token=change_token,
        )

    @staticmethod
    def _profile_summary(profile: Nl2SqlProfile) -> ProfileSummary:
        return ProfileSummary(
            id=profile.id,
            name=profile.name,
            category=profile.category,
            description=profile.description,
            archived=profile.archived,
            allowed_table_count=len(profile.allowed_tables),
            allowed_view_count=len(profile.allowed_views),
            glossary_count=len(profile.glossary),
            few_shot_count=len(profile.few_shot_examples),
            version=profile.version,
            etag=profile.etag,
            updated_at=profile.updated_at,
        )

    def _profile_summary_change_token(self, profiles: Sequence[Nl2SqlProfile]) -> int:
        payload = [
            self._profile_summary(profile).model_dump(mode="json")
            for profile in sorted(profiles, key=lambda item: (item.name.casefold(), item.id))
        ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return int(digest[:12], 16)

    def create_profile(self, profile: Nl2SqlProfile) -> Nl2SqlProfile:
        profile = self._canonical_profile_scope(
            self._profile_with_sql_rules_absorbed(profile),
            migrate_legacy_empty=False,
        ).model_copy(update={"object_scope_version": 2})
        profile = profile.model_copy(
            update={
                "select_ai_config": profile.select_ai_config.model_copy(
                    update={"previous_profile_name": ""}
                )
            }
        )
        self._assert_profile_name_available(profile.name, exclude_profile_id=profile.id)
        if self._incremental_repository is not None:
            try:
                stored = self._incremental_repository.save_profile(profile, expected_etag=None)
            except IncrementalVersionConflict as exc:
                raise ValueError("同じ profile ID が既に存在します。") from exc
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="profile_create",
                    exc=exc,
                    operation_error_code="profile_save_failed",
                )
            self._profile_cache.put(stored.id, stored)
            return stored
        with self._lock:
            self.ensure_persistence_available()
            self._profiles[profile.id] = profile
            self._persist_state()
        return profile

    def update_profile(
        self,
        profile_id: str,
        patcher: Callable[[Nl2SqlProfile], Nl2SqlProfile],
        *,
        expected_etag: str | None = None,
    ) -> Nl2SqlProfile:
        if self._incremental_repository is not None:
            current = self.get_profile(profile_id, include_archived=True)
            if expected_etag is not None and expected_etag != current.etag:
                raise IncrementalVersionConflict(current.etag)
            updated = self._canonical_profile_scope(
                self._profile_with_sql_rules_absorbed(patcher(current)),
                migrate_legacy_empty=False,
            ).model_copy(update={"object_scope_version": 2})
            updated = self._profile_with_select_ai_rename_marker(current, updated)
            self._assert_profile_name_available(updated.name, exclude_profile_id=profile_id)
            try:
                stored = self._incremental_repository.save_profile(
                    updated,
                    expected_etag=expected_etag or current.etag,
                )
            except IncrementalVersionConflict:
                raise
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="profile_update",
                    exc=exc,
                    operation_error_code="profile_save_failed",
                )
            self._profile_cache.discard(profile_id)
            self._profile_cache.put(profile_id, stored)
            return stored
        with self._lock:
            self.ensure_persistence_available()
            current = self._profiles[profile_id]
            updated = self._canonical_profile_scope(
                self._profile_with_sql_rules_absorbed(patcher(current)),
                migrate_legacy_empty=False,
            ).model_copy(update={"object_scope_version": 2})
            updated = self._profile_with_select_ai_rename_marker(current, updated)
            self._assert_profile_name_available(updated.name, exclude_profile_id=profile_id)
            self._profiles[profile_id] = updated
            self._persist_state()
        return updated

    def _assert_profile_name_available(
        self,
        profile_name: str,
        *,
        exclude_profile_id: str,
    ) -> None:
        name_key = profile_name.strip().upper()
        for existing in self.list_profiles(include_archived=True):
            if existing.id == exclude_profile_id:
                continue
            if existing.name.strip().upper() == name_key:
                raise ProfileNameConflict(profile_name)

    def _profile_with_select_ai_rename_marker(
        self,
        current: Nl2SqlProfile,
        updated: Nl2SqlProfile,
    ) -> Nl2SqlProfile:
        current_name = self._select_ai_profile_name(current).strip()
        current_previous_name = current.select_ai_config.previous_profile_name.strip()
        requested_previous_name = updated.select_ai_config.previous_profile_name.strip()
        new_name = self._select_ai_profile_name(updated).strip()
        select_ai_config = updated.select_ai_config
        if (
            current_previous_name
            and not requested_previous_name
            and current_name.upper() == new_name.upper()
        ):
            return updated
        old_name = current_previous_name or current_name
        if old_name and new_name and old_name.upper() != new_name.upper():
            select_ai_config = select_ai_config.model_copy(
                update={"previous_profile_name": old_name}
            )
        elif (
            select_ai_config.previous_profile_name.strip()
            and select_ai_config.previous_profile_name.strip().upper() == new_name.upper()
        ):
            select_ai_config = select_ai_config.model_copy(update={"previous_profile_name": ""})
        return updated.model_copy(update={"select_ai_config": select_ai_config})

    def clear_profile_select_ai_previous_name(
        self,
        profile_id: str,
        *,
        expected_etag: str | None = None,
    ) -> Nl2SqlProfile:
        return self.update_profile(
            profile_id,
            lambda profile: profile.model_copy(
                update={
                    "select_ai_config": profile.select_ai_config.model_copy(
                        update={"previous_profile_name": ""}
                    )
                }
            ),
            expected_etag=expected_etag,
        )

    def delete_profile(self, profile_id: str, *, expected_etag: str | None = None) -> Nl2SqlProfile:
        if self._incremental_repository is not None:
            try:
                current = self.get_profile(profile_id, include_archived=True)
            except ValueError as exc:
                raise KeyError(profile_id) from exc
            try:
                self._incremental_repository.delete_profile(
                    profile_id,
                    expected_etag=expected_etag or current.etag,
                )
            except IncrementalVersionConflict:
                raise
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="profile_delete",
                    exc=exc,
                    operation_error_code="profile_delete_failed",
                )
            self._profile_cache.discard(profile_id)
            return current
        with self._lock:
            self.ensure_persistence_available()
            deleted = self._profiles.pop(profile_id)
            self._persist_state()
        return deleted

    def delete_profile_with_oracle_cleanup(
        self,
        profile_id: str,
        *,
        expected_etag: str | None = None,
    ) -> ProfileDeleteData:
        """業務 profile 削除前に紐づく Oracle Select AI / Agent assets を削除する。"""
        try:
            current = self.get_profile(profile_id, include_archived=True)
        except ValueError as exc:
            raise KeyError(profile_id) from exc
        if (
            self._incremental_repository is not None
            and expected_etag is not None
            and expected_etag != current.etag
        ):
            raise IncrementalVersionConflict(current.etag)

        oracle_cleanup = self._cleanup_profile_oracle_assets_for_delete(current)
        if self._use_oracle_runtime() and any(
            item.status != "cleaned" or not item.executed for item in oracle_cleanup
        ):
            raise ProfileOracleCleanupFailed(oracle_cleanup)

        deleted = self.delete_profile(profile_id, expected_etag=expected_etag)
        return ProfileDeleteData(profile=deleted, oracle_cleanup=oracle_cleanup)

    def import_profile_learning_material(
        self,
        *,
        profile_id: str,
        filename: str,
        content: bytes,
        mode: str = "merge",
    ) -> ProfileLearningMaterialImportData:
        warnings: list[str] = []
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"merge", "replace"}:
            raise ValueError(
                f"{mode or '空の mode'}: 未対応の import mode です。"
                "mode は merge または replace を指定してください。"
            )
        _require_xlsx_template_upload(filename)
        parsed, skipped = self._parse_profile_learning_material_file(
            filename,
            content,
            warnings,
        )
        if not any(parsed.values()) and warnings:
            raise ValueError(" ".join(warnings))

        def patch(current: Nl2SqlProfile) -> Nl2SqlProfile:
            if normalized_mode == "replace":
                glossary = parsed["terms"]
                examples = parsed["examples"]
            else:
                glossary = {**current.glossary, **parsed["terms"]}
                examples = self._merge_few_shot_examples(
                    current.few_shot_examples,
                    parsed["examples"],
                )
            config = current.select_ai_config.model_copy(
                update={
                    "additional_instructions": self._merge_additional_instruction_lines(
                        current.select_ai_config.additional_instructions,
                        parsed["rules"],
                    )
                }
            )
            return current.model_copy(
                update={
                    "glossary": glossary,
                    "sql_rules": [],
                    "few_shot_examples": examples,
                    "select_ai_config": config,
                }
            )

        try:
            updated = self.update_profile(profile_id, patch)
        except ValueError as exc:
            if "profile" in str(exc).lower() and "見つ" in str(exc):
                raise KeyError(profile_id) from exc
            raise
        return ProfileLearningMaterialImportData(
            profile_id=updated.id,
            profile_name=updated.name,
            mode=normalized_mode,
            imported_terms=len(parsed["terms"]),
            imported_rules=len(parsed["rules"]),
            imported_examples=len(parsed["examples"]),
            skipped_count=skipped,
            warnings=warnings,
            profile=updated,
        )

    def export_profile_learning_material_xlsx(self, profile_id: str) -> tuple[str, bytes]:
        profile = self.get_profile(profile_id)
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        terms_sheet = workbook.active
        terms_sheet.title = "terms"
        _append_excel_text_row(terms_sheet, ["TERM", "DEFINITION"])
        for term, definition in profile.glossary.items():
            _append_excel_text_row(terms_sheet, [term, definition])
        if profile.few_shot_examples:
            examples_sheet = workbook.create_sheet("few_shot")
            _append_excel_text_row(examples_sheet, ["QUESTION", "SQL"])
            for example in profile.few_shot_examples:
                _append_excel_text_row(
                    examples_sheet,
                    [example.get("question", ""), example.get("sql", "")],
                )
        rules = [
            line.strip()
            for line in profile.select_ai_config.additional_instructions.splitlines()
            if line.strip()
        ]
        if rules:
            rules_sheet = workbook.create_sheet("rules")
            _append_excel_text_row(rules_sheet, ["RULE"])
            for rule in rules:
                _append_excel_text_row(rules_sheet, [rule])
        buffer = io.BytesIO()
        workbook.save(buffer)
        safe_profile = _csv_identifier(profile.id or profile.name, "PROFILE").lower()
        return f"nl2sql_{safe_profile}_learning_material.xlsx", buffer.getvalue()

    def _load_legacy_learning_material(
        self,
        *,
        force_reload: bool,
    ) -> LegacyLearningMaterialData:
        """Incremental singleton を request 時にだけ復元する。"""

        repository = self._incremental_repository
        if repository is None:
            with self._lock:
                return self._legacy_learning_material.model_copy(deep=True)

        with self._legacy_learning_material_io_lock:
            now = time.monotonic()
            with self._lock:
                cache_fresh = (
                    self._legacy_learning_material_loaded
                    and now - self._legacy_learning_material_checked_at
                    < self._cache_token_poll_seconds
                )
                if cache_fresh and not force_reload:
                    return self._legacy_learning_material.model_copy(deep=True)
            try:
                document = repository.get_document(
                    "singletons",
                    _LEGACY_LEARNING_MATERIAL_SINGLETON,
                )
                value = document.get("value") if document else {}
                material = LegacyLearningMaterialData.model_validate(value or {})
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="legacy_learning_material_load",
                    exc=exc,
                    operation_error_code="legacy_learning_material_query_failed",
                )

            payload = {"value": material.model_dump(mode="json")}
            digest = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            with self._lock:
                self._legacy_learning_material = material
                self._legacy_learning_material_loaded = True
                self._legacy_learning_material_checked_at = now
                key = ("singletons", _LEGACY_LEARNING_MATERIAL_SINGLETON)
                if document:
                    self._incremental_hashes[key] = digest
                else:
                    self._incremental_hashes.pop(key, None)
                return material.model_copy(deep=True)

    def _persist_legacy_learning_material(
        self,
        material: LegacyLearningMaterialData,
    ) -> None:
        payload = {"value": material.model_dump(mode="json")}
        self._persist_entities(
            [
                (
                    "singletons",
                    _LEGACY_LEARNING_MATERIAL_SINGLETON,
                    payload,
                )
            ]
        )
        with self._lock:
            self._legacy_learning_material_loaded = True
            self._legacy_learning_material_checked_at = time.monotonic()

    def get_legacy_learning_material(self) -> LegacyLearningMaterialData:
        return self._load_legacy_learning_material(force_reload=True)

    def import_legacy_terms(self, *, filename: str, content: bytes) -> LegacyLearningMaterialData:
        _require_xlsx_template_upload(filename)
        warnings: list[str] = []
        glossary = self._parse_legacy_terms_file(filename, content, warnings)
        if not glossary and warnings:
            raise ValueError(" ".join(warnings))
        with self._legacy_learning_material_io_lock:
            previous = self._load_legacy_learning_material(force_reload=True)
            updated = previous.model_copy(update={"glossary": glossary, "warnings": []})
            with self._lock:
                self._legacy_learning_material = updated
            try:
                self._persist_legacy_learning_material(updated)
            except Exception:
                with self._lock:
                    self._legacy_learning_material = previous
                    self._legacy_learning_material_loaded = True
                    self._legacy_learning_material_checked_at = time.monotonic()
                raise
            return updated.model_copy(update={"warnings": warnings}, deep=True)

    def import_legacy_rules(self, *, filename: str, content: bytes) -> LegacyLearningMaterialData:
        _require_xlsx_template_upload(filename)
        warnings: list[str] = []
        rules = self._parse_legacy_rules_file(filename, content, warnings)
        if not rules and warnings:
            raise ValueError(" ".join(warnings))
        with self._legacy_learning_material_io_lock:
            previous = self._load_legacy_learning_material(force_reload=True)
            updated = previous.model_copy(update={"rules": rules, "warnings": []})
            with self._lock:
                self._legacy_learning_material = updated
            try:
                self._persist_legacy_learning_material(updated)
            except Exception:
                with self._lock:
                    self._legacy_learning_material = previous
                    self._legacy_learning_material_loaded = True
                    self._legacy_learning_material_checked_at = time.monotonic()
                raise
            return updated.model_copy(update={"warnings": warnings}, deep=True)

    def export_legacy_terms_xlsx(self) -> tuple[str, bytes]:
        material = self.get_legacy_learning_material()
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "terms"
        _append_excel_text_row(sheet, ["TERM", "DEFINITION"])
        for term, definition in material.glossary.items():
            _append_excel_text_row(sheet, [term, definition])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "terms.xlsx", buffer.getvalue()

    def export_legacy_rules_xlsx(self) -> tuple[str, bytes]:
        material = self.get_legacy_learning_material()
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "rules"
        _append_excel_text_row(sheet, ["RULE"])
        for rule in material.rules:
            _append_excel_text_row(sheet, [rule])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "rules.xlsx", buffer.getvalue()

    def archive_profile(self, profile_id: str) -> Nl2SqlProfile:
        return self.update_profile(profile_id, lambda p: p.model_copy(update={"archived": True}))

    def restore_profile(self, profile_id: str) -> Nl2SqlProfile:
        return self.update_profile(profile_id, lambda p: p.model_copy(update={"archived": False}))

    def get_profile(
        self, profile_id: str | None, *, include_archived: bool = False
    ) -> Nl2SqlProfile:
        if self._incremental_repository is not None:
            resolved_id = profile_id or "default"
            cached = self._profile_cache.get(resolved_id)
            self._refresh_cache_token(
                PROFILE_NAMESPACE,
                allow_cached_on_failure=isinstance(cached, Nl2SqlProfile),
            )
            cached = self._profile_cache.get(resolved_id)
            if isinstance(cached, Nl2SqlProfile):
                if cached.archived and not include_archived:
                    raise ProfileNotFoundError(resolved_id)
                return self._profile_scope_for_read(cached, persist_migration=True)
            try:
                profile = self._incremental_repository.get_profile(resolved_id)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="profile_load",
                    exc=exc,
                    operation_error_code="profile_query_failed",
                )
            if profile is None or (profile.archived and not include_archived):
                raise ProfileNotFoundError(resolved_id)
            profile = self._profile_scope_for_read(profile, persist_migration=True)
            self._profile_cache.put(resolved_id, profile)
            return profile
        with self._lock:
            resolved_id = profile_id or "default"
            profile = self._profiles.get(resolved_id)
            if profile is None or (profile.archived and not include_archived):
                raise ProfileNotFoundError(resolved_id)
            return self._profile_scope_for_read(profile, persist_migration=True)

    def _refresh_cache_token(
        self,
        namespace: str,
        *,
        allow_cached_on_failure: bool,
    ) -> None:
        """5 秒ごとの token poll で別 instance の更新を bounded cache へ反映する。"""

        repository = self._incremental_repository
        if repository is None:
            return
        now = time.monotonic()
        with self._lock:
            checked_at = self._cache_token_checked_at.get(namespace, 0.0)
            if now - checked_at < self._cache_token_poll_seconds:
                return
        try:
            token = repository.get_change_token(namespace)
        except Exception as exc:
            if allow_cached_on_failure:
                return
            self._raise_incremental_repository_failure(
                operation="change_token",
                exc=exc,
                operation_error_code="change_token_query_failed",
            )
        with self._lock:
            previous = (
                self._profile_change_token
                if namespace == PROFILE_NAMESPACE
                else self._schema_change_token
            )
            if previous and token != previous:
                if namespace == PROFILE_NAMESPACE:
                    self._profile_cache.clear()
                else:
                    self._schema_cache.clear()
            if namespace == PROFILE_NAMESPACE:
                self._profile_change_token = token
            else:
                self._schema_change_token = token
            record_token_lag(namespace, abs(token - previous) if previous else 0)
            self._cache_token_checked_at[namespace] = now

    def _transition_job_steps(
        self,
        job_id: str,
        *,
        completed_stage: str | None = None,
        completed_status: JobStepStatus = JobStepStatus.DONE,
        elapsed_ms: int | None = None,
        running_stage: str | None = None,
    ) -> None:
        """実処理と UI の段階表示を同じ job snapshot 上で進める。"""

        with self._lock:
            job = self._jobs[job_id]
            known_stages = {step.stage for step in job.steps}
            requested_stages = {
                stage for stage in (completed_stage, running_stage) if stage is not None
            }
            unknown_stages = requested_stages - known_stages
            if unknown_stages:
                raise RuntimeError(
                    "未定義の NL2SQL job stage です: " + ", ".join(sorted(unknown_stages))
                )
            next_steps: list[JobStepData] = []
            for step in job.steps:
                if completed_stage and step.stage == completed_stage:
                    step = step.model_copy(
                        update={"status": completed_status, "elapsed_ms": elapsed_ms}
                    )
                elif running_stage and step.stage == running_stage:
                    step = step.model_copy(
                        update={"status": JobStepStatus.RUNNING, "elapsed_ms": None}
                    )
                next_steps.append(step)
            job.steps = next_steps
            if job.timing is not None:
                job.timing = job.timing.model_copy(
                    update={
                        "stage_timings": [
                            StageTiming(stage=step.stage, elapsed_ms=step.elapsed_ms)
                            for step in job.steps
                            if step.elapsed_ms is not None
                        ]
                    }
                )
        self._persist_job(job_id)

    def start_job(
        self,
        request: JobCreateRequest,
        *,
        actor_user_uuid: str = "",
        actor_is_system_admin: bool = False,
    ) -> JobCreateData:
        # Queue 投入前に profile と request scope を検証し、未知 profile を非同期
        # error へ隠さない。
        self.get_profile(request.profile_id)
        self._resolve_allowed_objects(request.profile_id, request.allowed_objects)
        job_id = str(uuid.uuid4())
        if self._deepsec_enabled and not actor_user_uuid:
            raise ValueError("DeepSec 有効時のジョブには認証済み actor が必要です。")
        job = StoredJob(
            job_id=job_id,
            request=request,
            actor_user_uuid=actor_user_uuid,
            actor_is_system_admin=actor_is_system_admin,
            steps=_new_job_steps(),
            owned=True,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._prune_terminal_jobs_locked()
        self._persist_job(job_id)
        response = JobCreateData(
            job_id=job_id,
            status=job.status,
            created_at=job.created_at,
            steps=[step.model_copy() for step in job.steps],
        )
        thread = threading.Thread(target=self._run_job_bounded, args=(job_id,), daemon=True)
        thread.start()
        return response

    @staticmethod
    def _assert_job_actor_access(
        job: StoredJob,
        *,
        actor_user_uuid: str,
        actor_can_manage: bool,
    ) -> None:
        """job の行レベルアクセスを検証する。

        管理権限なし・認証済み actor のとき、job の actor と一致しなければ拒否する。
        actor 不明の job(認証無効期間に作成・旧 snapshot 復元)は所有者を特定
        できないため同様に拒否する(旧実装はこのケースを素通ししていた)。
        """
        if actor_can_manage or not actor_user_uuid:
            return
        if job.actor_user_uuid != actor_user_uuid:
            raise PermissionError(job.job_id)

    def _run_job_bounded(self, job_id: str) -> None:
        """同時実行数を settings.nl2sql_job_max_concurrency に制限して実行する。"""
        with self._job_concurrency:
            self._run_job_safely(job_id)

    def _prune_history_locked(self) -> None:
        """プロセス内 history の保持上限(古い順に破棄)。DB 側の履歴が正本。"""
        overflow = len(self._history) - _HISTORY_RETENTION_LIMIT
        if overflow > 0:
            del self._history[:overflow]

    def _prune_terminal_jobs_locked(self) -> None:
        """保持上限を超えた terminal job を古い順に捨てる(実行中は残す)。"""
        overflow = len(self._jobs) - _JOB_RETENTION_LIMIT
        if overflow <= 0:
            return
        terminal = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {JobStatus.DONE, JobStatus.ERROR}
        ]
        for job_id in terminal[:overflow]:
            del self._jobs[job_id]

    def _raise_if_job_cancelled(self, job_id: str) -> None:
        """stage 境界の協調キャンセル判定。

        ローカルフラグに加えて repository 上のキャンセル要求も確認する。gunicorn の
        複数 worker 構成では cancel API が別プロセスへ届くため、ローカルフラグだけでは
        止まらない。repository の確認失敗は job を落とさず無視する(次の境界で再確認)。
        """

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.cancel_requested:
                raise JobCancelledError()
        repository = self._incremental_repository
        if repository is None:
            return
        try:
            document = repository.get_document(_JOB_CANCEL_COLLECTION, job_id)
        except Exception:
            logger.warning(
                "nl2sql_job_cancel_probe_failed",
                extra={"job_id": job_id},
                exc_info=True,
            )
            return
        if document is None:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.cancel_requested = True
        raise JobCancelledError()

    def request_job_cancel(
        self,
        job_id: str,
        *,
        actor_user_uuid: str = "",
        actor_can_manage: bool = False,
    ) -> JobData | None:
        """実行中 job の協調キャンセルを要求する(stage 境界で停止)。

        terminal な job には no-op。アクセス制御は get_job と同一。job を実行している
        worker が別プロセスでも届くよう、要求は repository の専用ドキュメントにも書く。
        """
        job = self._load_job_record(job_id)
        if job is None:
            return None
        self._assert_job_actor_access(
            job,
            actor_user_uuid=actor_user_uuid,
            actor_can_manage=actor_can_manage,
        )
        with self._lock:
            in_flight = job.status in _IN_FLIGHT_JOB_STATUSES
            if in_flight:
                job.cancel_requested = True
        if in_flight and self._incremental_repository is not None:
            self._persist_entities(
                [
                    (
                        _JOB_CANCEL_COLLECTION,
                        job_id,
                        {
                            "job_id": job_id,
                            "requested_at": _utc_now(),
                            "actor_user_uuid": actor_user_uuid,
                        },
                    )
                ]
            )
        return self.get_job(
            job_id,
            actor_user_uuid=actor_user_uuid,
            actor_can_manage=actor_can_manage,
        )

    def get_job(
        self,
        job_id: str,
        *,
        actor_user_uuid: str = "",
        actor_can_manage: bool = False,
    ) -> JobData | None:
        job = self._load_job_record(job_id)
        if job is None:
            return None
        self._assert_job_actor_access(
            job,
            actor_user_uuid=actor_user_uuid,
            actor_can_manage=actor_can_manage,
        )
        with self._lock:
            return JobData(
                job_id=job.job_id,
                status=job.status,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                elapsed_ms=job.elapsed_ms,
                result=job.result,
                error_message=job.error_message,
                error_code=job.error_code,
                warning_message=job.warning_message,
                timing=job.timing,
                steps=job.steps,
            )

    def preview(self, request: PreviewRequest) -> PreviewData:
        started = time.monotonic()
        created_at = _utc_now()
        allowed = self._resolve_allowed_objects(request.profile_id, request.allowed_objects)
        generated = self._generate_with_fallback(
            question=request.question,
            engine=request.engine,
            profile=self.get_profile(request.profile_id),
            allowed=allowed,
            row_limit=request.row_limit,
            select_ai_overrides=request.select_ai_overrides,
            ontology_context=request.ontology_context,
        )
        row_limit = self._resolve_row_limit(request.profile_id, request.row_limit)
        analysis = self.analyze_sql(
            generated.generated_sql,
            allowed,
            row_limit,
            catalog=generated.schema_catalog,
        )
        analysis = self._apply_empty_filter_generation_guard(request.question, analysis)
        timing = TimingEnvelope(
            created_at=created_at,
            started_at=created_at,
            finished_at=_utc_now(),
            elapsed_ms=_elapsed_ms(started),
            stage_timings=[StageTiming(stage="generate", elapsed_ms=_elapsed_ms(started))],
        )
        return PreviewData(
            sql=generated.generated_sql,
            is_safe=analysis.safety.is_safe,
            row_limit=row_limit or 0,
            note=f"質問を受領しました: {request.question[:80]}",
            engine=generated.engine,
            engine_meta=generated.engine_meta,
            fallback_reason=generated.fallback_reason,
            rewritten_question=self._rewrite_question_preserving_empty_filter(
                request.question, self.get_profile(request.profile_id)
            ),
            executable_sql=analysis.executable_sql,
            safety=analysis.safety,
            recommendations=analysis.recommendations,
            repaired_sql=analysis.repaired_sql,
            optimization_hints=analysis.optimization_hints,
            timing=timing,
        )

    def execute_sql(
        self,
        sql: str,
        allowed: AllowedObjects,
        row_limit: int | None,
        *,
        analysis: AnalyzeData | None = None,
    ) -> tuple[SafetyReport, str, QueryResults]:
        """安全判定を通った SELECT だけを実行する。

        `analysis` に呼び出し側で済ませた `analyze_sql` の結果を渡すと再解析を省略する
        (job は safety_check 段階で同じ SQL を解析済みなので、sqlglot の再パースを避ける)。
        """
        executable = normalize_executable_sql(sql)
        if not self._use_oracle_runtime() and not self._catalog.tables:
            return (
                SafetyReport(
                    is_safe=False,
                    is_select_only=is_select_only(sql),
                    row_limit_applied=row_limit or 0,
                    blocked_reason=_SCHEMA_EMPTY_MESSAGE,
                ),
                executable,
                QueryResults(columns=[], rows=[], total=0),
            )
        if analysis is None:
            analysis = self.analyze_sql(executable, allowed, row_limit)
        if not analysis.safety.is_safe:
            return analysis.safety, executable, QueryResults(columns=[], rows=[], total=0)
        if self._use_oracle_runtime():
            return (
                analysis.safety,
                executable,
                self._oracle_adapter.execute_select(executable, row_limit),
            )
        return analysis.safety, executable, self._mock_execute(executable, row_limit)

    def explain_sql(self, sql: str) -> ExplainPlanData:
        """意味 validation を代替しない Oracle performance check。"""

        semantic = parse_oracle_sql(sql)
        if semantic.graph is None:
            return ExplainPlanData(
                available=False,
                warning="SQL AST を解析できないため EXPLAIN PLAN を実行しません。",
            )
        if not self._use_oracle_runtime():
            return ExplainPlanData(
                available=False,
                warning="deterministic runtime では Oracle EXPLAIN PLAN を実行しません。",
            )
        return self._oracle_adapter.explain_select(sql)

    def analyze_sql(
        self,
        sql: str,
        allowed: AllowedObjects,
        row_limit: int | None,
        *,
        use_llm: bool = False,
        catalog: SchemaCatalog | None = None,
    ) -> AnalyzeData:
        semantic = parse_oracle_sql(sql)
        graph = semantic.graph
        current_owner = self._current_schema_owner()
        referenced = []
        alias_to_table: dict[str, str] = {}
        table_name_candidates: dict[str, list[str]] = {}
        if graph:
            for table_ref in graph.tables:
                if table_ref.is_cte:
                    continue
                qualified = qualified_object_name(
                    table_ref.owner.upper() or current_owner,
                    table_ref.name.upper(),
                )
                if qualified not in referenced:
                    referenced.append(qualified)
                table_name_candidates.setdefault(table_ref.name.upper(), []).append(qualified)
                if table_ref.alias:
                    alias_to_table[table_ref.alias.upper()] = qualified
            for name, candidates in table_name_candidates.items():
                unique = sorted(set(candidates))
                if len(unique) == 1:
                    alias_to_table[name] = unique[0]
        referenced_columns: list[str] = []
        if graph:
            for column in graph.columns:
                if column.owner and column.table:
                    table_name = qualified_object_name(column.owner, column.table)
                else:
                    table_name = alias_to_table.get(
                        column.table.upper(),
                        column.table.upper(),
                    )
                if not table_name and len(set(referenced)) == 1:
                    table_name = referenced[0]
                value = f"{table_name}.{column.name.upper()}" if table_name else column.name.upper()
                if value and value not in referenced_columns:
                    referenced_columns.append(value)
        has_wildcard = bool(
            graph and any("*" in projection.expression_sql for projection in graph.projections)
        )
        select_only = graph is not None and is_select_only(sql)
        warnings: list[str] = []
        blocked_reason = ""
        if graph is None:
            blocked_reason = semantic.validation.findings[0].message_ja
        elif not select_only:
            blocked_reason = (
                "SELECT/WITH 以外、複数 statement、または危険語を含む SQL は実行できません。"
            )
        hidden_referenced = _hidden_schema_object_names(referenced, current_owner=current_owner)
        if hidden_referenced:
            blocked_reason = _system_object_blocked_message(hidden_referenced)
        elif not _table_allowed(referenced, allowed, current_owner=current_owner):
            blocked_reason = "許可されていない表を参照しています。"
        elif not _column_allowed(
            referenced_columns,
            has_wildcard,
            referenced,
            allowed,
            current_owner=current_owner,
        ):
            blocked_reason = "許可されていない列を参照しています。"
        if re.search(r"\s+limit\s+\d+\s*;?\s*$", sql, flags=re.IGNORECASE):
            warnings.append("Oracle では LIMIT ではなく FETCH FIRST n ROWS ONLY を使用します。")
        elif row_limit and "fetch first" not in sql.lower():
            warnings.append(f"SQL に行数制限がないため、取得は先頭 {row_limit} 件までになります。")
        if sql.strip().endswith(";") and ";" not in sql.strip().rstrip(";"):
            warnings.append("API 実行時は末尾のセミコロンを除去します。")
        if has_wildcard and allowed.columns:
            warnings.append("列選択が制限されているため、SELECT * は実行できません。")
        safety = SafetyReport(
            is_safe=not blocked_reason,
            is_select_only=select_only,
            row_limit_applied=row_limit or 0,
            blocked_reason=blocked_reason,
            warnings=warnings,
            referenced_tables=referenced,
            referenced_columns=referenced_columns,
        )
        executable_sql = normalize_executable_sql(sql) if select_only else ""
        repaired_sql = self._repair_sql(
            sql=sql,
            safety=safety,
            allowed=allowed,
            referenced_tables=referenced,
            referenced_columns=referenced_columns,
            has_wildcard=has_wildcard,
            catalog=catalog,
        )
        structure = self._sql_structure(sql, referenced)
        optimization_hints = self._optimization_hints(safety=safety, sql=sql, row_limit=row_limit)
        risk_findings = [item for item in [blocked_reason, *warnings, *optimization_hints] if item]
        repair_candidates = [repaired_sql] if repaired_sql else []
        data = AnalyzeData(
            safety=safety,
            explanation=(
                "SQL は参照系クエリとして解析されました。" if safety.is_safe else blocked_reason
            ),
            recommendations=self._recommendations(
                safety,
                repaired_sql,
                sql=sql,
                allowed=allowed,
                catalog=catalog,
            ),
            executable_sql=executable_sql,
            repaired_sql=repaired_sql,
            optimization_hints=optimization_hints,
            structure_summary=structure["summary"],
            risk_level="low" if safety.is_safe else "high",
            statement_type=str(structure["statement_type"]),
            object_names=list(referenced),
            column_names=list(referenced_columns),
            conditions=list(structure["filters"]),
            group_by=list(structure["group_by"]),
            order_by=list(structure["order_by"]),
            risk_findings=risk_findings,
            repair_candidates=repair_candidates,
            operations=structure["operations"],
            filters=structure["filters"],
            joins=structure["joins"],
            aggregations=structure["aggregations"],
        )
        if use_llm:
            return self._enhance_sql_analysis_with_llm(data, sql, allowed, catalog=catalog)
        return data

    def _decode_page_cursor(self, cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            return max(0, int(base64.urlsafe_b64decode(padded).decode("ascii")))
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise ValueError("cursor が不正です。") from exc

    def _encode_page_cursor(self, offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")

    @staticmethod
    def _profile_in_allowed_profile_ids(
        profile_id: str, allowed_profile_ids: set[str] | None
    ) -> bool:
        if allowed_profile_ids is None:
            return True
        return (profile_id or "default") in allowed_profile_ids

    def _history_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        profile_id: str = "",
        status: str = "",
        query: str = "",
        actor_user_uuid: str = "",
        payload_filters: Mapping[str, str] | None = None,
        allowed_profile_ids: set[str] | None = None,
    ) -> tuple[list[HistoryItem], str, int]:
        if allowed_profile_ids is not None and not allowed_profile_ids:
            return [], "", 0
        if profile_id and not self._profile_in_allowed_profile_ids(profile_id, allowed_profile_ids):
            return [], "", 0
        filters = {key: value for key, value in (payload_filters or {}).items() if value}
        if actor_user_uuid:
            filters["actor_user_uuid"] = actor_user_uuid
        repository = self._incremental_repository
        if repository is not None:
            if allowed_profile_ids is not None and not profile_id:
                offset = self._decode_page_cursor(cursor)
                items: list[HistoryItem] = []
                repo_cursor: str | None = ""
                while True:
                    try:
                        documents, repo_cursor, _total = repository.list_documents_page(
                            "history",
                            cursor=repo_cursor or None,
                            limit=500,
                            status=status,
                            query=query,
                            payload_filters=filters or None,
                        )
                    except Exception as exc:
                        self._raise_incremental_repository_failure(
                            operation="history_search",
                            exc=exc,
                            operation_error_code="history_query_failed",
                        )
                    page_items = [HistoryItem.model_validate(document) for document in documents]
                    items.extend(
                        item
                        for item in page_items
                        if self._profile_in_allowed_profile_ids(
                            item.profile_id, allowed_profile_ids
                        )
                    )
                    if not repo_cursor or not documents:
                        break
                total = len(items)
                selected = items[offset : offset + limit]
                next_offset = offset + len(selected)
                return (
                    [item.model_copy(deep=True) for item in selected],
                    self._encode_page_cursor(next_offset) if next_offset < total else "",
                    total,
                )
            try:
                documents, next_cursor, total = repository.list_documents_page(
                    "history",
                    cursor=cursor,
                    limit=limit,
                    profile_id=profile_id,
                    status=status,
                    query=query,
                    payload_filters=filters or None,
                )
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="history_search",
                    exc=exc,
                    operation_error_code="history_query_failed",
                )
            return (
                [HistoryItem.model_validate(document) for document in documents],
                next_cursor or "",
                total,
            )
        offset = self._decode_page_cursor(cursor)
        query_key = query.casefold().strip()
        with self._lock:
            items = [
                item
                for item in reversed(self._history)
                if (not profile_id or item.profile_id == profile_id)
                and (
                    not status
                    or (item.feedback_rating.value if item.feedback_rating else "unrated") == status
                )
                and (
                    not query_key
                    or query_key
                    in f"{item.question} {item.generated_sql} {item.feedback_comment}".casefold()
                )
                and _history_item_matches_payload_filters(item, filters)
                and self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
            ]
        total = len(items)
        selected = items[offset : offset + limit]
        next_offset = offset + len(selected)
        return (
            [item.model_copy(deep=True) for item in selected],
            self._encode_page_cursor(next_offset) if next_offset < total else "",
            total,
        )

    def _history_snapshot(
        self, *, status: str = "", allowed_profile_ids: set[str] | None = None
    ) -> list[HistoryItem]:
        if self._incremental_repository is None:
            with self._lock:
                return [
                    item.model_copy(deep=True)
                    for item in reversed(self._history)
                    if (
                        not status
                        or (item.feedback_rating.value if item.feedback_rating else "unrated")
                        == status
                    )
                    and self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
                ]
        items: list[HistoryItem] = []
        cursor = ""
        while True:
            page, cursor, _total = self._history_page(
                cursor=cursor or None,
                limit=500,
                status=status,
                allowed_profile_ids=allowed_profile_ids,
            )
            items.extend(page)
            if not cursor:
                return items

    def _history_by_id(self, history_id: str) -> HistoryItem | None:
        repository = self._incremental_repository
        if repository is not None:
            document = repository.get_document("history", history_id)
            return HistoryItem.model_validate(document) if document else None
        with self._lock:
            item = next((entry for entry in self._history if entry.id == history_id), None)
            return item.model_copy(deep=True) if item else None

    def _replace_history_cache_locked(self, item: HistoryItem) -> None:
        replaced = False
        updated_history: list[HistoryItem] = []
        for entry in self._history:
            if entry.id == item.id:
                updated_history.append(item)
                replaced = True
            else:
                updated_history.append(entry)
        if not replaced:
            updated_history.append(item)
        self._history = updated_history
        if item.feedback_rating is None:
            self._feedback.pop(item.id, None)
        else:
            self._feedback[item.id] = item.feedback_rating

    def _patch_history_item(
        self,
        current: HistoryItem,
        updates: Mapping[str, Any],
    ) -> HistoryItem:
        preview = current.model_copy(update=dict(updates))
        payload = preview.model_dump(mode="json")
        patch_payload = {key: payload[key] for key in updates if key in payload}
        repository = self._incremental_repository
        if repository is not None:
            try:
                document = repository.patch_document("history", current.id, patch_payload)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="history_patch",
                    exc=exc,
                    operation_error_code="history_save_failed",
                )
            if document is None:
                raise KeyError(current.id)
            updated = HistoryItem.model_validate(document)
            digest = hashlib.sha256(
                json.dumps(
                    updated.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            with self._lock:
                self._replace_history_cache_locked(updated)
                self._incremental_hashes[("history", updated.id)] = digest
            return updated
        with self._lock:
            latest = next((entry for entry in self._history if entry.id == current.id), None)
            if latest is None:
                raise KeyError(current.id)
            updated = latest.model_copy(update=dict(updates))
            self._replace_history_cache_locked(updated)
        self._persist_entities([("history", updated.id, updated.model_dump(mode="json"))])
        return updated

    def _load_feedback_state(self) -> None:
        repository = self._incremental_repository
        if repository is None:
            return
        try:
            config_document = repository.get_document("singletons", "feedback_search_config")
            indexed_document = repository.get_document("singletons", "feedback_indexed_ids")
        except Exception as exc:
            self._raise_incremental_repository_failure(
                operation="feedback_state_load",
                exc=exc,
                operation_error_code="feedback_state_load_failed",
            )

        config_value = config_document.get("value") if config_document else None
        indexed_value = indexed_document.get("value") if indexed_document else None
        similarity_threshold: float | None = None
        match_limit: int | None = None
        indexed_ids: set[str] | None = None
        if isinstance(config_value, Mapping):
            similarity_threshold = float(config_value.get("similarity_threshold", 0.0))
            match_limit = int(config_value.get("match_limit", 3))
        if isinstance(indexed_value, Sequence) and not isinstance(
            indexed_value, (str, bytes, bytearray)
        ):
            indexed_ids = {str(item) for item in indexed_value if str(item)}
        with self._lock:
            if similarity_threshold is not None:
                self._feedback_similarity_threshold = similarity_threshold
            if match_limit is not None:
                self._feedback_match_limit = match_limit
            if indexed_ids is not None:
                self._feedback_indexed_ids = indexed_ids

    def _load_classifier_state(self) -> None:
        repository = self._incremental_repository
        if repository is None:
            return
        now = time.monotonic()
        with self._lock:
            if (
                self._classifier_state_loaded
                and now - self._classifier_state_checked_at < self._cache_token_poll_seconds
            ):
                return
        token = repository.get_change_token(STATE_NAMESPACE)
        with self._lock:
            if self._classifier_state_loaded and token == self._classifier_state_token:
                self._classifier_state_checked_at = now
                return
        documents: list[dict[str, Any]] = []
        cursor = ""
        while True:
            page, cursor_value, _total = repository.list_documents_page(
                "classifier_examples",
                cursor=cursor or None,
                limit=500,
            )
            documents.extend(page)
            cursor = cursor_value or ""
            if not cursor:
                break
        artifact_document = repository.get_document("singletons", "classifier_artifact")
        examples = [ClassifierTrainingExample.model_validate(item) for item in documents]
        artifact_value = artifact_document.get("value") if artifact_document else None
        artifact = dict(artifact_value) if isinstance(artifact_value, dict) else None
        with self._lock:
            self._classifier_examples = examples
            self._classifier_artifact = artifact
            self._classifier_state_loaded = True
            self._classifier_state_token = token
            self._classifier_state_checked_at = now
            self._classifier_model_payload_cache = None

    def list_history(
        self,
        *,
        actor_user_uuid: str = "",
        cursor: str | None = None,
        limit: int = _HISTORY_PAGE_DEFAULT_LIMIT,
    ) -> HistoryData:
        """検索履歴を新しい順に cursor page で返す(actor 制限は呼び出し側が決める)。"""

        page_limit = max(1, min(int(limit), _HISTORY_PAGE_MAX_LIMIT))
        items, next_cursor, total = self._history_page(
            cursor=cursor or None,
            limit=page_limit,
            actor_user_uuid=actor_user_uuid,
        )
        return HistoryData(items=items, next_cursor=next_cursor, total=total)

    def record_ontology_history(
        self,
        *,
        session_id: str,
        question: str,
        rewritten_question: str,
        engine: Nl2SqlEngine,
        generated_sql: str,
        executable_sql: str,
        profile_id: str,
        result: QueryResults,
        ontology_trace_summary: dict[str, Any],
        elapsed_ms: int | None = None,
        actor_user_uuid: str = "",
    ) -> HistoryItem:
        """Query Session 実行を legacy history へ一度だけ投影する。"""

        with self._lock:
            existing = next(
                (item for item in self._history if item.session_id == session_id),
                None,
            )
            if existing is not None:
                return existing.model_copy(deep=True)
            profile = self.get_profile(profile_id)
            item = HistoryItem(
                id=str(uuid.uuid4()),
                question=question,
                engine=engine,
                generated_sql=generated_sql,
                created_at=_utc_now(),
                elapsed_ms=elapsed_ms,
                profile_id=profile.id,
                profile_name=profile.name,
                profile_category=profile.category,
                rewritten_question=rewritten_question,
                executable_sql=executable_sql,
                safety_is_safe=True,
                result_row_count=result.total,
                result_columns=result.columns,
                session_id=session_id,
                actor_user_uuid=actor_user_uuid,
                ontology_trace_summary=dict(ontology_trace_summary),
            )
            self._history.append(item)
            self._prune_history_locked()
        self._persist_entities([("history", item.id, item.model_dump(mode="json"))])
        return item.model_copy(deep=True)

    def save_feedback(
        self,
        history_id: str,
        rating: FeedbackRating,
        comment: str = "",
        *,
        actor_user_uuid: str = "",
        actor_can_manage: bool = False,
    ) -> FeedbackData:
        current = self._history_by_id(history_id)
        if current is None:
            raise KeyError(history_id)
        if (
            not actor_can_manage
            and actor_user_uuid
            and current.actor_user_uuid
            and current.actor_user_uuid != actor_user_uuid
        ):
            raise PermissionError(history_id)
        updated = self._patch_history_item(
            current,
            {
                "feedback_rating": rating,
                "feedback_comment": comment.strip(),
                "feedback_updated_at": _utc_now(),
            },
        )
        return FeedbackData(
            history_id=history_id,
            rating=rating,
            saved=True,
            comment=updated.feedback_comment,
            feedback_content=updated.feedback_comment,
        )

    def save_admin_feedback_review(
        self,
        request: AdminFeedbackReviewRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> AdminFeedbackReviewData:
        current = self._history_by_id(request.history_id)
        if current is None:
            raise KeyError(request.history_id)
        if not self._profile_in_allowed_profile_ids(current.profile_id, allowed_profile_ids):
            raise PermissionError(current.profile_id)
        feedback_content = request.feedback_content.strip()
        updated = self._patch_history_item(
            current,
            {
                "admin_feedback_rating": request.rating,
                "admin_feedback_content": feedback_content,
                "admin_feedback_updated_at": _utc_now(),
            },
        )
        similar_history_publish = self._publish_admin_feedback_to_similar_history(updated)

        select_ai_feedback: SelectAiFeedbackAddData | None = None
        if request.register_select_ai_feedback:
            response = (
                request.select_ai_response.strip()
                or current.executable_sql.strip()
                or current.generated_sql.strip()
            )
            if not response:
                select_ai_feedback = SelectAiFeedbackAddData(
                    runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                    executed=False,
                    status="validation_error",
                    profile_name=request.select_ai_profile_name.strip(),
                    warnings=["Select AI feedback 登録用 response SQL を入力してください。"],
                )
            else:
                select_ai_feedback = self.add_select_ai_feedback(
                    SelectAiFeedbackAddRequest(
                        profile_id=current.profile_id or "default",
                        profile_name=request.select_ai_profile_name.strip(),
                        question=current.question,
                        feedback_type=(
                            "positive" if request.rating == FeedbackRating.GOOD else "negative"
                        ),
                        response=response,
                        feedback_content=feedback_content,
                        generated_sql=current.generated_sql,
                    )
                )

        return AdminFeedbackReviewData(
            history_id=request.history_id,
            rating=request.rating,
            saved=True,
            feedback_content=feedback_content,
            select_ai_feedback=select_ai_feedback,
            similar_history_publish=similar_history_publish,
        )

    def _publish_admin_feedback_to_similar_history(
        self, item: HistoryItem
    ) -> SimilarHistoryPublishData:
        self._load_feedback_state()
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if item.admin_feedback_rating != FeedbackRating.GOOD:
            return self._unpublish_similar_history_entry(item.id, runtime=runtime)
        if not item.safety_is_safe:
            with self._feedback_index_lock:
                with self._lock:
                    self._feedback_indexed_ids.discard(item.id)
                self._persist_singletons("feedback_indexed_ids")
            return SimilarHistoryPublishData(
                history_id=item.id,
                status="skipped",
                runtime=runtime,
                warnings=["安全チェックで unsafe の履歴は類似検索に公開しません。"],
            )
        if not self._use_oracle_runtime():
            return SimilarHistoryPublishData(
                history_id=item.id,
                status="published",
                runtime=runtime,
            )

        settings = get_settings()
        if not settings.nl2sql_feedback_embedding_enabled:
            return SimilarHistoryPublishData(
                history_id=item.id,
                status="warning",
                runtime=runtime,
                table_name=settings.nl2sql_feedback_vector_table,
                index_name=settings.nl2sql_feedback_vector_index,
                warnings=[
                    "NL2SQL_FEEDBACK_EMBEDDING_ENABLED が無効なため、"
                    "Oracle feedback vector table への公開をスキップしました。"
                ],
            )
        if not self._embedding_client.is_configured():
            return SimilarHistoryPublishData(
                history_id=item.id,
                status="warning",
                runtime=runtime,
                table_name=settings.nl2sql_feedback_vector_table,
                index_name=settings.nl2sql_feedback_vector_index,
                warnings=[
                    "OCI GenAI embedding 設定が不足しているため、"
                    "類似検索公開をスキップしました。"
                ],
            )

        try:
            embedding = self._embedding_client.embed_texts([self._feedback_embedding_text(item)])[0]
            with self._feedback_index_lock:
                meta = self._oracle_adapter.upsert_feedback_vector_entry(
                    table_name=settings.nl2sql_feedback_vector_table,
                    index_name=settings.nl2sql_feedback_vector_index,
                    row={
                        "history_id": item.id,
                        "profile_id": item.profile_id,
                        "question": item.question,
                        "generated_sql": item.generated_sql,
                        "feedback_rating": item.admin_feedback_rating.value,
                        "embedding": embedding,
                    },
                )
                with self._lock:
                    self._feedback_indexed_ids.add(item.id)
                self._persist_singletons("feedback_indexed_ids")
        except (EmbeddingClientError, OracleAdapterError, IndexError, ValueError) as exc:
            logger.warning("feedback similar-history publish warning: %s", exc)
            return SimilarHistoryPublishData(
                history_id=item.id,
                status="warning",
                runtime=runtime,
                table_name=settings.nl2sql_feedback_vector_table,
                index_name=settings.nl2sql_feedback_vector_index,
                warnings=[str(exc)],
            )
        return SimilarHistoryPublishData(
            history_id=item.id,
            status="published",
            runtime=str(meta.get("runtime") or runtime),
            executed=bool(meta.get("executed", True)),
            table_name=str(meta.get("table_name") or settings.nl2sql_feedback_vector_table),
            index_name=str(meta.get("index_name") or settings.nl2sql_feedback_vector_index),
        )

    def _unpublish_similar_history_entry(
        self, history_id: str, *, runtime: str
    ) -> SimilarHistoryPublishData:
        self._load_feedback_state()
        settings = get_settings()
        warnings: list[str] = []
        executed = False
        with self._feedback_index_lock:
            if self._use_oracle_runtime():
                try:
                    meta = self._oracle_adapter.delete_feedback_vector_entry(
                        table_name=settings.nl2sql_feedback_vector_table,
                        history_id=history_id,
                    )
                    executed = bool(meta.get("executed", True))
                except OracleAdapterError as exc:
                    logger.warning("feedback similar-history unpublish warning: %s", exc)
                    warnings.append(str(exc))
            with self._lock:
                self._feedback_indexed_ids.discard(history_id)
            self._persist_singletons("feedback_indexed_ids")
        return SimilarHistoryPublishData(
            history_id=history_id,
            status="warning" if warnings else "unpublished",
            runtime=runtime,
            executed=executed,
            table_name=settings.nl2sql_feedback_vector_table,
            index_name=settings.nl2sql_feedback_vector_index,
            warnings=warnings,
        )

    def clear_feedback(
        self,
        history_id: str,
        *,
        actor_user_uuid: str = "",
        actor_can_manage: bool = False,
    ) -> FeedbackClearData:
        current = self._history_by_id(history_id)
        if current is None:
            raise KeyError(history_id)
        if (
            not actor_can_manage
            and actor_user_uuid
            and current.actor_user_uuid
            and current.actor_user_uuid != actor_user_uuid
        ):
            raise PermissionError(history_id)
        self._patch_history_item(
            current,
            {
                "feedback_rating": None,
                "feedback_comment": "",
                "feedback_updated_at": _utc_now(),
            },
        )
        return FeedbackClearData(history_id=history_id)

    def list_feedback(
        self,
        *,
        cursor: str | None,
        limit: int,
        rating: str,
        profile_id: str,
        query: str,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackListData:
        self._load_classifier_state()
        status = rating if rating in {"good", "bad", "unrated"} else ""
        items, next_cursor, total = self._history_page(
            cursor=cursor,
            limit=limit,
            profile_id=profile_id,
            status=status,
            query=query,
            allowed_profile_ids=allowed_profile_ids,
        )
        records: list[FeedbackRecord] = []
        for item in items:
            candidate = self._classifier_candidate_from_history(item)
            records.append(
                FeedbackRecord(
                    **item.model_dump(mode="json"),
                    training_status=candidate.status if candidate else "",
                    training_example_id=candidate.training_example_id if candidate else "",
                )
            )
        return FeedbackListData(
            items=records,
            total=total,
            next_cursor=next_cursor,
        )

    def _normalize_classifier_question(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        return " ".join(normalized.split()).casefold()

    def _classifier_candidate_from_history(
        self, history: HistoryItem
    ) -> ClassifierTrainingCandidate | None:
        with self._lock:
            examples = list(self._classifier_examples)
        linked = next(
            (item for item in examples if item.source_history_id == history.id),
            None,
        )
        if linked is not None:
            source_matches = (
                history.feedback_rating == FeedbackRating.GOOD
                and bool(history.profile_id)
                and linked.profile_id == history.profile_id
                and self._normalize_classifier_question(linked.text)
                == self._normalize_classifier_question(history.question)
            )
            return ClassifierTrainingCandidate(
                history_id=history.id,
                question=history.question,
                profile_id=history.profile_id,
                profile_name=history.profile_name,
                profile_category=history.profile_category,
                feedback_rating=history.feedback_rating,
                feedback_comment=history.feedback_comment,
                created_at=history.feedback_updated_at or history.created_at,
                status="added" if source_matches else "source_changed",
                eligible=False,
                training_example_id=linked.id,
            )
        if history.feedback_rating != FeedbackRating.GOOD:
            return None
        profile = next(
            (
                item
                for item in self.list_profiles(include_archived=False)
                if item.id == history.profile_id
            ),
            None,
        )
        if profile is None:
            return ClassifierTrainingCandidate(
                history_id=history.id,
                question=history.question,
                profile_id=history.profile_id,
                profile_name=history.profile_name,
                profile_category=history.profile_category,
                feedback_rating=history.feedback_rating,
                feedback_comment=history.feedback_comment,
                created_at=history.feedback_updated_at or history.created_at,
                status="profile_missing",
            )
        normalized_question = self._normalize_classifier_question(history.question)
        matching = [
            item
            for item in examples
            if self._normalize_classifier_question(item.text) == normalized_question
        ]
        if any(item.profile_id == profile.id for item in matching):
            covered = next(item for item in matching if item.profile_id == profile.id)
            return ClassifierTrainingCandidate(
                history_id=history.id,
                question=history.question,
                profile_id=profile.id,
                profile_name=profile.name,
                profile_category=profile.category,
                feedback_rating=history.feedback_rating,
                feedback_comment=history.feedback_comment,
                created_at=history.feedback_updated_at or history.created_at,
                status="already_covered",
                training_example_id=covered.id,
            )
        conflicts = sorted({item.profile_id for item in matching if item.profile_id})
        if conflicts:
            return ClassifierTrainingCandidate(
                history_id=history.id,
                question=history.question,
                profile_id=profile.id,
                profile_name=profile.name,
                profile_category=profile.category,
                feedback_rating=history.feedback_rating,
                feedback_comment=history.feedback_comment,
                created_at=history.feedback_updated_at or history.created_at,
                status="conflict",
                conflict_profile_ids=conflicts,
            )
        return ClassifierTrainingCandidate(
            history_id=history.id,
            question=history.question,
            profile_id=profile.id,
            profile_name=profile.name,
            profile_category=profile.category,
            feedback_rating=history.feedback_rating,
            feedback_comment=history.feedback_comment,
            created_at=history.feedback_updated_at or history.created_at,
            status="pending",
            eligible=True,
        )

    def seed_demo_learning_data(self) -> DemoLearningData:
        """Legacy endpoint kept without inserting fixed business data."""
        return DemoLearningData(
            seeded_history_count=0,
            seeded_feedback_count=0,
            history_ids=[],
            profile_ids=[],
            message=(
                "固定 demo 学習データは投入しません。Data Tools の sample data を"
                "明示 import してください。"
            ),
        )

    def feedback_index_status(
        self, *, allowed_profile_ids: set[str] | None = None
    ) -> FeedbackIndexData:
        return self._feedback_index_data(
            operation="status",
            include_bad=False,
            allowed_profile_ids=allowed_profile_ids,
        )

    def rebuild_feedback_index(
        self,
        request: FeedbackIndexRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackIndexData:
        del request
        return self._feedback_index_data(
            operation="rebuild",
            include_bad=False,
            allowed_profile_ids=allowed_profile_ids,
        )

    def clear_feedback_index(
        self,
        request: FeedbackIndexRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackIndexData:
        del request
        self._load_feedback_state()
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        executed = False
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        source_count = self._feedback_source_history_count(allowed_profile_ids=allowed_profile_ids)
        indexable_count = len(
            self._feedback_indexable_history(False, allowed_profile_ids=allowed_profile_ids)
        )
        scoped_history_ids: set[str] | None = None
        if allowed_profile_ids is not None:
            scoped_history_ids = {
                item.id for item in self._history_snapshot(allowed_profile_ids=allowed_profile_ids)
            }
        with self._lock:
            current_indexed = (
                len(self._feedback_indexed_ids)
                if scoped_history_ids is None
                else len(self._feedback_indexed_ids & scoped_history_ids)
            )
        if not self._use_oracle_runtime():
            warnings.append(
                "Feedback vector index の clear 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"
            )
        else:
            with self._feedback_index_lock:
                try:
                    settings = get_settings()
                    if scoped_history_ids is None:
                        self._oracle_adapter.clear_feedback_vector_index(
                            table_name=settings.nl2sql_feedback_vector_table,
                            index_name=settings.nl2sql_feedback_vector_index,
                        )
                        with self._lock:
                            self._feedback_indexed_ids = set()
                    else:
                        with self._lock:
                            ids_to_delete = sorted(self._feedback_indexed_ids & scoped_history_ids)
                        for history_id in ids_to_delete:
                            self._oracle_adapter.delete_feedback_vector_entry(
                                table_name=settings.nl2sql_feedback_vector_table,
                                history_id=history_id,
                            )
                        with self._lock:
                            self._feedback_indexed_ids.difference_update(scoped_history_ids)
                    with self._lock:
                        current_indexed = (
                            len(self._feedback_indexed_ids)
                            if scoped_history_ids is None
                            else len(self._feedback_indexed_ids & scoped_history_ids)
                        )
                    executed = True
                    self._persist_singletons("feedback_indexed_ids")
                except OracleAdapterError as exc:
                    warnings.append(str(exc))
        embedding_configured = self._embedding_client.is_configured()
        settings = get_settings()
        return FeedbackIndexData(
            operation="clear",
            status=self._feedback_index_status(current_indexed, indexable_count),
            executed=executed,
            runtime=runtime,
            source_history_count=source_count,
            indexable_count=indexable_count,
            indexed_count=current_indexed,
            ddl=self._feedback_index_ddl(),
            embedding_model=settings.oci_genai_embed_model_id,
            embedding_configured=embedding_configured,
            warnings=warnings,
            timing=self._timing(created_at, started, "feedback_index"),
        )

    def similar_history(
        self,
        request: SimilarHistoryRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> SimilarHistoryData:
        ranked = self._similar_history_candidates(
            question=request.question,
            profile_id=request.profile_id,
            allowed_profile_ids=allowed_profile_ids,
            include_bad=False,
        )
        limit = request.limit or self._feedback_match_limit
        threshold = self._feedback_similarity_threshold
        filtered = [item for item in ranked if item.score >= threshold]
        return SimilarHistoryData(items=filtered[:limit])

    def list_feedback_entries(
        self,
        *,
        warnings: Sequence[str] | None = None,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackEntriesData:
        self._load_feedback_state()
        history = self._history_snapshot(allowed_profile_ids=allowed_profile_ids)
        with self._lock:
            items = [
                FeedbackVectorEntry(
                    history_id=item.id,
                    question=item.question,
                    generated_sql=item.generated_sql,
                    profile_id=item.profile_id,
                    profile_name=item.profile_name,
                    profile_category=item.profile_category,
                    feedback_rating=item.feedback_rating,
                    feedback_comment=item.feedback_comment,
                    admin_feedback_rating=item.admin_feedback_rating,
                    admin_feedback_content=item.admin_feedback_content,
                    admin_feedback_updated_at=item.admin_feedback_updated_at,
                    indexed=item.id in self._feedback_indexed_ids,
                    created_at=item.created_at,
                )
                for item in history
            ]
            indexed_count = sum(1 for item in items if item.indexed)
        return FeedbackEntriesData(
            items=items,
            total=len(items),
            indexed_count=indexed_count,
            warnings=list(warnings or []),
        )

    def delete_feedback_entries(
        self,
        history_ids: list[str],
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackEntriesData:
        self._load_feedback_state()
        self._load_classifier_state()
        ids = {item.strip() for item in history_ids if item.strip()}
        if not ids:
            return self.list_feedback_entries(allowed_profile_ids=allowed_profile_ids)
        denied = [
            item_id
            for item_id in ids
            if (current := self._history_by_id(item_id)) is not None
            and not self._profile_in_allowed_profile_ids(current.profile_id, allowed_profile_ids)
        ]
        if denied:
            raise PermissionError(denied[0])
        warnings: list[str] = []
        if self._use_oracle_runtime():
            settings = get_settings()
            for item_id in sorted(ids):
                try:
                    self._oracle_adapter.delete_feedback_vector_entry(
                        table_name=settings.nl2sql_feedback_vector_table,
                        history_id=item_id,
                    )
                except OracleAdapterError as exc:
                    logger.warning("feedback vector delete warning: %s", exc)
                    warnings.append(str(exc))
        with self._lock:
            classifier_example_ids = [
                item.id for item in self._classifier_examples if item.source_history_id in ids
            ]
        if self._incremental_repository is not None:
            try:
                for item_id in ids:
                    self._incremental_repository.delete_document("history", item_id)
                    self._incremental_hashes.pop(("history", item_id), None)
                for example_id in classifier_example_ids:
                    self._incremental_repository.delete_document(
                        "classifier_examples",
                        example_id,
                    )
                    self._incremental_hashes.pop(("classifier_examples", example_id), None)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="feedback_delete",
                    exc=exc,
                    operation_error_code="feedback_delete_failed",
                )
        with self._lock:
            self._history = [item for item in self._history if item.id not in ids]
            for item_id in ids:
                self._feedback.pop(item_id, None)
            self._feedback_indexed_ids.difference_update(ids)
            if classifier_example_ids:
                self._classifier_examples = [
                    item
                    for item in self._classifier_examples
                    if item.id not in classifier_example_ids
                ]
                self._classifier_model_payload_cache = None
        if self._incremental_repository is not None:
            self._persist_singletons("feedback_indexed_ids")
        else:
            self._persist_state()
        return self.list_feedback_entries(
            warnings=warnings,
            allowed_profile_ids=allowed_profile_ids,
        )

    def feedback_search_config(self) -> FeedbackSearchConfigData:
        self._load_feedback_state()
        with self._lock:
            return FeedbackSearchConfigData(
                similarity_threshold=self._feedback_similarity_threshold,
                match_limit=self._feedback_match_limit,
            )

    def update_feedback_search_config(
        self, request: FeedbackSearchConfigRequest
    ) -> FeedbackSearchConfigData:
        self._load_feedback_state()
        with self._lock:
            if request.similarity_threshold is not None:
                self._feedback_similarity_threshold = request.similarity_threshold
            if request.match_limit is not None:
                self._feedback_match_limit = request.match_limit
        self._persist_singletons("feedback_search_config")
        return self.feedback_search_config()

    def classifier_status(
        self, *, allowed_profile_ids: set[str] | None = None
    ) -> ClassifierStatusData:
        self._load_classifier_state()
        with self._lock:
            artifact = dict(self._classifier_artifact or {})
            examples = [
                item
                for item in self._classifier_examples
                if self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
            ]
        categories = sorted({self._classifier_training_label(item) for item in examples})
        warnings: list[str] = []
        artifact_payload: dict[str, Any] | None = None
        if artifact.get("model_base64"):
            try:
                artifact_payload = self._cached_classifier_model_payload_from_artifact(artifact)
            except ValueError as exc:
                warnings.append(f"{exc} classifier は deterministic recommendation に縮退します。")
        ready = artifact_payload is not None
        current_fingerprint = self._classifier_training_fingerprint(examples)
        trained_fingerprint = str(artifact.get("training_data_fingerprint") or "")
        stale = bool(ready and trained_fingerprint != current_fingerprint)
        metrics_warnings: list[str] = []
        metrics = self._sanitize_classifier_metrics(
            artifact.get("metrics"),
            metrics_warnings,
        )
        trained_example_count = self._classifier_metric_count(
            metrics,
            "training_examples",
            default=0,
            warnings=metrics_warnings,
        )
        source_example_count = self._classifier_metric_count(
            metrics,
            "source_example_count",
            default=trained_example_count,
            warnings=metrics_warnings,
        )
        pending_change_count = max(1, abs(len(examples) - source_example_count)) if stale else 0
        warnings.extend(metrics_warnings)
        if not examples:
            warnings.append("分類器の training data が未登録です。")
        if not ready:
            warnings.append("LogisticRegression classifier は未学習です。")
        if stale:
            warnings.append(
                "Training data に未学習の変更があります。現在のモデルは継続利用中です。"
            )
        if ready and metrics.get("embedding_fallback"):
            warnings.append(
                "分類器は deterministic fallback embedding で学習されています。"
                "OCI GenAI embedding とは併用できません。"
            )
        raw_vector_dimension = (
            artifact_payload.get("feature_dim", _CLASSIFIER_VECTOR_DIMENSION)
            if artifact_payload
            else artifact.get("vector_dimension") or _CLASSIFIER_VECTOR_DIMENSION
        )
        try:
            vector_dimension = int(raw_vector_dimension)
        except (TypeError, ValueError):
            vector_dimension = _CLASSIFIER_VECTOR_DIMENSION
            warnings.append(
                "classifier artifact の vector_dimension が不正なため既定値にしました。"
            )
        return ClassifierStatusData(
            ready=ready,
            trained=ready,
            stale=stale,
            classifier_version=str(artifact.get("version") or ""),
            updated_at=str(artifact.get("updated_at") or ""),
            example_count=len(examples),
            category_count=len(categories),
            categories=categories,
            embedding_model=str(
                artifact.get("embedding_model")
                or get_settings().oci_genai_embed_model_id
                or "deterministic-hash-1536"
            ),
            vector_dimension=vector_dimension,
            persistence_mode=self._store.mode,
            recommendation_source="classifier" if ready else "deterministic",
            metrics=metrics,
            trained_example_count=trained_example_count,
            pending_change_count=pending_change_count,
            warnings=warnings,
        )

    def import_classifier_training_data(
        self,
        *,
        filename: str,
        content: bytes,
        replace: bool = False,
        profile_id: str | None = None,
        allowed_profile_ids: set[str] | None = None,
    ) -> ClassifierImportData:
        _require_xlsx_template_upload(filename)
        with self._classifier_import_lock:
            self._load_classifier_state()
            warnings: list[str] = []
            parsed, skipped = self._parse_classifier_training_file(filename, content, warnings)
            if not parsed:
                detail = (
                    warnings[-1]
                    if warnings
                    else "classifier training data に有効な行がありません。"
                )
                raise ValueError(detail)

            now = _utc_now()
            examples: list[ClassifierTrainingExample] = []
            with self._lock:
                current_examples = list(self._classifier_examples)
            preserved_examples = (
                [
                    item
                    for item in current_examples
                    if not self._profile_in_allowed_profile_ids(
                        item.profile_id, allowed_profile_ids
                    )
                ]
                if replace and allowed_profile_ids is not None
                else []
            )
            comparison_examples = preserved_examples if replace else current_examples
            for category, text, row_profile_id in parsed:
                resolved = self._exact_profile_for_classifier_label(
                    profile_id or row_profile_id or category
                )
                if resolved is None:
                    skipped += 1
                    warnings.append(
                        f"{category or row_profile_id} に対応する Profile を一意に解決できないため"
                        "除外しました。"
                    )
                    continue
                if allowed_profile_ids is not None and resolved.id not in allowed_profile_ids:
                    skipped += 1
                    warnings.append(
                        f"{resolved.name} は利用権限がない Profile のため "
                        "training data から除外しました。"
                    )
                    continue
                normalized_question = self._normalize_classifier_question(text)
                matching = [
                    item
                    for item in [*comparison_examples, *examples]
                    if self._normalize_classifier_question(item.text) == normalized_question
                ]
                if any(item.profile_id == resolved.id for item in matching):
                    skipped += 1
                    warnings.append(
                        f"{text} は同じ Profile の training data に既に存在するため除外しました。"
                    )
                    continue
                if any(item.profile_id and item.profile_id != resolved.id for item in matching):
                    skipped += 1
                    warnings.append(
                        f"{text} は別の Profile に対応済みのため競合として除外しました。"
                    )
                    continue
                examples.append(
                    ClassifierTrainingExample(
                        id=str(uuid.uuid4()),
                        category=category or resolved.category or resolved.name,
                        text=text,
                        profile_id=resolved.id,
                        profile_name=resolved.name,
                        profile_category=resolved.category,
                        source=filename,
                        source_type="file",
                        created_at=now,
                        updated_at=now,
                    )
                )

            if replace and not examples:
                detail = (
                    warnings[-1]
                    if warnings
                    else "有効な training data がないため置換を中止しました。"
                )
                raise ValueError(detail)

            new_examples = [*preserved_examples, *examples] if replace else examples
            documents = [
                ("classifier_examples", item.id, item.model_dump(mode="json"))
                for item in new_examples
            ]
            if replace:
                if self._incremental_repository is not None:
                    self._replace_incremental_entity_collection(
                        "classifier_examples",
                        documents,
                    )
                with self._lock:
                    previous_examples = list(self._classifier_examples)
                    self._classifier_examples = new_examples
                if self._incremental_repository is None:
                    try:
                        self._persist_state(collections=("classifier_examples",))
                    except Exception:
                        with self._lock:
                            self._classifier_examples = previous_examples
                        raise
            else:
                if examples and self._incremental_repository is not None:
                    self._persist_entities(documents)
                with self._lock:
                    self._classifier_examples.extend(examples)
                if examples and self._incremental_repository is None:
                    self._persist_state(collections=("classifier_examples",))

            with self._lock:
                total_examples = len(self._classifier_examples)
                all_categories = sorted({item.category for item in self._classifier_examples})
            return ClassifierImportData(
                imported_count=len(examples),
                skipped_count=skipped,
                total_examples=total_examples,
                categories=all_categories,
                warnings=warnings,
                examples=examples[:50],
            )

    def classifier_training_data(
        self, *, allowed_profile_ids: set[str] | None = None
    ) -> ClassifierTrainingDataData:
        self._load_classifier_state()
        with self._lock:
            examples = [
                item
                for item in self._classifier_examples
                if self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
            ]
        categories = sorted({item.category for item in examples})
        warnings = [] if examples else ["分類器の training data が未登録です。"]
        return ClassifierTrainingDataData(
            total_examples=len(examples),
            categories=categories,
            warnings=warnings,
            examples=examples,
        )

    def classifier_training_candidates(
        self,
        *,
        cursor: str | None,
        limit: int,
        status: str,
        profile_id: str,
        query: str,
        history_id: str = "",
        allowed_profile_ids: set[str] | None = None,
    ) -> ClassifierTrainingCandidatesData:
        self._load_classifier_state()
        candidates = [
            candidate
            for history in self._history_snapshot(allowed_profile_ids=allowed_profile_ids)
            if (candidate := self._classifier_candidate_from_history(history)) is not None
        ]
        candidates.sort(key=lambda item: (item.created_at, item.history_id), reverse=True)
        pending_count = sum(item.status == "pending" for item in candidates)
        added_count = sum(item.status in {"added", "already_covered"} for item in candidates)
        attention_count = sum(
            item.status in {"conflict", "profile_missing", "source_changed"} for item in candidates
        )
        query_key = query.casefold().strip()
        filtered = [
            item
            for item in candidates
            if (not status or status == "all" or item.status == status)
            and (not profile_id or item.profile_id == profile_id)
            and (not history_id or item.history_id == history_id)
            and (
                not query_key
                or query_key
                in (
                    f"{item.question} {item.profile_name} "
                    f"{item.profile_category} {item.feedback_comment}"
                ).casefold()
            )
        ]
        offset = self._decode_page_cursor(cursor)
        selected = filtered[offset : offset + limit]
        next_offset = offset + len(selected)
        return ClassifierTrainingCandidatesData(
            items=selected,
            total=len(filtered),
            next_cursor=(
                self._encode_page_cursor(next_offset) if next_offset < len(filtered) else ""
            ),
            pending_count=pending_count,
            added_count=added_count,
            attention_count=attention_count,
        )

    def import_classifier_feedback_examples(
        self, request: ClassifierFeedbackImportRequest
    ) -> ClassifierFeedbackImportData:
        self._load_classifier_state()
        imported: list[ClassifierTrainingExample] = []
        results: list[ClassifierFeedbackImportResult] = []
        for selection in request.items:
            history = self._history_by_id(selection.history_id)
            if history is None:
                results.append(
                    ClassifierFeedbackImportResult(
                        history_id=selection.history_id,
                        status="not_found",
                        message="対象の SQL 履歴が見つかりません。",
                    )
                )
                continue
            if history.feedback_rating != FeedbackRating.GOOD:
                results.append(
                    ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="source_changed",
                        profile_id=history.profile_id,
                        message="good feedback ではないため training data に追加できません。",
                    )
                )
                continue
            target_profile_id = selection.profile_id.strip() or history.profile_id
            profile = self._exact_profile_for_classifier_label(target_profile_id)
            if profile is None:
                results.append(
                    ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="profile_missing",
                        profile_id=target_profile_id,
                        message="指定された Profile が存在しないか archived です。",
                    )
                )
                continue
            normalized_question = self._normalize_classifier_question(history.question)
            with self._lock:
                linked = next(
                    (
                        item
                        for item in self._classifier_examples
                        if item.source_history_id == history.id
                    ),
                    None,
                )
                matching = [
                    item
                    for item in self._classifier_examples
                    if self._normalize_classifier_question(item.text) == normalized_question
                ]
                if linked is not None:
                    result = ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="added",
                        training_example_id=linked.id,
                        profile_id=linked.profile_id,
                        message="この feedback は既に training data に追加済みです。",
                    )
                elif any(item.profile_id == profile.id for item in matching):
                    existing = next(item for item in matching if item.profile_id == profile.id)
                    result = ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="already_covered",
                        training_example_id=existing.id,
                        profile_id=existing.profile_id,
                        message="同じ質問と Profile の training data が既に存在します。",
                    )
                elif any(item.profile_id and item.profile_id != profile.id for item in matching):
                    result = ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="conflict",
                        profile_id=profile.id,
                        message="同じ質問が別の Profile に対応付けられています。",
                    )
                else:
                    now = _utc_now()
                    example = ClassifierTrainingExample(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"nl2sql-feedback:{history.id}")),
                        category=profile.category or profile.name,
                        text=history.question.strip(),
                        profile_id=profile.id,
                        profile_name=profile.name,
                        profile_category=profile.category,
                        source="feedback",
                        source_type="feedback",
                        source_history_id=history.id,
                        created_at=now,
                        updated_at=now,
                    )
                    self._classifier_examples.append(example)
                    imported.append(example)
                    result = ClassifierFeedbackImportResult(
                        history_id=history.id,
                        status="added",
                        training_example_id=example.id,
                        profile_id=profile.id,
                        message="training data に追加しました。",
                    )
            results.append(result)
        if imported:
            self._persist_entities(
                [
                    ("classifier_examples", item.id, item.model_dump(mode="json"))
                    for item in imported
                ]
            )
        status_data = self.classifier_status()
        return ClassifierFeedbackImportData(
            imported_count=len(imported),
            skipped_count=len(results) - len(imported),
            total_examples=status_data.example_count,
            stale=status_data.stale,
            results=results,
        )

    def update_classifier_training_example(
        self,
        example_id: str,
        request: ClassifierTrainingExampleUpdateRequest,
    ) -> ClassifierTrainingExample:
        self._load_classifier_state()
        profile = self._exact_profile_for_classifier_label(request.profile_id)
        if profile is None:
            raise ValueError("指定された Profile が存在しないか archived です。")
        normalized_question = self._normalize_classifier_question(request.text)
        with self._lock:
            current = next(
                (item for item in self._classifier_examples if item.id == example_id),
                None,
            )
            if current is None:
                raise KeyError(example_id)
            conflict = next(
                (
                    item
                    for item in self._classifier_examples
                    if item.id != example_id
                    and self._normalize_classifier_question(item.text) == normalized_question
                    and item.profile_id != profile.id
                ),
                None,
            )
            if conflict is not None:
                raise ValueError("同じ質問が別の Profile に対応付けられています。")
            updated = current.model_copy(
                update={
                    "category": profile.category or profile.name,
                    "text": request.text.strip(),
                    "profile_id": profile.id,
                    "profile_name": profile.name,
                    "profile_category": profile.category,
                    "updated_at": _utc_now(),
                }
            )
            self._classifier_examples = [
                updated if item.id == example_id else item for item in self._classifier_examples
            ]
        self._persist_entities(
            [("classifier_examples", updated.id, updated.model_dump(mode="json"))]
        )
        return updated

    def delete_classifier_training_example(self, example_id: str) -> ClassifierTrainingDataData:
        self._load_classifier_state()
        with self._lock:
            if not any(item.id == example_id for item in self._classifier_examples):
                raise KeyError(example_id)
            self._classifier_examples = [
                item for item in self._classifier_examples if item.id != example_id
            ]
        if self._incremental_repository is not None:
            self._incremental_repository.delete_document("classifier_examples", example_id)
            self._incremental_hashes.pop(("classifier_examples", example_id), None)
        else:
            self._persist_state(collections=("classifier_examples",))
        return self.classifier_training_data()

    def _classifier_training_label(self, example: ClassifierTrainingExample) -> str:
        return example.profile_id or example.category

    def _classifier_training_fingerprint(
        self, examples: Sequence[ClassifierTrainingExample]
    ) -> str:
        rows = sorted(
            (
                item.id,
                self._normalize_classifier_question(item.text),
                self._classifier_training_label(item),
            )
            for item in examples
        )
        return hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _sanitize_classifier_metrics(
        self,
        value: Any,
        warnings: list[str] | None = None,
    ) -> dict[str, float | int | str]:
        if not isinstance(value, Mapping):
            return {}
        metrics: dict[str, float | int | str] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key).strip()
            if not key:
                continue
            if isinstance(raw_value, int):
                metrics[key] = raw_value
            elif isinstance(raw_value, float):
                if math.isfinite(raw_value):
                    metrics[key] = raw_value
                elif warnings is not None:
                    warnings.append(f"metrics.{key} は finite でないため除外しました。")
            elif isinstance(raw_value, str):
                metrics[key] = raw_value
            elif warnings is not None:
                warnings.append(f"metrics.{key} は scalar 値ではないため除外しました。")
        return metrics

    def _classifier_metric_count(
        self,
        metrics: dict[str, float | int | str],
        key: str,
        *,
        default: int,
        warnings: list[str],
        reject_invalid: bool = False,
    ) -> int:
        if key not in metrics:
            return default
        raw_value = metrics[key]
        try:
            if isinstance(raw_value, bool):
                raise ValueError
            if isinstance(raw_value, int):
                value = raw_value
            elif isinstance(raw_value, float):
                if not math.isfinite(raw_value) or not raw_value.is_integer():
                    raise ValueError
                value = int(raw_value)
            elif isinstance(raw_value, str):
                text = raw_value.strip()
                if not re.fullmatch(r"[+-]?\d+", text):
                    raise ValueError
                value = int(text)
            else:
                raise ValueError
            if value < 0:
                raise ValueError
        except ValueError as exc:
            if reject_invalid:
                raise ValueError(f"metrics.{key} は 0 以上の整数で指定してください。") from exc
            warnings.append(f"metrics.{key} が不正なため欠落扱いにしました。")
            metrics.pop(key, None)
            return default
        metrics[key] = value
        return value

    def _classifier_float_matrix(self, value: Any, field_name: str) -> list[list[float]]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{field_name} は数値配列の配列で指定してください。")
        rows: list[list[float]] = []
        for row in value:
            if hasattr(row, "tolist"):
                row = row.tolist()
            if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
                raise ValueError(f"{field_name} は数値配列の配列で指定してください。")
            vector: list[float] = []
            for item in row:
                try:
                    number = float(item)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{field_name} は数値のみ指定してください。") from exc
                if not math.isfinite(number):
                    raise ValueError(f"{field_name} に finite でない数値が含まれています。")
                vector.append(number)
            rows.append(vector)
        if not rows:
            raise ValueError(f"{field_name} が空です。")
        return rows

    def _classifier_float_vector(self, value: Any, field_name: str) -> list[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{field_name} は数値配列で指定してください。")
        vector: list[float] = []
        for item in value:
            try:
                number = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} は数値のみ指定してください。") from exc
            if not math.isfinite(number):
                raise ValueError(f"{field_name} に finite でない数値が含まれています。")
            vector.append(number)
        if not vector:
            raise ValueError(f"{field_name} が空です。")
        return vector

    def _classifier_model_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        model_format = str(
            payload.get("format") or payload.get("model_format") or _CLASSIFIER_MODEL_FORMAT
        )
        if model_format != _CLASSIFIER_MODEL_FORMAT:
            raise ValueError(f"unsupported classifier model format: {model_format or 'unknown'}")
        raw_classes = payload.get("classes", payload.get("classes_"))
        if hasattr(raw_classes, "tolist"):
            raw_classes = raw_classes.tolist()
        if not isinstance(raw_classes, Sequence) or isinstance(
            raw_classes, (str, bytes, bytearray)
        ):
            raise ValueError("classes は文字列配列で指定してください。")
        classes = [str(item).strip() for item in raw_classes if str(item).strip()]
        if len(classes) < 2:
            raise ValueError("classes は 2 件以上必要です。")
        if len(set(classes)) != len(classes):
            raise ValueError("classes に重複があります。")

        coef_value = payload.get("coef", payload.get("coef_"))
        intercept_value = payload.get("intercept", payload.get("intercept_"))
        coef = self._classifier_float_matrix(coef_value, "coef")
        intercept = self._classifier_float_vector(intercept_value, "intercept")
        try:
            feature_dim = int(
                payload.get("feature_dim") or payload.get("vector_dimension") or len(coef[0])
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("feature_dim は整数で指定してください。") from exc
        if feature_dim != _CLASSIFIER_VECTOR_DIMENSION:
            raise ValueError(
                f"feature_dim は {_CLASSIFIER_VECTOR_DIMENSION} である必要があります。"
            )
        if any(len(row) != feature_dim for row in coef):
            raise ValueError(f"coef の列数は {feature_dim} で統一してください。")
        if len(classes) == 2:
            if len(coef) not in {1, 2}:
                raise ValueError("2 class model の coef 行数は 1 または 2 で指定してください。")
        elif len(coef) != len(classes):
            raise ValueError("multi-class model の coef 行数は classes 件数と一致させてください。")
        if len(intercept) != len(coef):
            raise ValueError("intercept 件数は coef 行数と一致させてください。")

        normalized = {
            "format": _CLASSIFIER_MODEL_FORMAT,
            "classes": classes,
            "coef": coef,
            "intercept": intercept,
            "feature_dim": feature_dim,
        }
        self._classifier_probabilities(normalized, [0.0] * feature_dim)
        return normalized

    def _classifier_model_payload_from_estimator(self, model: Any) -> dict[str, Any]:
        return self._classifier_model_payload(
            {
                "classes": getattr(model, "classes_", []),
                "coef": getattr(model, "coef_", []),
                "intercept": getattr(model, "intercept_", []),
                "feature_dim": _CLASSIFIER_VECTOR_DIMENSION,
            }
        )

    def _classifier_model_payload_from_import(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        model = payload.get("model")
        if isinstance(model, Mapping):
            return self._classifier_model_payload(model)
        if any(
            key in payload
            for key in ("classes", "classes_", "coef", "coef_", "intercept", "intercept_")
        ):
            return self._classifier_model_payload(payload)

        raw_base64 = str(payload.get("model_base64") or "")
        if not raw_base64:
            raise ValueError("coef / intercept / classes を含む JSON artifact を指定してください。")
        try:
            raw = base64.b64decode(raw_base64, validate=True)
            decoded = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("model_base64 は安全な JSON model payload ではありません。") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("model_base64 は JSON object である必要があります。")
        return self._classifier_model_payload(decoded)

    def _classifier_model_payload_from_artifact(
        self, artifact: Mapping[str, Any]
    ) -> dict[str, Any]:
        raw_base64 = str(artifact.get("model_base64") or "")
        if not raw_base64:
            raise ValueError("model_base64 がありません。")
        try:
            raw = base64.b64decode(raw_base64, validate=True)
            decoded = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "保存済み classifier artifact は安全な JSON 形式ではありません。"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("保存済み classifier artifact の model payload が不正です。")
        return self._classifier_model_payload(decoded)

    def _cached_classifier_model_payload_from_artifact(
        self, artifact: Mapping[str, Any]
    ) -> dict[str, Any]:
        raw_base64 = str(artifact.get("model_base64") or "")
        cache_key = (
            str(artifact.get("version") or ""),
            hashlib.sha256(raw_base64.encode("utf-8")).hexdigest(),
        )
        with self._lock:
            cached = self._classifier_model_payload_cache
            if cached is not None and cached[0] == cache_key[0] and cached[1] == cache_key[1]:
                return copy.deepcopy(cached[2])
        payload = self._classifier_model_payload_from_artifact(artifact)
        with self._lock:
            self._classifier_model_payload_cache = (cache_key[0], cache_key[1], payload)
        return copy.deepcopy(payload)

    def _classifier_model_base64(self, payload: Mapping[str, Any]) -> str:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.b64encode(content).decode("ascii")

    def _classifier_import_warnings(self, classes: Sequence[str]) -> list[str]:
        unresolved = [
            label for label in classes if self._exact_profile_for_classifier_label(label) is None
        ]
        if not unresolved:
            return []
        return [
            "classes に対応する active Profile を一意に解決できない値があります: "
            + ", ".join(unresolved[:8])
        ]

    def _classifier_runtime_embedding_model(self) -> tuple[str, list[str]]:
        try:
            _vectors, warnings, embedding_model = self._classifier_vectors([""])
        except Exception as exc:  # pragma: no cover - defensive embedding boundary
            return (
                "deterministic-hash-1536",
                [f"現在の classifier embedding model 判定に失敗しました: {exc}"],
            )
        return embedding_model or "deterministic-hash-1536", list(warnings)

    def _classifier_probabilities(
        self, payload: Mapping[str, Any], vector: Sequence[float]
    ) -> list[float]:
        classes = [str(item) for item in payload.get("classes", [])]
        coef = self._classifier_float_matrix(payload.get("coef"), "coef")
        intercept = self._classifier_float_vector(payload.get("intercept"), "intercept")
        if len(vector) != _CLASSIFIER_VECTOR_DIMENSION:
            raise ValueError(
                f"prediction vector は {_CLASSIFIER_VECTOR_DIMENSION} 次元である必要があります。"
            )
        if len(classes) == 2 and len(coef) == 1:
            score = sum(left * right for left, right in zip(coef[0], vector, strict=True))
            score += intercept[0]
            if score >= 0:
                scale = math.exp(-score)
                positive = 1.0 / (1.0 + scale)
            else:
                scale = math.exp(score)
                positive = scale / (1.0 + scale)
            return [1.0 - positive, positive]

        scores = [
            sum(left * right for left, right in zip(row, vector, strict=True)) + bias
            for row, bias in zip(coef, intercept, strict=True)
        ]
        max_score = max(scores)
        exp_scores = [math.exp(score - max_score) for score in scores]
        total = sum(exp_scores) or 1.0
        return [score / total for score in exp_scores]

    def _replace_classifier_artifact(self, artifact: dict[str, Any]) -> None:
        """唯一の classifier artifact を永続化と一体で置き換える。"""
        with self._lock:
            previous_artifact = copy.deepcopy(self._classifier_artifact)
            self._classifier_artifact = artifact
            self._classifier_model_payload_cache = None
            try:
                self._persist_singletons("classifier_artifact")
            except Exception:
                self._classifier_artifact = previous_artifact
                self._classifier_model_payload_cache = None
                raise

    def train_classifier(
        self,
        request: ClassifierTrainRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> ClassifierStatusData:
        self._load_classifier_state()
        with self._lock:
            examples = [
                item
                for item in self._classifier_examples
                if self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
            ]
        warnings: list[str] = []
        if not examples:
            raise ValueError("分類器の training data が未登録です。")

        counts: dict[str, int] = {}
        for item in examples:
            label = self._classifier_training_label(item)
            counts[label] = counts.get(label, 0) + 1
        eligible = [
            item
            for item in examples
            if counts.get(self._classifier_training_label(item), 0)
            >= request.min_examples_per_category
        ]
        categories = sorted({self._classifier_training_label(item) for item in eligible})
        if len(categories) < 2:
            raise ValueError("LogisticRegression には 2 category 以上の training data が必要です。")

        try:
            vectors, embedding_warnings, embedding_model = self._classifier_vectors(
                [item.text for item in eligible]
            )
            warnings.extend(embedding_warnings)
            linear_model = importlib.import_module("sklearn.linear_model")
            model = linear_model.LogisticRegression(max_iter=1000, random_state=42)
            labels = [self._classifier_training_label(item) for item in eligible]
            model.fit(vectors, labels)
            score = float(model.score(vectors, labels))
            model_payload = self._classifier_model_payload_from_estimator(model)
        except Exception as exc:
            raise ValueError(f"分類器の学習に失敗しました: {exc}") from exc

        now = _utc_now()
        artifact = {
            "version": str(uuid.uuid4()),
            "updated_at": now,
            "model_format": _CLASSIFIER_MODEL_FORMAT,
            "model_base64": self._classifier_model_base64(model_payload),
            "categories": model_payload["classes"],
            "embedding_model": embedding_model,
            "vector_dimension": _CLASSIFIER_VECTOR_DIMENSION,
            "training_data_fingerprint": self._classifier_training_fingerprint(examples),
            "metrics": {
                "training_examples": len(eligible),
                "source_example_count": len(examples),
                "category_count": len(categories),
                "training_accuracy": round(score, 4),
                "embedding_fallback": embedding_model == "deterministic-hash-1536",
            },
        }
        self._replace_classifier_artifact(artifact)
        return self.classifier_status(allowed_profile_ids=allowed_profile_ids).model_copy(
            update={"warnings": warnings}
        )

    def import_classifier_model_artifact(
        self, *, filename: str, content: bytes
    ) -> ClassifierModelImportData:
        suffix = Path(filename).suffix.lower()
        if suffix != ".json":
            raise ValueError(
                "pickle/joblib artifact は安全上の理由で import できません。"
                "coef / intercept / classes を含む JSON artifact を指定してください。"
            )
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON classifier artifact を読み取れません。") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("JSON classifier artifact は object で指定してください。")

        meta = dict(payload)
        warnings: list[str] = []
        model_payload = self._classifier_model_payload_from_import(meta)
        version = str(meta.get("version") or uuid.uuid4())
        runtime_embedding_model, embedding_warnings = self._classifier_runtime_embedding_model()
        raw_embedding_model = meta.get("embedding_model") or meta.get("embed_model")
        embedding_model = str(raw_embedding_model or runtime_embedding_model).strip()
        if not embedding_model:
            embedding_model = "deterministic-hash-1536"
        if raw_embedding_model:
            if embedding_model != runtime_embedding_model:
                warnings.extend(embedding_warnings)
                warnings.append(
                    "classifier artifact の embedding_model "
                    f"({embedding_model}) と現在の embedding model "
                    f"({runtime_embedding_model}) が一致しません。予測時は deterministic "
                    "recommendation へ縮退します。"
                )
        else:
            warnings.extend(embedding_warnings)
        metrics = self._sanitize_classifier_metrics(meta.get("metrics"), warnings)
        metrics.setdefault("category_count", len(model_payload["classes"]))
        for metric_name in sorted(_CLASSIFIER_COUNT_METRICS):
            self._classifier_metric_count(
                metrics,
                metric_name,
                default=0,
                warnings=warnings,
                reject_invalid=True,
            )
        with self._lock:
            examples = list(self._classifier_examples)
        artifact = {
            "version": version,
            "updated_at": str(meta.get("updated_at") or _utc_now()),
            "model_format": _CLASSIFIER_MODEL_FORMAT,
            "model_base64": self._classifier_model_base64(model_payload),
            "categories": model_payload["classes"],
            "embedding_model": embedding_model,
            "vector_dimension": model_payload["feature_dim"],
            "training_data_fingerprint": str(
                meta.get("training_data_fingerprint")
                or self._classifier_training_fingerprint(examples)
            ),
            "metrics": metrics,
            "source": f"json:{filename}",
        }
        warnings.extend(self._classifier_import_warnings(model_payload["classes"]))
        try:
            model_info = self._classifier_model_info(version, artifact, active_version=version)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ValueError("classifier artifact metadata が不正です。") from exc
        self._replace_classifier_artifact(artifact)
        return ClassifierModelImportData(
            imported=True,
            active_version=version,
            model=model_info,
            warnings=warnings,
        )

    def export_classifier_training_data_xlsx(
        self, *, allowed_profile_ids: set[str] | None = None
    ) -> tuple[str, bytes]:
        self._load_classifier_state()
        with self._lock:
            examples = [
                item
                for item in self._classifier_examples
                if self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
            ]
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "training_data"
        _append_excel_text_row(
            sheet,
            [
                "CATEGORY",
                "TEXT",
                "PROFILE_ID",
                "SOURCE",
                "SOURCE_TYPE",
                "SOURCE_HISTORY_ID",
            ],
        )
        for item in examples:
            _append_excel_text_row(
                sheet,
                [
                    item.category,
                    item.text,
                    item.profile_id,
                    item.source,
                    item.source_type,
                    item.source_history_id,
                ],
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "nl2sql_classifier_training_data.xlsx", buffer.getvalue()

    def _classifier_model_info(
        self, version: str, artifact: dict[str, Any], *, active_version: str
    ) -> ClassifierModelInfo:
        categories = [str(item) for item in artifact.get("categories", [])]
        try:
            vector_dimension = int(artifact.get("vector_dimension") or 1536)
        except (TypeError, ValueError):
            vector_dimension = 1536
        return ClassifierModelInfo(
            version=version,
            active=version == active_version,
            updated_at=str(artifact.get("updated_at") or ""),
            category_count=len(categories),
            categories=categories,
            embedding_model=str(artifact.get("embedding_model") or ""),
            vector_dimension=vector_dimension,
            metrics=self._sanitize_classifier_metrics(artifact.get("metrics")),
            source=str(artifact.get("source") or "oracle_state"),
        )

    def predict_classifier(self, request: ClassifierPredictRequest) -> ClassifierPredictionData:
        prediction, warnings = self._classifier_prediction(request.question, request.top_k)
        if prediction is None:
            return ClassifierPredictionData(
                recommendation_source="deterministic",
                warnings=warnings or ["LogisticRegression classifier は未学習です。"],
            )
        prediction.warnings.extend(warnings)
        return prediction

    def _classifier_prediction(
        self, question: str, top_k: int
    ) -> tuple[ClassifierPredictionData | None, list[str]]:
        self._load_classifier_state()
        with self._lock:
            artifact = dict(self._classifier_artifact or {})
        if not artifact.get("model_base64"):
            return None, []
        warnings: list[str] = []
        try:
            model_payload = self._cached_classifier_model_payload_from_artifact(artifact)
            artifact_embedding_model = str(artifact.get("embedding_model") or "")
            if artifact_embedding_model == "deterministic-hash-1536":
                vectors = [self._deterministic_embedding(question)]
                embedding_model = artifact_embedding_model
            else:
                vectors, embedding_warnings, embedding_model = self._classifier_vectors([question])
                warnings.extend(embedding_warnings)
            if artifact_embedding_model and artifact_embedding_model != embedding_model:
                warnings.append(
                    "分類器の embedding model "
                    f"({artifact_embedding_model}) と現在の embedding model "
                    f"({embedding_model}) が一致しないため deterministic recommendation "
                    "に切り替えました。"
                )
                return None, warnings
            probabilities = self._classifier_probabilities(model_payload, vectors[0])
            classes = [str(item) for item in model_payload["classes"]]
        except Exception as exc:
            return None, [f"分類器の予測に失敗しました: {exc}"]

        ranked = sorted(
            zip(classes, probabilities, strict=False),
            key=lambda item: item[1],
            reverse=True,
        )
        candidates: list[ClassifierPredictionCandidate] = []
        for category, score in ranked[:top_k]:
            profile = self._profile_for_classifier_category(category)
            candidates.append(
                ClassifierPredictionCandidate(
                    category=category,
                    score=round(float(score), 4),
                    profile_id=profile.id if profile else "",
                    profile_name=profile.name if profile else "",
                    profile_category=profile.category if profile else "",
                )
            )
        best = candidates[0] if candidates else None
        return (
            ClassifierPredictionData(
                recommendation_source="classifier",
                classifier_version=str(artifact.get("version") or ""),
                predicted_category=best.category if best else "",
                confidence=best.score if best else 0.0,
                candidates=candidates,
            ),
            warnings,
        )

    def _parse_classifier_training_file(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> tuple[list[tuple[str, str, str]], int]:
        suffix = Path(filename).suffix.lower()
        if suffix in WORKBOOK_SUFFIXES:
            return self._parse_classifier_training_workbook(filename, content, warnings)
        if suffix in {".csv", ".txt", ""}:
            validate_tabular_text_signature(content)
            text = content.decode("utf-8-sig", errors="replace")
            return self._parse_classifier_training_csv(text, warnings)
        raise ValueError(
            f"{suffix} は未対応の形式です。CSV、XLSX、XLS のいずれかを指定してください。"
        )

    def _parse_classifier_training_csv(
        self, text: str, warnings: list[str]
    ) -> tuple[list[tuple[str, str, str]], int]:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            warnings.append("CSV header が見つかりません。")
            return [], 0
        if len(reader.fieldnames) > _CLASSIFIER_TRAINING_MAX_COLUMNS:
            raise ValueError(
                "classifier training data は "
                f"{_CLASSIFIER_TRAINING_MAX_COLUMNS} 列以内で指定してください。"
            )
        category_key, text_key, profile_key = self._classifier_header_keys(reader.fieldnames)
        if (not category_key and not profile_key) or not text_key:
            warnings.append("CSV は CATEGORY または PROFILE_ID と TEXT/QUESTION 列が必要です。")
            return [], 0
        rows: list[tuple[str, str, str]] = []
        skipped = 0
        for row_number, row in enumerate(reader, start=1):
            if row_number > _CLASSIFIER_TRAINING_MAX_ROWS:
                raise ValueError(
                    "classifier training data は "
                    f"{_CLASSIFIER_TRAINING_MAX_ROWS} 行以内で指定してください。"
                )
            category = str(row.get(category_key) or "").strip() if category_key else ""
            value = str(row.get(text_key) or "").strip()
            row_profile_id = str(row.get(profile_key) or "").strip() if profile_key else ""
            if (not category and not row_profile_id) or not value:
                skipped += 1
                continue
            rows.append((category, value, row_profile_id))
        return rows, skipped

    def _parse_classifier_training_workbook(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> tuple[list[tuple[str, str, str]], int]:
        sheet, sheet_warnings = select_workbook_sheet(read_workbook_sheets(filename, content))
        warnings.extend(sheet_warnings)
        header_row_index = -1
        headers: list[str] = []
        for row_index, raw_row in enumerate(sheet.rows):
            if len(raw_row) > _CLASSIFIER_TRAINING_MAX_COLUMNS:
                raise ValueError(
                    "classifier training data は "
                    f"{_CLASSIFIER_TRAINING_MAX_COLUMNS} 列以内で指定してください。"
                )
            values = [self._normalize_classifier_workbook_scalar(value) for value in raw_row]
            if any(values):
                header_row_index = row_index
                headers = values
                break
        if header_row_index < 0:
            warnings.append("Excel header が見つかりません。")
            return [], 0
        category_key, text_key, profile_key = self._classifier_header_keys(headers)
        if (not category_key and not profile_key) or not text_key:
            warnings.append("Excel は CATEGORY または PROFILE_ID と TEXT/QUESTION 列が必要です。")
            return [], 0
        category_index = headers.index(category_key) if category_key else None
        text_index = headers.index(text_key)
        profile_index = headers.index(profile_key) if profile_key else None
        rows: list[tuple[str, str, str]] = []
        skipped = 0
        for row_number, raw_row in enumerate(sheet.rows[header_row_index + 1 :], start=1):
            if row_number > _CLASSIFIER_TRAINING_MAX_ROWS:
                raise ValueError(
                    "classifier training data は "
                    f"{_CLASSIFIER_TRAINING_MAX_ROWS} 行以内で指定してください。"
                )
            if len(raw_row) > _CLASSIFIER_TRAINING_MAX_COLUMNS:
                raise ValueError(
                    "classifier training data は "
                    f"{_CLASSIFIER_TRAINING_MAX_COLUMNS} 列以内で指定してください。"
                )
            values = [self._normalize_classifier_workbook_scalar(value) for value in raw_row]
            category = (
                values[category_index]
                if category_index is not None and len(values) > category_index
                else ""
            )
            value = values[text_index] if len(values) > text_index else ""
            row_profile_id = (
                values[profile_index]
                if profile_index is not None and len(values) > profile_index
                else ""
            )
            if (not category and not row_profile_id) or not value:
                skipped += 1
                continue
            rows.append((category, value, row_profile_id))
        return rows, skipped

    def _normalize_classifier_workbook_scalar(self, value: Any) -> str:
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return normalize_workbook_scalar(value).strip()

    def _classifier_header_keys(self, headers: Sequence[str]) -> tuple[str, str, str]:
        normalized = {
            key: header for header in headers if (key := self._normalize_training_header(header))
        }
        category = (
            normalized.get("CATEGORY") or normalized.get("PROFILE") or normalized.get("LABEL")
        )
        text = (
            normalized.get("TEXT")
            or normalized.get("QUESTION")
            or normalized.get("PROMPT")
            or normalized.get("UTTERANCE")
        )
        profile_id = normalized.get("PROFILE_ID") or normalized.get("PROFILEID")
        return category or "", text or "", profile_id or ""

    def _normalize_training_header(self, value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")

    def _parse_profile_learning_material_file(
        self,
        filename: str,
        content: bytes,
        warnings: list[str],
    ) -> tuple[dict[str, Any], int]:
        suffix = Path(filename).suffix.lower()
        if suffix in WORKBOOK_SUFFIXES:
            return self._parse_profile_learning_material_workbook(filename, content, warnings)
        if suffix in {".csv", ".tsv", ".txt", ""}:
            validate_tabular_text_signature(content)
            text = content.decode("utf-8-sig", errors="replace")
            first_line = text.splitlines()[0] if text.splitlines() else ""
            delimiter = "\t" if suffix == ".tsv" or "\t" in first_line else ","
            return self._parse_profile_learning_material_csv(
                text,
                warnings,
                kind_hint=self._learning_material_kind_hint(filename),
                delimiter=delimiter,
            )
        raise ValueError(
            f"{suffix} は未対応の形式です。CSV、XLSX、XLS のいずれかを指定してください。"
        )

    def _legacy_material_rows(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> list[tuple[list[str], list[Sequence[Any]]]]:
        suffix = Path(filename).suffix.lower()
        if suffix in WORKBOOK_SUFFIXES:
            workbook_sheets = read_workbook_sheets(filename, content)
            sheets: list[tuple[list[str], list[Sequence[Any]]]] = []
            for sheet in workbook_sheets:
                rows_iter = iter(sheet.rows)
                headers = [str(value or "").strip() for value in next(rows_iter, [])]
                if any(headers):
                    sheets.append((headers, list(rows_iter)))
            return sheets
        if suffix in {".csv", ".tsv", ".txt", ""}:
            validate_tabular_text_signature(content)
            text = content.decode("utf-8-sig", errors="replace")
            first_line = text.splitlines()[0] if text.splitlines() else ""
            delimiter = "\t" if suffix == ".tsv" or "\t" in first_line else ","
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            try:
                headers = [str(value or "").strip() for value in next(reader)]
            except StopIteration:
                warnings.append("CSV header が見つかりません。")
                return []
            return [(headers, list(reader))]
        raise ValueError(
            f"{suffix} は未対応の形式です。CSV、XLSX、XLS のいずれかを指定してください。"
        )

    def _parse_legacy_terms_file(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> dict[str, str]:
        glossary: dict[str, str] = {}
        for headers, rows in self._legacy_material_rows(filename, content, warnings):
            term_index = self._learning_header_index(headers, {"TERM", "KEY", "WORD", "用語"})
            definition_index = self._learning_header_index(
                headers,
                {"DEFINITION", "DESCRIPTION", "VALUE", "REPLACEMENT", "定義", "説明"},
            )
            for row in rows:
                term = self._row_cell(row, term_index)
                definition = self._row_cell(row, definition_index)
                if term and definition:
                    glossary[term] = definition
        if not glossary:
            warnings.append("取り込み可能な TERM/DEFINITION 列が見つかりません。")
        return glossary

    def _parse_legacy_rules_file(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> list[str]:
        rules: list[str] = []
        seen: set[str] = set()
        for headers, rows in self._legacy_material_rows(filename, content, warnings):
            rule_index = self._learning_header_index(
                headers, {"RULE", "SQL_RULE", "GUIDELINE", "INSTRUCTION", "TEXT", "ルール"}
            )
            for row in rows:
                rule = self._row_cell(row, rule_index)
                if not rule or rule in seen:
                    continue
                seen.add(rule)
                rules.append(rule)
        if not rules:
            warnings.append("取り込み可能な RULE 列が見つかりません。")
        return rules

    def _parse_profile_learning_material_csv(
        self,
        text: str,
        warnings: list[str],
        *,
        kind_hint: str = "",
        delimiter: str = ",",
    ) -> tuple[dict[str, Any], int]:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        try:
            headers = [str(value or "").strip() for value in next(reader)]
        except StopIteration:
            warnings.append("CSV header が見つかりません。")
            return self._empty_learning_material(), 0
        return self._parse_profile_learning_rows(headers, reader, warnings, kind_hint=kind_hint)

    def _parse_profile_learning_material_workbook(
        self,
        filename: str,
        content: bytes,
        warnings: list[str],
    ) -> tuple[dict[str, Any], int]:
        workbook_sheets = read_workbook_sheets(filename, content)
        merged = self._empty_learning_material()
        skipped = 0
        for sheet in workbook_sheets:
            rows_iter = iter(sheet.rows)
            headers = [str(value or "").strip() for value in next(rows_iter, [])]
            if not any(headers):
                continue
            parsed, sheet_skipped = self._parse_profile_learning_rows(
                headers,
                rows_iter,
                warnings,
                kind_hint=self._learning_material_kind_hint(sheet.title),
            )
            merged["terms"].update(parsed["terms"])
            merged["rules"] = self._merge_unique_strings(merged["rules"], parsed["rules"])
            merged["examples"] = self._merge_few_shot_examples(
                merged["examples"],
                parsed["examples"],
            )
            skipped += sheet_skipped
        return merged, skipped

    def _parse_profile_learning_rows(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        warnings: list[str],
        *,
        kind_hint: str = "",
    ) -> tuple[dict[str, Any], int]:
        material = self._empty_learning_material()
        term_index = self._learning_header_index(headers, {"TERM", "KEY", "WORD", "用語"})
        definition_index = self._learning_header_index(
            headers,
            {"DEFINITION", "DESCRIPTION", "VALUE", "REPLACEMENT", "定義", "説明"},
        )
        rule_names = {"RULE", "SQL_RULE", "GUIDELINE", "INSTRUCTION", "ルール"}
        if kind_hint == "rules":
            rule_names = rule_names | {"TEXT"}
        rule_index = self._learning_header_index(headers, rule_names)
        question_index = self._learning_header_index(
            headers,
            {"QUESTION", "PROMPT", "UTTERANCE", "質問"},
        )
        sql_index = self._learning_header_index(headers, {"SQL", "EXPECTED_SQL"})
        skipped = 0
        for raw_row in rows:
            term = self._row_cell(raw_row, term_index)
            definition = self._row_cell(raw_row, definition_index)
            rule = self._row_cell(raw_row, rule_index)
            question = self._row_cell(raw_row, question_index)
            sql = self._row_cell(raw_row, sql_index)
            if term and definition:
                material["terms"][term] = definition
                continue
            if rule:
                material["rules"] = self._merge_unique_strings(material["rules"], [rule])
                continue
            if question and sql:
                material["examples"] = self._merge_few_shot_examples(
                    material["examples"],
                    [{"question": question, "sql": sql}],
                )
                continue
            if any(str(value or "").strip() for value in raw_row):
                skipped += 1
        if not material["terms"] and not material["rules"] and not material["examples"]:
            warnings.append(
                "取り込み可能な TERM/DEFINITION, RULE, QUESTION/SQL 列が見つかりません。"
            )
        return material, skipped

    def _empty_learning_material(self) -> dict[str, Any]:
        return {"terms": {}, "rules": [], "examples": []}

    def _learning_material_kind_hint(self, value: str) -> str:
        normalized = value.strip().lower()
        if any(token in normalized for token in ("term", "glossary", "用語")):
            return "terms"
        if any(token in normalized for token in ("rule", "ルール")):
            return "rules"
        if any(token in normalized for token in ("few", "example", "training", "sql", "例")):
            return "examples"
        return ""

    def _learning_header_index(self, headers: Sequence[str], names: set[str]) -> int | None:
        normalized_names = {
            normalized for name in names if (normalized := self._normalize_training_header(name))
        }
        raw_names = {name.strip().upper() for name in names if name.strip()}
        for index, header in enumerate(headers):
            raw = header.strip()
            if not raw:
                continue
            normalized = self._normalize_training_header(raw)
            if (normalized and normalized in normalized_names) or raw.upper() in raw_names:
                return index
        return None

    def _row_cell(self, row: Sequence[Any], index: int | None) -> str:
        if index is None or len(row) <= index:
            return ""
        return str(row[index] or "").strip()

    def _merge_unique_strings(self, current: Sequence[str], incoming: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        merged: list[str] = []
        for item in [*current, *incoming]:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
        return merged

    def _merge_few_shot_examples(
        self,
        current: Sequence[dict[str, str]],
        incoming: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        merged: list[dict[str, str]] = []
        for item in [*current, *incoming]:
            question = str(item.get("question") or "").strip()
            sql = str(item.get("sql") or item.get("expected_sql") or "").strip()
            key = (question, sql)
            if not question or not sql or key in seen:
                continue
            seen.add(key)
            merged.append({"question": question, "sql": sql})
        return merged

    def _classifier_vectors(self, texts: list[str]) -> tuple[list[list[float]], list[str], str]:
        settings = get_settings()
        if self._embedding_client.is_configured():
            try:
                return (
                    self._embedding_client.embed_texts(texts),
                    [],
                    settings.oci_genai_embed_model_id,
                )
            except EmbeddingClientError as exc:
                return (
                    [self._deterministic_embedding(text) for text in texts],
                    [
                        "OCI GenAI embedding に失敗したため deterministic fallback "
                        f"を使いました: {exc}"
                    ],
                    "deterministic-hash-1536",
                )
            except Exception as exc:  # pragma: no cover - defensive SDK boundary
                return (
                    [self._deterministic_embedding(text) for text in texts],
                    [
                        "OCI GenAI embedding に失敗したため deterministic fallback "
                        f"を使いました: {exc}"
                    ],
                    "deterministic-hash-1536",
                )
        return (
            [self._deterministic_embedding(text) for text in texts],
            ["OCI GenAI embedding が未設定のため deterministic fallback を使いました。"],
            "deterministic-hash-1536",
        )

    def _deterministic_embedding(self, text: str) -> list[float]:
        vector = [0.0] * 1536
        tokens = _similarity_tokens(text)
        if not tokens:
            tokens = {text.strip() or "empty"}
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=12).digest()
            index = int.from_bytes(digest[:4], "big") % len(vector)
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + (digest[5] / 255.0)
            vector[index] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [round(value / norm, 8) for value in vector]

    def _profile_id_for_classifier_category(self, category: str) -> str:
        profile = self._exact_profile_for_classifier_label(category)
        return profile.id if profile else ""

    def _exact_profile_for_classifier_label(self, label: str) -> Nl2SqlProfile | None:
        normalized = label.strip().casefold()
        if normalized in {"標準業務プロファイル", "default profile"}:
            normalized = "default"
        profiles = self.list_profiles(include_archived=False)
        direct = [
            profile
            for profile in profiles
            if normalized in {profile.id.casefold(), profile.name.casefold()}
        ]
        if len(direct) == 1:
            return direct[0]
        category_matches = [
            profile
            for profile in profiles
            if profile.category and normalized == profile.category.casefold()
        ]
        return category_matches[0] if len(category_matches) == 1 else None

    def _profile_for_classifier_category(self, category: str) -> Nl2SqlProfile | None:
        exact = self._exact_profile_for_classifier_label(category)
        if exact is not None:
            return exact
        profiles = self.list_profiles()
        scored = [
            (
                len(
                    _similarity_tokens(category)
                    & _similarity_tokens(f"{profile.name} {profile.category}")
                ),
                profile,
            )
            for profile in profiles
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
        return None

    def _feedback_index_data(
        self,
        *,
        operation: str,
        include_bad: bool,
        allowed_profile_ids: set[str] | None = None,
    ) -> FeedbackIndexData:
        self._load_feedback_state()
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        indexable = self._feedback_indexable_history(
            include_bad,
            allowed_profile_ids=allowed_profile_ids,
        )
        source_count = self._feedback_source_history_count(allowed_profile_ids=allowed_profile_ids)
        scoped_history_ids: set[str] | None = None
        if allowed_profile_ids is not None:
            scoped_history_ids = {
                item.id for item in self._history_snapshot(allowed_profile_ids=allowed_profile_ids)
            }
        with self._lock:
            indexed_count = (
                len(self._feedback_indexed_ids)
                if scoped_history_ids is None
                else len(self._feedback_indexed_ids & scoped_history_ids)
            )
        executed = False
        if operation == "rebuild":
            if not self._use_oracle_runtime():
                warnings.append(
                    "Feedback vector index の rebuild 実行には "
                    "NL2SQL_RUNTIME_MODE=oracle が必要です。"
                )
            elif not self._embedding_client.is_configured():
                warnings.append(
                    "OCI GenAI embedding が未設定です。"
                    "NL2SQL_FEEDBACK_EMBEDDING_ENABLED と OCI 設定を確認してください。"
                )
            else:
                try:
                    texts = [self._feedback_embedding_text(item) for item in indexable]
                    vectors = self._embedding_client.embed_texts(texts)
                    settings = get_settings()
                    rows = [
                        {
                            "history_id": item.id,
                            "profile_id": item.profile_id,
                            "question": item.question,
                            "generated_sql": item.generated_sql,
                            "feedback_rating": (
                                item.admin_feedback_rating.value
                                if item.admin_feedback_rating
                                else ""
                            ),
                            "embedding": vector,
                        }
                        for item, vector in zip(indexable, vectors, strict=True)
                    ]
                    with self._feedback_index_lock:
                        if scoped_history_ids is None:
                            self._oracle_adapter.rebuild_feedback_vector_index(
                                table_name=settings.nl2sql_feedback_vector_table,
                                index_name=settings.nl2sql_feedback_vector_index,
                                rows=rows,
                            )
                        else:
                            indexable_ids = {item.id for item in indexable}
                            with self._lock:
                                obsolete_ids = sorted(
                                    (self._feedback_indexed_ids & scoped_history_ids)
                                    - indexable_ids
                                )
                            for history_id in obsolete_ids:
                                self._oracle_adapter.delete_feedback_vector_entry(
                                    table_name=settings.nl2sql_feedback_vector_table,
                                    history_id=history_id,
                                )
                            for row in rows:
                                self._oracle_adapter.upsert_feedback_vector_entry(
                                    table_name=settings.nl2sql_feedback_vector_table,
                                    index_name=settings.nl2sql_feedback_vector_index,
                                    row=row,
                                )
                        with self._lock:
                            if scoped_history_ids is None:
                                self._feedback_indexed_ids = {item.id for item in indexable}
                            else:
                                self._feedback_indexed_ids.difference_update(scoped_history_ids)
                                self._feedback_indexed_ids.update(item.id for item in indexable)
                            indexed_count = (
                                len(self._feedback_indexed_ids)
                                if scoped_history_ids is None
                                else len(self._feedback_indexed_ids & scoped_history_ids)
                            )
                        executed = True
                        self._persist_singletons("feedback_indexed_ids")
                except (EmbeddingClientError, OracleAdapterError, ValueError) as exc:
                    warnings.append(str(exc))
                    with self._lock:
                        indexed_count = (
                            len(self._feedback_indexed_ids)
                            if scoped_history_ids is None
                            else len(self._feedback_indexed_ids & scoped_history_ids)
                        )
        settings = get_settings()
        return FeedbackIndexData(
            operation=operation,
            status=self._feedback_index_status(indexed_count, len(indexable)),
            executed=executed,
            runtime=runtime,
            source_history_count=source_count,
            indexable_count=len(indexable),
            indexed_count=indexed_count,
            ddl=self._feedback_index_ddl(),
            embedding_model=settings.oci_genai_embed_model_id,
            embedding_configured=self._embedding_client.is_configured(),
            warnings=warnings,
            timing=self._timing(created_at, started, "feedback_index"),
        )

    def _feedback_indexable_history(
        self,
        include_bad: bool,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> list[HistoryItem]:
        del include_bad
        good = FeedbackRating.GOOD.value
        if self._incremental_repository is None:
            with self._lock:
                return [
                    item.model_copy(deep=True)
                    for item in self._history
                    if item.admin_feedback_rating == FeedbackRating.GOOD
                    and item.safety_is_safe
                    and self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
                ]
        indexable: list[HistoryItem] = []
        cursor = ""
        while True:
            page, cursor, _total = self._history_page(
                cursor=cursor or None,
                limit=500,
                payload_filters={"admin_feedback_rating": good},
                allowed_profile_ids=allowed_profile_ids,
            )
            indexable.extend(
                item
                for item in page
                if item.admin_feedback_rating == FeedbackRating.GOOD and item.safety_is_safe
            )
            if not cursor or not page:
                return indexable

    def _feedback_source_history_count(self, *, allowed_profile_ids: set[str] | None = None) -> int:
        if self._incremental_repository is None:
            with self._lock:
                return sum(
                    1
                    for item in self._history
                    if self._profile_in_allowed_profile_ids(item.profile_id, allowed_profile_ids)
                )
        _page, _cursor, total = self._history_page(
            cursor=None,
            limit=1,
            allowed_profile_ids=allowed_profile_ids,
        )
        return total

    def _feedback_index_status(self, indexed_count: int, indexable_count: int) -> str:
        if indexable_count == 0 and indexed_count == 0:
            return "empty"
        if indexed_count < indexable_count:
            return "stale"
        if indexed_count > indexable_count:
            return "needs_cleanup"
        return "ready"

    def _feedback_index_ddl(self) -> list[str]:
        settings = get_settings()
        table_name = settings.nl2sql_feedback_vector_table
        index_name = settings.nl2sql_feedback_vector_index
        return [
            (
                f"CREATE TABLE {table_name} ("
                "HISTORY_ID VARCHAR2(64) PRIMARY KEY, "
                "PROFILE_ID VARCHAR2(128), "
                "QUESTION CLOB, GENERATED_SQL CLOB, FEEDBACK_RATING VARCHAR2(32), "
                "EMBEDDING VECTOR(1536, FLOAT32), CREATED_AT TIMESTAMP WITH TIME ZONE)"
            ),
            (
                f"CREATE VECTOR INDEX {index_name} "
                f"ON {table_name} (EMBEDDING) "
                "ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE"
            ),
        ]

    def _feedback_embedding_text(self, item: HistoryItem) -> str:
        admin_feedback = item.admin_feedback_rating.value if item.admin_feedback_rating else ""
        user_feedback = item.feedback_rating.value if item.feedback_rating else ""
        return "\n".join(
            [
                f"question: {item.question}",
                f"rewritten_question: {item.rewritten_question}",
                f"sql: {item.generated_sql}",
                f"admin_feedback: {admin_feedback}",
                f"admin_feedback_content: {item.admin_feedback_content}",
                f"user_feedback: {user_feedback}",
                f"user_feedback_content: {item.feedback_comment}",
                f"profile: {item.profile_name or item.profile_id}",
            ]
        )

    def _timing(self, created_at: str, started: float, stage: str) -> TimingEnvelope:
        elapsed = _elapsed_ms(started)
        return TimingEnvelope(
            created_at=created_at,
            started_at=created_at,
            finished_at=_utc_now(),
            elapsed_ms=elapsed,
            stage_timings=[StageTiming(stage=stage, elapsed_ms=elapsed)],
        )

    def recommend_profile(
        self,
        request: ProfileRecommendationRequest,
        *,
        allowed_profile_ids: set[str] | None = None,
    ) -> ProfileRecommendationData:
        classifier_prediction, classifier_warnings = self._classifier_prediction(
            request.question, top_k=3
        )
        classifier_category_scores: dict[str, float] = {}
        if classifier_prediction and classifier_prediction.candidates:
            mapped_candidates: list[ProfileRecommendationCandidate] = []
            for candidate in classifier_prediction.candidates:
                if (
                    allowed_profile_ids is not None
                    and candidate.profile_id not in allowed_profile_ids
                ):
                    continue
                profile = self.get_profile(candidate.profile_id) if candidate.profile_id else None
                if profile is None:
                    continue
                mapped_candidates.append(
                    ProfileRecommendationCandidate(
                        profile_id=profile.id,
                        profile_name=profile.name,
                        score=candidate.score,
                        matched_terms=[candidate.category],
                        allowed_tables=self.profile_allowed_object_names(profile),
                        category=profile.category or candidate.category or profile.name,
                    )
                )
            if mapped_candidates:
                best = mapped_candidates[0]
                # category_scores は legacy 名だが、同一 category の複数 profile を潰さない。
                classifier_category_scores = {
                    candidate.profile_id: candidate.score for candidate in mapped_candidates
                }
                profile = self.get_profile(best.profile_id)
                reason = (
                    f"LogisticRegression classifier が category "
                    f"{best.category or classifier_prediction.predicted_category} を"
                    "予測しました。"
                )
                if classifier_warnings:
                    reason = f"{reason} {' '.join(classifier_warnings)}"
                return self._recommendation_from_profile(
                    profile=profile,
                    question=request.question,
                    confidence=round(best.score, 3),  # predict_proba をそのまま信頼度に
                    matched_terms=best.matched_terms,
                    candidates=mapped_candidates,
                ).model_copy(
                    update={
                        "reason": reason,
                        "recommendation_source": "classifier",
                        "classifier_version": classifier_prediction.classifier_version,
                        "confidence_threshold": _CLASSIFIER_RECOMMENDATION_CONFIDENCE_THRESHOLD,
                        "category_scores": classifier_category_scores,
                        "warnings": classifier_warnings,
                    }
                )

        profiles = self.list_profiles()
        if allowed_profile_ids is not None:
            profiles = [profile for profile in profiles if profile.id in allowed_profile_ids]
        if not profiles:
            profile = self.get_profile(request.current_profile_id)
            if allowed_profile_ids is not None and profile.id not in allowed_profile_ids:
                raise ValueError("この業務プロファイルを利用する権限がありません。")
            fallback = self._recommendation_from_profile(
                profile=profile,
                question=request.question,
                confidence=0.0,
                matched_terms=[],
                candidates=[],
            )
            fallback_update: dict[str, Any] = {
                "confidence_threshold": _CLASSIFIER_RECOMMENDATION_CONFIDENCE_THRESHOLD,
            }
            if classifier_category_scores:
                fallback_update["category_scores"] = classifier_category_scores
            if classifier_warnings:
                fallback_update["reason"] = f"{fallback.reason} {' '.join(classifier_warnings)}"
                fallback_update["warnings"] = classifier_warnings
            return fallback.model_copy(update=fallback_update)

        # (rank=順序用 bias 込み, evidence=実マッチ由来, profile, matched_terms)
        scored: list[tuple[float, float, Nl2SqlProfile, list[str]]] = []
        for profile in profiles:
            evidence, matched_terms = self._score_profile_for_question(profile, request.question)
            rank = evidence
            if profile.id == request.current_profile_id:
                rank += _PROFILE_RECOMMEND_CURRENT_PROFILE_BIAS
            if not matched_terms and profile.id == "default":
                rank += _PROFILE_RECOMMEND_DEFAULT_PROFILE_BIAS
            scored.append((rank, evidence, profile, matched_terms))
        # 並び順は rank（tiebreak 込み）で決め、信頼度は evidence だけで算出する。
        scored.sort(key=lambda item: item[0], reverse=True)
        _, _, best_profile, best_terms = scored[0]
        confidence = self._relative_confidence([evidence for _, evidence, _, _ in scored])
        total_evidence = sum(evidence for _, evidence, _, _ in scored) or 1.0
        candidates = [
            ProfileRecommendationCandidate(
                profile_id=profile.id,
                profile_name=profile.name,
                # 0..1 の相対シェア（「スコア X%」が常に 0-100% に収まる）。
                score=round(evidence / total_evidence, 3),
                matched_terms=terms[:8],
                allowed_tables=self.profile_allowed_object_names(profile),
                category=profile.category or profile.name,
            )
            for _, evidence, profile, terms in scored[:3]
        ]
        fallback = self._recommendation_from_profile(
            profile=best_profile,
            question=request.question,
            confidence=confidence,
            matched_terms=best_terms,
            candidates=candidates,
        )
        if classifier_warnings:
            fallback = fallback.model_copy(
                update={
                    "reason": f"{fallback.reason} {' '.join(classifier_warnings)}",
                    "warnings": classifier_warnings,
                }
            )
        deterministic_fallback_update: dict[str, Any] = {
            "confidence_threshold": _CLASSIFIER_RECOMMENDATION_CONFIDENCE_THRESHOLD,
        }
        if classifier_category_scores:
            deterministic_fallback_update["category_scores"] = classifier_category_scores
        return fallback.model_copy(update=deterministic_fallback_update)

    def rewrite(self, request: RewriteRequest) -> RewriteData:
        """用語・同義語の置換だけを行う(LLM による自由な書き換えはしない)。

        `use_glossary` が False のときは一切変換しない。質問の意味を変える書き換え
        (件数・抽出条件の追加など)を裏で行わないため、決定論処理のみで完結させる。
        """
        profile = self.get_profile(request.profile_id)
        question = request.question.strip()
        if not request.use_glossary:
            return RewriteData(
                original_question=request.question,
                rewritten_question=question,
                source="deterministic",
            )
        if _question_has_empty_filter_slot(question):
            return RewriteData(
                original_question=request.question,
                rewritten_question=question,
                source="deterministic",
                warnings=[_EMPTY_FILTER_SLOT_WARNING],
            )
        return RewriteData(
            original_question=request.question,
            rewritten_question=self.rewrite_question(question, profile),
            source="deterministic",
        )

    def reverse_sql(self, request: ReverseSqlRequest) -> ReverseSqlData:
        profile = self.get_profile(request.profile_id)
        referenced = _extract_referenced_tables(
            request.sql,
            current_owner=self._current_schema_owner(),
        )
        catalog = self._reverse_sql_catalog(profile, referenced)
        structure = self._sql_structure(request.sql, referenced)
        table_labels = self._reverse_table_labels(
            referenced,
            profile=profile,
            catalog=catalog,
            use_glossary=request.use_glossary,
        )
        if table_labels:
            structure = {
                **structure,
                "summary": (
                    f"{', '.join(table_labels)} を参照し、"
                    f"{', '.join(structure['operations']) if structure['operations'] else 'SQL'} "
                    "操作を行います。"
                ),
            }
        question = self._reverse_business_question(
            request.sql,
            structure=structure,
            referenced=referenced,
            profile=profile,
            catalog=catalog,
            table_labels=table_labels,
            use_glossary=request.use_glossary,
        )
        logical_structure = self._reverse_logical_structure(structure)
        if request.use_glossary:
            logical_structure = self._apply_reverse_glossary(
                logical_structure,
                profile=profile,
                enabled=True,
            )
        limit = self._sql_fetch_limit(request.sql)
        logical_steps = self._logical_steps_from_structure(structure, limit=limit)
        if request.use_glossary:
            logical_steps = [
                self._apply_reverse_glossary(step, profile=profile, enabled=True)
                for step in logical_steps
            ]
        table_label, column_label = self._business_label_resolvers(
            profile=profile,
            catalog=catalog,
            referenced=referenced,
            use_glossary=request.use_glossary,
        )
        logical_step_details = self._apply_glossary_to_steps(
            build_logical_steps(
                structure,
                limit=limit,
                table_labels=table_labels,
                table_label=table_label,
                column_label=column_label,
            ),
            profile=profile,
            enabled=request.use_glossary,
        )
        logical_structure_items = self._apply_glossary_to_structure_items(
            build_logical_structure_items(
                structure,
                table_labels=table_labels,
                table_label=table_label,
                column_label=column_label,
            ),
            profile=profile,
            enabled=request.use_glossary,
        )
        return ReverseSqlData(
            question=question,
            explanation=self._reverse_business_explanation(structure, table_labels),
            referenced_tables=referenced,
            logical_structure=logical_structure,
            logical_structure_items=logical_structure_items,
            logical_steps=logical_steps,
            logical_step_details=logical_step_details,
        )

    def reverse_sql_deep(self, request: ReverseSqlRequest) -> ReverseSqlData:
        deterministic = self.reverse_sql(request)
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": [
                        "OCI Enterprise AI が未設定のため deterministic reverse を使用しました。"
                    ]
                }
            )
        try:
            profile = self.get_profile(request.profile_id)
            context_profile = (
                profile if request.use_glossary else profile.model_copy(update={"glossary": {}})
            )
            catalog = self._reverse_sql_catalog(profile, deterministic.referenced_tables)
            allowed = AllowedObjects(table_names=deterministic.referenced_tables)
            context_catalog = catalog or SchemaCatalog(refreshed_at=_utc_now(), tables=[])
            raw = self._enterprise_ai_client.generate(
                prompt=request.sql,
                context=self._enterprise_ai_schema_context(
                    profile=context_profile,
                    allowed=allowed,
                    catalog=context_catalog,
                    use_glossary=request.use_glossary,
                ),
                system_prompt=(
                    "Oracle SQL を日本語の自然な業務質問へ逆生成してください。"
                    "question はSQLの説明文ではなく、"
                    "業務担当者が検索欄に入力しそうな1文にしてください。"
                    "物理テーブル名・列名よりも、schema の logical name、comment、"
                    "glossary の業務語彙を優先してください。"
                    "SQL の列・条件・集計・結合・並び順を省略しないでください。"
                    "JSON object で question, explanation, logical_structure, logical_steps "
                    "を返してください。"
                ),
            )
            payload = self._json_object_from_text(raw)
            question = str(payload.get("question") or deterministic.question).strip()
            explanation = str(payload.get("explanation") or deterministic.explanation).strip()
            logical_structure = str(
                payload.get("logical_structure") or deterministic.logical_structure
            ).strip()
            steps = _reverse_deep_steps(payload.get("logical_steps"))
            update: dict[str, Any] = {
                "question": question,
                "explanation": explanation,
                "logical_structure": logical_structure,
                "source": "oci_enterprise_ai",
            }
            if steps:
                # UI は logical_step_details を優先して描画するため、文字列だけ差し替えると
                # LLM の手順が表示されない。details にも写す(技術行は決定論版を対応付け)。
                update["logical_steps"] = steps
                update["logical_step_details"] = _reverse_deep_step_details(
                    steps, deterministic.logical_step_details
                )
            return deterministic.model_copy(update=update)
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": [f"Enterprise AI reverse に失敗したため fallback しました: {exc}"]
                }
            )

    def _reverse_sql_catalog(
        self,
        profile: Nl2SqlProfile,
        referenced: list[str],
    ) -> SchemaCatalog | None:
        table_names = referenced or self.profile_allowed_object_names(profile)
        try:
            if table_names:
                catalog = self._generation_schema_catalog(
                    profile,
                    AllowedObjects(table_names=table_names),
                )
            else:
                catalog = self.get_catalog()
        except Exception:
            logger.debug("reverse_sql_catalog_unavailable", exc_info=True)
            return None
        return catalog if catalog.tables else None

    def _reverse_table_labels(
        self,
        referenced: list[str],
        *,
        profile: Nl2SqlProfile,
        catalog: SchemaCatalog | None,
        use_glossary: bool,
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for value in referenced:
            table = self._reverse_table_for_ref(value, catalog)
            fallback_label = value.strip().replace('"', "").rsplit(".", 1)[-1]
            label = (
                table.logical_name.strip()
                if table and table.logical_name.strip()
                else table.comment.strip() if table and table.comment.strip() else fallback_label
            )
            label = self._apply_reverse_glossary(
                label,
                profile=profile,
                enabled=use_glossary,
            )
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
        return labels

    def _reverse_table_for_ref(
        self,
        value: str,
        catalog: SchemaCatalog | None,
    ) -> SchemaTable | None:
        if catalog is None:
            return None
        normalized = value.strip().replace('"', "").upper()
        object_name = normalized.rsplit(".", 1)[-1]
        for table in catalog.tables:
            qualified = self._catalog_qualified_name(table)
            if (
                normalized in {qualified, table.table_name.upper()}
                or object_name == table.table_name.upper()
            ):
                return table
        return None

    def _reverse_column_labels(
        self,
        sql: str,
        referenced: list[str],
        *,
        profile: Nl2SqlProfile,
        catalog: SchemaCatalog | None,
        use_glossary: bool,
    ) -> list[str]:
        referenced_columns, has_wildcard = _extract_referenced_columns(sql, referenced)
        if has_wildcard:
            return []
        labels: list[str] = []
        seen: set[str] = set()
        for column_name in referenced_columns[:5]:
            column = self._reverse_column_for_ref(column_name, referenced, catalog)
            fallback_label = column_name.strip().replace('"', "").rsplit(".", 1)[-1]
            label = (
                column.logical_name.strip()
                if column and column.logical_name.strip()
                else column.comment.strip() if column and column.comment.strip() else fallback_label
            )
            label = self._apply_reverse_glossary(
                label,
                profile=profile,
                enabled=use_glossary,
            )
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
        return labels

    def _reverse_column_for_ref(
        self,
        column_name: str,
        referenced: list[str],
        catalog: SchemaCatalog | None,
    ) -> SchemaColumn | None:
        if catalog is None:
            return None
        normalized_column = column_name.strip().replace('"', "").upper().rsplit(".", 1)[-1]
        tables = [self._reverse_table_for_ref(value, catalog) for value in referenced]
        candidate_tables = [table for table in tables if table is not None] or catalog.tables
        for table in candidate_tables:
            for column in table.columns:
                if column.column_name.upper() == normalized_column:
                    return column
        return None

    def _reverse_business_question(
        self,
        sql: str,
        *,
        structure: dict[str, Any],
        referenced: list[str],
        profile: Nl2SqlProfile,
        catalog: SchemaCatalog | None,
        table_labels: list[str],
        use_glossary: bool,
    ) -> str:
        column_labels = self._reverse_column_labels(
            sql,
            referenced,
            profile=profile,
            catalog=catalog,
            use_glossary=use_glossary,
        )
        table_text = "、".join(table_labels[:3]) if table_labels else "対象業務データ"
        if column_labels:
            column_text = "、".join(column_labels[:3])
            subject = f"{table_text}の{column_text}" if table_labels else column_text
        else:
            subject = f"{table_text}のデータ"
        if structure["aggregations"]:
            return f"{subject}を集計して確認したい"
        if structure["filters"]:
            return f"条件に合う{subject}を確認したい"
        if structure["joins"]:
            return f"{table_text}に関連する{subject}を確認したい"
        return f"{subject}を一覧で確認したい"

    def _reverse_logical_structure(self, structure: dict[str, Any]) -> str:
        lines = [
            "SQL 論理構造",
            f"- Statement: {structure['statement_type']}",
            f"- Summary: {structure['summary']}",
        ]
        sections = [
            ("操作", structure["operations"]),
            ("条件", structure["filters"]),
            ("結合", structure["joins"]),
            ("Group by", structure["group_by"]),
            ("Order by", structure["order_by"]),
            ("集計", structure["aggregations"]),
        ]
        for label, items in sections:
            if items:
                lines.append(f"- {label}: " + "; ".join(items))
        return "\n".join(lines)

    def _business_label_resolvers(
        self,
        *,
        profile: Nl2SqlProfile,
        catalog: SchemaCatalog | None,
        referenced: list[str],
        use_glossary: bool,
    ) -> tuple[LabelResolver, LabelResolver]:
        """物理参照 -> 業務ラベル(表 / 列)の解決関数を作る。

        処理手順・SQL 論理構造の「業務者向け」文面で使う。カタログに無い参照は物理名の
        末尾へ縮退させ、勝手な言い換えはしない。
        """

        def table_label(value: str) -> str:
            table = self._reverse_table_for_ref(value, catalog)
            fallback = value.strip().replace('"', "").rsplit(".", 1)[-1]
            label = (
                table.logical_name.strip()
                if table and table.logical_name.strip()
                else table.comment.strip() if table and table.comment.strip() else fallback
            )
            return self._apply_reverse_glossary(label, profile=profile, enabled=use_glossary)

        def column_label(value: str) -> str:
            column = self._reverse_column_for_ref(value, referenced, catalog)
            fallback = value.strip().replace('"', "").rsplit(".", 1)[-1]
            label = (
                column.logical_name.strip()
                if column and column.logical_name.strip()
                else column.comment.strip() if column and column.comment.strip() else fallback
            )
            return self._apply_reverse_glossary(label, profile=profile, enabled=use_glossary)

        return table_label, column_label

    def _apply_glossary_to_steps(
        self,
        steps: list[Nl2SqlLogicalStep],
        *,
        profile: Nl2SqlProfile,
        enabled: bool,
    ) -> list[Nl2SqlLogicalStep]:
        if not enabled:
            return steps
        return [
            step.model_copy(
                update={
                    "business": self._apply_reverse_glossary(
                        step.business, profile=profile, enabled=True
                    ),
                    "technical": self._apply_reverse_glossary(
                        step.technical, profile=profile, enabled=True
                    ),
                }
            )
            for step in steps
        ]

    def _apply_glossary_to_structure_items(
        self,
        items: list[Nl2SqlLogicalStructureItem],
        *,
        profile: Nl2SqlProfile,
        enabled: bool,
    ) -> list[Nl2SqlLogicalStructureItem]:
        if not enabled:
            return items
        return [
            item.model_copy(
                update={
                    "business": self._apply_reverse_glossary(
                        item.business, profile=profile, enabled=True
                    ),
                    "technical": self._apply_reverse_glossary(
                        item.technical, profile=profile, enabled=True
                    ),
                }
            )
            for item in items
        ]

    def _reverse_business_explanation(
        self,
        structure: dict[str, Any],
        table_labels: list[str],
    ) -> str:
        """説明文も業務者向けを主・SQL 構造を副にして併記する。"""

        return build_business_explanation(structure, table_labels)

    def _logical_steps_from_structure(
        self,
        structure: dict[str, Any],
        *,
        limit: int | None = None,
    ) -> list[str]:
        """SQL 構造から業務向けの処理手順(決定論)を組み立てる。

        reverse(SQL→質問)と生成側 interpretation の両方で共有し、
        条件・結合・集計に加えてグループ化・並び替え・件数制限も手順化する。
        """
        steps = [
            str(structure.get("summary", "")).strip(),
            *[f"条件: {item}" for item in list(structure.get("filters", []))[:3]],
            *[f"結合: {item}" for item in list(structure.get("joins", []))[:3]],
            *[f"集計: {item}" for item in list(structure.get("aggregations", []))[:3]],
            *[f"グループ化: {item}" for item in list(structure.get("group_by", []))[:3]],
            *[f"並び替え: {item}" for item in list(structure.get("order_by", []))[:3]],
        ]
        if limit is not None and limit > 0:
            steps.append(f"件数制限: 上位{limit}件")
        return [step for step in steps if step]

    @staticmethod
    def _sql_fetch_limit(sql: str) -> int | None:
        """FETCH FIRST N ROWS 形式の上位N件を決定論で読み取る(無ければ None)。"""
        match = re.search(r"\bfetch\s+first\s+(\d+)\s+rows?\b", sql, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _semantic_join_lines(joins: Sequence[Any]) -> list[str]:
        lines: list[str] = []
        for join in joins:
            join_type = str(getattr(join, "join_type", "") or "inner").replace("_", " ").upper()
            left_source = str(getattr(join, "left_source", "") or "").strip()
            right_source = str(getattr(join, "right_source", "") or "").strip()
            if not right_source:
                continue
            line = f"{join_type}: {left_source} JOIN {right_source}".strip()
            condition_sql = str(getattr(join, "condition_sql", "") or "").strip()
            using_columns = list(getattr(join, "using_columns", []) or [])
            if condition_sql:
                line = f"{line} ON {condition_sql}"
            elif using_columns:
                line = f"{line} USING ({', '.join(str(column) for column in using_columns)})"
            lines.append(line)
        return lines

    def _apply_reverse_glossary(self, text: str, *, profile: Nl2SqlProfile, enabled: bool) -> str:
        glossary = self._effective_glossary(profile)
        if not enabled or not glossary:
            return text
        result = text
        replacements: list[tuple[str, str]] = []
        for term, definition in glossary.items():
            term_text = str(term or "").strip()
            normalized_definition = str(definition).strip()
            if not term_text or not normalized_definition:
                continue
            candidates = [normalized_definition]
            if "." in normalized_definition:
                candidates.append(normalized_definition.rsplit(".", 1)[-1])
            for candidate in candidates:
                candidate = candidate.strip()
                if candidate:
                    replacements.append((candidate, term_text))
        for candidate, term in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
            result = self._replace_reverse_glossary_candidate(result, candidate, term)
        return result

    @staticmethod
    def _replace_reverse_glossary_candidate(text: str, candidate: str, term: str) -> str:
        boundary = r"A-Za-z0-9_$#\""
        pattern = re.compile(rf"(?<![{boundary}]){re.escape(candidate)}(?![{boundary}])")
        return pattern.sub(term, text)

    def _sql_structure(self, sql: str, referenced: list[str]) -> dict[str, Any]:
        normalized = " ".join(sql.strip().split())
        statement_type = "WITH" if re.match(r"^\s*with\b", sql, re.IGNORECASE) else "SELECT"
        if not is_select_only(sql):
            statement_type = _admin_statement_type(sql)
        operations = []
        if re.search(r"\bselect\b", sql, re.IGNORECASE):
            operations.append("SELECT")
        if re.search(r"\bwith\b", sql, re.IGNORECASE):
            operations.append("WITH")
        if re.search(r"\bgroup\s+by\b", sql, re.IGNORECASE):
            operations.append("GROUP BY")
        if re.search(r"\border\s+by\b", sql, re.IGNORECASE):
            operations.append("ORDER BY")
        semantic = parse_oracle_sql(sql)
        graph = semantic.graph
        semantic_filters = (
            [
                predicate.expression_sql
                for predicate in graph.filters
                if predicate.clause == "where" and predicate.expression_sql
            ]
            if graph is not None
            else []
        )
        fallback_filters = self._extract_sql_clauses(
            normalized,
            "where",
            ["group by", "order by", "fetch"],
        )
        filters = semantic_filters or fallback_filters
        fallback_joins = [
            match.group(0).strip()
            for match in re.finditer(
                rf"\b(?:left|right|inner|outer|cross)?\s*join\s+{_SQL_OBJECT_REF}(?:\s+on\s+.*?)(?=\s+(?:left|right|inner|outer|cross)?\s*join\s+|\s+where\s+|\s+group\s+by\s+|\s+order\s+by\s+|$)",
                normalized,
                re.IGNORECASE,
            )
        ]
        joins = self._semantic_join_lines(graph.joins) if graph is not None else []
        if not joins:
            joins = fallback_joins
        group_by = self._extract_sql_clauses(
            normalized,
            "group by",
            ["having", "order by", "fetch"],
        )
        order_by = self._extract_sql_clauses(normalized, "order by", ["fetch"])
        aggregations = sorted(
            {
                match.group(1).upper()
                for match in re.finditer(r"\b(count|sum|avg|min|max)\s*\(", sql, re.IGNORECASE)
            }
        )
        return {
            "summary": (
                f"{', '.join(referenced) if referenced else '指定表'} を参照し、"
                f"{', '.join(operations) if operations else 'SQL'} 操作を行います。"
            ),
            "statement_type": statement_type,
            "operations": operations,
            "filters": filters,
            "group_by": group_by,
            "order_by": order_by,
            "joins": joins[:10],
            "aggregations": aggregations,
        }

    def _enhance_sql_analysis_with_llm(
        self,
        deterministic: AnalyzeData,
        sql: str,
        allowed: AllowedObjects,
        *,
        catalog: SchemaCatalog | None = None,
    ) -> AnalyzeData:
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "llm_warnings": [
                        "OCI Enterprise AI が未設定のため deterministic analysis を使用しました。"
                    ]
                }
            )
        try:
            profile = self.get_profile(None)
            raw = self._enterprise_ai_client.generate(
                prompt=sql,
                context=self._enterprise_ai_schema_context(
                    profile=profile,
                    allowed=allowed,
                    catalog=catalog or self._generation_schema_catalog(profile, allowed),
                ),
                system_prompt=(
                    "Oracle SQL を構造化分析してください。JSON object のみを返してください。"
                    "keys: explanation, structure_summary, risk_level, statement_type, "
                    "object_names, column_names, conditions, group_by, order_by, joins, "
                    "aggregations, risk_findings, repair_candidates, natural_language_question, "
                    "logical_steps。"
                ),
            )
            payload = _SqlAnalysisLlmPayload.model_validate(self._json_object_from_text(raw))
            risk_level = (payload.risk_level or deterministic.risk_level).lower()
            if risk_level not in {"low", "medium", "high"}:
                risk_level = deterministic.risk_level
            return deterministic.model_copy(
                update={
                    "explanation": payload.explanation or deterministic.explanation,
                    "structure_summary": payload.structure_summary
                    or deterministic.structure_summary,
                    "risk_level": risk_level,
                    "statement_type": payload.statement_type or deterministic.statement_type,
                    "object_names": payload.object_names or deterministic.object_names,
                    "column_names": payload.column_names or deterministic.column_names,
                    "conditions": payload.conditions or deterministic.conditions,
                    "group_by": payload.group_by or deterministic.group_by,
                    "order_by": payload.order_by or deterministic.order_by,
                    "joins": payload.joins or deterministic.joins,
                    "aggregations": payload.aggregations or deterministic.aggregations,
                    "risk_findings": payload.risk_findings or deterministic.risk_findings,
                    "repair_candidates": payload.repair_candidates
                    or deterministic.repair_candidates,
                    "llm_enhanced": True,
                }
            )
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={
                    "llm_warnings": [
                        f"Enterprise AI analysis に失敗したため fallback しました: {exc}"
                    ]
                }
            )

    def _extract_sql_clauses(
        self, normalized_sql: str, start_keyword: str, end_keywords: list[str]
    ) -> list[str]:
        match = re.search(rf"\b{re.escape(start_keyword)}\b\s+(.*)", normalized_sql, re.IGNORECASE)
        if not match:
            return []
        value = match.group(1)
        for keyword in end_keywords:
            end = re.search(rf"\b{re.escape(keyword)}\b", value, re.IGNORECASE)
            if end:
                value = value[: end.start()]
                break
        return [value.strip()] if value.strip() else []

    def _json_object_from_text(self, raw: str) -> dict[str, Any]:
        cleaned = self._strip_code_fence(raw)
        if "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise ValueError("JSON object ではありません。")
        return payload

    def suggest_comments(
        self,
        request: CommentSuggestionRequest | None = None,
    ) -> CommentSuggestionData:
        options = request or CommentSuggestionRequest()
        deterministic = CommentSuggestionData(
            suggestions=self._deterministic_comment_suggestions(options.max_items)
        )
        if not options.use_llm:
            return deterministic
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": [
                        "OCI Enterprise AI が未設定のため deterministic comment 候補を"
                        "使用しました。"
                    ]
                }
            )
        try:
            raw = self._enterprise_ai_client.generate(
                prompt=(
                    "表・列・ビュー・マテリアライズドビューの COMMENT ON 候補を"
                    "日本語で生成してください。"
                ),
                context=self._comment_generation_context(options.max_items),
                system_prompt=(
                    "Oracle schema metadata を読み、業務利用者が理解しやすい日本語 comment "
                    "を生成してください。JSON object で suggestions 配列だけを返してください。"
                    "各要素は object_name, object_type, suggested_comment を持ち、"
                    "object_type は table/view/materialized_view/column のいずれかです。"
                ),
            )
            payload = self._json_object_from_text(raw)
            suggestions = self._comment_suggestions_from_payload(
                payload,
                max_items=options.max_items,
            )
            if not suggestions:
                raise ValueError("comment 候補が空です。")
            return CommentSuggestionData(
                suggestions=suggestions,
                source="oci_enterprise_ai",
            )
        except (EnterpriseAiDirectError, ValueError, TypeError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": [
                        f"Enterprise AI comment 生成に失敗したため fallback しました: {exc}"
                    ]
                }
            )

    def _deterministic_comment_suggestions(self, max_items: int) -> list[CommentSuggestion]:
        suggestions: list[CommentSuggestion] = []
        for table in self._management_catalog_tables():
            object_type = _catalog_metadata_object_kind(table)
            suggestions.append(
                CommentSuggestion(
                    object_name=table.table_name,
                    object_type=object_type,
                    suggested_comment=table.comment or f"{table.logical_name} に関する業務データ",
                )
            )
            for column in table.columns:
                suggestions.append(
                    CommentSuggestion(
                        object_name=f"{table.table_name}.{column.column_name}",
                        object_type="column",
                        suggested_comment=column.comment
                        or f"{table.logical_name} の {column.logical_name}",
                    )
                )
        return suggestions[:max_items]

    def _comment_generation_context(self, max_items: int) -> str:
        lines = [f"max_items: {max_items}", "schema:"]
        for table in self._management_catalog_tables():
            lines.append(
                f"- {table.table_type} {table.table_name}: logical={table.logical_name} "
                f"comment={table.comment} rows={table.row_count}"
            )
            if table.constraints:
                lines.append(f"  constraints: {', '.join(table.constraints)}")
            for column in table.columns:
                samples = ", ".join(column.sample_values[:3])
                lines.append(
                    "  - column "
                    f"{column.column_name}: logical={column.logical_name} "
                    f"type={column.data_type} nullable={column.nullable} "
                    f"comment={column.comment} samples={samples}"
                )
        return "\n".join(lines)

    def _comment_suggestions_from_payload(
        self,
        payload: dict[str, Any],
        *,
        max_items: int,
    ) -> list[CommentSuggestion]:
        raw_items = payload.get("suggestions")
        if not isinstance(raw_items, list):
            raise ValueError("suggestions 配列がありません。")
        suggestions: list[CommentSuggestion] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            object_name = str(raw_item.get("object_name") or "").strip()
            object_type = str(raw_item.get("object_type") or "").strip().lower()
            comment = str(raw_item.get("suggested_comment") or "").strip()
            if (
                not object_name
                or object_type
                not in {"table", "view", "materialized_view", "materialized view", "column"}
                or not comment
            ):
                continue
            try:
                statement = self._comment_statement(
                    CommentApplyItem(
                        object_name=object_name,
                        object_type=object_type,
                        comment=comment,
                    )
                )
            except ValueError:
                continue
            suggestions.append(
                CommentSuggestion(
                    object_name=statement.object_name,
                    object_type=statement.object_type,
                    suggested_comment=statement.comment,
                )
            )
            if len(suggestions) >= max_items:
                break
        return suggestions

    def suggest_annotations(self) -> AnnotationSuggestionData:
        suggestions: list[AnnotationSuggestion] = []
        for table in self._management_catalog_tables():
            object_type = _catalog_metadata_object_kind(table)
            table_value = table.comment or table.logical_name or table.table_name
            suggestions.append(
                AnnotationSuggestion(
                    object_name=table.table_name,
                    object_type=object_type,
                    annotation_name="Display",
                    annotation_value=table_value,
                )
            )
            if object_type != "table":
                continue
            for column in table.columns:
                suggestions.append(
                    AnnotationSuggestion(
                        object_name=f"{table.table_name}.{column.column_name}",
                        object_type="column",
                        annotation_name="Display",
                        annotation_value=column.comment
                        or column.logical_name
                        or column.column_name,
                    )
                )
        return AnnotationSuggestionData(suggestions=suggestions)

    def generate_comment_sql(
        self,
        request: MetadataSqlGenerateRequest,
    ) -> MetadataSqlGenerateData:
        """SQL Assist コメント管理の SQL 生成を OCI Enterprise AI へ再マップする。"""
        started = time.monotonic()
        created_at = _utc_now()
        deterministic_sql = self._deterministic_comment_sql(request)
        deterministic = MetadataSqlGenerateData(
            sql=deterministic_sql,
            source="deterministic",
            warnings=[] if deterministic_sql else ["COMMENT 対象がありません。"],
            timing=self._timing(created_at, started, "comment_sql_generate"),
        )
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": deterministic.warnings
                    + ["OCI Enterprise AI が未設定のため deterministic SQL を使用しました。"]
                }
            )

        try:
            raw = self._enterprise_ai_client.generate(
                prompt=(
                    "以下の情報に基づき、Oracle COMMENT ON 文のみを生成してください。"
                    "説明文、前置き、markdown code fence は出力しないでください。"
                ),
                context=self._metadata_generation_context(request),
                system_prompt=(
                    "あなたはOracleデータベース専門家です。純粋なCOMMENT ON "
                    "TABLE/COLUMN/MATERIALIZED VIEW ステートメントのみを出力してください。"
                    "通常ビューへのコメントは COMMENT ON TABLE を使用してください。"
                    "対象は必ず OWNER.OBJECT の owner 修飾を保持してください。"
                    "表・ビューはA-Z順、列は定義順、各説明文は200字以内です。"
                ),
            )
            sql = self._clean_generated_metadata_sql(raw, "comment_sql")
            if not self._metadata_sql_preserves_targets(sql, request):
                warning = (
                    "生成 SQL が owner 修飾を保持しなかったため deterministic SQL を使用しました。"
                )
                return deterministic.model_copy(
                    update={
                        "warnings": deterministic.warnings + [warning],
                        "timing": self._timing(created_at, started, "comment_sql_generate"),
                    }
                )
            return MetadataSqlGenerateData(
                sql=sql,
                source="oci_enterprise_ai",
                warnings=[],
                timing=self._timing(created_at, started, "comment_sql_generate"),
            )
        except (EnterpriseAiDirectError, ValueError, TypeError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": deterministic.warnings
                    + [f"Enterprise AI comment SQL 生成に失敗したため fallback しました: {exc}"],
                    "timing": self._timing(created_at, started, "comment_sql_generate"),
                }
            )

    def get_metadata_samples(self, request: MetadataSqlSampleRequest) -> MetadataSqlSampleData:
        """コメント/アノテーション SQL 生成に使う列代表値を取得する。"""
        if request.sample_limit == 0:
            runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
            return MetadataSqlSampleData(runtime=runtime)

        warnings: list[str] = []
        samples: dict[str, dict[str, list[str]]]
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if self._use_oracle_runtime():
            try:
                samples, adapter_warnings = self._oracle_adapter.fetch_metadata_sample_values(
                    [target.model_dump() for target in request.targets], request.sample_limit
                )
                warnings.extend(adapter_warnings)
            except OracleAdapterError as exc:
                warnings.append(f"Oracle のサンプル再取得に失敗したため既存値を使用しました: {exc}")
                samples = self._metadata_samples_from_catalog(request)
        else:
            warnings.append("deterministic runtime のため既存のサンプル値を使用しました。")
            samples = self._metadata_samples_from_catalog(request)

        sample_text, sample_count = self._format_metadata_samples(request, samples)
        return MetadataSqlSampleData(
            sample_text=sample_text,
            sample_count=sample_count,
            runtime=runtime,
            warnings=warnings,
        )

    def _metadata_samples_from_catalog(
        self, request: MetadataSqlSampleRequest
    ) -> dict[str, dict[str, list[str]]]:
        samples: dict[str, dict[str, list[str]]] = {}
        for target in request.targets:
            table = self._find_catalog_table(
                self._db_admin_object_identity(target.object_name, target.owner).qualified_name
            )
            if table is None:
                continue
            requested_columns = {column.upper() for column in target.columns}
            values = {
                column.column_name.upper(): column.sample_values[: request.sample_limit]
                for column in table.columns
                if (not requested_columns or column.column_name.upper() in requested_columns)
                and column.sample_values
            }
            if values:
                samples[_qualified_display_name(table.owner, table.table_name)] = values
        return samples

    def _format_metadata_samples(
        self,
        request: MetadataSqlSampleRequest,
        samples: dict[str, dict[str, list[str]]],
    ) -> tuple[str, int]:
        blocks: list[str] = []
        sample_count = 0
        for target in request.targets:
            object_name = self._db_admin_object_identity(
                target.object_name, target.owner
            ).qualified_name
            legacy_name = _normalize_identifier(target.object_name)
            column_samples = samples.get(object_name, {}) or samples.get(legacy_name, {})
            display_name = (
                object_name
                if target.owner.strip() or "." in target.object_name
                else target.object_name
            )
            lines: list[str] = []
            for column in target.columns:
                values = column_samples.get(_normalize_identifier(column), [])
                if values:
                    lines.append(f"{column}: {', '.join(values)}")
                    sample_count += len(values)
            if lines:
                blocks.append(f"OBJECT: {display_name}\n" + "\n".join(lines))
        return "\n\n".join(blocks), sample_count

    def generate_annotation_sql(
        self,
        request: MetadataSqlGenerateRequest,
    ) -> MetadataSqlGenerateData:
        """SQL Assist アノテーション管理の SQL 生成を OCI Enterprise AI へ再マップする。"""
        started = time.monotonic()
        created_at = _utc_now()
        deterministic_sql = self._deterministic_annotation_sql(request)
        deterministic = MetadataSqlGenerateData(
            sql=deterministic_sql,
            source="deterministic",
            warnings=[] if deterministic_sql else ["ANNOTATIONS 対象がありません。"],
            timing=self._timing(created_at, started, "annotation_sql_generate"),
        )
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": deterministic.warnings
                    + ["OCI Enterprise AI が未設定のため deterministic SQL を使用しました。"]
                }
            )
        try:
            has_samples = bool(request.sample_text.strip())
            raw = self._enterprise_ai_client.generate(
                prompt=(
                    "以下の情報に基づき、Oracle ALTER TABLE/ALTER VIEW/"
                    "ALTER MATERIALIZED VIEW の ANNOTATIONS 文のみを"
                    "生成してください。\n\n"
                    "出力ルール:\n"
                    "- 純粋な ALTER TABLE/ALTER VIEW/ALTER MATERIALIZED VIEW "
                    "ANNOTATIONS ステートメントのみを出力\n"
                    "- Markdown 記号、説明文、前置きは出力しない\n"
                    "- テーブル・ビューは A-Z 順、列は定義順で出力\n"
                    "- ビュー列の annotation は生成しない\n\n"
                    "annotation の割り当て:\n"
                    "- COMMENT: は入力メタデータの項目名であり、annotation 名として使用しない\n"
                    "- 表・ビュー・列の説明や表示名には UI_Display を使用\n"
                    "- 列型には data_type、NULL 可否には nullable を使用\n"
                    + (
                        "- サンプルがあるため sample_header / sample_data を生成可能\n"
                        if has_samples
                        else "- サンプルが無いため sample_header / sample_data を生成しない\n"
                    )
                    + "- annotation 名 COMMENT は生成しない\n\n"
                    "参考例:\n"
                    "ALTER TABLE T1 ANNOTATIONS (ADD OR REPLACE UI_Display 'Table 1');\n"
                    "ALTER TABLE T1 MODIFY (ID ANNOTATIONS "
                    "(ADD OR REPLACE UI_Display 'ID', ADD OR REPLACE data_type 'NUMBER', "
                    "ADD OR REPLACE nullable 'N'));\n"
                    "ALTER VIEW SALES_V ANNOTATIONS (ADD OR REPLACE UI_Display 'Sales View');\n"
                    "ALTER MATERIALIZED VIEW SALES_MV ANNOTATIONS "
                    "(ADD OR REPLACE UI_Display 'Sales MV');"
                ),
                context=self._metadata_generation_context(request),
                system_prompt=(
                    "あなたは Oracle Database の専門家です。純粋な annotation SQL のみを"
                    "出力してください。\n"
                    "テーブル: ALTER TABLE <表> ANNOTATIONS (<annotation>);\n"
                    "列: ALTER TABLE <表> MODIFY (<列> ANNOTATIONS (<annotation>));\n"
                    "ビュー: ALTER VIEW <ビュー> ANNOTATIONS (<annotation>);\n"
                    "マテリアライズドビュー: ALTER MATERIALIZED VIEW <MV> "
                    "ANNOTATIONS (<annotation>);\n"
                    "ビュー/MV 列への annotation は生成しないでください。\n"
                    "対象は必ず OWNER.OBJECT の owner 修飾を保持してください。"
                    "更新を反映する生成 SQL では ADD OR REPLACE を使用してください。"
                    "ADD / DROP / REPLACE も使用できます。annotation 名は Oracle 識別子です。"
                    "予約語や空白を含む名前は二重引用符で囲み、未引用の COMMENT は禁止します。"
                    "値は最大4000文字で、値内の単一引用符は '' にエスケープしてください。"
                    "複数 annotation は同じ括弧内へカンマ区切りで指定できます。"
                ),
            )
            sql = self._clean_generated_metadata_sql(
                raw,
                "annotation_sql",
                has_annotation_samples=has_samples,
            )
            if not self._metadata_sql_preserves_targets(sql, request):
                warning = (
                    "生成 SQL が owner 修飾を保持しなかったため deterministic SQL を使用しました。"
                )
                return deterministic.model_copy(
                    update={
                        "warnings": deterministic.warnings + [warning],
                        "timing": self._timing(created_at, started, "annotation_sql_generate"),
                    }
                )
            return MetadataSqlGenerateData(
                sql=sql,
                source="oci_enterprise_ai",
                warnings=[],
                timing=self._timing(created_at, started, "annotation_sql_generate"),
            )
        except (EnterpriseAiDirectError, ValueError, TypeError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": deterministic.warnings
                    + [f"Enterprise AI annotation SQL 生成に失敗したため fallback しました: {exc}"],
                    "timing": self._timing(created_at, started, "annotation_sql_generate"),
                }
            )

    def _metadata_generation_context(self, request: MetadataSqlGenerateRequest) -> str:
        targets = ", ".join(
            (
                f"{target.object_type}:"
                f"{self._db_admin_object_identity(target.object_name, target.owner).qualified_name}"
            )
            for target in request.targets
        )
        return "\n\n".join(
            [
                f"targets: {targets or 'ALL'}",
                "<構造>\n" + request.structure_text,
                "<主キー>\n" + request.primary_key_text,
                "<外部キー>\n" + request.foreign_key_text,
                "<サンプル>\n" + request.sample_text,
                "<追加入力>\n" + request.extra_text,
            ]
        )

    def _metadata_sql_preserves_targets(
        self,
        sql: str,
        request: MetadataSqlGenerateRequest,
    ) -> bool:
        if not request.targets:
            return True
        identities = [
            self._db_admin_object_identity(target.object_name, target.owner)
            for target in request.targets
        ]
        current_owner = self._current_schema_owner()
        if all(
            identity.owner == current_owner
            and not target.owner.strip()
            and "." not in target.object_name
            for identity, target in zip(identities, request.targets, strict=True)
        ):
            return True
        normalized_sql = re.sub(r"\s*\.\s*", ".", sql.replace('"', "")).upper()
        for identity in identities:
            expected = re.sub(r"\s*\.\s*", ".", identity.qualified_name.replace('"', "")).upper()
            pattern = rf"(?<![A-Z0-9_$#]){re.escape(expected)}(?![A-Z0-9_$#])"
            if not re.search(pattern, normalized_sql):
                return False
        return True

    def _selected_metadata_tables(self, request: MetadataSqlGenerateRequest) -> list[SchemaTable]:
        if not request.targets:
            return list(self._catalog.tables)
        selected: list[SchemaTable] = []
        for target in request.targets:
            table = self._find_catalog_table(
                self._db_admin_object_identity(target.object_name, target.owner).qualified_name
            )
            if table is not None:
                selected.append(table)
        return selected

    def _metadata_target_types(self, request: MetadataSqlGenerateRequest) -> dict[str, str]:
        target_types: dict[str, str] = {}
        for target in request.targets:
            identity = self._db_admin_object_identity(target.object_name, target.owner)
            table = self._find_catalog_table(identity.qualified_name)
            target_types[identity.qualified_name] = _catalog_metadata_object_kind(
                table,
                target.object_type,
            )
        return target_types

    def _metadata_input_objects(self, request: MetadataSqlGenerateRequest) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in request.structure_text.splitlines():
            line = raw_line.strip()
            if line.startswith("OBJECT:"):
                current = {
                    "name": line.removeprefix("OBJECT:").strip(),
                    "type": "table",
                    "comment": "",
                    "columns": [],
                }
                objects.append(current)
                continue
            if current is None:
                continue
            if line.startswith("TYPE:"):
                current["type"] = line.removeprefix("TYPE:").strip().lower() or "table"
            elif line.startswith("COMMENT:"):
                comment = line.removeprefix("COMMENT:").strip()
                current["comment"] = "" if comment == "-" else comment
            elif line.startswith("- "):
                column_name = line[2:].split(":", 1)[0].strip()
                match = re.search(r"\sCOMMENT=(.*)$", line)
                comment = match.group(1).strip() if match else ""
                current["columns"].append(
                    {"name": column_name, "comment": "" if comment == "-" else comment}
                )
        return [item for item in objects if item.get("name")]

    def _deterministic_comment_sql(self, request: MetadataSqlGenerateRequest) -> str:
        statements: list[str] = []
        target_types = self._metadata_target_types(request)
        selected_tables = self._selected_metadata_tables(request)
        for table in selected_tables:
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            object_kind = _comment_ddl_kind_for_metadata(
                _metadata_object_kind(target_types.get(identity.qualified_name, table.table_type))
            )
            object_comment = table.comment or table.logical_name or table.table_name
            if object_comment:
                statements.append(
                    f"COMMENT ON {object_kind} {_quote_object_identity(identity)} "
                    f"IS {_quote_sql_string(object_comment)};"
                )
            for column in table.columns:
                column_comment = column.comment or column.logical_name or column.column_name
                if column_comment:
                    statements.append(
                        f"COMMENT ON COLUMN {_quote_object_identity(identity)}."
                        f"{_quote_identifier(column.column_name)} IS "
                        f"{_quote_sql_string(column_comment)};"
                    )
        if selected_tables:
            return "\n".join(statements)
        for item in self._metadata_input_objects(request):
            identity = self._db_admin_object_identity(str(item["name"]))
            object_kind = _comment_ddl_kind_for_metadata(_metadata_object_kind(str(item["type"])))
            object_comment = item["comment"] or item["name"]
            statements.append(
                f"COMMENT ON {object_kind} {_quote_object_identity(identity)} "
                f"IS {_quote_sql_string(object_comment)};"
            )
            for column in item["columns"]:
                column_comment = column["comment"] or column["name"]
                statements.append(
                    f"COMMENT ON COLUMN {_quote_object_identity(identity)}."
                    f"{_quote_identifier(column['name'])} IS "
                    f"{_quote_sql_string(column_comment)};"
                )
        return "\n".join(statements)

    def _deterministic_annotation_sql(self, request: MetadataSqlGenerateRequest) -> str:
        statements: list[str] = []
        target_types = self._metadata_target_types(request)
        selected_tables = sorted(
            self._selected_metadata_tables(request),
            key=lambda table: (table.owner.upper(), table.table_name.upper()),
        )
        for table in selected_tables:
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            object_value = table.comment or table.logical_name or table.table_name
            object_type = _metadata_object_kind(
                target_types.get(identity.qualified_name, table.table_type)
            )
            if object_type != "table":
                statements.append(
                    f"ALTER {_annotation_ddl_kind_for_metadata(object_type)} "
                    f"{_quote_object_identity(identity)} "
                    "ANNOTATIONS (ADD OR REPLACE UI_Display "
                    f"{_quote_sql_string(object_value)});"
                )
                continue
            statements.append(
                f"ALTER TABLE {_quote_object_identity(identity)} "
                "ANNOTATIONS (ADD OR REPLACE UI_Display "
                f"{_quote_sql_string(object_value)});"
            )
            for column in table.columns:
                column_value = column.comment or column.logical_name or column.column_name
                statements.append(
                    f"ALTER TABLE {_quote_object_identity(identity)} "
                    f"MODIFY ({_quote_identifier(column.column_name)} "
                    "ANNOTATIONS (ADD OR REPLACE UI_Display "
                    f"{_quote_sql_string(column_value)}));"
                )
        if selected_tables:
            return "\n".join(statements)
        for item in sorted(
            self._metadata_input_objects(request),
            key=lambda value: str(value["name"]).upper(),
        ):
            identity = self._db_admin_object_identity(str(item["name"]))
            object_value = item["comment"] or item["name"]
            object_type = _metadata_object_kind(str(item["type"]))
            if object_type != "table":
                statements.append(
                    f"ALTER {_annotation_ddl_kind_for_metadata(object_type)} "
                    f"{_quote_object_identity(identity)} "
                    "ANNOTATIONS (ADD OR REPLACE UI_Display "
                    f"{_quote_sql_string(object_value)});"
                )
                continue
            statements.append(
                f"ALTER TABLE {_quote_object_identity(identity)} "
                "ANNOTATIONS (ADD OR REPLACE UI_Display "
                f"{_quote_sql_string(object_value)});"
            )
            for column in item["columns"]:
                column_value = column["comment"] or column["name"]
                statements.append(
                    f"ALTER TABLE {_quote_object_identity(identity)} "
                    f"MODIFY ({_quote_identifier(column['name'])} "
                    "ANNOTATIONS (ADD OR REPLACE UI_Display "
                    f"{_quote_sql_string(column_value)}));"
                )
        return "\n".join(statements)

    def _clean_generated_metadata_sql(
        self,
        raw: str,
        policy: str,
        *,
        has_annotation_samples: bool = True,
    ) -> str:
        cleaned = self._strip_code_fence(raw)
        statements = []
        for statement in _split_sql_statements(cleaned):
            candidate = statement
            if policy == "comment_sql":
                candidate = _rewrite_comment_on_view_statement(candidate)
            if policy == "annotation_sql" and not has_annotation_samples:
                candidate = _without_sample_annotations(candidate)
                if not candidate:
                    continue
            policy_error = _db_admin_policy_error(candidate, policy)
            if policy_error:
                if policy == "annotation_sql":
                    raise ValueError(policy_error)
                continue
            if policy == "annotation_sql":
                candidate = _normalize_annotation_add_operations(candidate)
            statements.append(candidate.rstrip(";") + ";")
        if not statements:
            raise ValueError("許可された metadata SQL が生成されませんでした。")
        return "\n".join(statements)

    def apply_comments(self, request: CommentApplyRequest) -> CommentApplyData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        statements: list[CommentApplyStatement] = []
        for item in request.items:
            try:
                statements.append(self._comment_statement(item))
            except ValueError as exc:
                warnings.append(str(exc))

        executed = False
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        runtime = "deterministic"
        if statements:
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target="ADMIN_EXECUTE",
            )
            if confirmation_error:
                warnings.append(confirmation_error)
                statements = [
                    statement.model_copy(update={"status": "confirmation_required"})
                    for statement in statements
                ]
            elif not self._use_oracle_runtime():
                warnings.append("COMMENT ON の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
                statements = [
                    statement.model_copy(update={"status": "requires_oracle"})
                    for statement in statements
                ]
            else:
                runtime = "oracle"
                try:
                    oracle_results = _align_admin_statement_results(
                        self._oracle_adapter.execute_admin_statements(
                            [statement.sql for statement in statements],
                            atomic=False,
                        ),
                        len(statements),
                    )
                    applied_sql: list[str] = []
                    updated_statements: list[CommentApplyStatement] = []
                    for index, statement in enumerate(statements):
                        result = oracle_results[index]
                        if _admin_statement_result_succeeded(result):
                            applied_sql.append(statement.sql)
                            updated_statements.append(
                                statement.model_copy(
                                    update={"status": "applied", "error_message": ""}
                                )
                            )
                            continue
                        error_message = (
                            _admin_statement_result_error_message(result)
                            or "COMMENT ON の適用に失敗しました。"
                        )
                        updated_statements.append(
                            statement.model_copy(
                                update={
                                    "status": "error",
                                    "error_message": error_message,
                                }
                            )
                        )
                    statements = updated_statements
                    executed = bool(applied_sql)
                    if applied_sql:
                        try:
                            self._record_admin_audit(
                                operation="comments_apply",
                                target="ADMIN_EXECUTE",
                                executed=True,
                                reason=request.reason,
                                detail={
                                    "statement_count": len(statements),
                                    "success_count": len(applied_sql),
                                    "error_count": len(statements) - len(applied_sql),
                                },
                            )
                        except (
                            Nl2SqlPersistenceUnavailable,
                            Nl2SqlRepositoryOperationFailed,
                        ) as exc:
                            warnings.append(f"COMMENT 適用監査の保存に失敗しました: {exc}")
                        try:
                            sync = self._submit_schema_refresh_after_admin_mutation(
                                target_objects=_schema_refresh_targets_for_statements(
                                    applied_sql,
                                    current_owner=self._current_schema_owner(),
                                ),
                                source="comments_apply",
                            )
                            schema_refresh_job_id = sync.job_id
                            schema_refresh_required = sync.required
                            schema_refresh_reason_code = sync.reason_code
                            if sync.required:
                                warnings.append(_schema_refresh_required_warning(sync.reason_code))
                        except Nl2SqlPersistenceUnavailable as exc:
                            warnings.append(
                                f"COMMENT 適用後の Schema job 投入に失敗しました: {exc}"
                            )
                    if 0 < len(applied_sql) < len(statements):
                        warnings.append(
                            "COMMENT は部分的に成功しました"
                            f"({len(applied_sql)}/{len(statements)} 件)。"
                        )
                    elif not applied_sql:
                        warnings.append("COMMENT は Oracle へ適用されませんでした。")
                except OracleAdapterError as exc:
                    warnings.append(str(exc))
                    statements = [
                        statement.model_copy(update={"status": "error", "error_message": str(exc)})
                        for statement in statements
                    ]
        else:
            warnings.append("適用対象の COMMENT がありません。")

        if not request.items:
            warnings.append("COMMENT 対象が指定されていません。")

        finished_at = _utc_now()
        return CommentApplyData(
            executed=executed,
            runtime=runtime,
            statements=statements,
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            warnings=warnings,
            timing=TimingEnvelope(
                created_at=created_at,
                started_at=created_at,
                finished_at=finished_at,
                elapsed_ms=_elapsed_ms(started),
                stage_timings=[StageTiming(stage="comments", elapsed_ms=_elapsed_ms(started))],
            ),
        )

    def _comment_statement(self, item: CommentApplyItem) -> CommentApplyStatement:
        object_type = item.object_type.strip().lower()
        comment = item.comment.strip()
        if not comment:
            raise ValueError(f"{item.object_name}: コメントが空です。")
        _validate_oracle_metadata_literal_bytes(
            comment,
            target=item.object_name,
            label="コメント",
        )
        if object_type in {"table", "view", "materialized_view", "materialized view"}:
            table = self._find_catalog_table(item.object_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない object です。")
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            catalog_object_type = _catalog_metadata_object_kind(table, object_type)
            return CommentApplyStatement(
                object_name=identity.qualified_name,
                object_type=catalog_object_type,
                comment=comment,
                sql=(
                    f"COMMENT ON {_comment_ddl_kind_for_metadata(catalog_object_type)} "
                    f"{_quote_object_identity(identity)} "
                    f"IS {_quote_sql_string(comment)};"
                ),
            )
        if object_type == "column":
            table_name, column_name = self._split_comment_column_name(item.object_name)
            table = self._find_catalog_table(table_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない table です。")
            column = self._find_catalog_column(table, column_name)
            if column is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない column です。")
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            return CommentApplyStatement(
                object_name=f"{identity.qualified_name}.{column.column_name}",
                object_type="column",
                comment=comment,
                sql=(
                    f"COMMENT ON COLUMN {_quote_object_identity(identity)}."
                    f"{_quote_identifier(column.column_name)} IS {_quote_sql_string(comment)};"
                ),
            )
        raise ValueError(
            f"{item.object_name}: object_type は table/view/materialized_view/column "
            "のみ指定できます。"
        )

    def apply_annotations(self, request: AnnotationApplyRequest) -> AnnotationApplyData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        statements: list[AnnotationApplyStatement] = []
        for item in request.items:
            try:
                statements.append(self._annotation_statement(item))
            except ValueError as exc:
                warnings.append(str(exc))

        executed = False
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        runtime = "deterministic"
        if statements:
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target="ADMIN_EXECUTE",
            )
            if confirmation_error:
                warnings.append(confirmation_error)
                statements = [
                    statement.model_copy(update={"status": "confirmation_required"})
                    for statement in statements
                ]
            elif not self._use_oracle_runtime():
                warnings.append("ANNOTATIONS の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
                statements = [
                    statement.model_copy(update={"status": "requires_oracle"})
                    for statement in statements
                ]
            else:
                runtime = "oracle"
                try:
                    oracle_results = _align_admin_statement_results(
                        self._oracle_adapter.execute_admin_statements(
                            [statement.sql for statement in statements],
                            atomic=False,
                        ),
                        len(statements),
                    )
                    applied_sql: list[str] = []
                    updated_statements: list[AnnotationApplyStatement] = []
                    for index, statement in enumerate(statements):
                        result = oracle_results[index]
                        if _admin_statement_result_succeeded(result):
                            applied_sql.append(statement.sql)
                            updated_statements.append(
                                statement.model_copy(
                                    update={"status": "applied", "error_message": ""}
                                )
                            )
                            continue
                        error_message = (
                            _admin_statement_result_error_message(result)
                            or "ANNOTATIONS の適用に失敗しました。"
                        )
                        updated_statements.append(
                            statement.model_copy(
                                update={
                                    "status": "error",
                                    "error_message": error_message,
                                }
                            )
                        )
                    statements = updated_statements
                    executed = bool(applied_sql)
                    if applied_sql:
                        try:
                            self._record_admin_audit(
                                operation="annotations_apply",
                                target="ADMIN_EXECUTE",
                                executed=True,
                                reason=request.reason,
                                detail={
                                    "statement_count": len(statements),
                                    "success_count": len(applied_sql),
                                    "error_count": len(statements) - len(applied_sql),
                                },
                            )
                        except (
                            Nl2SqlPersistenceUnavailable,
                            Nl2SqlRepositoryOperationFailed,
                        ) as exc:
                            warnings.append(f"ANNOTATION 適用監査の保存に失敗しました: {exc}")
                        try:
                            sync = self._submit_schema_refresh_after_admin_mutation(
                                target_objects=_schema_refresh_targets_for_statements(
                                    applied_sql,
                                    current_owner=self._current_schema_owner(),
                                ),
                                source="annotations_apply",
                            )
                            schema_refresh_job_id = sync.job_id
                            schema_refresh_required = sync.required
                            schema_refresh_reason_code = sync.reason_code
                            if sync.required:
                                warnings.append(_schema_refresh_required_warning(sync.reason_code))
                        except Nl2SqlPersistenceUnavailable as exc:
                            warnings.append(
                                f"ANNOTATION 適用後の Schema job 投入に失敗しました: {exc}"
                            )
                    if 0 < len(applied_sql) < len(statements):
                        warnings.append(
                            "ANNOTATIONS は部分的に成功しました"
                            f"({len(applied_sql)}/{len(statements)} 件)。"
                        )
                    elif not applied_sql:
                        warnings.append("ANNOTATIONS は Oracle へ適用されませんでした。")
                except OracleAdapterError as exc:
                    warnings.append(str(exc))
                    statements = [
                        statement.model_copy(update={"status": "error", "error_message": str(exc)})
                        for statement in statements
                    ]
        else:
            warnings.append("適用対象の ANNOTATIONS がありません。")

        if not request.items:
            warnings.append("ANNOTATIONS 対象が指定されていません。")

        finished_at = _utc_now()
        return AnnotationApplyData(
            executed=executed,
            runtime=runtime,
            statements=statements,
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            warnings=warnings,
            timing=TimingEnvelope(
                created_at=created_at,
                started_at=created_at,
                finished_at=finished_at,
                elapsed_ms=_elapsed_ms(started),
                stage_timings=[StageTiming(stage="annotations", elapsed_ms=_elapsed_ms(started))],
            ),
        )

    def _annotation_statement(self, item: AnnotationApplyItem) -> AnnotationApplyStatement:
        object_type = item.object_type.strip().lower()
        annotation_name = self._annotation_name(item.annotation_name)
        annotation_value = item.annotation_value.strip()
        if not annotation_value:
            raise ValueError(f"{item.object_name}: annotation value が空です。")
        _validate_oracle_metadata_literal_bytes(
            annotation_value,
            target=item.object_name,
            label="annotation value",
        )
        if object_type in {"table", "view", "materialized_view", "materialized view"}:
            table = self._find_catalog_table(item.object_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない object です。")
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            catalog_object_type = _catalog_metadata_object_kind(table, object_type)
            return AnnotationApplyStatement(
                object_name=identity.qualified_name,
                object_type=catalog_object_type,
                annotation_name=annotation_name,
                annotation_value=annotation_value,
                sql=(
                    f"ALTER {_annotation_ddl_kind_for_metadata(catalog_object_type)} "
                    f"{_quote_object_identity(identity)} "
                    f"ANNOTATIONS (ADD OR REPLACE {annotation_name} "
                    f"{_quote_sql_string(annotation_value)});"
                ),
            )
        if object_type == "column":
            table_name, column_name = self._split_comment_column_name(item.object_name)
            table = self._find_catalog_table(table_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない table です。")
            catalog_object_type = _catalog_metadata_object_kind(table)
            if catalog_object_type != "table":
                raise ValueError(
                    f"{item.object_name}: ビュー/MV の列 annotation は生成できません。"
                )
            column = self._find_catalog_column(table, column_name)
            if column is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない column です。")
            identity = OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
            return AnnotationApplyStatement(
                object_name=f"{identity.qualified_name}.{column.column_name}",
                object_type="column",
                annotation_name=annotation_name,
                annotation_value=annotation_value,
                sql=(
                    f"ALTER TABLE {_quote_object_identity(identity)} "
                    f"MODIFY ({_quote_identifier(column.column_name)} "
                    f"ANNOTATIONS (ADD OR REPLACE {annotation_name} "
                    f"{_quote_sql_string(annotation_value)}));"
                ),
            )
        raise ValueError(
            f"{item.object_name}: object_type は table/view/materialized_view/column "
            "のみ指定できます。"
        )

    def _annotation_name(self, value: str) -> str:
        normalized = value.strip().replace('"', "").upper()
        if not _STRICT_IDENTIFIER.fullmatch(normalized):
            raise ValueError(f"{value}: annotation name が不正です。")
        return normalized

    def _split_comment_column_name(self, object_name: str) -> tuple[str, str]:
        parts = _split_object_ref_parts(object_name)
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 3:
            return ".".join(parts[:2]), parts[2]
        raise ValueError(
            f"{object_name}: column は TABLE.COLUMN または OWNER.TABLE.COLUMN "
            "形式で指定してください。"
        )

    def _find_catalog_table(self, table_name: str) -> SchemaTable | None:
        current_owner = self._current_schema_owner()
        identity = parse_object_identity(table_name.strip(), default_owner=current_owner)
        normalized_owner = identity.owner
        normalized = identity.object_name
        if not is_user_visible_schema_object(normalized_owner, normalized):
            return None
        return next(
            (
                table
                for table in self._catalog.tables
                if table.owner == normalized_owner and table.table_name == normalized
            ),
            None,
        )

    def _management_catalog_tables(self) -> list[SchemaTable]:
        """DDL/COMMENT/ANNOTATION 管理画面から見せる可視 catalog。"""

        return [
            table
            for table in self._catalog.tables
            if is_user_visible_schema_object(table.owner, table.table_name)
        ]

    def _synthetic_unsupported_columns(self, table: SchemaTable) -> list[SchemaColumn]:
        return [
            column
            for column in table.columns
            if _synthetic_data_type_key(column.data_type) in _SYNTHETIC_DATA_UNSUPPORTED_DATA_TYPES
        ]

    def _find_catalog_column(self, table: SchemaTable, column_name: str) -> SchemaColumn | None:
        normalized = _normalize_identifier(column_name)
        return next(
            (column for column in table.columns if column.column_name == normalized),
            None,
        )

    def generate_synthetic_data(
        self, request: SyntheticDataGenerateRequest
    ) -> SyntheticDataOperationData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        requested_source = (
            [request.table_name] if request.table_name.strip() else request.object_list
        )
        safe_objects: list[str] = []
        for raw_object_name in requested_source:
            if not raw_object_name.strip():
                continue
            identity = self._db_admin_object_identity(raw_object_name)
            object_name = identity.qualified_name
            table = self._find_catalog_table(object_name)
            if table is None:
                warnings.append(f"{object_name}: catalog に存在しない table です。")
                safe_objects.append(object_name)
                continue
            unsupported_columns = self._synthetic_unsupported_columns(table)
            if unsupported_columns:
                column_text = ", ".join(
                    f"{column.column_name}({column.data_type})" for column in unsupported_columns
                )
                warnings.append(
                    f"{object_name}: DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA は "
                    f"{column_text} を含む table をサポートしません。"
                    "BLOB/CLOB/RAW/VECTOR などを除いた view または別 table を対象にしてください。"
                )
                continue
            safe_objects.append(object_name)
        safe_table_name = safe_objects[0] if safe_objects else ""
        if not safe_objects:
            warnings.append("synthetic data の対象にできる table がありません。")
        executed = False
        status = "error"
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        row_count = request.rows_per_table or request.row_count
        profile_name = request.profile_name.strip()
        if not profile_name and request.profile_id:
            profile_name = self._select_ai_profile_name(self.get_profile(request.profile_id))
        prompt = "\n".join(
            part for part in [request.user_prompt.strip(), request.extra_prompt.strip()] if part
        )
        object_summary = ", ".join(safe_objects) or "-"
        message = f"{object_summary} に {row_count} 行/表の synthetic data を生成する plan です。"
        if not safe_objects:
            message = "synthetic data の対象にできる table がありません。"
        if safe_objects:
            # 単一テーブル指定(table_name)は対象名の入力を必須にする。複数テーブル
            # 指定(object_list)は単一の対象名が存在しないため ADMIN_EXECUTE で確認
            # する。判定はユーザーが確認した時点の request の形に合わせる(skip に
            # よる絞り込み後の件数では変えない)。
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=safe_table_name if request.table_name.strip() else "ADMIN_EXECUTE",
            )
            if (
                confirmation_error
                and request.table_name.strip()
                and safe_table_name.split(".", 1)[0] == self._current_schema_owner()
            ):
                confirmation_error = self._admin_confirmation_error(
                    confirmation=request.confirmation,
                    target=safe_table_name.split(".", 1)[1],
                )
            if confirmation_error:
                status = "confirmation_required"
                warnings.append(confirmation_error)
            elif not self._use_oracle_runtime():
                status = "requires_oracle"
                warnings.append(
                    "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の実行には "
                    "NL2SQL_RUNTIME_MODE=oracle が必要です。"
                )
            elif not profile_name:
                raise ValueError(
                    "DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA の実行には "
                    "Select AI profile 名が必要です。"
                )
            else:
                try:
                    use_object_list = not request.table_name.strip() and len(safe_objects) > 1
                    raw_engine_meta = self._oracle_adapter.generate_synthetic_data(
                        table_name="" if use_object_list else safe_table_name,
                        object_list=safe_objects if use_object_list else [],
                        row_count=row_count,
                        profile_name=profile_name,
                        user_prompt=prompt,
                        sample_rows=request.sample_rows,
                        use_comments=request.use_comments,
                    )
                    engine_meta.update(
                        {
                            key: value
                            for key, value in raw_engine_meta.items()
                            if key not in {"operation_id", "operationId"}
                        }
                    )
                    executed = True
                    status = "executed"
                    message = "DBMS_CLOUD_AI synthetic data generation を実行しました。"
                    self._record_admin_audit(
                        operation="synthetic_data_generate",
                        target=",".join(safe_objects),
                        executed=True,
                        reason=request.reason,
                        detail={
                            "profile_name": profile_name,
                            "row_count": row_count,
                        },
                    )
                except OracleAdapterError as exc:
                    status = "error"
                    warnings.append(str(exc))
        return SyntheticDataOperationData(
            table_name=safe_table_name,
            object_list=safe_objects,
            row_count=row_count,
            executed=executed,
            runtime=runtime,
            status=status,
            message=message,
            warnings=warnings,
            engine_meta=engine_meta,
            timing=self._timing(created_at, started, "synthetic_data"),
        )

    def synthetic_data_results(self, table_name: str, limit: int = 100) -> SyntheticDataResultsData:
        identity = self._db_admin_object_identity(table_name)
        safe_table_name = identity.qualified_name
        quoted_table_name = _quote_object_identity(identity)
        sql = normalize_executable_sql(f"SELECT * FROM {quoted_table_name}")  # nosec B608
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                results = self._oracle_adapter.execute_select(sql, limit)
                return SyntheticDataResultsData(
                    table_name=safe_table_name,
                    runtime="oracle",
                    results=results,
                    warnings=warnings,
                )
            except OracleAdapterError:
                raise
        return SyntheticDataResultsData(
            table_name=safe_table_name,
            runtime="deterministic",
            results=self._mock_execute(sql, min(limit, 20)),
            warnings=warnings
            or ["Oracle runtime ではないため deterministic result preview を返しました。"],
        )

    def diagnostics(self) -> DiagnosticsData:
        env = dotenv_values(Path(".env"))

        def check_present(name: str, label: str) -> DiagnosticCheck:
            value = str(env.get(name) or "").strip()
            return DiagnosticCheck(
                name=name,
                status="ok" if value else "warning",
                message=f"{label} は設定済みです。" if value else f"{label} が未設定です。",
            )

        settings = get_settings()
        oracle_configured = self._oracle_adapter.is_configured()
        oracle_module_available = self._oracle_adapter.module_available()
        embedding_configured = self._embedding_client.is_configured()
        embedding_module_available = self._embedding_client.module_available()
        enterprise_ai_configured = self._enterprise_ai_client.is_configured()
        uses_oracle_runtime = self._use_oracle_runtime()
        with self._lock:
            select_ai_asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI)
            agent_asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        select_ai_assets_ready = (
            select_ai_asset_meta is not None
            and select_ai_asset_meta.refreshed
            and select_ai_asset_meta.status == "ready"
        )
        agent_assets_ready = (
            agent_asset_meta is not None
            and agent_asset_meta.refreshed
            and agent_asset_meta.status == "ready"
        )
        oracle_live_ok = False
        oracle_live_message = "deterministic runtime のため live 接続は未確認です。"
        if uses_oracle_runtime:
            oracle_live_ok, oracle_live_message = self._oracle_adapter.test_connection()
        persistence_ready, persistence_message = self._store.check()
        checks = [
            check_present("ORACLE_DSN", "Oracle DSN"),
            check_present("ORACLE_USER", "Oracle user"),
            check_present("ORACLE_ADB_OCID", "ADB OCID"),
            check_present("OCI_REGION", "OCI region"),
            check_present("OCI_COMPARTMENT_ID", "OCI compartment"),
            DiagnosticCheck(
                name="OCI_ENTERPRISE_AI_ENDPOINT",
                status="ok" if settings.oci_enterprise_ai_endpoint.strip() else "warning",
                message=(
                    "OCI Enterprise AI endpoint は設定済みです。"
                    if settings.oci_enterprise_ai_endpoint.strip()
                    else "OCI Enterprise AI endpoint が未設定です。"
                ),
            ),
            DiagnosticCheck(
                name="OCI_ENTERPRISE_AI_API_KEY",
                status="ok" if settings.oci_enterprise_ai_api_key.strip() else "warning",
                message=(
                    "OCI Enterprise AI API key は設定済みです。"
                    if settings.oci_enterprise_ai_api_key.strip()
                    else "OCI Enterprise AI API key が未設定です。"
                ),
            ),
            DiagnosticCheck(
                name="OCI_ENTERPRISE_AI_LLM_MODEL",
                status="ok" if self._enterprise_ai_client.model_id() else "warning",
                message=(
                    f"OCI Enterprise AI LLM model は {self._enterprise_ai_client.model_id()} です。"
                    if self._enterprise_ai_client.model_id()
                    else "OCI Enterprise AI LLM model が未設定です。"
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_PERSISTENCE_MODE",
                status=(
                    "ok"
                    if settings.nl2sql_persistence_mode.strip().lower()
                    in {"memory", "in_memory", "deterministic", "oracle"}
                    else "warning"
                ),
                message=f"persistence mode は {self._store.mode} です。",
            ),
            DiagnosticCheck(
                name="NL2SQL_PERSISTENCE_READY",
                status="ok" if persistence_ready else "warning",
                message=persistence_message,
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_ENABLED",
                status="ok" if settings.nl2sql_select_ai_enabled else "warning",
                message=(
                    "Select AI engine は有効です。"
                    if settings.nl2sql_select_ai_enabled
                    else "Select AI engine は無効です。"
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_PROVIDER",
                status="ok" if settings.nl2sql_select_ai_provider else "warning",
                message=(
                    f"Select AI provider は {settings.nl2sql_select_ai_provider} です。"
                    if settings.nl2sql_select_ai_provider
                    else "Select AI provider が未設定です。"
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_CREDENTIAL_NAME",
                status=(
                    "ok"
                    if settings.nl2sql_select_ai_credential_name or not uses_oracle_runtime
                    else "warning"
                ),
                message=(
                    "Select AI credential name は設定済みです。"
                    if settings.nl2sql_select_ai_credential_name
                    else (
                        "Oracle runtime では Select AI credential name の設定を推奨します。"
                        if uses_oracle_runtime
                        else "deterministic runtime のため credential name は任意です。"
                    )
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_PROFILE_REFRESHED",
                status="ok" if (select_ai_assets_ready or not uses_oracle_runtime) else "warning",
                message=(
                    f"Select AI profile は {select_ai_asset_meta.profile_name} として更新済みです。"
                    if select_ai_assets_ready and select_ai_asset_meta is not None
                    else (
                        "deterministic runtime のため Select AI profile refresh は任意です。"
                        if not uses_oracle_runtime
                        else "Select AI profile refresh がこの app state では未確認です。"
                    )
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_AGENT_ENABLED",
                status="ok" if settings.nl2sql_select_ai_agent_enabled else "warning",
                message=(
                    "Select AI Agent engine は有効です。"
                    if settings.nl2sql_select_ai_agent_enabled
                    else "Select AI Agent engine は無効です。"
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_SELECT_AI_AGENT_ASSETS_REFRESHED",
                status="ok" if (agent_assets_ready or not uses_oracle_runtime) else "warning",
                message=(
                    f"Select AI Agent team は {agent_asset_meta.team_name} として更新済みです。"
                    if agent_assets_ready and agent_asset_meta is not None
                    else (
                        "deterministic runtime のため Agent assets refresh は任意です。"
                        if not uses_oracle_runtime
                        else "Select AI Agent assets refresh がこの app state では未確認です。"
                    )
                ),
            ),
            DiagnosticCheck(
                name="NL2SQL_RUNTIME_MODE",
                status=(
                    "ok"
                    if settings.nl2sql_runtime_mode.strip().lower() in {"deterministic", "oracle"}
                    else "warning"
                ),
                message=f"runtime mode は {settings.nl2sql_runtime_mode} です。",
            ),
            DiagnosticCheck(
                name="PYTHON_ORACLEDB",
                status="ok" if oracle_module_available else "warning",
                message=(
                    "python-oracledb は利用可能です。"
                    if oracle_module_available
                    else "python-oracledb が見つかりません。Oracle runtime には追加が必要です。"
                ),
            ),
            DiagnosticCheck(
                name="ORACLE_RUNTIME_READY",
                status=(
                    "ok"
                    if (not uses_oracle_runtime) or (oracle_configured and oracle_live_ok)
                    else "warning"
                ),
                message=oracle_live_message,
            ),
            DiagnosticCheck(
                name="NL2SQL_FEEDBACK_EMBEDDING_ENABLED",
                status="ok" if settings.nl2sql_feedback_embedding_enabled else "warning",
                message=(
                    "feedback embedding は有効です。"
                    if settings.nl2sql_feedback_embedding_enabled
                    else "feedback embedding は無効です。"
                ),
            ),
            DiagnosticCheck(
                name="OCI_GENAI_ENDPOINT",
                status=(
                    "ok"
                    if settings.oci_genai_endpoint.strip()
                    or not settings.nl2sql_feedback_embedding_enabled
                    else "warning"
                ),
                message=(
                    "OCI GenAI endpoint は設定済みです。"
                    if settings.oci_genai_endpoint.strip()
                    else (
                        "feedback embedding は無効なため OCI GenAI endpoint は任意です。"
                        if not settings.nl2sql_feedback_embedding_enabled
                        else "OCI GenAI endpoint が未設定です。"
                    )
                ),
            ),
            DiagnosticCheck(
                name="OCI_GENAI_EMBED_MODEL_ID",
                status="ok" if settings.oci_genai_embed_model_id.strip() else "warning",
                message=(
                    f"OCI GenAI embedding model は {settings.oci_genai_embed_model_id} です。"
                    if settings.oci_genai_embed_model_id.strip()
                    else "OCI GenAI embedding model が未設定です。"
                ),
            ),
            DiagnosticCheck(
                name="OCI_GENAI_EMBEDDING",
                status=(
                    "ok"
                    if (
                        not settings.nl2sql_feedback_embedding_enabled
                        or (embedding_configured and embedding_module_available)
                    )
                    else "warning"
                ),
                message=(
                    f"feedback embedding model は {settings.oci_genai_embed_model_id} です。"
                    if embedding_configured and embedding_module_available
                    else (
                        "feedback embedding は無効です。"
                        if not settings.nl2sql_feedback_embedding_enabled
                        else "feedback embedding の OCI 設定または OCI SDK が不足しています。"
                    )
                ),
            ),
        ]
        readiness = self._diagnostic_readiness(
            checks=checks,
            settings=settings,
            uses_oracle_runtime=uses_oracle_runtime,
            oracle_configured=oracle_configured,
            oracle_live_ok=oracle_live_ok,
            oracle_live_message=oracle_live_message,
            persistence_ready=persistence_ready,
            embedding_configured=embedding_configured,
            embedding_module_available=embedding_module_available,
            enterprise_ai_configured=enterprise_ai_configured,
            select_ai_assets_ready=select_ai_assets_ready,
            agent_assets_ready=agent_assets_ready,
        )
        smoke_checks = self._diagnostic_smoke_checks(readiness=readiness)
        config_guides = self._diagnostic_config_guides(
            checks=checks,
            readiness=readiness,
            settings=settings,
        )
        return DiagnosticsData(
            checks=checks,
            readiness=readiness,
            smoke_checks=smoke_checks,
            config_guides=config_guides,
        )

    def _diagnostic_smoke_checks(
        self, *, readiness: list[DiagnosticReadiness]
    ) -> list[DiagnosticSmokeCheck]:
        readiness_by_area = {item.area: item for item in readiness}

        def is_ready(areas: list[str]) -> bool:
            for area in areas:
                item = readiness_by_area.get(area)
                if item is None or item.status != "ok":
                    return False
            return True

        def next_action(areas: list[str], fallback: str) -> str:
            for area in areas:
                item = readiness_by_area.get(area)
                if item and item.next_action:
                    return item.next_action
            return "" if is_ready(areas) else fallback

        def status(areas: list[str]) -> str:
            return "ok" if is_ready(areas) else "warning"

        return [
            DiagnosticSmokeCheck(
                id="refresh_select_ai_profile",
                label="Select AI profile refresh",
                category="asset_refresh",
                status=status(["oracle_adb", "select_ai"]),
                method="POST",
                endpoint="/api/nl2sql/select-ai/profiles/refresh?profile_id=<business-profile-id>",
                expected="refreshed=true, status=ready, profile_name が返ること。",
                next_action=next_action(
                    ["oracle_adb", "select_ai"],
                    "Oracle runtime と Select AI provider / credential を設定してください。",
                ),
                related_readiness=["oracle_adb", "select_ai"],
            ),
            DiagnosticSmokeCheck(
                id="refresh_select_ai_agent_assets",
                label="Select AI Agent assets refresh",
                category="asset_refresh",
                status=status(["oracle_adb", "select_ai_agent"]),
                method="POST",
                endpoint="/api/nl2sql/select-ai-agent/assets/refresh?profile_id=<business-profile-id>",
                expected="tool / agent / task / team 名と status=ready が返ること。",
                next_action=next_action(
                    ["oracle_adb", "select_ai_agent"],
                    "Select AI profile 更新後に Agent assets refresh を実行してください。",
                ),
                related_readiness=["oracle_adb", "select_ai_agent"],
            ),
            DiagnosticSmokeCheck(
                id="preview_select_ai",
                label="Select AI preview",
                category="engine_preview",
                status=status(["oracle_adb", "select_ai"]),
                method="POST",
                endpoint="/api/nl2sql/preview",
                request_hint='{"engine":"select_ai","question":"登録済みの表から主要な列を一覧したい"}',
                expected="engine=select_ai, safety.is_safe=true, generated SQL が SELECT/WITH。",
                next_action=next_action(
                    ["oracle_adb", "select_ai"],
                    "Select AI profile refresh を先に完了してください。",
                ),
                related_readiness=["oracle_adb", "select_ai"],
            ),
            DiagnosticSmokeCheck(
                id="preview_select_ai_agent",
                label="Select AI Agent preview",
                category="engine_preview",
                status=status(["oracle_adb", "select_ai_agent"]),
                method="POST",
                endpoint="/api/nl2sql/preview",
                request_hint=(
                    '{"engine":"select_ai_agent","profile_id":"<business-profile-id>",'
                    '"question":"登録済みの表から主要な列を一覧したい"}'
                ),
                expected=(
                    "engine=select_ai_agent, engine_meta.team_name / conversation_id, "
                    "safety.is_safe=true。"
                ),
                next_action=next_action(
                    ["oracle_adb", "select_ai_agent"],
                    "Agent tool / task / team assets refresh を先に完了してください。",
                ),
                related_readiness=["oracle_adb", "select_ai_agent"],
            ),
            DiagnosticSmokeCheck(
                id="preview_enterprise_ai_direct",
                label="Enterprise AI Direct preview",
                category="engine_preview",
                status=status(["enterprise_ai_direct"]),
                method="POST",
                endpoint="/api/nl2sql/preview",
                request_hint=(
                    '{"engine":"enterprise_ai_direct","question":"登録済みの表から主要な列を一覧したい"}'
                ),
                expected=(
                    "engine=enterprise_ai_direct, provider=enterprise_ai_direct, SQL が返ること。"
                ),
                next_action=next_action(
                    ["enterprise_ai_direct"],
                    "OCI Enterprise AI endpoint / API key / model を設定してください。",
                ),
                related_readiness=["enterprise_ai_direct"],
            ),
            DiagnosticSmokeCheck(
                id="feedback_vector_rebuild",
                label="Feedback vector rebuild",
                category="learning",
                status=status(["oracle_adb", "feedback_embedding"]),
                method="POST",
                endpoint="/api/nl2sql/feedback-index/rebuild",
                request_hint='{"execute":true}',
                expected=(
                    "executed=true, VECTOR(1536, FLOAT32) index が Oracle 26ai に作成されること。"
                ),
                next_action=next_action(
                    ["oracle_adb", "feedback_embedding"],
                    "Oracle runtime と OCI GenAI embedding 設定を確認してください。",
                ),
                related_readiness=["oracle_adb", "feedback_embedding"],
            ),
            DiagnosticSmokeCheck(
                id="manual_integration_script",
                label="Manual integration script",
                category="manual_script",
                status=status(["oracle_adb", "select_ai", "select_ai_agent"]),
                command=(
                    "cd backend && uv run python scripts/nl2sql_manual_integration.py "
                    "--require-oracle --refresh-assets --execute "
                    "--check-supporting-features "
                    "--engines select_ai_agent,select_ai,enterprise_ai_direct"
                ),
                expected="[ok] diagnostics / refresh / preview / job lines が表示されること。",
                next_action=next_action(
                    ["oracle_adb", "select_ai", "select_ai_agent"],
                    "Oracle / Select AI / Agent readiness を ok にしてください。",
                ),
                related_readiness=["oracle_adb", "select_ai", "select_ai_agent"],
            ),
        ]

    def _diagnostic_config_guides(
        self,
        *,
        checks: list[DiagnosticCheck],
        readiness: list[DiagnosticReadiness],
        settings: Any,
    ) -> list[DiagnosticConfigGuide]:
        checks_by_name = {check.name: check for check in checks}
        readiness_by_area = {item.area: item for item in readiness}

        def env_var(name: str, *, required: bool = True, note: str = "") -> DiagnosticConfigVar:
            check = checks_by_name.get(name)
            return DiagnosticConfigVar(
                name=name,
                status=check.status if check else ("warning" if required else "optional"),
                required=required,
                note=note or (check.message if check else ""),
            )

        def guide_status(area: str) -> str:
            readiness_item = readiness_by_area.get(area)
            return readiness_item.status if readiness_item else "warning"

        def guide_summary(area: str, fallback: str) -> str:
            readiness_item = readiness_by_area.get(area)
            return readiness_item.summary if readiness_item else fallback

        def guide_next_action(area: str, fallback: str) -> str:
            readiness_item = readiness_by_area.get(area)
            return (
                readiness_item.next_action
                if readiness_item and readiness_item.next_action
                else fallback
            )

        enterprise_model_name = (
            "OCI_ENTERPRISE_AI_DEFAULT_MODEL"
            if settings.oci_enterprise_ai_default_model.strip()
            else "OCI_ENTERPRISE_AI_LLM_MODEL"
        )

        return [
            DiagnosticConfigGuide(
                id="enterprise_ai_direct",
                label="Enterprise AI Direct",
                status=guide_status("enterprise_ai_direct"),
                summary=guide_summary(
                    "enterprise_ai_direct",
                    "OCI Enterprise AI Direct fallback の設定状態です。",
                ),
                next_action=guide_next_action(
                    "enterprise_ai_direct",
                    "OCI Enterprise AI endpoint / API key / model を設定してください。",
                ),
                required_env_vars=[
                    env_var("OCI_ENTERPRISE_AI_ENDPOINT"),
                    env_var("OCI_ENTERPRISE_AI_API_KEY"),
                    env_var("OCI_ENTERPRISE_AI_LLM_MODEL"),
                ],
                optional_env_vars=[
                    env_var("OCI_ENTERPRISE_AI_PROJECT_OCID", required=False),
                    env_var("OCI_ENTERPRISE_AI_DEFAULT_MODEL", required=False),
                    env_var("OCI_ENTERPRISE_AI_LLM_PATH", required=False),
                    env_var("OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE", required=False),
                    env_var("OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH", required=False),
                ],
                env_template=(
                    "NL2SQL_ENTERPRISE_AI_DIRECT_ENABLED=true\n"
                    "OCI_ENTERPRISE_AI_ENDPOINT=<enterprise-ai-endpoint>\n"
                    "OCI_ENTERPRISE_AI_API_KEY=<enterprise-ai-api-key>\n"
                    f"{enterprise_model_name}=<enterprise-ai-model>\n"
                    "OCI_ENTERPRISE_AI_LLM_PATH=/responses"
                ),
                smoke_command=(
                    "uv run python scripts/nl2sql_manual_integration.py "
                    "--require-enterprise-ai --engines enterprise_ai_direct --execute "
                    "--json-report reports/nl2sql-enterprise-ai-direct.json"
                ),
                related_readiness=["enterprise_ai_direct"],
            ),
            DiagnosticConfigGuide(
                id="feedback_embedding",
                label="Feedback vector learning",
                status=guide_status("feedback_embedding"),
                summary=guide_summary(
                    "feedback_embedding",
                    "Oracle 26ai feedback vector learning の設定状態です。",
                ),
                next_action=guide_next_action(
                    "feedback_embedding",
                    "NL2SQL_FEEDBACK_EMBEDDING_ENABLED と OCI GenAI embedding "
                    "設定を確認してください。",
                ),
                required_env_vars=[
                    env_var("NL2SQL_FEEDBACK_EMBEDDING_ENABLED"),
                    env_var("OCI_REGION"),
                    env_var("OCI_COMPARTMENT_ID"),
                    env_var("OCI_GENAI_ENDPOINT"),
                    env_var("OCI_GENAI_EMBED_MODEL_ID"),
                ],
                optional_env_vars=[
                    env_var("NL2SQL_FEEDBACK_VECTOR_TABLE", required=False),
                    env_var("NL2SQL_FEEDBACK_VECTOR_INDEX", required=False),
                ],
                env_template=(
                    "NL2SQL_FEEDBACK_EMBEDDING_ENABLED=true\n"
                    "OCI_REGION=<oci-region>\n"
                    "OCI_COMPARTMENT_ID=<compartment-ocid>\n"
                    "OCI_GENAI_ENDPOINT=<oci-genai-endpoint>\n"
                    "OCI_GENAI_EMBED_MODEL_ID=cohere.embed-v4.0\n"
                    "NL2SQL_FEEDBACK_VECTOR_TABLE=NL2SQL_FEEDBACK_VECTORS\n"
                    "NL2SQL_FEEDBACK_VECTOR_INDEX=NL2SQL_FEEDBACK_VEC_IDX"
                ),
                smoke_command=(
                    "uv run python scripts/nl2sql_manual_integration.py "
                    "--require-oracle --require-feedback-embedding "
                    "--seed-demo-learning --execute-feedback-index "
                    "--engines enterprise_ai_direct "
                    "--json-report reports/nl2sql-feedback-vector.json"
                ),
                related_readiness=["oracle_adb", "feedback_embedding"],
            ),
            DiagnosticConfigGuide(
                id="production_release_gate",
                label="Production release gate",
                status=(
                    "ok"
                    if all(
                        readiness_by_area.get(area) and readiness_by_area[area].status == "ok"
                        for area in ["oracle_adb", "persistence", "select_ai", "select_ai_agent"]
                    )
                    else "warning"
                ),
                summary=("Oracle / persistence / Select AI / Agent assets の本番 gate 設定です。"),
                next_action=(
                    "Select AI / Agent assets refresh と diagnostics-only を実行してから "
                    "release gate を実行してください。"
                ),
                required_env_vars=[
                    env_var("ORACLE_USER"),
                    env_var("ORACLE_DSN"),
                    env_var("NL2SQL_RUNTIME_MODE"),
                    env_var("NL2SQL_PERSISTENCE_MODE"),
                    env_var("NL2SQL_SELECT_AI_CREDENTIAL_NAME"),
                ],
                optional_env_vars=[
                    env_var("NL2SQL_ORACLE_STATE_TABLE", required=False),
                    env_var("NL2SQL_SELECT_AI_PROFILE_PREFIX", required=False),
                    env_var("NL2SQL_SELECT_AI_MODEL", required=False),
                ],
                env_template=(
                    "NL2SQL_RUNTIME_MODE=oracle\n"
                    "NL2SQL_PERSISTENCE_MODE=oracle\n"
                    "NL2SQL_SELECT_AI_ENABLED=true\n"
                    "NL2SQL_SELECT_AI_AGENT_ENABLED=true\n"
                    "NL2SQL_SELECT_AI_CREDENTIAL_NAME=<dbms-cloud-ai-credential>\n"
                    "NL2SQL_SELECT_AI_MODEL=<select-ai-model>"
                ),
                smoke_command=(
                    "uv run python scripts/nl2sql_manual_integration.py "
                    "--release-gate --engines select_ai_agent,select_ai "
                    "--allowed-table YOUR_TABLE --json-report reports/nl2sql-release-gate.json"
                ),
                related_readiness=["oracle_adb", "persistence", "select_ai", "select_ai_agent"],
            ),
        ]

    def _diagnostic_readiness(
        self,
        *,
        checks: list[DiagnosticCheck],
        settings: Any,
        uses_oracle_runtime: bool,
        oracle_configured: bool,
        oracle_live_ok: bool,
        oracle_live_message: str,
        persistence_ready: bool,
        embedding_configured: bool,
        embedding_module_available: bool,
        enterprise_ai_configured: bool,
        select_ai_assets_ready: bool,
        agent_assets_ready: bool,
    ) -> list[DiagnosticReadiness]:
        oracle_ready = uses_oracle_runtime and oracle_configured and oracle_live_ok
        select_ai_config_ready = (
            settings.nl2sql_select_ai_enabled
            and bool(settings.nl2sql_select_ai_provider)
            and (
                not uses_oracle_runtime
                or (oracle_ready and bool(settings.nl2sql_select_ai_credential_name))
            )
        )
        select_ai_ready = select_ai_config_ready and (
            select_ai_assets_ready or not uses_oracle_runtime
        )
        agent_ready = (
            settings.nl2sql_select_ai_agent_enabled
            and select_ai_ready
            and (agent_assets_ready or not uses_oracle_runtime)
        )
        direct_ready = settings.nl2sql_enterprise_ai_direct_enabled and enterprise_ai_configured
        embedding_ready = (
            settings.nl2sql_feedback_embedding_enabled
            and embedding_configured
            and embedding_module_available
        )
        persistence_production_ready = persistence_ready and self._store.mode == "oracle"

        oracle_summary = (
            "Oracle / ADB runtime は live 接続まで確認済みです。"
            if oracle_ready
            else (
                "deterministic runtime のため Oracle / ADB live 接続は未確認です。"
                if not uses_oracle_runtime
                else oracle_live_message
            )
        )
        select_ai_summary = (
            "Select AI profile 作成・実行に必要な設定が揃っています。"
            if select_ai_ready
            else (
                "Select AI profile refresh が未確認です。"
                if select_ai_config_ready and uses_oracle_runtime
                else "Select AI の provider / credential / Oracle runtime 設定を確認してください。"
            )
        )
        agent_summary = (
            "Select AI Agent assets を更新・実行できる設定です。"
            if agent_ready
            else (
                "Select AI Agent assets refresh が未確認です。"
                if (
                    settings.nl2sql_select_ai_agent_enabled
                    and select_ai_ready
                    and uses_oracle_runtime
                )
                else "Agent は Select AI profile と credential を前提にするため未準備です。"
            )
        )
        direct_summary = (
            "Enterprise AI Direct fallback に必要な OCI 基本設定があります。"
            if direct_ready
            else "Enterprise AI Direct 用の endpoint / API key / model を確認してください。"
        )
        embedding_summary = (
            (
                f"Feedback 学習は {settings.oci_genai_embed_model_id} で "
                "1536 次元 embedding を作成できます。"
            )
            if embedding_ready
            else (
                "Feedback embedding は無効です。必要な場合は "
                "NL2SQL_FEEDBACK_EMBEDDING_ENABLED=true にしてください。"
                if not settings.nl2sql_feedback_embedding_enabled
                else (
                    "Feedback embedding 用 OCI SDK / endpoint / region / compartment を"
                    "確認してください。"
                )
            )
        )
        persistence_summary = (
            "profile / job / history を Oracle に永続化できます。"
            if persistence_production_ready
            else "現在は local/CI 向け persistence です。本番は Oracle 永続化を推奨します。"
        )

        return [
            DiagnosticReadiness(
                area="oracle_adb",
                label="Oracle / ADB",
                status="ok" if oracle_ready else "warning",
                summary=oracle_summary,
                next_action=(
                    ""
                    if oracle_ready
                    else (
                        "NL2SQL_RUNTIME_MODE=oracle と ORACLE_DSN / ORACLE_USER / "
                        "Wallet 設定を確認してください。"
                    )
                ),
                related_checks=[
                    "NL2SQL_RUNTIME_MODE",
                    "ORACLE_DSN",
                    "ORACLE_USER",
                    "ORACLE_ADB_OCID",
                    "PYTHON_ORACLEDB",
                    "ORACLE_RUNTIME_READY",
                ],
            ),
            DiagnosticReadiness(
                area="select_ai",
                label="Oracle Select AI",
                status="ok" if select_ai_ready else "warning",
                summary=select_ai_summary,
                next_action=(
                    ""
                    if select_ai_ready
                    else (
                        "NL2SQL_SELECT_AI_PROVIDER と NL2SQL_SELECT_AI_CREDENTIAL_NAME "
                        "を設定し、profile refresh を実行してください。"
                        if not (select_ai_config_ready and uses_oracle_runtime)
                        else "Select AI profile refresh を実行してください。"
                    )
                ),
                related_checks=[
                    "NL2SQL_SELECT_AI_ENABLED",
                    "NL2SQL_SELECT_AI_PROVIDER",
                    "NL2SQL_SELECT_AI_CREDENTIAL_NAME",
                    "NL2SQL_SELECT_AI_PROFILE_REFRESHED",
                    "ORACLE_RUNTIME_READY",
                ],
            ),
            DiagnosticReadiness(
                area="select_ai_agent",
                label="Oracle Select AI Agent",
                status="ok" if agent_ready else "warning",
                summary=agent_summary,
                next_action=(
                    ""
                    if agent_ready
                    else (
                        "Agent tool / task / team assets を refresh してください。"
                        if select_ai_ready
                        else (
                            "Select AI profile を更新後、Agent tool / task / team assets "
                            "を refresh してください。"
                        )
                    )
                ),
                related_checks=[
                    "NL2SQL_SELECT_AI_AGENT_ENABLED",
                    "NL2SQL_SELECT_AI_PROFILE_REFRESHED",
                    "NL2SQL_SELECT_AI_AGENT_ASSETS_REFRESHED",
                    "NL2SQL_SELECT_AI_PROVIDER",
                    "NL2SQL_SELECT_AI_CREDENTIAL_NAME",
                    "ORACLE_RUNTIME_READY",
                ],
            ),
            DiagnosticReadiness(
                area="enterprise_ai_direct",
                label="OCI Enterprise AI Direct",
                status="ok" if direct_ready else "warning",
                summary=direct_summary,
                next_action=(
                    ""
                    if direct_ready
                    else (
                        "OCI_ENTERPRISE_AI_ENDPOINT / OCI_ENTERPRISE_AI_API_KEY / "
                        "OCI_ENTERPRISE_AI_LLM_MODEL を設定してください。"
                    )
                ),
                related_checks=[
                    "OCI_ENTERPRISE_AI_ENDPOINT",
                    "OCI_ENTERPRISE_AI_API_KEY",
                    "OCI_ENTERPRISE_AI_LLM_MODEL",
                ],
            ),
            DiagnosticReadiness(
                area="feedback_embedding",
                label="Feedback Vector Learning",
                status="ok" if embedding_ready else "warning",
                summary=embedding_summary,
                next_action=(
                    ""
                    if embedding_ready
                    else (
                        "OCI GenAI embedding 設定を有効化して feedback index rebuild "
                        "を実行してください。"
                    )
                ),
                related_checks=[
                    "NL2SQL_FEEDBACK_EMBEDDING_ENABLED",
                    "OCI_GENAI_EMBEDDING",
                ],
            ),
            DiagnosticReadiness(
                area="persistence",
                label="Oracle Persistence",
                status="ok" if persistence_production_ready else "warning",
                summary=persistence_summary,
                next_action=(
                    ""
                    if persistence_production_ready
                    else (
                        "NL2SQL_PERSISTENCE_MODE=oracle と NL2SQL_ORACLE_STATE_TABLE "
                        "を確認してください。"
                    )
                ),
                related_checks=["NL2SQL_PERSISTENCE_MODE", "NL2SQL_PERSISTENCE_READY"],
            ),
        ]

    def _db_admin_object_identity(self, object_name: str, owner: str = "") -> OracleObjectIdentity:
        """管理画面の既存 object 参照を owner-aware に正規化する。"""

        requested_owner = normalize_object_part(owner) if owner.strip() else ""
        try:
            identity = parse_object_identity(
                object_name,
                default_owner=requested_owner or self._current_schema_owner(),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if requested_owner and identity.owner != requested_owner:
            raise ValueError("owner と object_name の owner 指定が一致しません。")
        if not is_user_visible_schema_object(identity.owner, identity.object_name):
            raise ValueError(_system_object_blocked_message([identity.qualified_name]))
        return identity

    def _db_admin_summary_from_schema_object(
        self,
        item: SchemaObjectSummary,
    ) -> DbAdminObjectSummary:
        object_type = (
            "view" if item.object_type.upper() in {"VIEW", "MATERIALIZED VIEW"} else "table"
        )
        return DbAdminObjectSummary(
            name=item.object_name,
            owner=item.owner,
            qualified_name=_qualified_display_name(item.owner, item.object_name),
            object_type=object_type,
            row_count=item.row_count,
            comment=item.comment,
        )

    def _db_admin_summary_from_catalog_table(
        self, table: SchemaTable, object_type: str | None = None
    ) -> DbAdminObjectSummary:
        normalized_type = object_type or ("view" if table.table_type.lower() == "view" else "table")
        return DbAdminObjectSummary(
            name=table.table_name,
            owner=table.owner,
            qualified_name=_qualified_display_name(table.owner, table.table_name),
            object_type=normalized_type,
            row_count=table.row_count,
            comment=table.comment,
        )

    def list_db_admin_tables(self) -> DbAdminObjectsData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return DbAdminObjectsData(
                    runtime="oracle",
                    items=[
                        DbAdminObjectSummary.model_validate(item)
                        for item in self._oracle_adapter.list_db_admin_objects("table")
                        if is_user_visible_schema_object(
                            str(item.get("owner") or ""),
                            str(item.get("name") or ""),
                        )
                    ],
                    refreshed_at=self._catalog.refreshed_at,
                )
            except OracleAdapterError:
                raise
        return DbAdminObjectsData(
            runtime="deterministic",
            items=[
                self._db_admin_summary_from_catalog_table(table, "table")
                for table in self._catalog.tables
                if table.table_type.lower() != "view"
                and is_user_visible_schema_object(table.owner, table.table_name)
            ],
            refreshed_at=self._catalog.refreshed_at,
            warnings=warnings,
        )

    def list_db_admin_objects_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        query: str,
        object_type: str,
        row_state: str,
        owner: str = "",
        owner_prefix: str = "",
        query_scope: str = "all",
        include_counts: bool = True,
    ) -> DbAdminObjectPage:
        """管理画面用 read model。全量 Catalog/CLOB を経由しない。"""

        normalized_type = object_type.strip().lower()
        schema_type = ""
        if normalized_type == "table":
            schema_type = "TABLE"
        elif normalized_type == "view":
            schema_type = "VIEW"
        configured_owner = get_settings().oracle_user.strip().upper()
        page = self.search_schema_objects(
            cursor=cursor,
            limit=limit,
            query=query,
            owner=owner,
            owner_prefix=owner_prefix,
            query_scope=query_scope,
            object_type=schema_type,
            profile_id=None,
            row_state=row_state,
            include_counts=include_counts,
        )
        items = [
            self._db_admin_summary_from_schema_object(item)
            for item in page.items
            if is_user_visible_schema_object(item.owner, item.object_name)
        ]
        return DbAdminObjectPage(
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            owner=configured_owner,
            items=items,
            total=page.total or 0,
            table_count=page.table_count,
            view_count=page.view_count,
            counts_included=page.counts_included,
            next_cursor=page.next_cursor,
            refreshed_at=page.refreshed_at,
            catalog_version=page.catalog_version,
        )

    def list_db_admin_views(self) -> DbAdminObjectsData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return DbAdminObjectsData(
                    runtime="oracle",
                    items=[
                        DbAdminObjectSummary.model_validate(item)
                        for item in self._oracle_adapter.list_db_admin_objects("view")
                        if is_user_visible_schema_object(
                            str(item.get("owner") or ""),
                            str(item.get("name") or ""),
                        )
                    ],
                    refreshed_at=self._catalog.refreshed_at,
                )
            except OracleAdapterError:
                raise
        return DbAdminObjectsData(
            runtime="deterministic",
            items=[
                self._db_admin_summary_from_catalog_table(table, "view")
                for table in self._catalog.tables
                if table.table_type.lower() == "view"
                and is_user_visible_schema_object(table.owner, table.table_name)
            ],
            refreshed_at=self._catalog.refreshed_at,
            warnings=warnings,
        )

    def _ontology_business_names(
        self,
        *,
        owner: str,
        object_name: str,
        object_type: str,
    ) -> dict[str, str]:
        """物理列名(大文字) -> business_name_ja。未構築/失敗時は空 dict。

        論理名を「オントロジーの業務日本語名」ソースに分離するための lookup。
        comment(生カラムコメント)とは別系統にする。
        """
        try:
            # ontology_router が nl2sql_service を import するため遅延 import で循環回避
            from app.features.nl2sql.ontology_router import ontology_runtime

            return ontology_runtime.column_business_names(
                owner=owner,
                object_name=object_name,
                object_type="view" if object_type == "view" else "table",
            )
        except Exception:
            return {}

    def get_db_admin_object(
        self,
        object_name: str,
        object_type: str,
        *,
        owner: str = "",
        include_ddl: bool = True,
        exact_count: bool = False,
    ) -> DbAdminObjectDetail:
        normalized_type = "view" if object_type.lower() == "view" else "table"
        identity = self._db_admin_object_identity(object_name, owner)
        if self._use_oracle_runtime():
            try:
                detail = DbAdminObjectDetail.model_validate(
                    self._oracle_adapter.get_db_admin_object_detail(
                        object_name=identity.qualified_name,
                        owner=identity.owner,
                        object_type=normalized_type,
                        include_ddl=include_ddl,
                        exact_count=exact_count,
                    )
                )
            except OracleAdapterError as exc:
                fallback = self._catalog_object_detail(
                    identity.qualified_name, normalized_type, include_ddl=include_ddl
                )
                detail = fallback.model_copy(update={"warnings": [str(exc)]})
        else:
            detail = self._catalog_object_detail(
                identity.qualified_name, normalized_type, include_ddl=include_ddl
            )
        # 一覧と同じ in-memory catalog を「1オブジェクト分」の付帯情報ソースにする:
        # - logical_name: オントロジー業務名のみ正、無ければ空(生コメントは流用しない Option B)
        # - sample_values: catalog の該当テーブル列から補完(Oracle 追加クエリなし。詳細を自己完結化)
        # - row_count: exact_count 時のみ COUNT(*)、他は num_rows 統計(一覧と意味を統一)
        catalog_table: SchemaTable | None = None
        lookup_owner = detail.owner or identity.owner
        if lookup_owner:
            try:
                schema_detail = self.get_schema_object(lookup_owner, detail.name)
                catalog_table = schema_detail.table if schema_detail is not None else None
            except Nl2SqlPersistenceUnavailable:
                catalog_table = None
        if catalog_table is None and self._incremental_repository is None:
            catalog_table = self._find_catalog_table(identity.qualified_name)
        names = self._ontology_business_names(
            owner=detail.owner or (catalog_table.owner if catalog_table is not None else ""),
            object_name=detail.name,
            object_type=normalized_type,
        )
        samples: dict[str, list[str]] = {}
        if catalog_table is not None:
            samples = {
                column.column_name.upper(): column.sample_values for column in catalog_table.columns
            }
        updates: dict[str, Any] = {
            "constraints": catalog_table.constraints if catalog_table is not None else [],
            "columns": [
                col.model_copy(
                    update={
                        "logical_name": names.get(col.column_name.upper(), ""),
                        "sample_values": samples.get(col.column_name.upper(), col.sample_values),
                    }
                )
                for col in detail.columns
            ],
        }
        if not exact_count and catalog_table is not None:
            updates["row_count"] = catalog_table.row_count
        detail = detail.model_copy(update=updates)
        return detail

    def drop_db_admin_table(self, request: DbAdminDropTableRequest) -> DbAdminExecuteData:
        identity = self._db_admin_object_identity(request.table_name, request.owner)
        table_name = identity.object_name
        target_name = identity.qualified_name
        sql = f"DROP TABLE {_quote_object_identity(identity)}{' PURGE' if request.purge else ''}"
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=target_name,
        )
        if confirmation_error and identity.owner == self._current_schema_owner():
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=table_name,
            )
        if confirmation_error:
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                statements=[
                    DbAdminStatementResult(
                        index=1,
                        statement_type="DDL",
                        status="confirmation_required",
                        sql=sql,
                        error_message=confirmation_error,
                    )
                ],
                warnings=[confirmation_error],
                timing=self._timing(_utc_now(), time.monotonic(), "db_admin_drop_table"),
            )
        execution = self.execute_db_admin_sql(
            DbAdminExecuteRequest(
                sql=sql,
                confirmation="ADMIN_EXECUTE",
                reason=request.reason,
            )
        )
        return execution

    def truncate_db_admin_table(self, request: DbAdminTruncateTableRequest) -> DbAdminExecuteData:
        identity = self._db_admin_object_identity(request.table_name, request.owner)
        table_name = identity.object_name
        target_name = identity.qualified_name
        sql = f"TRUNCATE TABLE {_quote_object_identity(identity)}"
        object_type = self._db_admin_truncate_target_type(identity.qualified_name)
        if "view" in object_type:
            warning = f"{target_name}: ビューは TRUNCATE できません。"
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                statements=[
                    DbAdminStatementResult(
                        index=1,
                        statement_type="TRUNCATE",
                        status="blocked",
                        sql=sql,
                        error_message=warning,
                    )
                ],
                warnings=[warning],
                timing=self._timing(_utc_now(), time.monotonic(), "db_admin_truncate_table"),
            )
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=target_name,
        )
        if confirmation_error and identity.owner == self._current_schema_owner():
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=table_name,
            )
        if confirmation_error:
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                statements=[
                    DbAdminStatementResult(
                        index=1,
                        statement_type="TRUNCATE",
                        status="confirmation_required",
                        sql=sql,
                        error_message=confirmation_error,
                    )
                ],
                warnings=[confirmation_error],
                timing=self._timing(_utc_now(), time.monotonic(), "db_admin_truncate_table"),
            )
        return self.execute_db_admin_statements(
            DbAdminStatementsRequest(
                sql=sql,
                policy="data_dml",
                confirmation="ADMIN_EXECUTE",
                reason=request.reason,
            )
        )

    def _db_admin_truncate_target_type(self, table_name: str, owner: str = "") -> str:
        identity = self._db_admin_object_identity(table_name, owner)
        catalog_object = self._find_catalog_table(identity.qualified_name)
        if catalog_object is not None:
            return catalog_object.table_type.lower()
        finder = getattr(self._oracle_adapter, "find_db_admin_object_type", None)
        if not self._use_oracle_runtime() or not callable(finder):
            return ""
        try:
            return str(finder(identity.qualified_name) or "").lower()
        except OracleAdapterError:
            return ""

    def execute_db_admin_sql(self, request: DbAdminExecuteRequest) -> DbAdminExecuteData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        select_vpd_context_enforced = (
            self._use_oracle_runtime()
            and self._deepsec_enabled
            and not current_actor_is_system_admin()
        )
        select_execution_context = (
            "deepsec_data_plane"
            if select_vpd_context_enforced
            else "oracle_data_plane" if self._use_oracle_runtime() else "deterministic"
        )
        statements = _split_sql_statements(request.sql)
        if not statements:
            warnings.append("SQL statement がありません。")
        statement_types = [_admin_statement_type(statement) for statement in statements]
        # 確認不要の read-only 経路は、先頭 keyword ではなく通常の SELECT-only
        # safety guard で判定する。WITH で始まる更新文も管理 SQL として確認を必須にする。
        select_only_flags = [is_select_only(statement) for statement in statements]
        select_count = sum(select_only_flags)
        system_object_errors = [
            _db_admin_system_object_error(
                statement,
                current_owner=self._current_schema_owner(),
            )
            for statement in statements
        ]
        if any(system_object_errors):
            warnings.append(_SYSTEM_OBJECT_BLOCKED_MESSAGE)
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=statement_types[index],
                        status="blocked",
                        sql=statements[index],
                        error_message=system_object_errors[index] or _SYSTEM_OBJECT_BLOCKED_MESSAGE,
                    )
                    for index in range(len(statements))
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        if len(statements) > 1 and select_count > 0:
            warnings.append("複数 statement 実行に SELECT は含められません。")
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="blocked",
                        sql=statements[index],
                        error_message="複数 statement 実行に SELECT は含められません。",
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        if len(statements) == 1 and select_only_flags == [True]:
            sql = normalize_executable_sql(statements[0])
            try:
                results = (
                    self._oracle_adapter.execute_select(sql, request.row_limit)
                    if self._use_oracle_runtime()
                    else self._mock_execute(sql, request.row_limit)
                )
            except OracleAdapterError as exc:
                warning = str(exc)
                warnings.append(warning)
                return DbAdminExecuteData(
                    executed=False,
                    runtime="oracle",
                    execution_context=select_execution_context,
                    vpd_context_enforced=select_vpd_context_enforced,
                    statements=[
                        DbAdminStatementResult(
                            index=1,
                            statement_type="SELECT",
                            status="error",
                            sql=sql,
                            error_message=warning,
                        )
                    ],
                    warnings=warnings,
                    timing=self._timing(created_at, started, "db_admin_execute"),
                )
            return DbAdminExecuteData(
                executed=True,
                runtime=runtime,
                execution_context=results.execution_context,
                vpd_context_enforced=results.vpd_context_enforced,
                select_result=results,
                statements=[
                    DbAdminStatementResult(
                        index=1,
                        statement_type="SELECT",
                        status="executed",
                        sql=sql,
                        row_count=results.total,
                        message=f"{results.total} rows",
                    )
                ],
                committed=False,
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        # data_dml 互換契約を管理 SQL 実行でも再利用する。
        # 全文が whitelist に一致する場合だけ既存経路へ委譲し、部分成功 commit・
        # policy audit・互換 API と同一の結果契約を維持する。WITH 更新や DDL/PLSQL
        # を含む管理 SQL は一致しないため、従来どおり下段の atomic 経路で実行する。
        if statements and all(
            not _db_admin_policy_error(statement, "data_dml") for statement in statements
        ):
            return self.execute_db_admin_statements(
                DbAdminStatementsRequest(
                    sql=request.sql,
                    policy="data_dml",
                    confirmation=request.confirmation,
                    reason=request.reason,
                )
            )
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target="ADMIN_EXECUTE",
        )
        if confirmation_error:
            warnings.append(confirmation_error)
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="confirmation_required",
                        sql=statements[index],
                        error_message=confirmation_error,
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        if not self._use_oracle_runtime():
            warnings.append("Admin SQL 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
            return DbAdminExecuteData(
                executed=False,
                runtime="deterministic",
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="requires_oracle",
                        sql=statements[index],
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        try:
            statement_results = [
                DbAdminStatementResult.model_validate(item)
                for item in self._oracle_adapter.execute_admin_statements(statements)
            ]
            ok = all(item.status == "success" for item in statement_results)
            successful_statement_indexes = [
                index
                for index, item in enumerate(statement_results)
                if item.status == "success" and index < len(statements)
            ]
            successful_types = [
                str(statement_results[index].statement_type or statement_types[index]).upper()
                for index in successful_statement_indexes
            ]
            successful_implicit_commit_statements = [
                statements[index]
                for index, statement_type in zip(
                    successful_statement_indexes,
                    successful_types,
                    strict=False,
                )
                if statement_type in _IMPLICIT_COMMIT_STATEMENT_TYPES
            ]
            successful_rollbackable_dml = any(
                statement_type in _ROLLBACKABLE_DML_STATEMENT_TYPES
                for statement_type in successful_types
            )
            committed = ok or bool(successful_implicit_commit_statements)
            rolled_back = not ok and successful_rollbackable_dml and not committed
            if successful_implicit_commit_statements and not ok:
                warnings.append(
                    "DDL/metadata 系 statement は Oracle の暗黙 commit により、"
                    "成功した文が rollback されていません。失敗した SQL を修正し、"
                    "必要に応じて DB 構造を再取得してください。"
                )
            try:
                self._record_admin_audit(
                    operation="db_admin_execute",
                    target="ADMIN_EXECUTE",
                    executed=ok,
                    reason=request.reason,
                    detail={
                        "statement_count": len(statements),
                        "success_count": len(successful_statement_indexes),
                        "types": statement_types,
                    },
                )
            except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed) as exc:
                warnings.append(f"Admin SQL の監査保存に失敗しました: {exc}")
            schema_refresh_job_id = ""
            schema_refresh_required = False
            schema_refresh_reason_code = ""
            has_unresolved_schema_side_effect = any(
                (statement_results[index].statement_type or statement_types[index]) == "PLSQL"
                for index in successful_statement_indexes
            )
            successful_schema_statements = [
                statements[index]
                for index, statement_type in zip(
                    successful_statement_indexes,
                    successful_types,
                    strict=False,
                )
                if statement_type in _SCHEMA_MUTATION_STATEMENT_TYPES
            ]
            if has_unresolved_schema_side_effect or _statements_change_schema(
                successful_schema_statements
            ):
                try:
                    if has_unresolved_schema_side_effect:
                        sync = self._manual_schema_refresh_sync()
                    else:
                        sync = self._submit_schema_refresh_after_admin_mutation(
                            target_objects=_schema_refresh_targets_for_statements(
                                successful_schema_statements,
                                current_owner=self._current_schema_owner(),
                            ),
                            source="db_admin_execute",
                        )
                    schema_refresh_job_id = sync.job_id
                    schema_refresh_required = sync.required
                    schema_refresh_reason_code = sync.reason_code
                    if sync.required:
                        warnings.append(_schema_refresh_required_warning(sync.reason_code))
                except Nl2SqlPersistenceUnavailable as exc:
                    warnings.append(f"Admin SQL 後の Schema job 投入に失敗しました: {exc}")
            return DbAdminExecuteData(
                executed=ok or committed,
                runtime="oracle",
                execution_context="admin_control_plane",
                statements=statement_results,
                committed=committed,
                rolled_back=rolled_back,
                schema_refresh_job_id=schema_refresh_job_id,
                schema_refresh_required=schema_refresh_required,
                schema_refresh_reason_code=schema_refresh_reason_code,
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        except OracleAdapterError as exc:
            warnings.append(str(exc))
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle",
                execution_context="admin_control_plane",
                rolled_back=True,
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="error",
                        sql=statements[index],
                        error_message=str(exc),
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )

    def import_db_admin_tabular(
        self, request: DbAdminImportTabularRequest
    ) -> DbAdminImportTabularData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        mode = request.mode.strip().lower()
        if mode not in {"create", "replace", "append", "truncate"}:
            raise ValueError(
                f"{request.mode or '空の mode'}: 未対応 mode です。"
                "mode は create、replace、append、truncate のいずれかを指定してください。"
            )
        try:
            content = base64.b64decode(request.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 の decode に失敗しました: {exc}") from exc
        csv_text, sheet_name, sheet_warnings = self._tabular_content_to_csv_text(
            filename=request.filename,
            content=content,
            sheet_name=request.sheet_name,
            require_sheet_name=True,
        )
        warnings.extend(sheet_warnings)
        settings = get_settings()
        row_limit = request.max_rows or settings.nl2sql_csv_import_max_rows
        columns, rows, parse_warnings = self._parse_csv_sample(
            table_name=request.table_name,
            csv_text=csv_text,
            max_rows=min(row_limit, settings.nl2sql_csv_import_max_rows),
            max_columns=settings.nl2sql_csv_import_max_columns,
            infer_data_types=mode in {"create", "replace"},
        )
        warnings.extend(parse_warnings)
        table_name = self._sanitize_import_table_name(request.table_name)
        ddl = self._csv_import_ddl(table_name, columns)
        insert_sql = self._csv_import_insert_sql(table_name, columns)
        executed = False
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        confirmation_target = table_name if mode == "replace" else "ADMIN_EXECUTE"
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=confirmation_target,
        )
        if confirmation_error:
            warnings.append(confirmation_error)
        elif self._use_oracle_runtime():
            try:
                self._oracle_adapter.import_tabular_table(
                    table_name=table_name,
                    columns=columns,
                    rows=rows,
                    mode=mode,
                )
            except TabularImportValidationError:
                raise
            except OracleAdapterError as exc:
                object_type = ""
                if _safe_oracle_error_code(exc) == "ORA-00955":
                    try:
                        object_type = (
                            self._oracle_adapter.find_db_admin_object_type(table_name) or ""
                        )
                    except Exception:
                        # エラー表示の補足取得に失敗しても、元の import エラーを優先する。
                        object_type = ""
                logger.error(
                    "db_admin_import_tabular_failed",
                    extra={
                        "table_name": table_name,
                        "mode": mode,
                        "error_code": _safe_oracle_error_code(exc) or "oracle_import_error",
                    },
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                raise _db_admin_error(
                    exc,
                    target_name=table_name,
                    target_type=object_type,
                    operation="tabular_import",
                ) from exc
            executed = True
            self._record_admin_audit(
                operation="db_admin_import_tabular",
                target=table_name,
                executed=True,
                reason=request.reason,
                detail={"mode": mode, "row_count": len(rows), "filename": request.filename},
            )
            if mode in {"create", "replace"}:
                try:
                    target = self._schema_refresh_target_for_object_name(
                        table_name,
                        object_type="table",
                        expected_state="present",
                    )
                    sync = self._submit_schema_refresh_after_admin_mutation(
                        target_objects=[target] if target is not None else [],
                        source="db_admin_import_tabular",
                    )
                    schema_refresh_job_id = sync.job_id
                    schema_refresh_required = sync.required
                    schema_refresh_reason_code = sync.reason_code
                    if sync.required:
                        warnings.append(_schema_refresh_required_warning(sync.reason_code))
                except Nl2SqlPersistenceUnavailable as exc:
                    warnings.append(f"import 後の Schema job 投入に失敗しました: {exc}")
        else:
            warnings.append("Tabular import 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
        return DbAdminImportTabularData(
            table_name=table_name,
            filename=request.filename,
            sheet_name=sheet_name,
            mode=mode,
            columns=columns,
            row_count=len(rows),
            executed=executed,
            ddl=ddl,
            insert_sql=insert_sql,
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            warnings=warnings,
            sample_rows=rows[:5],
            timing=self._timing(created_at, started, "db_admin_import_tabular"),
        )

    def _export_db_admin_object_columns_xlsx(
        self, object_name: str, object_type: str, limit: int = 1000, owner: str = ""
    ) -> tuple[str, bytes]:
        _ = limit
        normalized_type = "view" if object_type.lower() == "view" else "table"
        identity = self._db_admin_object_identity(object_name, owner)
        detail = self.get_db_admin_object(
            identity.qualified_name,
            normalized_type,
            owner=identity.owner,
        )
        safe_name = detail.qualified_name or identity.qualified_name
        catalog_table = self._find_catalog_table(identity.qualified_name)
        sample_by_column = {
            column.column_name.upper(): ", ".join(column.sample_values)
            for column in (catalog_table.columns if catalog_table else [])
            if column.sample_values
        }
        openpyxl = importlib.import_module("openpyxl")
        styles = importlib.import_module("openpyxl.styles")
        workbook = openpyxl.Workbook()
        columns_sheet = workbook.active
        columns_sheet.title = "columns"
        headers = ["物理名", "論理名", "コメント", "型", "NULL 可", "サンプル"]
        columns_sheet.append(headers)
        for cell in columns_sheet[1]:
            cell.font = styles.Font(bold=True)
        columns_sheet.freeze_panes = "A2"
        for column in detail.columns:
            sample_values = column.sample_values or []
            sample_text = ", ".join(sample_values) or sample_by_column.get(
                column.column_name.upper(),
                "",
            )
            columns_sheet.append(
                [
                    column.column_name,
                    column.logical_name or "-",
                    column.comment or "-",
                    column.data_type,
                    "YES" if column.nullable else "NO",
                    sample_text or "-",
                ]
            )
        for column_letter, width in {
            "A": 28,
            "B": 32,
            "C": 40,
            "D": 22,
            "E": 12,
            "F": 48,
        }.items():
            columns_sheet.column_dimensions[column_letter].width = width
        buffer = io.BytesIO()
        workbook.save(buffer)
        return f"{safe_name.lower().replace('.', '_')}_columns.xlsx", buffer.getvalue()

    def export_db_admin_table_xlsx(
        self, table_name: str, limit: int = 1000, owner: str = ""
    ) -> tuple[str, bytes]:
        return self._export_db_admin_object_columns_xlsx(
            table_name, "table", limit=limit, owner=owner
        )

    def export_db_admin_view_xlsx(
        self, view_name: str, limit: int = 1000, owner: str = ""
    ) -> tuple[str, bytes]:
        return self._export_db_admin_object_columns_xlsx(
            view_name, "view", limit=limit, owner=owner
        )

    def execute_db_admin_statements(self, request: DbAdminStatementsRequest) -> DbAdminExecuteData:
        """文種 whitelist 付き複数 statement 実行(SQL Assist のテーブル/ビュー/データ SQL 実行)。"""
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        statements = _split_sql_statements(request.sql)
        if not statements:
            warnings.append("SQL statement がありません。")
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        if request.policy == "annotation_sql":
            # 操作句省略だけを補い、ユーザーが明示した ADD/REPLACE は尊重する。
            statements = [_normalize_annotation_add_operations(stmt) for stmt in statements]
        statement_types = [_admin_statement_type(statement) for statement in statements]
        policy_errors = [
            _db_admin_policy_error(statement, request.policy) for statement in statements
        ]
        system_object_errors = [
            _db_admin_system_object_error(
                statement,
                current_owner=self._current_schema_owner(),
            )
            for statement in statements
        ]
        if any(system_object_errors):
            warnings.append(_SYSTEM_OBJECT_BLOCKED_MESSAGE)
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=statement_types[index],
                        status="blocked",
                        sql=statements[index],
                        error_message=system_object_errors[index] or _SYSTEM_OBJECT_BLOCKED_MESSAGE,
                    )
                    for index in range(len(statements))
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        if any(policy_errors):
            warnings.append("禁止された操作が含まれるため実行しませんでした。")
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=statement_types[index],
                        status="blocked",
                        sql=statements[index],
                        error_message=policy_errors[index],
                    )
                    for index in range(len(statements))
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target="ADMIN_EXECUTE",
        )
        if confirmation_error:
            warnings.append(confirmation_error)
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="confirmation_required",
                        sql=statements[index],
                        error_message=confirmation_error,
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        if not self._use_oracle_runtime():
            warnings.append("Admin SQL 実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
            return DbAdminExecuteData(
                executed=False,
                runtime="deterministic",
                execution_context="admin_control_plane",
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="requires_oracle",
                        sql=statements[index],
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        try:
            statement_results = [
                DbAdminStatementResult.model_validate(item)
                for item in self._oracle_adapter.execute_admin_statements(statements, atomic=False)
            ]
        except OracleAdapterError as exc:
            warnings.append(str(exc))
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle",
                execution_context="admin_control_plane",
                rolled_back=True,
                statements=[
                    DbAdminStatementResult(
                        index=index + 1,
                        statement_type=kind,
                        status="error",
                        sql=statements[index],
                        error_message=str(exc),
                    )
                    for index, kind in enumerate(statement_types)
                ],
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        success_count = sum(1 for item in statement_results if item.status == "success")
        committed = success_count > 0
        try:
            self._record_admin_audit(
                operation=f"db_admin_statements_{request.policy}",
                target="ADMIN_EXECUTE",
                executed=committed,
                reason=request.reason,
                detail={
                    "statement_count": len(statements),
                    "success_count": success_count,
                    "types": statement_types,
                },
            )
        except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed) as exc:
            warnings.append(f"実行監査の保存に失敗しました: {exc}")
        schema_refresh_job_id = ""
        schema_refresh_required = False
        schema_refresh_reason_code = ""
        successful_schema_statements = [
            statements[index]
            for index, item in enumerate(statement_results)
            if item.status == "success" and index < len(statements)
        ]
        if committed and _statements_change_schema(successful_schema_statements):
            try:
                sync = self._submit_schema_refresh_after_admin_mutation(
                    target_objects=_schema_refresh_targets_for_statements(
                        successful_schema_statements,
                        current_owner=self._current_schema_owner(),
                    ),
                    source=f"db_admin_statements_{request.policy}",
                )
                schema_refresh_job_id = sync.job_id
                schema_refresh_required = sync.required
                schema_refresh_reason_code = sync.reason_code
                if sync.required:
                    warnings.append(_schema_refresh_required_warning(sync.reason_code))
            except Nl2SqlPersistenceUnavailable as exc:
                warnings.append(f"実行後の Schema job 投入に失敗しました: {exc}")
        if 0 < success_count < len(statement_results):
            warnings.append(f"部分的に成功しました({success_count}/{len(statement_results)} 件)。")
        return DbAdminExecuteData(
            executed=committed,
            runtime="oracle",
            execution_context="admin_control_plane",
            statements=statement_results,
            committed=committed,
            rolled_back=not committed,
            schema_refresh_job_id=schema_refresh_job_id,
            schema_refresh_required=schema_refresh_required,
            schema_refresh_reason_code=schema_refresh_reason_code,
            warnings=warnings,
            timing=self._timing(created_at, started, "db_admin_statements"),
        )

    def drop_db_admin_view(self, request: DbAdminDropViewRequest) -> DbAdminExecuteData:
        identity = self._db_admin_object_identity(request.view_name, request.owner)
        view_name = identity.object_name
        target_name = identity.qualified_name
        sql = f"DROP VIEW {_quote_object_identity(identity)}"
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=target_name,
        )
        if confirmation_error and identity.owner == self._current_schema_owner():
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=view_name,
            )
        if confirmation_error:
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                statements=[
                    DbAdminStatementResult(
                        index=1,
                        statement_type="DDL",
                        status="confirmation_required",
                        sql=sql,
                        error_message=confirmation_error,
                    )
                ],
                warnings=[confirmation_error],
                timing=self._timing(_utc_now(), time.monotonic(), "db_admin_drop_view"),
            )
        return self.execute_db_admin_statements(
            DbAdminStatementsRequest(
                sql=sql,
                policy="view_ddl",
                confirmation="ADMIN_EXECUTE",
                reason=request.reason,
            )
        )

    def preview_db_admin_data(self, request: DbAdminDataPreviewRequest) -> DbAdminDataPreviewData:
        """テーブル/ビューのデータ表示(SQL Assist display_table_data の再マップ)。"""
        sql = self._build_db_admin_preview_sql(request)
        if self._use_oracle_runtime():
            try:
                results = self._oracle_adapter.execute_select(sql, request.limit)
            except OracleAdapterError as exc:
                raise ValueError(str(exc)) from exc
            return DbAdminDataPreviewData(
                runtime="oracle",
                sql=sql,
                results=_normalize_db_admin_preview_results(results),
            )
        return DbAdminDataPreviewData(
            runtime="deterministic",
            sql=sql,
            results=_normalize_db_admin_preview_results(
                self._mock_execute(sql, min(request.limit, 20))
            ),
        )

    def export_db_admin_preview_xlsx(self, request: DbAdminDataPreviewRequest) -> tuple[str, bytes]:
        """テーブル/ビュープレビュー結果を Excel workbook として出力する。"""
        data = self.preview_db_admin_data(request)
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        data_sheet = workbook.active
        data_sheet.title = "data"
        for column_index, column_name in enumerate(data.results.columns, start=1):
            _write_workbook_cell(data_sheet, row=1, column=column_index, value=column_name)
        for row_index, result_row in enumerate(data.results.rows, start=2):
            for column_index, column_name in enumerate(data.results.columns, start=1):
                _write_workbook_cell(
                    data_sheet,
                    row=row_index,
                    column=column_index,
                    value=result_row.get(column_name),
                )
        query_sheet = workbook.create_sheet("query")
        _write_workbook_cell(query_sheet, row=1, column=1, value="SQL")
        _write_workbook_cell(query_sheet, row=2, column=1, value=data.sql)
        buffer = io.BytesIO()
        workbook.save(buffer)
        identity = self._db_admin_object_identity(request.object_name, request.owner)
        filename = f"{identity.qualified_name.lower().replace('.', '_')}_preview.xlsx"
        return filename, buffer.getvalue()

    def _build_db_admin_preview_sql(self, request: DbAdminDataPreviewRequest) -> str:
        # 既存 object 名は import 用の正規化を通さない。Oracle が許可する `$` / `#` を
        # underscore へ置換すると、一覧で選んだ object とは別名を SELECT してしまう。
        identity = self._db_admin_object_identity(request.object_name, request.owner)
        sql = f"SELECT * FROM {_quote_object_identity(identity)}"  # nosec B608
        where_clause = request.where_clause.strip()
        if where_clause:
            if ";" in _mask_sql_literals_and_comments(where_clause):
                raise ValueError("WHERE 句に複数 statement は指定できません。")
            where_body = re.sub(r"^where\s+", "", where_clause, flags=re.IGNORECASE)
            sql += f" WHERE {where_body}"
        if len(_split_sql_statements(sql)) != 1 or not is_select_only(sql):
            raise ValueError("WHERE 句が不正です。単一の SELECT になる条件のみ指定できます。")
        system_object_error = _db_admin_system_object_error(
            sql,
            current_owner=self._current_schema_owner(),
        )
        if system_object_error:
            raise ValueError(system_object_error)
        # 行数上限は SQL へ書き足さず、取得時の fetch 上限だけで効かせる。
        return sql

    def upload_db_admin_csv(self, request: DbAdminCsvUploadRequest) -> DbAdminCsvUploadData:
        """既存テーブルへの表形式アップロード(SQL Assist upload_csv_data の再マップ)。"""
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        try:
            content = base64.b64decode(request.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 の decode に失敗しました: {exc}") from exc
        csv_text, _sheet_name, sheet_warnings = self._tabular_content_to_csv_text(
            filename=request.filename,
            content=content,
        )
        warnings.extend(sheet_warnings)
        settings = get_settings()
        row_limit = request.max_rows or settings.nl2sql_csv_import_max_rows
        columns, rows, parse_warnings = self._parse_csv_sample(
            table_name=request.table_name,
            csv_text=csv_text,
            max_rows=min(row_limit, settings.nl2sql_csv_import_max_rows),
            max_columns=settings.nl2sql_csv_import_max_columns,
            infer_data_types=False,
        )
        warnings.extend(parse_warnings)
        identity = self._db_admin_object_identity(request.table_name, request.owner)
        table_name = identity.object_name
        target_name = identity.qualified_name
        truncate = request.mode == "truncate_insert"
        matched_columns, unmatched_csv = self._match_csv_columns_to_catalog(
            identity.qualified_name, columns
        )
        executed = False
        success_count = 0
        error_count = 0
        row_errors: list[str] = []
        hint = ""
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=target_name,
        )
        if confirmation_error and identity.owner == self._current_schema_owner():
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=table_name,
            )
        if confirmation_error:
            warnings.append(confirmation_error)
        elif self._use_oracle_runtime():
            try:
                result = self._oracle_adapter.upload_csv_to_existing_table(
                    table_name=identity.qualified_name,
                    owner=identity.owner,
                    columns=columns,
                    rows=rows,
                    truncate=truncate,
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
            else:
                executed = True
                if result.get("matched_columns") is not None:
                    matched_columns = list(result["matched_columns"])
                if result.get("unmatched_csv_columns") is not None:
                    unmatched_csv = list(result["unmatched_csv_columns"])
                success_count = int(result.get("success_count") or 0)
                error_count = int(result.get("error_count") or 0)
                row_errors = list(result.get("row_errors") or [])
                hint = str(result.get("hint") or "")
                self._record_admin_audit(
                    operation="db_admin_upload_csv",
                    target=target_name,
                    executed=True,
                    reason=request.reason,
                    detail={
                        "mode": request.mode,
                        "row_count": len(rows),
                        "success_count": success_count,
                        "error_count": error_count,
                        "filename": request.filename,
                    },
                )
        else:
            warnings.append("表形式アップロード実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
        return DbAdminCsvUploadData(
            table_name=target_name,
            filename=request.filename,
            mode=request.mode,
            matched_columns=matched_columns,
            unmatched_csv_columns=unmatched_csv,
            row_count=len(rows),
            success_count=success_count,
            error_count=error_count,
            row_errors=row_errors,
            hint=hint,
            executed=executed,
            runtime=runtime,
            sample_rows=rows[:5],
            warnings=warnings,
            timing=self._timing(created_at, started, "db_admin_upload_csv"),
        )

    def _match_csv_columns_to_catalog(
        self, table_name: str, columns: list[CsvImportColumn]
    ) -> tuple[list[str], list[str]]:
        """catalog 上のテーブル列と CSV 列を大文字比較でマッチングする。"""
        table = self._find_catalog_table(table_name)
        if table is None:
            return [], [column.source_name for column in columns]
        table_column_names = {column.column_name.upper() for column in table.columns}
        matched: list[str] = []
        unmatched: list[str] = []
        for column in columns:
            candidates = {column.source_name.strip().upper(), column.column_name.upper()}
            hit = next(
                (name for name in table_column_names if name in candidates),
                None,
            )
            if hit:
                matched.append(hit)
            else:
                unmatched.append(column.source_name)
        return matched, unmatched

    def analyze_db_admin_failure(self, request: DbAdminAiAnalysisRequest) -> DbAdminAiAnalysisData:
        """SQL 実行エラーの AI 分析(SQL Assist の AI 分析タブ再マップ)。"""
        deterministic = self._deterministic_failure_analysis(request)
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": [
                        "OCI Enterprise AI が未設定のため deterministic 分析を使用しました。"
                    ]
                }
            )
        target_label = {
            "table": "テーブル作成",
            "view": "ビュー作成",
            "data": "データ操作",
            "comment": "COMMENT ON",
            "annotation": "ALTER ... ANNOTATIONS",
        }[request.target]
        try:
            raw = self._enterprise_ai_client.generate(
                prompt=(
                    f"以下は Oracle Database での {target_label} SQL とその実行結果です。"
                    "出力は次の 3 点のみに限定してください。"
                    "1) エラー原因 2) 解決方法 3) 簡潔な結論"
                ),
                context=f"SQL:\n{request.sql}\n\n実行結果:\n{request.result_text}",
                system_prompt=(
                    "あなたはシニア DB エンジニアです。SQL と実行結果の故障診断に特化し、"
                    "エラー原因と実行可能な修復策のみを日本語で簡潔に提示してください。"
                ),
            )
            analysis = self._strip_code_fence(raw).strip()
            if not analysis:
                raise ValueError("AI 分析結果が空です。")
            return DbAdminAiAnalysisData(analysis=analysis, source="oci_enterprise_ai")
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={"warnings": [f"Enterprise AI 分析に失敗したため fallback しました: {exc}"]}
            )

    def _deterministic_failure_analysis(
        self, request: DbAdminAiAnalysisRequest
    ) -> DbAdminAiAnalysisData:
        known: dict[str, tuple[str, str]] = {
            "ORA-00955": (
                "同名のオブジェクトが既に存在します。",
                "別名にするか、先に DROP してから再実行してください。",
            ),
            "ORA-00942": (
                "対象の表またはビューが存在しません。",
                "オブジェクト名の綴りとスキーマを確認してください。",
            ),
            "ORA-00904": (
                "無効な列名が指定されています。",
                "列名の綴りを表定義と突き合わせて修正してください。",
            ),
            "ORA-01861": (
                "リテラルが日付書式と一致しません。",
                "日付は YYYY-MM-DD 形式(例: 2026-01-31)で指定してください。",
            ),
            "ORA-01843": (
                "無効な月が指定されています。",
                "日付は YYYY-MM-DD 形式で指定してください。",
            ),
            "ORA-01722": (
                "数値への変換に失敗しました。",
                "数値列に文字列が入っていないか確認してください。",
            ),
            "ORA-12899": (
                "列の最大長を超えています。",
                "値を短くするか、列の長さを ALTER で拡張してください。",
            ),
        }
        codes = re.findall(r"ORA-\d{5}", request.result_text or "")
        for code in codes:
            if code in known:
                cause, fix = known[code]
                return DbAdminAiAnalysisData(
                    analysis=(
                        f"1) エラー原因: {code}: {cause}\n"
                        f"2) 解決方法: {fix}\n"
                        "3) 結論: SQL を修正して再実行してください。"
                    ),
                )
        if codes:
            return DbAdminAiAnalysisData(
                analysis=(
                    f"1) エラー原因: {codes[0]} が発生しました。\n"
                    "2) 解決方法: エラーメッセージの対象オブジェクト・列・値を確認してください。\n"
                    "3) 結論: メッセージ本文を手掛かりに SQL を修正して再実行してください。"
                ),
            )
        return DbAdminAiAnalysisData(
            analysis=(
                "1) エラー原因: 実行結果から既知の ORA エラーコードを検出できませんでした。\n"
                "2) 解決方法: SQL 文法と対象オブジェクトの存在を確認してください。\n"
                "3) 結論: 詳細分析には OCI Enterprise AI の設定が必要です。"
            ),
        )

    def _view_query_sql_from_ddl(self, ddl: str) -> str:
        first_statement = (_split_sql_statements(ddl) or [str(ddl or "")])[0]
        match = re.search(r"\b(SELECT|WITH)\b[\s\S]*", first_statement, re.IGNORECASE)
        return (match.group(0) if match else first_statement).strip()

    def extract_db_admin_join_where(self, request: DbAdminJoinWhereRequest) -> DbAdminJoinWhereData:
        """ビュー DDL から JOIN/WHERE 条件を抽出する(SQL Assist の AI 抽出再マップ)。"""
        view_sql = self._view_query_sql_from_ddl(request.ddl)
        prompt_profile: _JoinWherePromptProfile = "sql_structure"
        deterministic = self._deterministic_join_where(view_sql, prompt_profile)
        if not self._enterprise_ai_client.is_configured():
            return deterministic.model_copy(
                update={
                    "warnings": [
                        f"{prompt_profile}: OCI Enterprise AI が未設定のため "
                        "deterministic 抽出を使用しました。"
                    ]
                }
            )
        try:
            raw = self._enterprise_ai_client.generate(
                prompt=_SQL_STRUCTURE_ANALYSIS_PROMPT.format(sql=view_sql),
                context="",
                system_prompt=_SQL_STRUCTURE_SYSTEM_PROMPT,
            )
            return self._parse_structure_join_where(raw, deterministic, prompt_profile)
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": [
                        f"{prompt_profile}: Enterprise AI 抽出に失敗したため "
                        f"fallback しました: {exc}"
                    ]
                }
            )

    def _parse_structure_join_where(
        self,
        raw: str,
        deterministic: DbAdminJoinWhereData,
        prompt_profile: _JoinWherePromptProfile,
    ) -> DbAdminJoinWhereData:
        structure_markdown = self._clean_join_where_ai_text(raw)
        join_lines, join_section_found = self._markdown_sql_section_lines(
            structure_markdown, ("JOIN句", "JOIN")
        )
        where_lines, where_section_found = self._markdown_sql_section_lines(
            structure_markdown, ("WHERE句", "WHERE")
        )
        expected_conditions = (
            deterministic.join_text != "None" or deterministic.where_text != "None"
        )
        if expected_conditions and not join_section_found and not where_section_found:
            raise ValueError("SQL構造解析の JOIN句 / WHERE句 セクションを解析できませんでした。")
        return DbAdminJoinWhereData(
            join_text="\n".join(join_lines) if join_lines else "None",
            where_text="\n".join(where_lines) if where_lines else "None",
            source="oci_enterprise_ai",
            prompt_profile=prompt_profile,
            structure_markdown=structure_markdown,
        )

    def _markdown_sql_section_lines(
        self, markdown: str, heading_tokens: tuple[str, ...]
    ) -> tuple[list[str], bool]:
        heading_pattern = re.compile(r"^#{2,4}\s+(.+?)\s*$", re.MULTILINE)
        for match in heading_pattern.finditer(markdown):
            heading = match.group(1).strip()
            if not any(token.lower() in heading.lower() for token in heading_tokens):
                continue
            next_match = heading_pattern.search(markdown, match.end())
            end = next_match.start() if next_match else len(markdown)
            return self._clean_markdown_sql_lines(markdown[match.end() : end]), True
        return [], False

    def _clean_markdown_sql_lines(self, section: str) -> list[str]:
        lines: list[str] = []
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not line or line == "---":
                continue
            line = re.sub(r"^[-*]\s*", "", line)
            line = re.sub(r"^\d+[.)]\s*", "", line)
            line = line.replace("**", "").strip()
            if line.lower() in {"none", "n/a", "not present", "なし", "該当なし"}:
                continue
            lines.append(line)
        return lines

    def _clean_join_where_ai_text(self, raw: str) -> str:
        cleaned = re.sub(r"```+\w*", "", str(raw or ""))
        cleaned = re.sub(r"```+", "", cleaned)
        return cleaned.strip()

    def _deterministic_join_where(
        self,
        view_sql: str,
        prompt_profile: _JoinWherePromptProfile = "sql_structure",
    ) -> DbAdminJoinWhereData:
        structure = self._sql_structure(view_sql, [])
        joins = structure.get("joins") or []
        filters = structure.get("filters") or []
        return DbAdminJoinWhereData(
            join_text="\n".join(joins) if joins else "None",
            where_text="\n".join(filters) if filters else "None",
            prompt_profile=prompt_profile,
        )

    def _catalog_object_detail(
        self, object_name: str, object_type: str, *, include_ddl: bool = True
    ) -> DbAdminObjectDetail:
        try:
            identity = self._db_admin_object_identity(object_name)
        except ValueError:
            identity = None
        table = self._find_catalog_table(object_name)
        if table is None:
            missing_name = (
                identity.object_name if identity is not None else _normalize_identifier(object_name)
            )
            missing_owner = identity.owner if identity is not None else ""
            return DbAdminObjectDetail(
                name=missing_name,
                owner=missing_owner,
                qualified_name=(
                    identity.qualified_name
                    if identity is not None
                    else _qualified_display_name(missing_owner, missing_name)
                ),
                object_type=object_type,
                warnings=[f"{object_name}: catalog に存在しません。"],
            )
        qualified_name = _qualified_display_name(table.owner, table.table_name)
        quoted_object = _quote_object_identity(
            OracleObjectIdentity(owner=table.owner, object_name=table.table_name)
        )
        if not include_ddl:
            return DbAdminObjectDetail(
                name=table.table_name,
                owner=table.owner,
                qualified_name=qualified_name,
                object_type=object_type,
                row_count=table.row_count,
                comment=table.comment,
                columns=table.columns,
                constraints=table.constraints,
                ddl="",
            )
        column_defs = ", ".join(
            f"{_quote_identifier(column.column_name)} {column.data_type}"
            for column in table.columns
        )
        ddl_kind = "VIEW" if object_type == "view" else "TABLE"
        ddl = f"CREATE {ddl_kind} {quoted_object} ({column_defs});"
        if table.comment:
            ddl += f"\nCOMMENT ON TABLE {quoted_object} IS {_quote_sql_string(table.comment)};"
        for column in table.columns:
            if column.comment:
                column_comment = _quote_sql_string(column.comment)
                ddl += (
                    f"\nCOMMENT ON COLUMN {quoted_object}."
                    f"{_quote_identifier(column.column_name)} IS {column_comment};"
                )
        return DbAdminObjectDetail(
            name=table.table_name,
            owner=table.owner,
            qualified_name=qualified_name,
            object_type=object_type,
            row_count=table.row_count,
            comment=table.comment,
            columns=table.columns,
            constraints=table.constraints,
            ddl=ddl,
        )

    def _tabular_content_to_csv_text(
        self,
        *,
        filename: str,
        content: bytes,
        sheet_name: str = "",
        require_sheet_name: bool = False,
    ) -> tuple[str, str, list[str]]:
        suffix = Path(filename).suffix.lower()
        warnings: list[str] = []
        if suffix in WORKBOOK_SUFFIXES:
            sheet, sheet_warnings = read_workbook_sheet(
                filename,
                content,
                sheet_name,
                require_requested_name=require_sheet_name,
            )
            warnings.extend(sheet_warnings)
            output = io.StringIO()
            writer = csv.writer(output)
            for row in sheet.rows:
                writer.writerow([normalize_workbook_scalar(value) for value in row])
            return output.getvalue(), sheet.title, warnings
        if suffix not in {".csv", ""}:
            raise ValueError(
                f"{suffix} は未対応の形式です。CSV、XLSX、XLS のいずれかを指定してください。"
            )
        validate_tabular_text_signature(content)
        decoded, encoding_label = _decode_tabular_text_content(content)
        if encoding_label != "UTF-8":
            warnings.append(f"CSV の文字エンコーディングは {encoding_label} として読み込みました。")
        return decoded, "", warnings

    def _admin_confirmation_error(self, *, confirmation: str, target: str) -> str:
        # 対象名 target を要求する操作は対象名の完全一致のみ受理する。
        # ADMIN_EXECUTE をマスターワードとして代替させると、対象を意識させる
        # 確認の意味が失われるため迂回を許可しない。
        normalized = confirmation.strip()
        if normalized == target:
            return ""
        if target == "ADMIN_EXECUTE":
            return "実行には confirmation=ADMIN_EXECUTE が必要です。"
        return f"実行には confirmation={target} が必要です。(ADMIN_EXECUTE では代替できません)"

    def _record_admin_audit(
        self,
        *,
        operation: str,
        target: str,
        executed: bool,
        reason: str,
        detail: dict[str, Any],
    ) -> None:
        with self._lock:
            self._admin_audit.append(
                {
                    "id": str(uuid.uuid4()),
                    "created_at": _utc_now(),
                    "operation": operation,
                    "target": target,
                    "executed": executed,
                    "reason": reason,
                    "detail": detail,
                }
            )
            self._admin_audit = self._admin_audit[-200:]
        self._persist_state(collections=("admin_audit",))

    def refresh_select_ai_profile(self, profile_id: str | None) -> AssetRefreshData:
        profile = self.get_profile(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        attributes = self.build_select_ai_profile_attributes(profile)
        expected_scope = self._select_ai_object_scope_set(attributes.get("object_list"))
        actual_scope = set(expected_scope)
        warning = ""
        engine_meta: dict[str, Any] = {
            "allowed_tables": profile.allowed_tables,
            "allowed_views": profile.allowed_views,
            "allowed_objects": self.profile_allowed_object_names(profile),
            "profile_attributes": self._redact_select_ai_context_attributes(attributes),
            "runtime": "deterministic",
        }
        if self._use_oracle_runtime():
            try:
                oracle_meta = self._oracle_adapter.upsert_select_ai_profile_low_level(
                    profile_name=profile_name,
                    description="",
                    attributes=attributes,
                )
                if isinstance(oracle_meta.get("attributes"), dict):
                    oracle_meta["attributes"] = self._redact_select_ai_context_attributes(
                        oracle_meta["attributes"]
                    )
                engine_meta.update(oracle_meta)
                detail = self._enrich_select_ai_db_profile(
                    SelectAiDbProfile.model_validate(
                        self._oracle_adapter.get_select_ai_profile_detail(profile_name=profile_name)
                    )
                )
                actual_scope = self._select_ai_object_scope_set(detail.object_list)
            except OracleAdapterError as exc:
                warning = str(exc)
                actual_scope = set()
        data = self._record_select_ai_scope_state(
            profile_name=profile_name,
            expected_scope=expected_scope,
            actual_scope=actual_scope,
            warning=warning,
        )
        return data.model_copy(update={"engine_meta": {**engine_meta, **data.engine_meta}})

    def upsert_profile_select_ai_profile(
        self,
        profile_id: str,
        request: ProfileSelectAiProfileRequest,
    ) -> SelectAiDbProfileMutationData:
        profile = self.get_profile(profile_id)
        attributes = self.build_select_ai_profile_attributes(profile)
        if request.attributes_override:
            attributes = {**attributes, **request.attributes_override}
        profile_name = self._select_ai_profile_name(profile)
        original_name = (
            request.original_name.strip() or profile.select_ai_config.previous_profile_name.strip()
        )
        # Oracle profile 名は機械導出でありユーザーが入力しないため、この wrapper が
        # ユーザー境界として ADMIN_EXECUTE を受理し、内部委譲時に導出名へ変換する。
        confirmation = request.confirmation.strip()
        if confirmation == "ADMIN_EXECUTE":
            confirmation = profile_name
        return self.upsert_select_ai_db_profile(
            SelectAiDbProfileUpsertRequest(
                profile_name=profile_name,
                attributes=attributes,
                description="",
                category=profile.category or profile.name,
                confirmation=confirmation,
                original_name=original_name,
                reason=request.reason,
            )
        )

    def _parse_csv_sample(
        self,
        *,
        table_name: str,
        csv_text: str,
        max_rows: int,
        max_columns: int,
        infer_data_types: bool = True,
    ) -> tuple[list[CsvImportColumn], list[dict[str, str | None]], list[str]]:
        self._sanitize_import_table_name(table_name)
        warnings: list[str] = []
        text = csv_text.lstrip("\ufeff")
        delimiter = ","
        skipinitialspace = False
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=",\t;|")
            delimiter = dialect.delimiter
            skipinitialspace = dialect.skipinitialspace
        except csv.Error:
            pass
        # csv.reader は newline="" で開いた text stream を前提とする。
        # これにより LF / CRLF / CR をすべて record separator として扱いつつ、
        # quote 内の改行は cell 値として保持できる。
        reader = csv.reader(
            io.StringIO(text, newline=""),
            delimiter=delimiter,
            quotechar='"',
            doublequote=True,
            skipinitialspace=skipinitialspace,
        )
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV header が見つかりません。") from exc
        except csv.Error as exc:
            raise ValueError(
                "CSV の解析に失敗しました。改行形式・引用符・セルの長さを確認してください。"
            ) from exc
        if not raw_header or all(not cell.strip() for cell in raw_header):
            raise ValueError("CSV header が空です。")
        if len(raw_header) > max_columns:
            warnings.append(
                f"列数が上限 {max_columns} を超えたため、先頭 {max_columns} 列だけを使用します。"
            )
            raw_header = raw_header[:max_columns]
        column_names = self._dedupe_csv_column_names(raw_header)
        raw_rows: list[tuple[int, list[str]]] = []
        truncated = False
        try:
            for file_row_number, row in enumerate(reader, start=2):
                if len(raw_rows) >= max_rows:
                    truncated = True
                    break
                raw_rows.append((file_row_number, row[: len(column_names)]))
        except csv.Error as exc:
            raise ValueError(
                "CSV の解析に失敗しました。改行形式・引用符・セルの長さを確認してください。"
            ) from exc
        if truncated:
            warnings.append(
                f"行数が上限 {max_rows} を超えたため、先頭 {max_rows} 行だけを使用します。"
            )
        columns = [
            CsvImportColumn(
                source_name=raw_header[index].strip() or f"column_{index + 1}",
                column_name=column_name,
                data_type=self._infer_csv_data_type(
                    [row[index] if index < len(row) else "" for _file_row, row in raw_rows],
                    enforce_varchar2_limit=infer_data_types,
                ),
                nullable=any(
                    (row[index] if index < len(row) else "").strip() == ""
                    for _file_row, row in raw_rows
                ),
            )
            for index, column_name in enumerate(column_names)
        ]
        rows: list[dict[str, str | None]] = [
            _CsvRow(
                {
                    column.column_name: self._normalize_csv_cell(
                        row[index] if index < len(row) else ""
                    )
                    for index, column in enumerate(columns)
                },
                file_row_number=file_row_number,
            )
            for file_row_number, row in raw_rows
            if any(cell.strip() for cell in row)
        ]
        if not rows:
            warnings.append("データ行がありません。取込対象を確認してください。")
        return columns, rows, warnings

    def _sanitize_import_table_name(self, table_name: str) -> str:
        normalized = _csv_identifier(table_name, "CSV_IMPORT")
        if not _STRICT_IDENTIFIER.fullmatch(normalized):
            raise ValueError("table_name は英数字と underscore の Oracle 識別子へ変換できません。")
        if not is_user_visible_object_name(normalized):
            raise ValueError(_system_object_blocked_message([normalized]))
        return normalized

    def _sanitize_truncate_table_name(self, table_name: str) -> str:
        raw = table_name.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", raw):
            raise ValueError(
                "table_name は英字で始まる英数字と underscore の Oracle 識別子で指定してください。"
            )
        return raw.upper()

    def _dedupe_csv_column_names(self, raw_header: list[str]) -> list[str]:
        seen: dict[str, int] = {}
        used: set[str] = set()
        names: list[str] = []
        for index, source_name in enumerate(raw_header):
            base = _csv_identifier(source_name, f"COLUMN_{index + 1}")
            count = seen.get(base, 0)
            if count == 0 and base not in used:
                candidate = base
                seen[base] = 1
            else:
                suffix_number = max(count + 1, 2)
                while True:
                    suffix = f"_{suffix_number}"
                    candidate = f"{base[: 128 - len(suffix)]}{suffix}"
                    if candidate not in used:
                        break
                    suffix_number += 1
                seen[base] = suffix_number
            used.add(candidate)
            names.append(candidate)
        return names

    def _infer_csv_data_type(
        self,
        values: list[str],
        *,
        enforce_varchar2_limit: bool = True,
    ) -> str:
        normalized = [value.strip() for value in values if value.strip()]
        if normalized and all(self._is_csv_number(value) for value in normalized):
            return "NUMBER"
        max_len = max((len(value) for value in normalized), default=1)
        if max_len > 4000:
            if not enforce_varchar2_limit:
                return "CLOB"
            raise ValueError(
                "4000 文字を超えるセルは自動 VARCHAR2 取込できません。"
                "値を短くするか、CLOB 列を持つ既存テーブルへ取り込んでください。"
            )
        return f"VARCHAR2({max(max_len, 1)} CHAR)"

    def _is_csv_number(self, value: str) -> bool:
        stripped = value.strip()
        if not re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)", stripped):
            return False
        unsigned = stripped[1:] if stripped[:1] in {"+", "-"} else stripped
        integer_part = unsigned.split(".", 1)[0]
        return not (len(integer_part) > 1 and integer_part.startswith("0"))

    def _normalize_csv_cell(self, value: str) -> str | None:
        stripped = value.strip()
        return stripped or None

    def _csv_import_ddl(self, table_name: str, columns: list[CsvImportColumn]) -> str:
        column_defs = ", ".join(f'"{column.column_name}" {column.data_type}' for column in columns)
        return f'CREATE TABLE "{table_name}" ({column_defs})'

    def _csv_import_insert_sql(self, table_name: str, columns: list[CsvImportColumn]) -> str:
        column_names = ", ".join(f'"{column.column_name}"' for column in columns)
        binds = ", ".join(f":c{index}" for index, _column in enumerate(columns))
        # Safe: generated SQL uses sanitized CSV identifiers; execution path uses Oracle binds.
        return f'INSERT INTO "{table_name}" ({column_names}) VALUES ({binds})'  # nosec B608

    def refresh_select_ai_agent_assets(
        self,
        profile_id: str | None,
        *,
        profile_already_synced: bool = False,
    ) -> AssetRefreshData:
        profile = self.get_profile(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        asset_names = self._select_ai_agent_asset_names(profile)
        tool_name = asset_names["tool"]
        agent_name = asset_names["agent"]
        task_name = asset_names["task"]
        team_name = asset_names["team"]
        warning = ""
        refreshed = True
        status = "ready"
        engine_meta: dict[str, Any] = {
            "tool_name": tool_name,
            "agent_name": agent_name,
            "task_name": task_name,
            "allowed_tables": profile.allowed_tables,
            "allowed_views": profile.allowed_views,
            "allowed_objects": self.profile_allowed_object_names(profile),
            "runtime": "deterministic",
        }
        if self._use_oracle_runtime():
            profile_sync = (
                None if profile_already_synced else self.refresh_select_ai_profile(profile.id)
            )
            if profile_sync is not None:
                engine_meta["select_ai_profile_sync"] = profile_sync.model_dump(mode="json")
            if profile_sync is not None and not profile_sync.refreshed:
                data = AssetRefreshData(
                    engine=Nl2SqlEngine.SELECT_AI_AGENT,
                    refreshed=False,
                    status="error",
                    refreshed_at=_utc_now(),
                    profile_name=profile_name,
                    team_name=team_name,
                    warning=profile_sync.warning,
                    asset_names={
                        "profile": profile_name,
                        "tool": tool_name,
                        "agent": agent_name,
                        "task": task_name,
                        "team": team_name,
                    },
                    engine_meta=engine_meta,
                )
                with self._lock:
                    self._asset_meta[Nl2SqlEngine.SELECT_AI_AGENT] = data
                self._persist_singletons("asset_meta")
                return data
            try:
                previous_warning = self._cleanup_previous_select_ai_agent_team(
                    profile_name=profile_name,
                    tool_name=tool_name,
                    agent_name=agent_name,
                    task_name=task_name,
                    base_team_name=team_name,
                )
                if previous_warning:
                    warning = previous_warning
                try:
                    engine_meta.update(
                        self._refresh_select_ai_agent_assets_with_team(
                            profile=profile,
                            profile_name=profile_name,
                            tool_name=tool_name,
                            agent_name=agent_name,
                            task_name=task_name,
                            team_name=team_name,
                        )
                    )
                except OracleAdapterError as exc:
                    if not self._looks_like_agent_generated_profile_conflict(str(exc)):
                        raise
                    team_name = self._versioned_select_ai_team_name(team_name)
                    version_warning = (
                        "Oracle maintained Agent profile が残っていたため、"
                        f"versioned team {team_name} を使用しました。"
                    )
                    warning = f"{warning} {version_warning}".strip()
                    engine_meta.update(
                        self._refresh_select_ai_agent_assets_with_team(
                            profile=profile,
                            profile_name=profile_name,
                            tool_name=tool_name,
                            agent_name=agent_name,
                            task_name=task_name,
                            team_name=team_name,
                        )
                    )
            except OracleAdapterError as exc:
                refreshed = False
                status = "error"
                warning = f"{warning} {exc}".strip()
        data = AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            refreshed=refreshed,
            status=status,
            refreshed_at=_utc_now(),
            profile_name=profile_name,
            team_name=team_name,
            warning=warning,
            asset_names={
                "profile": profile_name,
                "tool": tool_name,
                "agent": agent_name,
                "task": task_name,
                "team": team_name,
            },
            engine_meta=engine_meta,
        )
        with self._lock:
            self._asset_meta[Nl2SqlEngine.SELECT_AI_AGENT] = data
        self._persist_singletons("asset_meta")
        return data

    def _cleanup_previous_select_ai_agent_team(
        self,
        *,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        base_team_name: str,
    ) -> str:
        previous = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        if (
            previous is None
            or previous.profile_name != profile_name
            or not previous.team_name
            or previous.team_name == base_team_name
        ):
            return ""
        try:
            self._oracle_adapter.drop_select_ai_agent_assets(
                profile_name=profile_name,
                tool_name=tool_name,
                agent_name=agent_name,
                task_name=task_name,
                team_name=previous.team_name,
            )
        except OracleAdapterError as exc:
            return f"previous Agent team cleanup warning: {exc}"
        return f"previous Agent team {previous.team_name} を cleanup しました。"

    def cleanup_select_ai_assets(
        self,
        profile_id: str | None,
        engines: list[Nl2SqlEngine],
        confirmation: str = "",
        reason: str = "",
    ) -> list[AssetCleanupData]:
        """Select AI / Agent assets を確認後に cleanup する。"""
        confirmation_error = self._admin_confirmation_error(
            confirmation=confirmation,
            target="ADMIN_EXECUTE",
        )
        if confirmation_error:
            return [
                AssetCleanupData(
                    engine=engine,
                    executed=False,
                    status="confirmation_required",
                    cleaned_at=_utc_now(),
                    warning=confirmation_error,
                    engine_meta={"runtime": "deterministic"},
                )
                for engine in engines
                if engine != Nl2SqlEngine.AUTO
            ]
        cleaned: list[AssetCleanupData] = []
        for engine in engines:
            if engine == Nl2SqlEngine.AUTO:
                continue
            if engine == Nl2SqlEngine.SELECT_AI:
                cleaned.append(self._cleanup_select_ai_profile(profile_id))
            elif engine == Nl2SqlEngine.SELECT_AI_AGENT:
                cleaned.append(self._cleanup_select_ai_agent_assets(profile_id))
            else:
                cleaned.append(
                    AssetCleanupData(
                        engine=engine,
                        executed=False,
                        status="skipped",
                        cleaned_at=_utc_now(),
                        warning="この engine に cleanup 対象の Oracle asset はありません。",
                        engine_meta={"runtime": "deterministic"},
                    )
                )
        if any(item.executed for item in cleaned):
            self._record_admin_audit(
                operation="select_ai_assets_cleanup",
                target="ADMIN_EXECUTE",
                executed=True,
                reason=reason,
                detail={"engines": [engine.value for engine in engines], "profile_id": profile_id},
            )
        self._persist_singletons("asset_meta")
        return cleaned

    def list_select_ai_db_profiles(
        self,
        include_detail: bool = False,
        *,
        business_profiles_only: bool = False,
        include_archived_business_profiles: bool = True,
    ) -> SelectAiDbProfilesData:
        del include_detail
        warnings: list[str] = []
        profile_list_refresh_required = False
        profile_list_refresh_reason_code = ""
        business_profile_names = (
            self._business_select_ai_profile_names(
                include_archived=include_archived_business_profiles
            )
            if business_profiles_only
            else None
        )
        profiles = [
            self._enrich_select_ai_db_profile(profile)
            for profile in self._load_select_ai_db_profile_documents()
            if self._is_business_select_ai_profile(profile.name, business_profile_names)
        ]
        if not profiles and not self._select_ai_db_profile_refresh_initialized():
            profile_list_refresh_required = True
            profile_list_refresh_reason_code = "profile_list_read_model_uninitialized"
            profiles = self._fallback_select_ai_db_profiles_from_asset_meta(business_profile_names)
            warnings.append(
                "DB Profile 一覧 read model が未初期化です。DB Profile 一覧を再取得してください。"
            )
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if not self._use_oracle_runtime() and not profiles:
            warnings.append("Oracle runtime ではないため保存済み asset metadata を表示しています。")
        return SelectAiDbProfilesData(
            runtime=runtime,
            profiles=profiles,
            warnings=warnings,
            profile_list_refresh_required=profile_list_refresh_required,
            profile_list_refresh_reason_code=profile_list_refresh_reason_code,
        )

    def get_select_ai_db_profile(self, profile_name: str) -> SelectAiDbProfileDetailData:
        warnings: list[str] = []
        key = self._select_ai_db_profile_key(profile_name)
        document = self._refresh_job_repository.get_document(
            _SELECT_AI_DB_PROFILE_COLLECTION,
            key,
        )
        if document is not None:
            return SelectAiDbProfileDetailData(
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                profile=self._enrich_select_ai_db_profile(
                    SelectAiDbProfile.model_validate(document)
                ),
                warnings=warnings,
            )
        profiles = self.list_select_ai_db_profiles()
        profile = next(
            (
                item
                for item in profiles.profiles
                if item.name.upper() == profile_name.strip().upper()
            ),
            SelectAiDbProfile(name=profile_name, status="not_found"),
        )
        return SelectAiDbProfileDetailData(
            runtime=profiles.runtime,
            profile=profile,
            warnings=[*warnings, *profiles.warnings],
        )

    def _enrich_select_ai_db_profile(self, profile: SelectAiDbProfile) -> SelectAiDbProfile:
        attributes = dict(profile.attributes)
        object_list = profile.object_list
        raw_object_list = attributes.get("object_list")
        if not object_list and isinstance(raw_object_list, list):
            object_list = [item for item in raw_object_list if isinstance(item, dict)]
        table_names, view_names = self._split_select_ai_object_names(object_list)
        return profile.model_copy(
            update={
                "object_list": object_list,
                "tables": table_names,
                "views": view_names,
                "region": str(attributes.get("region") or profile.region or ""),
                "model": str(attributes.get("model") or profile.model or ""),
                "embedding_model": str(
                    attributes.get("embedding_model") or profile.embedding_model or ""
                ),
                "category": profile.category or profile.description,
            }
        )

    def _split_select_ai_object_names(
        self, object_list: Sequence[dict[str, Any]]
    ) -> tuple[list[str], list[str]]:
        catalog_types = {
            self._catalog_qualified_name(table): table.table_type.lower()
            for table in self._catalog.tables
        }
        tables: list[str] = []
        views: list[str] = []
        for item in object_list:
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue
            identity = parse_object_identity(
                raw_name,
                default_owner=str(item.get("owner") or self._current_schema_owner()),
            )
            qualified = identity.qualified_name
            object_type = catalog_types.get(qualified, "")
            if "view" in object_type or identity.object_name.startswith("V_"):
                views.append(qualified)
            else:
                tables.append(qualified)
        return self._dedupe_object_names(tables), self._dedupe_object_names(views)

    def _select_ai_object_scope_set(self, value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        scope: set[str] = set()
        for item in value:
            if isinstance(item, str):
                scope.add(self._resolve_profile_object_name(item))
                continue
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("object_name") or "").strip()
            if not name:
                continue
            identity = parse_object_identity(
                name,
                default_owner=str(item.get("owner") or self._current_schema_owner()),
            )
            scope.add(identity.qualified_name)
        return scope

    @staticmethod
    def _select_ai_db_profile_key(profile_name: str) -> str:
        return profile_name.strip().upper()

    def _load_select_ai_db_profile_documents(self) -> list[SelectAiDbProfile]:
        documents = self._refresh_job_repository.list_documents(
            _SELECT_AI_DB_PROFILE_COLLECTION,
            limit=10000,
        )
        profiles: list[SelectAiDbProfile] = []
        for document in documents:
            try:
                profiles.append(SelectAiDbProfile.model_validate(document))
            except Exception:  # nosec B112
                continue
        return sorted(profiles, key=lambda item: item.name.upper())

    def _select_ai_db_profile_refresh_initialized(self) -> bool:
        return (
            self._refresh_job_repository.get_document(
                _SELECT_AI_DB_PROFILE_REFRESH_META_COLLECTION,
                "head",
            )
            is not None
        )

    def _save_select_ai_db_profile_refresh_meta(
        self,
        *,
        mode: SelectAiDbProfileRefreshMode,
    ) -> None:
        self._refresh_job_repository.put_document(
            _SELECT_AI_DB_PROFILE_REFRESH_META_COLLECTION,
            "head",
            {
                "refreshed_at": _utc_now(),
                "mode": mode.value,
                "profile_count": len(self._load_select_ai_db_profile_documents()),
            },
            status="ready",
        )

    def _save_select_ai_db_profile_refresh_job(
        self, job: SelectAiDbProfileRefreshJobData
    ) -> SelectAiDbProfileRefreshJobData:
        self._refresh_job_repository.put_document(
            _SELECT_AI_DB_PROFILE_REFRESH_JOB_COLLECTION,
            job.job_id,
            job.model_dump(mode="json"),
            status=job.status.value,
        )
        return job.model_copy(deep=True)

    def _load_select_ai_db_profile_refresh_job(
        self,
        job_id: str,
    ) -> SelectAiDbProfileRefreshJobData | None:
        document = self._refresh_job_repository.get_document(
            _SELECT_AI_DB_PROFILE_REFRESH_JOB_COLLECTION,
            job_id,
        )
        if document is None:
            return None
        return SelectAiDbProfileRefreshJobData.model_validate(document)

    def get_select_ai_db_profile_refresh_job(
        self,
        job_id: str,
    ) -> SelectAiDbProfileRefreshJobData | None:
        job = self._load_select_ai_db_profile_refresh_job(job_id)
        if job is None:
            return None
        if job.status in {
            SelectAiDbProfileRefreshStatus.PENDING,
            SelectAiDbProfileRefreshStatus.RUNNING,
        }:
            self._dispatch_select_ai_db_profile_refresh_job(job.job_id)
        return job

    def start_select_ai_db_profile_refresh_job(
        self,
        *,
        dispatch: bool = True,
        mode: SelectAiDbProfileRefreshMode | str = SelectAiDbProfileRefreshMode.FULL,
        source: str = "manual",
        target_profiles: Sequence[SelectAiDbProfileRefreshTarget] | None = None,
    ) -> SelectAiDbProfileRefreshJobData:
        normalized_mode = SelectAiDbProfileRefreshMode(mode)
        targets = self._dedupe_select_ai_db_profile_refresh_targets(target_profiles or [])
        if normalized_mode == SelectAiDbProfileRefreshMode.TARGETED and not targets:
            raise SelectAiDbProfileListRefreshFullRequired("profile_list_refresh_target_unresolved")
        job = SelectAiDbProfileRefreshJobData(
            job_id=str(uuid.uuid4()),
            mode=normalized_mode,
            source=source,
            target_profiles=targets,
            created_at=_utc_now(),
        )
        saved = self._save_select_ai_db_profile_refresh_job(job)
        if dispatch:
            self._dispatch_select_ai_db_profile_refresh_job(saved.job_id)
        return saved

    def _dispatch_select_ai_db_profile_refresh_job(self, job_id: str) -> bool:
        with self._profile_list_refresh_dispatch_lock:
            if job_id in self._profile_list_refresh_dispatching_job_ids:
                return False
            self._profile_list_refresh_dispatching_job_ids.add(job_id)

        def run() -> None:
            try:
                self._run_select_ai_db_profile_refresh_job(job_id)
            finally:
                with self._profile_list_refresh_dispatch_lock:
                    self._profile_list_refresh_dispatching_job_ids.discard(job_id)

        thread = threading.Thread(
            target=run,
            daemon=True,
            name=f"db-profile-refresh-{job_id[:8]}",
        )
        thread.start()
        return True

    @staticmethod
    def _dedupe_select_ai_db_profile_refresh_targets(
        targets: Sequence[SelectAiDbProfileRefreshTarget],
    ) -> list[SelectAiDbProfileRefreshTarget]:
        merged: dict[str, SelectAiDbProfileRefreshTarget] = {}
        for target in targets:
            key = target.profile_name.strip().upper()
            if not key:
                continue
            current = merged.get(key)
            if current is None or target.expected_state != "unknown":
                merged[key] = target.model_copy(update={"profile_name": key})
        return list(merged.values())

    def _submit_select_ai_db_profile_list_refresh_after_mutation(
        self,
        *,
        target_profiles: Sequence[SelectAiDbProfileRefreshTarget] | None,
        source: str,
    ) -> SelectAiDbProfileListRefreshSync:
        targets = self._dedupe_select_ai_db_profile_refresh_targets(target_profiles or [])
        if not targets:
            return SelectAiDbProfileListRefreshSync(
                required=True,
                reason_code="profile_list_refresh_target_unresolved",
            )
        try:
            job = self.start_select_ai_db_profile_refresh_job(
                mode=SelectAiDbProfileRefreshMode.TARGETED,
                source=source,
                target_profiles=targets,
            )
        except SelectAiDbProfileListRefreshFullRequired as exc:
            return SelectAiDbProfileListRefreshSync(
                required=True,
                reason_code=exc.reason_code,
            )
        except Exception:
            logger.warning(
                "select_ai_db_profile_refresh_submit_failed",
                exc_info=True,
                extra={"source": source},
            )
            return SelectAiDbProfileListRefreshSync(
                required=True,
                reason_code="profile_list_refresh_submit_failed",
            )
        if job.requires_full_refresh:
            return SelectAiDbProfileListRefreshSync(
                job_id=job.job_id,
                required=True,
                reason_code=job.error_code or "profile_list_refresh_full_required",
            )
        return SelectAiDbProfileListRefreshSync(job_id=job.job_id)

    @staticmethod
    def _select_ai_db_profile_target(
        profile_name: str,
        *,
        expected_state: Literal["present", "absent", "unknown"],
    ) -> SelectAiDbProfileRefreshTarget | None:
        key = profile_name.strip().upper()
        if not key:
            return None
        return SelectAiDbProfileRefreshTarget(
            profile_name=key,
            expected_state=expected_state,
        )

    def _select_ai_db_profile_upsert_refresh_targets(
        self,
        *,
        profile_name: str,
        original_name: str,
    ) -> list[SelectAiDbProfileRefreshTarget]:
        targets: list[SelectAiDbProfileRefreshTarget] = []
        old_key = original_name.strip().upper()
        new_key = profile_name.strip().upper()
        if old_key and old_key != new_key:
            old_target = self._select_ai_db_profile_target(
                original_name,
                expected_state="absent",
            )
            if old_target is not None:
                targets.append(old_target)
        new_target = self._select_ai_db_profile_target(
            profile_name,
            expected_state="present",
        )
        if new_target is not None:
            targets.append(new_target)
        return targets

    def _select_ai_db_profile_from_oracle_detail(
        self,
        profile_name: str,
    ) -> SelectAiDbProfile:
        return self._enrich_select_ai_db_profile(
            SelectAiDbProfile.model_validate(
                self._oracle_adapter.get_select_ai_profile_detail(profile_name=profile_name)
            )
        )

    def _fallback_select_ai_db_profiles_from_asset_meta(
        self,
        business_profile_names: set[str] | None,
    ) -> list[SelectAiDbProfile]:
        with self._lock:
            profiles = [
                self._enrich_select_ai_db_profile(
                    SelectAiDbProfile(
                        name=data.profile_name,
                        status=data.status,
                        attributes=dict(
                            data.engine_meta.get("profile_attributes") or data.engine_meta
                        ),
                        created_at=data.refreshed_at,
                    )
                )
                for data in self._asset_meta.values()
                if data.profile_name
                and self._is_business_select_ai_profile(data.profile_name, business_profile_names)
            ]
        return sorted(profiles, key=lambda item: item.name.upper())

    def _run_select_ai_db_profile_refresh_job(self, job_id: str) -> bool:
        if not self._profile_list_refresh_lock.acquire(blocking=False):
            return False
        try:
            current = self._load_select_ai_db_profile_refresh_job(job_id)
            if current is None or current.status not in {
                SelectAiDbProfileRefreshStatus.PENDING,
                SelectAiDbProfileRefreshStatus.RUNNING,
            }:
                return False
            job = self._save_select_ai_db_profile_refresh_job(
                current.model_copy(
                    update={
                        "status": SelectAiDbProfileRefreshStatus.RUNNING,
                        "phase": SelectAiDbProfileRefreshPhase.FETCHING,
                        "started_at": current.started_at or _utc_now(),
                        "error_code": "",
                        "error_message": "",
                    }
                )
            )
            targets = self._dedupe_select_ai_db_profile_refresh_targets(job.target_profiles)
            target_names = {target.profile_name for target in targets}
            if job.mode == SelectAiDbProfileRefreshMode.TARGETED and not target_names:
                raise SelectAiDbProfileListRefreshFullRequired(
                    "profile_list_refresh_target_unresolved"
                )

            existing = {
                self._select_ai_db_profile_key(profile.name): profile
                for profile in self._load_select_ai_db_profile_documents()
            }
            expected = {target.profile_name: target.expected_state for target in targets}
            to_upsert: dict[str, SelectAiDbProfile] = {}
            to_delete: set[str] = set()
            if self._use_oracle_runtime():
                present_names = self._oracle_adapter.fetch_select_ai_profile_names(
                    target_names if job.mode == SelectAiDbProfileRefreshMode.TARGETED else None
                )
                if job.mode == SelectAiDbProfileRefreshMode.TARGETED:
                    missing_present = {
                        name
                        for name in target_names
                        if expected.get(name) == "present" and name not in present_names
                    }
                    still_present = {
                        name
                        for name in target_names
                        if expected.get(name) == "absent" and name in present_names
                    }
                    if missing_present or still_present:
                        raise SelectAiDbProfileListRefreshFullRequired(
                            "profile_list_refresh_full_required"
                        )
                    scoped_names = target_names
                else:
                    scoped_names = present_names
                for name in sorted(scoped_names):
                    if name in present_names:
                        try:
                            to_upsert[name] = self._select_ai_db_profile_from_oracle_detail(name)
                        except OracleAdapterError as exc:
                            if job.mode != SelectAiDbProfileRefreshMode.TARGETED:
                                raise
                            raise SelectAiDbProfileListRefreshFullRequired(
                                "profile_list_refresh_full_required"
                            ) from exc
                    elif name in existing:
                        to_delete.add(name)
                if job.mode == SelectAiDbProfileRefreshMode.FULL:
                    to_delete.update(set(existing) - present_names)
            else:
                fallback = {
                    self._select_ai_db_profile_key(profile.name): profile
                    for profile in self._fallback_select_ai_db_profiles_from_asset_meta(None)
                }
                present_names = set(fallback)
                scoped_names = (
                    target_names
                    if job.mode == SelectAiDbProfileRefreshMode.TARGETED
                    else present_names
                )
                for name in sorted(scoped_names):
                    if name in fallback:
                        to_upsert[name] = fallback[name]
                    elif name in existing:
                        to_delete.add(name)
                if job.mode == SelectAiDbProfileRefreshMode.FULL:
                    to_delete.update(set(existing) - present_names)

            changed = {name for name, profile in to_upsert.items() if existing.get(name) != profile}
            deleted = {name for name in to_delete if name in existing}
            total_profiles = (
                len(target_names)
                if job.mode == SelectAiDbProfileRefreshMode.TARGETED
                else len(to_upsert)
            )
            job = self._save_select_ai_db_profile_refresh_job(
                job.model_copy(
                    update={
                        "phase": SelectAiDbProfileRefreshPhase.PERSISTING,
                        "total_profiles": total_profiles,
                        "processed_profiles": total_profiles,
                    }
                )
            )
            for name in sorted(deleted):
                self._refresh_job_repository.delete_document(
                    _SELECT_AI_DB_PROFILE_COLLECTION,
                    name,
                )
            for name in sorted(changed):
                profile = to_upsert[name]
                self._refresh_job_repository.put_document(
                    _SELECT_AI_DB_PROFILE_COLLECTION,
                    name,
                    profile.model_dump(mode="json"),
                    status=profile.status,
                )
            self._save_select_ai_db_profile_refresh_meta(mode=job.mode)
            self._save_select_ai_db_profile_refresh_job(
                job.model_copy(
                    update={
                        "status": SelectAiDbProfileRefreshStatus.DONE,
                        "phase": SelectAiDbProfileRefreshPhase.DONE,
                        "finished_at": _utc_now(),
                        "scanned_profiles": total_profiles,
                        "changed_profiles": len(changed),
                        "deleted_profiles": len(deleted),
                    }
                )
            )
            return True
        except SelectAiDbProfileListRefreshFullRequired as exc:
            current = self._load_select_ai_db_profile_refresh_job(job_id)
            if current is not None:
                self._save_select_ai_db_profile_refresh_job(
                    current.model_copy(
                        update={
                            "status": SelectAiDbProfileRefreshStatus.ERROR,
                            "phase": SelectAiDbProfileRefreshPhase.FETCHING,
                            "requires_full_refresh": True,
                            "error_code": exc.reason_code,
                            "error_message": _profile_list_refresh_required_warning(
                                exc.reason_code
                            ),
                            "finished_at": _utc_now(),
                        }
                    )
                )
            return False
        except Exception as exc:
            current = self._load_select_ai_db_profile_refresh_job(job_id)
            if current is not None:
                self._save_select_ai_db_profile_refresh_job(
                    current.model_copy(
                        update={
                            "status": SelectAiDbProfileRefreshStatus.ERROR,
                            "phase": SelectAiDbProfileRefreshPhase.FETCHING,
                            "error_code": "profile_list_refresh_failed",
                            "error_message": str(exc),
                            "finished_at": _utc_now(),
                        }
                    )
                )
            return False
        finally:
            self._profile_list_refresh_lock.release()

    def _record_select_ai_scope_state(
        self,
        *,
        profile_name: str,
        expected_scope: set[str],
        actual_scope: set[str],
        warning: str = "",
    ) -> AssetRefreshData:
        synchronized = not warning and expected_scope == actual_scope
        if not warning and not synchronized:
            warning = (
                "Oracle Profile の object_list 再読込結果が要求 scope と一致しません。"
                f" missing={sorted(expected_scope - actual_scope) or '-'}"
                f" unexpected={sorted(actual_scope - expected_scope) or '-'}"
            )
        with self._lock:
            previous = self._asset_meta.get(Nl2SqlEngine.SELECT_AI)
            previous_states = (
                previous.engine_meta.get("profile_scope_states", {}) if previous is not None else {}
            )
            profile_scope_states = {
                str(key): dict(value)
                for key, value in (
                    previous_states.items() if isinstance(previous_states, dict) else []
                )
                if isinstance(value, dict)
            }
            refreshed_at = _utc_now()
            profile_scope_states[profile_name.upper()] = {
                "refreshed": synchronized,
                "status": "ready" if synchronized else "error",
                "warning": warning,
                "refreshed_at": refreshed_at,
                "expected_object_scope": sorted(expected_scope),
                "actual_object_scope": sorted(actual_scope),
            }
            data = AssetRefreshData(
                engine=Nl2SqlEngine.SELECT_AI,
                refreshed=synchronized,
                status="ready" if synchronized else "error",
                refreshed_at=refreshed_at,
                profile_name=profile_name,
                warning=warning,
                asset_names={"profile": profile_name},
                engine_meta={
                    "expected_object_scope": sorted(expected_scope),
                    "actual_object_scope": sorted(actual_scope),
                    "profile_scope_states": profile_scope_states,
                },
            )
            self._asset_meta[Nl2SqlEngine.SELECT_AI] = data
        self._persist_singletons("asset_meta")
        return data

    def _assert_select_ai_scope_ready(self, profile: Nl2SqlProfile) -> None:
        profile_name = self._select_ai_profile_name(profile)
        scope_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI)
        if scope_meta is None:
            return
        raw_states = scope_meta.engine_meta.get("profile_scope_states", {})
        profile_state = (
            raw_states.get(profile_name.upper()) if isinstance(raw_states, dict) else None
        )
        if isinstance(profile_state, dict):
            if (
                not bool(profile_state.get("refreshed"))
                or str(profile_state.get("status") or "") != "ready"
            ):
                raise OracleAdapterError(
                    "Oracle Select AI Profile の object scope が未同期です。"
                    "Profile を再同期してから実行してください。"
                )
            return
        if scope_meta.profile_name == profile_name and (
            not scope_meta.refreshed or scope_meta.status != "ready"
        ):
            raise OracleAdapterError(
                "Oracle Select AI Profile の object scope が未同期です。"
                "Profile を再同期してから実行してください。"
            )

    def list_select_ai_feedback_entries(
        self, profile_name: str, limit: int = 50
    ) -> SelectAiFeedbackEntriesData:
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if self._use_oracle_runtime():
            try:
                data = self._oracle_adapter.list_select_ai_feedback_entries(
                    profile_name=profile_name,
                    limit=limit,
                )
                return SelectAiFeedbackEntriesData(
                    runtime="oracle",
                    profile_name=str(data.get("profile_name") or profile_name),
                    index_name=str(data.get("index_name") or ""),
                    table_name=str(data.get("table_name") or ""),
                    items=[
                        SelectAiFeedbackEntry.model_validate(item) for item in data.get("items", [])
                    ],
                    total=int(data.get("total") or 0),
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        else:
            warnings.append("Select AI feedback 管理には NL2SQL_RUNTIME_MODE=oracle が必要です。")
        return SelectAiFeedbackEntriesData(
            runtime=runtime,
            profile_name=profile_name,
            warnings=warnings,
        )

    def delete_select_ai_feedback(
        self, request: SelectAiFeedbackDeleteRequest
    ) -> SelectAiFeedbackMutationData:
        if not self._use_oracle_runtime():
            return SelectAiFeedbackMutationData(
                runtime="deterministic",
                executed=False,
                status="requires_oracle",
                profile_name=request.profile_name,
                warnings=["Select AI feedback 削除には NL2SQL_RUNTIME_MODE=oracle が必要です。"],
            )
        try:
            meta = self._oracle_adapter.delete_select_ai_feedback(
                profile_name=request.profile_name,
                sql_text=request.sql_text,
            )
            self._record_admin_audit(
                operation="select_ai_feedback_delete",
                target=str(meta.get("profile_name") or request.profile_name),
                executed=True,
                reason="ui-select-ai-feedback-delete",
                detail={"sql_text": request.sql_text},
            )
            return SelectAiFeedbackMutationData(
                runtime="oracle",
                executed=True,
                status="deleted",
                profile_name=str(meta.get("profile_name") or request.profile_name),
                index_name=str(meta.get("index_name") or ""),
                table_name=str(meta.get("table_name") or ""),
                engine_meta=meta,
            )
        except OracleAdapterError as exc:
            return SelectAiFeedbackMutationData(
                runtime="oracle",
                executed=False,
                status="error",
                profile_name=request.profile_name,
                warnings=[str(exc)],
            )

    def add_select_ai_feedback(
        self, request: SelectAiFeedbackAddRequest
    ) -> SelectAiFeedbackAddData:
        profile_name = request.profile_name.strip()
        if not profile_name:
            profile_name = self._select_ai_profile_name(self.get_profile(request.profile_id))
        sql_text = self._select_ai_feedback_showsql(request.question)
        stored_feedback_type = "NEGATIVE"
        response = (
            request.generated_sql.strip()
            if request.feedback_type == "positive" and not request.response.strip()
            else request.response.strip()
        )
        feedback_content = request.feedback_content.strip()
        plsql_preview = self._select_ai_feedback_plsql_preview(
            profile_name=profile_name,
            sql_text=sql_text,
            feedback_type=stored_feedback_type,
            response=response,
            feedback_content=feedback_content,
        )
        if not response:
            return SelectAiFeedbackAddData(
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                executed=False,
                status="validation_error",
                profile_name=profile_name,
                sql_text=sql_text,
                stored_feedback_type=stored_feedback_type,
                plsql_preview=plsql_preview,
                warnings=["feedback response が空です。生成SQLまたは修正SQLを入力してください。"],
            )
        if not self._use_oracle_runtime():
            return SelectAiFeedbackAddData(
                runtime="deterministic",
                executed=False,
                status="requires_oracle",
                profile_name=profile_name,
                sql_text=sql_text,
                stored_feedback_type=stored_feedback_type,
                plsql_preview=plsql_preview,
                warnings=["Select AI feedback 追加には NL2SQL_RUNTIME_MODE=oracle が必要です。"],
            )
        try:
            meta = self._oracle_adapter.add_select_ai_feedback(
                profile_name=profile_name,
                sql_text=sql_text,
                feedback_type=stored_feedback_type,
                response=response,
                feedback_content=feedback_content,
            )
            self._record_admin_audit(
                operation="select_ai_feedback_add",
                target=str(meta.get("profile_name") or profile_name),
                executed=True,
                reason="ui-select-ai-feedback-add",
                detail={
                    "sql_text": sql_text,
                    "feedback_type": stored_feedback_type,
                    "source_feedback_type": request.feedback_type,
                },
            )
            return SelectAiFeedbackAddData(
                runtime="oracle",
                executed=True,
                status="added",
                profile_name=str(meta.get("profile_name") or profile_name),
                index_name=str(meta.get("index_name") or ""),
                table_name=str(meta.get("table_name") or ""),
                sql_text=sql_text,
                stored_feedback_type=stored_feedback_type,
                plsql_preview=plsql_preview,
                engine_meta=meta,
            )
        except OracleAdapterError as exc:
            return SelectAiFeedbackAddData(
                runtime="oracle",
                executed=False,
                status="error",
                profile_name=profile_name,
                sql_text=sql_text,
                stored_feedback_type=stored_feedback_type,
                plsql_preview=plsql_preview,
                warnings=[str(exc)],
            )

    def update_select_ai_feedback_vector_index(
        self, request: SelectAiFeedbackVectorIndexRequest
    ) -> SelectAiFeedbackMutationData:
        if not self._use_oracle_runtime():
            return SelectAiFeedbackMutationData(
                runtime="deterministic",
                executed=False,
                status="requires_oracle",
                profile_name=request.profile_name,
                warnings=[
                    "Select AI feedback vector index 更新には "
                    "NL2SQL_RUNTIME_MODE=oracle が必要です。"
                ],
            )
        try:
            meta = self._oracle_adapter.update_select_ai_feedback_vector_index(
                profile_name=request.profile_name,
                similarity_threshold=request.similarity_threshold,
                match_limit=request.match_limit,
            )
            self._record_admin_audit(
                operation="select_ai_feedback_vector_index_update",
                target=str(meta.get("index_name") or request.profile_name),
                executed=True,
                reason="ui-select-ai-feedback-vector-index-update",
                detail={
                    "similarity_threshold": request.similarity_threshold,
                    "match_limit": request.match_limit,
                },
            )
            return SelectAiFeedbackMutationData(
                runtime="oracle",
                executed=True,
                status="updated",
                profile_name=str(meta.get("profile_name") or request.profile_name),
                index_name=str(meta.get("index_name") or ""),
                table_name=str(meta.get("table_name") or ""),
                engine_meta=meta,
            )
        except OracleAdapterError as exc:
            return SelectAiFeedbackMutationData(
                runtime="oracle",
                executed=False,
                status="error",
                profile_name=request.profile_name,
                warnings=[str(exc)],
            )

    def upsert_select_ai_db_profile(
        self, request: SelectAiDbProfileUpsertRequest
    ) -> SelectAiDbProfileMutationData:
        profile_name = request.profile_name.strip()
        original_name = request.original_name.strip()
        escaped_profile_name = profile_name.replace("'", "''")
        ddl = [
            f"BEGIN DBMS_CLOUD_AI.DROP_PROFILE(profile_name => '{escaped_profile_name}'); END;",
            "BEGIN DBMS_CLOUD_AI.CREATE_PROFILE(profile_name => :name, attributes => :attrs); END;",
        ]
        warnings: list[str] = []
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=profile_name,
        )
        if confirmation_error:
            return SelectAiDbProfileMutationData(
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
                executed=False,
                status="confirmation_required",
                profile_name=profile_name,
                original_name=original_name,
                ddl=ddl,
                warnings=[confirmation_error],
            )
        if not self._use_oracle_runtime():
            return SelectAiDbProfileMutationData(
                runtime="deterministic",
                executed=False,
                status="requires_oracle",
                profile_name=profile_name,
                original_name=original_name,
                ddl=ddl,
                warnings=[
                    "DBMS_CLOUD_AI profile の作成/更新には NL2SQL_RUNTIME_MODE=oracle が必要です。"
                ],
            )
        try:
            meta = self._oracle_adapter.upsert_select_ai_profile_low_level(
                profile_name=profile_name,
                attributes=request.attributes,
                description=request.description,
                original_name=original_name,
            )
            detail = self._enrich_select_ai_db_profile(
                SelectAiDbProfile.model_validate(
                    self._oracle_adapter.get_select_ai_profile_detail(profile_name=profile_name)
                )
            )
            expected_scope = self._select_ai_object_scope_set(request.attributes.get("object_list"))
            actual_scope = self._select_ai_object_scope_set(detail.object_list)
            if expected_scope != actual_scope:
                asset_error = self._record_select_ai_scope_state(
                    profile_name=profile_name,
                    expected_scope=expected_scope,
                    actual_scope=actual_scope,
                )
                sync = self._submit_select_ai_db_profile_list_refresh_after_mutation(
                    target_profiles=self._select_ai_db_profile_upsert_refresh_targets(
                        profile_name=profile_name,
                        original_name=original_name,
                    ),
                    source="select_ai_db_profile_upsert_scope_mismatch",
                )
                response_warnings = [asset_error.warning]
                if sync.required:
                    response_warnings.append(
                        _profile_list_refresh_required_warning(sync.reason_code)
                    )
                return SelectAiDbProfileMutationData(
                    runtime="oracle",
                    executed=False,
                    status="scope_mismatch",
                    profile_name=profile_name,
                    original_name=original_name,
                    ddl=ddl,
                    profile=detail,
                    warnings=response_warnings,
                    engine_meta={
                        **meta,
                        "expected_object_scope": sorted(expected_scope),
                        "actual_object_scope": sorted(actual_scope),
                    },
                    profile_list_refresh_job_id=sync.job_id,
                    profile_list_refresh_required=sync.required,
                    profile_list_refresh_reason_code=sync.reason_code,
                )
            self._record_select_ai_scope_state(
                profile_name=profile_name,
                expected_scope=expected_scope,
                actual_scope=actual_scope,
            )
            self._record_admin_audit(
                operation="select_ai_profile_upsert",
                target=profile_name,
                executed=True,
                reason=request.reason,
                detail={
                    "original_name": original_name,
                    "category": request.category,
                    "attributes": self._redact_select_ai_context_attributes(request.attributes),
                },
            )
            sync = self._submit_select_ai_db_profile_list_refresh_after_mutation(
                target_profiles=self._select_ai_db_profile_upsert_refresh_targets(
                    profile_name=profile_name,
                    original_name=original_name,
                ),
                source="select_ai_db_profile_upsert",
            )
            if sync.required:
                warnings.append(_profile_list_refresh_required_warning(sync.reason_code))
            return SelectAiDbProfileMutationData(
                runtime="oracle",
                executed=True,
                status="saved",
                profile_name=profile_name,
                original_name=original_name,
                ddl=ddl,
                profile=detail,
                warnings=warnings,
                engine_meta={
                    **meta,
                    "attributes": self._redact_select_ai_context_attributes(request.attributes),
                },
                profile_list_refresh_job_id=sync.job_id,
                profile_list_refresh_required=sync.required,
                profile_list_refresh_reason_code=sync.reason_code,
            )
        except OracleAdapterError as exc:
            expected_scope = self._select_ai_object_scope_set(request.attributes.get("object_list"))
            self._record_select_ai_scope_state(
                profile_name=profile_name,
                expected_scope=expected_scope,
                actual_scope=set(),
                warning=str(exc),
            )
            if isinstance(exc, SelectAiCredentialMissingError):
                raise
            return SelectAiDbProfileMutationData(
                runtime="oracle",
                executed=False,
                status="error",
                profile_name=profile_name,
                original_name=original_name,
                ddl=ddl,
                warnings=[str(exc)],
            )

    def export_select_ai_profiles_json(
        self,
        *,
        business_profiles_only: bool = False,
        include_archived_business_profiles: bool = True,
    ) -> SelectAiProfilesExportData:
        return SelectAiProfilesExportData(
            profiles=self.list_select_ai_db_profiles(
                business_profiles_only=business_profiles_only,
                include_archived_business_profiles=include_archived_business_profiles,
            ).profiles,
            exported_at=_utc_now(),
        )

    def import_select_ai_profiles_json(
        self, request: SelectAiProfilesImportRequest
    ) -> list[SelectAiDbProfileMutationData]:
        results: list[SelectAiDbProfileMutationData] = []
        for profile in request.profiles:
            results.append(
                self.upsert_select_ai_db_profile(
                    SelectAiDbProfileUpsertRequest(
                        profile_name=profile.name,
                        attributes=profile.attributes,
                        description=profile.description,
                        category=profile.category,
                        confirmation=request.confirmation,
                        reason=request.reason,
                    )
                )
            )
        return results

    def drop_select_ai_db_profile(
        self, profile_name: str, confirmation: str = "", reason: str = ""
    ) -> AssetCleanupData:
        cleaned_at = _utc_now()
        status = "error"
        warning = ""
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        sync = SelectAiDbProfileListRefreshSync()
        confirmation_error = self._admin_confirmation_error(
            confirmation=confirmation,
            target=profile_name,
        )
        if confirmation_error:
            status = "confirmation_required"
            warning = confirmation_error
        elif not self._use_oracle_runtime():
            warning = "DBMS_CLOUD_AI profile drop には NL2SQL_RUNTIME_MODE=oracle が必要です。"
        else:
            try:
                engine_meta.update(
                    self._oracle_adapter.drop_select_ai_profile(profile_name=profile_name)
                )
                status = "cleaned"
                executed = True
                self._record_admin_audit(
                    operation="select_ai_profile_drop",
                    target=profile_name,
                    executed=True,
                    reason=reason,
                    detail={},
                )
                sync = self._submit_select_ai_db_profile_list_refresh_after_mutation(
                    target_profiles=[
                        target
                        for target in [
                            self._select_ai_db_profile_target(
                                profile_name,
                                expected_state="absent",
                            )
                        ]
                        if target is not None
                    ],
                    source="select_ai_db_profile_drop",
                )
                if sync.required:
                    warning = _profile_list_refresh_required_warning(sync.reason_code)
            except OracleAdapterError as exc:
                warning = str(exc)
        return AssetCleanupData(
            engine=Nl2SqlEngine.SELECT_AI,
            executed=executed,
            status=status,
            cleaned_at=cleaned_at,
            profile_name=profile_name,
            warning=warning,
            asset_names={"profile": profile_name},
            engine_meta=engine_meta,
            profile_list_refresh_job_id=sync.job_id,
            profile_list_refresh_required=sync.required,
            profile_list_refresh_reason_code=sync.reason_code,
        )

    def run_select_ai_agent_team(self, request: AgentTeamRunRequest) -> AgentTeamRunData:
        profile = self.get_profile(request.profile_id)
        team_name = request.team_name.strip() or self._select_ai_runtime_team_name(profile)
        warnings: list[str] = []
        if self._use_oracle_runtime():
            self._assert_select_ai_scope_ready(profile)
            sql, conversation_id = self._oracle_adapter.run_select_ai_agent_team(
                team_name=team_name,
                question=request.prompt,
                tool_name=self._select_ai_agent_asset_names(profile)["tool"],
            )
            return AgentTeamRunData(
                team_name=team_name,
                prompt=request.prompt,
                generated_sql=sql,
                conversation_id=conversation_id,
                runtime="oracle",
                engine_meta={"package": "DBMS_CLOUD_AI_AGENT"},
            )
        generated = self._generate_sql(
            Nl2SqlEngine.SELECT_AI_AGENT,
            request.prompt,
            profile,
            AllowedObjects(),
            None,
            warnings,
        )
        return AgentTeamRunData(
            team_name=team_name,
            prompt=request.prompt,
            generated_sql=generated.generated_sql,
            conversation_id=str(generated.engine_meta.get("conversation_id") or ""),
            runtime="deterministic",
            warnings=warnings
            or ["Oracle runtime ではないため deterministic Agent 生成を返しました。"],
            engine_meta=generated.engine_meta,
        )

    def list_select_ai_agent_assets(self) -> SelectAiAgentAssetsData:
        with self._lock:
            meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
            profiles = self.list_profiles()
        items: list[SelectAiAgentAsset] = []
        if meta is not None:
            items.append(
                SelectAiAgentAsset(
                    profile_name=meta.profile_name,
                    tool_name=meta.asset_names.get("tool", ""),
                    agent_name=meta.asset_names.get("agent", ""),
                    task_name=meta.asset_names.get("task", ""),
                    team_name=meta.asset_names.get("team", meta.team_name),
                    source="state",
                    attributes=meta.engine_meta,
                )
            )
        for profile in profiles:
            names = self._select_ai_agent_asset_names(profile)
            if any(item.team_name == names["team"] for item in items):
                continue
            items.append(
                SelectAiAgentAsset(
                    profile_id=profile.id,
                    profile_name=self._select_ai_profile_name(profile),
                    tool_name=names["tool"],
                    agent_name=names["agent"],
                    task_name=names["task"],
                    team_name=self._select_ai_runtime_team_name(profile),
                    source="derived",
                )
            )
        return SelectAiAgentAssetsData(
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            items=items,
        )

    def run_select_ai_agent_tool(self, request: AgentToolRunRequest) -> AgentTeamRunData:
        warnings: list[str] = []
        profile = next(
            (
                candidate
                for candidate in self.list_profiles()
                if self._select_ai_agent_asset_names(candidate)["tool"].upper()
                == request.tool_name.strip().upper()
            ),
            self.get_profile(None),
        )
        if self._use_oracle_runtime():
            self._assert_select_ai_scope_ready(profile)
            sql, conversation_id = self._oracle_adapter.run_select_ai_agent_tool(
                tool_name=request.tool_name,
                question=request.prompt,
            )
            return AgentTeamRunData(
                team_name="",
                prompt=request.prompt,
                generated_sql=sql,
                conversation_id=request.conversation_id or conversation_id,
                runtime="oracle",
                engine_meta={
                    "package": "DBMS_CLOUD_AI_AGENT",
                    "tool_name": request.tool_name,
                },
            )
        generated = self._generate_sql(
            Nl2SqlEngine.SELECT_AI_AGENT,
            request.prompt,
            profile,
            AllowedObjects(),
            None,
            warnings,
        )
        return AgentTeamRunData(
            team_name="",
            prompt=request.prompt,
            generated_sql=generated.generated_sql,
            conversation_id=request.conversation_id,
            runtime="deterministic",
            warnings=warnings
            or ["Oracle runtime ではないため deterministic Agent tool 生成を返しました。"],
            engine_meta={"tool_name": request.tool_name, **generated.engine_meta},
        )

    def create_select_ai_agent_conversation(
        self, request: AgentConversationCreateRequest
    ) -> AgentConversationCreateData:
        del request
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return AgentConversationCreateData(
                    conversation_id=self._oracle_adapter.create_agent_conversation(),
                    runtime="oracle",
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        return AgentConversationCreateData(
            conversation_id=f"deterministic-{uuid.uuid4()}",
            runtime="deterministic",
            warnings=warnings
            or ["Oracle runtime ではないため deterministic conversation id を返しました。"],
        )

    def cleanup_select_ai_agent_assets_low_level(
        self, request: AssetCleanupRequest
    ) -> list[AssetCleanupData]:
        return self.cleanup_select_ai_assets(
            profile_id=request.profile_id,
            engines=[Nl2SqlEngine.SELECT_AI_AGENT],
            confirmation=request.confirmation,
            reason=request.reason,
        )

    def list_select_ai_agent_conversations(
        self, team_name: str | None = None, limit: int = 20
    ) -> AgentConversationsData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return AgentConversationsData(
                    runtime="oracle",
                    items=[
                        AgentConversationItem.model_validate(item)
                        for item in self._oracle_adapter.list_agent_conversations(
                            team_name=team_name,
                            limit=limit,
                        )
                    ],
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        return AgentConversationsData(
            runtime="deterministic",
            items=[],
            warnings=warnings
            or ["Oracle runtime ではないため conversation 履歴は取得していません。"],
        )

    def check_select_ai_agent_privileges(self) -> AgentPrivilegeCheckData:
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if not self._use_oracle_runtime():
            return AgentPrivilegeCheckData(
                runtime=runtime,
                status="warning",
                checks=[
                    DiagnosticCheck(
                        name="nl2sql_runtime_mode",
                        status="warning",
                        message=(
                            "NL2SQL_RUNTIME_MODE=oracle ではないため Oracle 権限を"
                            "確認していません。"
                        ),
                    )
                ],
                warnings=["Oracle runtime ではないため Select AI Agent 権限は未確認です。"],
            )
        try:
            checks = [
                DiagnosticCheck.model_validate(item)
                for item in self._oracle_adapter.check_select_ai_agent_privileges()
            ]
            ok = bool(checks) and all(item.status == "ok" for item in checks)
            return AgentPrivilegeCheckData(
                runtime=runtime,
                status="ok" if ok else "warning",
                checks=checks,
            )
        except OracleAdapterError as exc:
            return AgentPrivilegeCheckData(
                runtime=runtime,
                status="error",
                checks=[
                    DiagnosticCheck(
                        name="select_ai_agent_privileges",
                        status="error",
                        message=str(exc),
                    )
                ],
                warnings=[str(exc)],
            )

    def _cleanup_profile_oracle_assets_for_delete(
        self,
        profile: Nl2SqlProfile,
    ) -> list[AssetCleanupData]:
        cleaned = [
            self._cleanup_select_ai_agent_assets_for_profile(profile),
            self._cleanup_select_ai_profile_for_profile(profile),
        ]
        if any(item.executed for item in cleaned):
            self._record_admin_audit(
                operation="profile_delete_oracle_assets_cleanup",
                target=profile.id,
                executed=True,
                reason="profile_delete",
                detail={
                    "profile_id": profile.id,
                    "engines": [item.engine.value for item in cleaned],
                },
            )
            self._persist_singletons("asset_meta")
        return cleaned

    def _cleanup_select_ai_profile(self, profile_id: str | None) -> AssetCleanupData:
        return self._cleanup_select_ai_profile_for_profile(self._cleanup_profile_target(profile_id))

    def _cleanup_select_ai_profile_for_profile(self, profile: Nl2SqlProfile) -> AssetCleanupData:
        profile_name = self._select_ai_profile_name(profile)
        warning = ""
        status = "error"
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        sync = SelectAiDbProfileListRefreshSync()
        if self._use_oracle_runtime():
            try:
                engine_meta.update(
                    self._oracle_adapter.drop_select_ai_profile(profile_name=profile_name)
                )
                status = "cleaned"
                executed = True
                with self._lock:
                    self._asset_meta.pop(Nl2SqlEngine.SELECT_AI, None)
                sync = self._submit_select_ai_db_profile_list_refresh_after_mutation(
                    target_profiles=[
                        target
                        for target in [
                            self._select_ai_db_profile_target(
                                profile_name,
                                expected_state="absent",
                            )
                        ]
                        if target is not None
                    ],
                    source="select_ai_profile_cleanup",
                )
                if sync.required:
                    warning = _profile_list_refresh_required_warning(sync.reason_code)
            except OracleAdapterError as exc:
                warning = str(exc)
        else:
            status = "skipped"
            warning = "cleanup の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"
        return AssetCleanupData(
            engine=Nl2SqlEngine.SELECT_AI,
            executed=executed,
            status=status,
            cleaned_at=_utc_now(),
            profile_name=profile_name,
            warning=warning,
            asset_names={"profile": profile_name},
            engine_meta=engine_meta,
            profile_list_refresh_job_id=sync.job_id,
            profile_list_refresh_required=sync.required,
            profile_list_refresh_reason_code=sync.reason_code,
        )

    def _cleanup_select_ai_agent_assets(self, profile_id: str | None) -> AssetCleanupData:
        return self._cleanup_select_ai_agent_assets_for_profile(
            self._cleanup_profile_target(profile_id)
        )

    def _cleanup_select_ai_agent_assets_for_profile(
        self,
        profile: Nl2SqlProfile,
    ) -> AssetCleanupData:
        profile_name = self._select_ai_profile_name(profile)
        asset_names = self._select_ai_agent_asset_names(profile)
        asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        if asset_meta and asset_meta.profile_name == profile_name and asset_meta.team_name:
            asset_names["team"] = asset_meta.team_name
        warning = ""
        status = "error"
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        if self._use_oracle_runtime():
            try:
                engine_meta.update(
                    self._oracle_adapter.drop_select_ai_agent_assets(
                        profile_name=profile_name,
                        tool_name=asset_names["tool"],
                        agent_name=asset_names["agent"],
                        task_name=asset_names["task"],
                        team_name=asset_names["team"],
                    )
                )
                status = "cleaned"
                executed = True
                with self._lock:
                    self._asset_meta.pop(Nl2SqlEngine.SELECT_AI_AGENT, None)
            except OracleAdapterError as exc:
                warning = str(exc)
        else:
            status = "skipped"
            warning = "cleanup の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。"
        return AssetCleanupData(
            engine=Nl2SqlEngine.SELECT_AI_AGENT,
            executed=executed,
            status=status,
            cleaned_at=_utc_now(),
            profile_name=profile_name,
            team_name=asset_names["team"],
            warning=warning,
            asset_names={"profile": profile_name, **asset_names},
            engine_meta=engine_meta,
        )

    def _refresh_select_ai_agent_assets_with_team(
        self,
        *,
        profile: Nl2SqlProfile,
        profile_name: str,
        tool_name: str,
        agent_name: str,
        task_name: str,
        team_name: str,
    ) -> dict[str, Any]:
        return self._oracle_adapter.refresh_select_ai_agent_assets(
            profile_name=profile_name,
            tool_name=tool_name,
            agent_name=agent_name,
            task_name=task_name,
            team_name=team_name,
            allowed_tables=self.profile_allowed_object_names(profile),
            row_limit=None,
            description="",
            # service boundary で Profile sync を一度だけ実行する。ProfileSyncJob では
            # 直前の phase で完了済みなので、ここでも再利用する。
            refresh_profile=False,
        )

    def _cleanup_profile_target(self, profile_id: str | None) -> Nl2SqlProfile:
        if not profile_id:
            return self.get_profile(None)
        try:
            return self.get_profile(profile_id, include_archived=True)
        except ValueError:
            pass
        return Nl2SqlProfile(
            id=profile_id,
            name=profile_id,
            description="Cleanup target profile",
            default_row_limit=get_settings().nl2sql_default_row_limit,
        )

    def _effective_glossary(self, profile: Nl2SqlProfile) -> dict[str, str]:
        legacy = dict(self._load_legacy_learning_material(force_reload=False).glossary)
        return {**legacy, **profile.glossary}

    def _effective_sql_rules(self, profile: Nl2SqlProfile) -> list[str]:
        global_rules = list(self._load_legacy_learning_material(force_reload=False).rules)
        return self._merge_unique_strings(global_rules, profile.sql_rules)

    def _append_rules_to_question(self, question: str, profile: Nl2SqlProfile) -> str:
        rules = self._effective_sql_rules(profile)
        if not rules:
            return question
        return f"{question.rstrip()}\n\n=== Rules ===\n" + "\n\n".join(rules)

    def rewrite_question(self, question: str, profile: Nl2SqlProfile) -> str:
        base_question = question.strip()
        annotations: list[str] = []
        for term, replacement in self._effective_glossary(profile).items():
            term_text = str(term or "").strip()
            replacement_text = str(replacement or "").strip()
            if (
                term_text
                and replacement_text
                and term_text in base_question
                and replacement_text not in base_question
            ):
                annotations.append(f"{term_text}={replacement_text}")
        if not annotations:
            return base_question
        return f"{base_question}{''.join(f'（{annotation}）' for annotation in annotations)}"

    def _rewrite_question_preserving_empty_filter(
        self, question: str, profile: Nl2SqlProfile
    ) -> str:
        if _question_has_empty_filter_slot(question):
            return question.strip()
        return self.rewrite_question(question, profile)

    def _apply_empty_filter_generation_guard(
        self, question: str, analysis: AnalyzeData
    ) -> AnalyzeData:
        if not _question_has_empty_filter_slot(question) or not analysis.filters:
            return analysis
        safety = analysis.safety.model_copy(
            update={
                "is_safe": False,
                "blocked_reason": _EMPTY_FILTER_BLOCK_REASON,
                "warnings": self._merge_unique_strings(
                    analysis.safety.warnings,
                    [_EMPTY_FILTER_BLOCK_REASON],
                ),
            }
        )
        return analysis.model_copy(
            update={
                "safety": safety,
                "explanation": _EMPTY_FILTER_BLOCK_REASON,
                "executable_sql": "",
                "risk_level": "high",
                "risk_findings": self._merge_unique_strings(
                    analysis.risk_findings,
                    [_EMPTY_FILTER_BLOCK_REASON],
                ),
                "recommendations": self._merge_unique_strings(
                    [_EMPTY_FILTER_BLOCK_REASON],
                    analysis.recommendations,
                ),
            }
        )

    def _strip_code_fence(self, value: str) -> str:
        cleaned = value.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip().strip('"')

    def _learning_examples_for_generation(
        self, *, question: str, profile: Nl2SqlProfile
    ) -> list[LearningExample]:
        examples: list[LearningExample] = []
        for profile_example in profile.few_shot_examples[:3]:
            example_question = str(profile_example.get("question") or "").strip()
            sql = str(
                profile_example.get("sql") or profile_example.get("expected_sql") or ""
            ).strip()
            if example_question and sql:
                examples.append(
                    LearningExample(
                        source="profile_few_shot",
                        question=example_question,
                        sql=sql,
                    )
                )
        for history_candidate in self._similar_history_candidates(
            question=question,
            profile_id=profile.id,
            include_bad=False,
        )[:3]:
            if history_candidate.item.generated_sql.strip():
                examples.append(
                    LearningExample(
                        source="similar_history",
                        question=history_candidate.item.question,
                        sql=history_candidate.item.generated_sql,
                        history_id=history_candidate.item.id,
                        score=history_candidate.score,
                        feedback=(
                            history_candidate.item.feedback_rating.value
                            if history_candidate.item.feedback_rating
                            else None
                        ),
                        reason=history_candidate.reason,
                    )
                )
        return examples[:5]

    def _learning_example_meta(self, example: LearningExample) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": example.source,
            "question": example.question,
            "sql": example.sql,
        }
        if example.history_id:
            data["history_id"] = example.history_id
        if example.score is not None:
            data["score"] = example.score
        if example.feedback:
            data["feedback"] = example.feedback
        if example.reason:
            data["reason"] = example.reason
        return data

    def _learning_examples_context(self, examples: list[LearningExample]) -> str:
        if not examples:
            return ""
        lines = ["learning_examples:"]
        for index, example in enumerate(examples, start=1):
            lines.append(f"- example {index} source={example.source}")
            lines.append(f"  question: {example.question}")
            lines.append(f"  sql: {one_line_sql(example.sql)}")
        return "\n".join(lines)

    def _augment_question_with_learning_examples(
        self, question: str, examples: list[LearningExample]
    ) -> str:
        context = self._learning_examples_context(examples)
        if not context:
            return question
        return (
            "以下は過去の成功例です。表・列・粒度の参考にし、危険な SQL は生成しないでください。\n"
            f"{context}\n"
            "今回の質問:\n"
            f"{question}"
        )

    def _relative_confidence(self, real_scores: list[float]) -> float:
        """実マッチ由来スコアから相対信頼度（0..1）を算出する。

        分離度（2 位との差）× 証拠量（絶対的なマッチ量）の積で、独走かつ十分な
        根拠があるときだけ高くなる。実マッチが皆無なら 0.0 を返し、`+0.5`/`+0.2`
        の見かけ倒し加点が信頼度へ混入しないようにする。
        """
        positives = sorted((s for s in real_scores if s > 0.0), reverse=True)
        if not positives:
            return 0.0
        s1 = positives[0]
        s2 = positives[1] if len(positives) > 1 else 0.0
        dominance = s1 / (s1 + s2)  # 0.5(拮抗)..1.0(独走)
        strength = s1 / (s1 + _RECOMMEND_CONFIDENCE_SMOOTHING)  # 0..1(証拠量)
        return round(dominance * strength, 3)

    def _recommendation_from_profile(
        self,
        *,
        profile: Nl2SqlProfile,
        question: str,
        confidence: float,
        matched_terms: list[str],
        candidates: list[ProfileRecommendationCandidate],
    ) -> ProfileRecommendationData:
        confidence = round(confidence, 3)
        allowed_tables = self.profile_allowed_object_names(profile) or [
            table.table_name for table in self._catalog.tables
        ]
        reason_terms = "、".join(matched_terms[:4]) if matched_terms else profile.name
        return ProfileRecommendationData(
            recommended_profile_id=profile.id,
            recommended_profile_name=profile.name,
            recommended_profile_category=profile.category,
            confidence=confidence,
            reason=f"{reason_terms} に一致したため、この profile を推薦しました。",
            rewritten_question=self._rewrite_question_preserving_empty_filter(question, profile),
            recommended_allowed_objects=AllowedObjects(table_names=allowed_tables, columns={}),
            candidates=candidates,
        )

    def _score_profile_for_question(
        self, profile: Nl2SqlProfile, question: str
    ) -> tuple[float, list[str]]:
        normalized_question = question.upper()
        question_tokens = _profile_recommendation_tokens(question)
        matched_terms: list[str] = []
        score = 0.0

        def remember_match(term: str) -> None:
            if term and term not in matched_terms:
                matched_terms.append(term)

        def add_match(term: str, weight: float) -> bool:
            nonlocal score
            if not term:
                return False
            if term.upper() in normalized_question or term in question:
                score += weight
                remember_match(term)
                return True
            return False

        for term, replacement in self._effective_glossary(profile).items():
            add_match(term, 2.0)
            add_match(replacement, 1.0)
        for token in re.split(r"[\s、。・/]+", f"{profile.name} {profile.category}"):
            add_match(token.strip(), 0.6)
        for example in profile.few_shot_examples:
            example_question = str(example.get("question", "")).strip()
            if add_match(example_question, 1.2):
                continue
            overlap = sorted(question_tokens & _profile_recommendation_tokens(example_question))
            if overlap:
                score += min(1.2, 0.4 * len(overlap))
                for token in overlap[:3]:
                    remember_match(token)

        allowed_tables = {
            _normalize_identifier(table) for table in self.profile_allowed_object_names(profile)
        }
        for table in self._catalog.tables:
            if allowed_tables and table.table_name not in allowed_tables:
                continue
            add_match(table.table_name, 1.6)
            add_match(table.logical_name, 1.6)
            add_match(table.comment, 0.8)
            for column in table.columns:
                add_match(column.column_name, 0.9)
                add_match(column.logical_name, 0.9)
        return score, matched_terms

    @staticmethod
    def _similar_history_profile_scope(
        profile_id: str | None,
        allowed_profile_ids: set[str] | None,
    ) -> set[str] | None:
        requested = str(profile_id or "").strip()
        if requested:
            return {requested}
        if allowed_profile_ids is None:
            return None
        return {str(item or "").strip() for item in allowed_profile_ids if str(item or "").strip()}

    @staticmethod
    def _similar_history_item_in_profile_scope(
        item: HistoryItem,
        profile_scope: set[str] | None,
    ) -> bool:
        if profile_scope is None:
            return True
        return item.profile_id in profile_scope

    def _similar_history_pool(
        self,
        *,
        profile_scope: set[str] | None = None,
    ) -> list[HistoryItem]:
        """類似履歴 / few-shot の母集団。

        ランキングは管理者 GOOD かつ安全な履歴しか使わないため、全履歴を読んで捨てる
        代わりに DB 側で `admin_feedback_rating=good` に絞り、新しい順に上限件数までで止める
        (呼び出しは質問入力のデバウンスごと・job ごとに発生する)。
        """

        good = FeedbackRating.GOOD.value
        if profile_scope is not None and not profile_scope:
            return []
        if self._incremental_repository is None:
            with self._lock:
                items = [
                    item.model_copy(deep=True)
                    for item in reversed(self._history)
                    if item.admin_feedback_rating == FeedbackRating.GOOD
                    and self._similar_history_item_in_profile_scope(item, profile_scope)
                ]
            return items[:_SIMILAR_HISTORY_POOL_LIMIT]
        pool: list[HistoryItem] = []
        cursor = ""
        page_profile_id = (
            next(iter(profile_scope)) if profile_scope and len(profile_scope) == 1 else ""
        )
        while len(pool) < _SIMILAR_HISTORY_POOL_LIMIT:
            page, cursor, _total = self._history_page(
                cursor=cursor or None,
                limit=min(500, _SIMILAR_HISTORY_POOL_LIMIT - len(pool)),
                profile_id=page_profile_id,
                payload_filters={"admin_feedback_rating": good},
            )
            pool.extend(
                item
                for item in page
                if self._similar_history_item_in_profile_scope(item, profile_scope)
            )
            if not cursor or not page:
                break
        return pool

    def _similar_history_candidates(
        self,
        *,
        question: str,
        profile_id: str | None,
        allowed_profile_ids: set[str] | None = None,
        include_bad: bool,
    ) -> list[SimilarHistoryItem]:
        self._load_feedback_state()
        profile_scope = self._similar_history_profile_scope(profile_id, allowed_profile_ids)
        if profile_scope is not None and not profile_scope:
            return []
        history = self._similar_history_pool(profile_scope=profile_scope)
        target_objects = self._similar_history_query_target_objects(question)
        vector_ranked = self._rank_oracle_vector_history(
            question=question,
            profile_id=profile_id,
            profile_scope=profile_scope,
            history=history,
            include_bad=include_bad,
            limit=10,
            target_objects=target_objects,
        )
        deterministic_ranked = self._rank_similar_history(
            question=question,
            profile_id=profile_id,
            profile_scope=profile_scope,
            history=history,
            include_bad=include_bad,
            target_objects=target_objects,
        )
        return self._merge_similar_history_rankings(vector_ranked, deterministic_ranked)

    def _merge_similar_history_rankings(
        self,
        vector_ranked: list[SimilarHistoryItem],
        deterministic_ranked: list[SimilarHistoryItem],
    ) -> list[SimilarHistoryItem]:
        by_history_id: dict[str, SimilarHistoryItem] = {}
        for candidate in [*vector_ranked, *deterministic_ranked]:
            history_id = candidate.item.id
            current = by_history_id.get(history_id)
            if current is None or candidate.score > current.score:
                by_history_id[history_id] = candidate
        merged = list(by_history_id.values())
        merged.sort(
            key=lambda candidate: (
                candidate.score,
                candidate.item.admin_feedback_updated_at or candidate.item.created_at,
            ),
            reverse=True,
        )
        return merged

    def _similar_history_query_target_objects(self, question: str) -> set[str]:
        """構造化質問の対象テーブルを catalog 上の canonical object 名へ解決する。"""

        target_objects: set[str] = set()
        for raw_value in _structured_question_target_table_values(question):
            for value in self._similar_history_target_value_candidates(raw_value):
                target_objects.update(self._resolve_similar_history_target_object(value))
        return target_objects

    def _similar_history_target_value_candidates(self, value: str) -> list[str]:
        cleaned = unicodedata.normalize("NFKC", str(value or "")).strip()
        cleaned = re.sub(r"^\s*[-*・]\s*", "", cleaned).strip()
        if not cleaned:
            return []
        candidates = [cleaned]
        parts = [part.strip() for part in re.split(r"[,、;；/／]+", cleaned) if part.strip()]
        if len(parts) > 1:
            candidates.extend(parts)
        seen: set[str] = set()
        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            unique_candidates.append(candidate)
        return unique_candidates

    def _resolve_similar_history_target_object(self, value: str) -> set[str]:
        cleaned = unicodedata.normalize("NFKC", str(value or "")).strip()
        cleaned = cleaned.strip("\"'`“”‘’「」『』")
        if not cleaned:
            return set()
        requested_key = self._similar_history_target_match_key(cleaned)
        matches: set[str] = set()
        for table in self._catalog.tables:
            qualified = self._catalog_qualified_name(table)
            match_keys = {
                self._similar_history_target_match_key(qualified),
                self._similar_history_target_match_key(table.table_name),
                self._similar_history_target_match_key(table.logical_name),
                self._similar_history_target_match_key(table.comment),
            }
            if requested_key in match_keys:
                matches.add(qualified)
        if matches:
            return matches
        try:
            return {self._resolve_profile_object_name(cleaned)}
        except ValueError:
            return set()

    def _similar_history_target_match_key(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
        return re.sub(r"[\s　\"'`“”‘’「」『』（）()\[\]【】]+", "", normalized)

    def _similar_history_item_matches_targets(
        self,
        item: HistoryItem,
        target_objects: set[str],
    ) -> bool:
        if not target_objects:
            return True
        return bool(self._similar_history_item_target_objects(item) & target_objects)

    def _similar_history_item_target_objects(self, item: HistoryItem) -> set[str]:
        sql = (item.executable_sql or item.generated_sql).strip()
        if not sql:
            return set()
        current_owner = self._current_schema_owner()
        targets: set[str] = set()
        for reference in _extract_referenced_tables(sql, current_owner=current_owner):
            try:
                targets.add(_scope_object_name(reference, current_owner=current_owner))
            except ValueError:
                continue
        return targets

    def _rank_oracle_vector_history(
        self,
        *,
        question: str,
        profile_id: str | None,
        profile_scope: set[str] | None,
        history: list[HistoryItem],
        include_bad: bool,
        limit: int,
        target_objects: set[str],
    ) -> list[SimilarHistoryItem]:
        if not history:
            return []
        settings = get_settings()
        if (
            not self._use_oracle_runtime()
            or not settings.nl2sql_feedback_embedding_enabled
            or not self._embedding_client.is_configured()
        ):
            return []
        try:
            embedding = self._embedding_client.embed_texts([question])[0]
            rows = self._oracle_adapter.search_feedback_vector_index(
                table_name=settings.nl2sql_feedback_vector_table,
                embedding=embedding,
                profile_id=profile_id,
                profile_ids=profile_scope,
                include_bad=include_bad,
                limit=limit,
            )
        except (EmbeddingClientError, OracleAdapterError, IndexError, ValueError) as exc:
            logger.warning("oracle feedback vector search fallback: %s", exc)
            return []
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            logger.warning("oracle feedback vector search fallback: %s", exc)
            return []
        history_by_id = {item.id: item for item in history}
        ranked: list[SimilarHistoryItem] = []
        for row in rows:
            history_id = str(row.get("history_id") or "")
            if not history_id:
                continue
            item = history_by_id.get(history_id)
            if item is None:
                continue
            # 管理者が GOOD にした履歴だけを検索・few-shot 対象にする。
            if item.admin_feedback_rating != FeedbackRating.GOOD:
                continue
            if not self._similar_history_item_in_profile_scope(item, profile_scope):
                continue
            if not item.safety_is_safe:
                continue
            if not self._similar_history_item_matches_targets(item, target_objects):
                continue
            score = float(row.get("score") or 0)
            ranked.append(
                SimilarHistoryItem(
                    item=item,
                    score=round(max(0.0, min(score, 1.0)), 3),
                    reason="Oracle 26ai vector search で質問意味が近い履歴です。",
                )
            )
        return ranked

    def _feedback_rating_from_text(self, value: str) -> FeedbackRating | None:
        normalized = value.strip().lower()
        try:
            return FeedbackRating(normalized) if normalized else None
        except ValueError:
            return None

    def _rank_similar_history(
        self,
        *,
        question: str,
        profile_id: str | None,
        profile_scope: set[str] | None,
        history: list[HistoryItem],
        include_bad: bool,
        target_objects: set[str],
    ) -> list[SimilarHistoryItem]:
        del include_bad
        query_tokens = _similarity_tokens(question)
        if not query_tokens:
            return []
        scored: list[SimilarHistoryItem] = []
        for item in history:
            # 管理者が GOOD にした履歴だけを検索・few-shot 対象にする。
            if item.admin_feedback_rating != FeedbackRating.GOOD:
                continue
            if not self._similar_history_item_in_profile_scope(item, profile_scope):
                continue
            if not item.safety_is_safe:
                continue
            if not self._similar_history_item_matches_targets(item, target_objects):
                continue
            item_tokens = _similarity_tokens(
                " ".join(
                    [
                        item.question,
                        item.rewritten_question,
                        item.generated_sql,
                        item.profile_name,
                        " ".join(item.result_columns),
                    ]
                )
            )
            overlap = sorted(query_tokens & item_tokens)
            if not overlap:
                continue
            query_overlap = len(overlap) / max(len(query_tokens), 1)
            item_overlap = len(overlap) / max(len(item_tokens), 1)
            base_score = (query_overlap * 0.75) + (item_overlap * 0.25)
            score = round(min(base_score, 1.0), 3)
            visible_terms = self._visible_similarity_terms(question, item, overlap)
            reason_terms = "、".join(visible_terms[:4] or overlap[:4])
            target_reason = "対象テーブルが一致し、" if target_objects else ""
            reason = (
                f"{target_reason}{reason_terms} が一致し、管理者の良い feedback が付いています。"
            )
            scored.append(SimilarHistoryItem(item=item, score=score, reason=reason))
        scored.sort(
            key=lambda candidate: (
                candidate.score,
                bool(profile_id and candidate.item.profile_id == profile_id),
                candidate.item.admin_feedback_rating == FeedbackRating.GOOD,
                candidate.item.created_at,
            ),
            reverse=True,
        )
        return scored

    def _visible_similarity_terms(
        self, question: str, item: HistoryItem, overlap: list[str]
    ) -> list[str]:
        compared = f"{item.question} {item.rewritten_question} {item.generated_sql}".upper()
        candidates: list[str] = []
        for profile in self.list_profiles():
            glossary = self._effective_glossary(profile)
            candidates.extend(glossary.keys())
            candidates.extend(glossary.values())
        for table in self._catalog.tables:
            candidates.extend([table.logical_name, table.table_name])
            if table.table_name in compared:
                compared = f"{compared} {table.logical_name}"
            candidates.extend(column.logical_name for column in table.columns)
            candidates.extend(column.column_name for column in table.columns)
            for column in table.columns:
                if column.column_name in compared:
                    compared = f"{compared} {column.logical_name}"

        visible: list[str] = []
        for term in sorted(set(candidates), key=lambda value: (-len(value), value)):
            if not term:
                continue
            normalized = term.upper()
            if (term in question or normalized in question.upper()) and normalized in compared:
                visible.append(term)
            if len(visible) >= 4:
                return visible

        return [
            token
            for token in sorted(overlap, key=lambda value: (-len(value), value))
            if len(token) >= 2 and re.search(r"[A-Z0-9_\u4e00-\u9fff]", token)
        ]

    def _run_job_safely(self, job_id: str) -> None:
        try:
            with self._lock:
                pending = self._jobs.get(job_id)
                if pending is None:
                    logger.warning("nl2sql_job_missing_before_run", extra={"job_id": job_id})
                    return
                actor_user_uuid = pending.actor_user_uuid
                actor_is_system_admin = pending.actor_is_system_admin
            with actor_scope(actor_user_uuid, is_system_admin=actor_is_system_admin):
                self._run_job(job_id)
        except Exception as exc:  # pragma: no cover - defensive boundary
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    # 例外ハンドラ内で KeyError を起こすと worker スレッドごと落ちる。
                    logger.exception(
                        "nl2sql_job_failed_without_record",
                        extra={"job_id": job_id, "exception_type": type(exc).__name__},
                    )
                    return
                job.status = JobStatus.ERROR
                if isinstance(exc, JobCancelledError):
                    job.error_message = str(exc)
                    job.error_code = JOB_CANCELLED_ERROR_CODE
                else:
                    job.error_message = f"NL2SQL ジョブに失敗しました: {exc}"
                    job.error_code = (
                        SCHEMA_CATALOG_EMPTY_ERROR_CODE
                        if isinstance(exc, SchemaCatalogEmptyError)
                        else None
                    )
                job.finished_at = _utc_now()
                failure_index = _job_failure_step_index(job.steps)
                failure_stage = job.steps[failure_index].stage if failure_index is not None else ""
                if failure_index is not None:
                    job.steps[failure_index] = job.steps[failure_index].model_copy(
                        update={"status": JobStepStatus.ERROR}
                    )
                request = job.request
            logger.exception(
                "nl2sql_job_failed",
                extra={
                    "job_id": job_id,
                    "failure_stage": failure_stage,
                    "engine": request.engine.value,
                    "profile_id": request.profile_id or "",
                    "exception_type": type(exc).__name__,
                },
            )
            try:
                self._persist_job(job_id)
            except Exception as persist_exc:  # pragma: no cover - defensive log boundary
                logger.exception(
                    "nl2sql_job_error_state_persist_failed",
                    extra={
                        "job_id": job_id,
                        "failure_stage": failure_stage,
                        "engine": request.engine.value,
                        "profile_id": request.profile_id or "",
                        "exception_type": type(persist_exc).__name__,
                    },
                )

    def _build_interpretation_artifact(
        self,
        *,
        request: JobCreateRequest,
        profile: Nl2SqlProfile,
        rewritten_question: str,
        generated_sql: str,
        executable_sql: str,
        analysis: AnalyzeData,
        safety: SafetyReport,
        row_limit: int | None,
        ontology_graph: Nl2SqlOntologyGraphSnapshot | None = None,
        ontology_graph_warnings: list[str] | None = None,
        include_logical_steps: bool = True,
        ontology_grounding_enabled: bool = True,
    ) -> Nl2SqlInterpretationArtifact:
        try:
            sql_for_analysis = executable_sql or generated_sql
            semantic = parse_oracle_sql(sql_for_analysis)
            graph = semantic.graph
            graph_dump = graph.model_dump(mode="json") if graph is not None else {}
            graph_warnings = list(graph.parse_warnings) if graph is not None else []
            warnings = [
                *analysis.llm_warnings,
                *analysis.safety.warnings,
                *graph_warnings,
                *(ontology_graph_warnings or []),
            ]
            question_filters = _structured_question_filter_values(request.question)
            sql_summary = analysis.structure_summary or analysis.explanation
            sql_limit = (
                graph.limit if graph is not None else self._sql_fetch_limit(sql_for_analysis)
            )
            logical_steps: list[str] = []
            logical_step_details: list[Nl2SqlLogicalStep] = []
            if include_logical_steps:
                # 「処理手順を表示」用の決定論 steps。SQL に無い行数上限は手順へ含めない。
                step_structure = {
                    "summary": sql_summary,
                    "filters": analysis.filters or analysis.conditions,
                    "joins": analysis.joins,
                    "aggregations": analysis.aggregations,
                    "group_by": analysis.group_by,
                    "order_by": analysis.order_by,
                }
                logical_steps = [
                    self._apply_reverse_glossary(step, profile=profile, enabled=True)
                    for step in self._logical_steps_from_structure(
                        step_structure,
                        limit=sql_limit,
                    )
                ]
                # 業務者向け併記は表示専用。カタログが引けなくても手順自体は落とさない。
                referenced_tables = analysis.object_names or safety.referenced_tables
                step_catalog = self._reverse_sql_catalog(profile, list(referenced_tables))
                table_labels = self._reverse_table_labels(
                    list(referenced_tables),
                    profile=profile,
                    catalog=step_catalog,
                    use_glossary=True,
                )
                table_label, column_label = self._business_label_resolvers(
                    profile=profile,
                    catalog=step_catalog,
                    referenced=list(referenced_tables),
                    use_glossary=True,
                )
                logical_step_details = self._apply_glossary_to_steps(
                    build_logical_steps(
                        step_structure,
                        limit=sql_limit,
                        table_labels=table_labels,
                        table_label=table_label,
                        column_label=column_label,
                    ),
                    profile=profile,
                    enabled=True,
                )
            sql_interpretation = Nl2SqlSqlInterpretation(
                available=bool(sql_summary or graph_dump),
                source="sql_semantics",
                summary=sql_summary,
                statement_type=analysis.statement_type,
                tables=analysis.object_names or safety.referenced_tables,
                columns=analysis.column_names or safety.referenced_columns,
                joins=analysis.joins,
                filters=analysis.filters or analysis.conditions,
                aggregations=analysis.aggregations,
                group_by=analysis.group_by,
                order_by=analysis.order_by,
                limit=graph.limit if graph is not None else (row_limit or None),
                logical_steps=logical_steps,
                logical_step_details=logical_step_details,
                semantic_graph=graph_dump,
                warnings=warnings,
            )
            question_interpretation = Nl2SqlQuestionInterpretation(
                available=True,
                source="deterministic",
                original_question=request.question,
                rewritten_question=rewritten_question,
                profile_id=profile.id,
                profile_name=profile.name,
                profile_category=profile.category,
                target_objects=analysis.object_names or safety.referenced_tables,
                filters=question_filters,
                group_by=analysis.group_by,
                order_by=analysis.order_by,
                aggregations=analysis.aggregations,
                row_limit=safety.row_limit_applied or row_limit,
                confidence=0.9 if safety.is_safe else 0.4,
                warnings=warnings,
            )
            return Nl2SqlInterpretationArtifact(
                available=question_interpretation.available or sql_interpretation.available,
                question=question_interpretation,
                sql=sql_interpretation,
                ontology_graph=ontology_graph,
                ontology_grounding_enabled=ontology_grounding_enabled,
                warnings=warnings,
            )
        except Exception as exc:  # pragma: no cover - artifact must never fail the job
            logger.warning("nl2sql_interpretation_artifact_failed", exc_info=True)
            return Nl2SqlInterpretationArtifact(
                available=False,
                warnings=[f"解釈 artifact の生成に失敗しました: {exc}"],
            )

    def _build_interpretation_ontology_graph_snapshot(
        self,
        *,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
    ) -> tuple[Nl2SqlOntologyGraphSnapshot | None, list[str]]:
        try:
            # ontology_router imports nl2sql_service at module load time, so keep this lazy.
            from app.features.nl2sql.ontology_router import ontology_runtime

            snapshot = ontology_runtime.profile_scoped_graph_snapshot_for_job(
                profile=profile,
                allowed=allowed,
            )
            return Nl2SqlOntologyGraphSnapshot.model_validate(snapshot), []
        except Exception as exc:  # pragma: no cover - artifact must never fail the job
            logger.info(
                "nl2sql_interpretation_ontology_graph_unavailable",
                exc_info=True,
                extra={"profile_id": profile.id},
            )
            return None, [f"Ontology グラフ artifact の生成に失敗しました: {exc}"]

    def _build_show_prompt_artifact(
        self,
        *,
        request: JobCreateRequest,
        profile: Nl2SqlProfile,
        generated: GeneratedSql,
        rewritten_question: str,
        ontology_context: Any | None = None,
    ) -> Nl2SqlShowPromptArtifact:
        if generated.engine != Nl2SqlEngine.SELECT_AI:
            return Nl2SqlShowPromptArtifact(
                available=False,
                engine=generated.engine,
                unavailable_reason="Show Prompt は Select AI 実行時のみ利用できます。",
            )
        if generated.engine_meta.get("runtime") != "oracle" or not self._use_oracle_runtime():
            return Nl2SqlShowPromptArtifact(
                available=False,
                engine=generated.engine,
                unavailable_reason=(
                    "Show Prompt は Oracle Select AI runtime で生成された場合のみ表示できます。"
                ),
            )
        try:
            effective_overrides = self._select_ai_overrides_with_ontology_context(
                request.select_ai_overrides,
                ontology_context,
            )
            attributes = self._select_ai_generate_attributes(profile, effective_overrides)
            prompt = self._oracle_adapter.generate_select_ai_prompt(
                profile_name=str(
                    generated.engine_meta.get("select_ai_profile")
                    or self._select_ai_profile_name(profile)
                ),
                question=_question_with_empty_filter_guard(rewritten_question),
                attributes=attributes,
            )
            if not prompt.strip():
                return Nl2SqlShowPromptArtifact(
                    available=False,
                    engine=generated.engine,
                    unavailable_reason="Oracle Select AI から Show Prompt が返りませんでした。",
                )
            return Nl2SqlShowPromptArtifact(
                available=True,
                engine=generated.engine,
                prompt=prompt,
            )
        except Exception as exc:  # pragma: no cover - artifact must never fail the job
            logger.warning("nl2sql_showprompt_artifact_failed", exc_info=True)
            return Nl2SqlShowPromptArtifact(
                available=False,
                engine=generated.engine,
                unavailable_reason="Show Prompt の取得に失敗しました。",
                warnings=[str(exc)],
            )

    def _job_ontology_context(
        self,
        *,
        request: JobCreateRequest,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
    ) -> Any | None:
        if not request.use_ontology_context:
            return None
        try:
            # ontology_router imports nl2sql_service at module load time, so keep this lazy.
            from app.features.nl2sql.ontology_router import ontology_runtime

            return ontology_runtime.compile_generation_context_for_job(
                question=question,
                profile=profile,
                allowed=allowed,
                row_limit=row_limit,
                engine=request.engine,
            )
        except Exception:
            logger.info(
                "nl2sql_job_ontology_context_unavailable",
                exc_info=True,
                extra={
                    "profile_id": profile.id,
                    "engine": request.engine.value,
                },
            )
            return None

    def _run_job(self, job_id: str) -> None:
        total_started = time.monotonic()
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.RUNNING
            job.started_at = _utc_now()
            job.timing = TimingEnvelope(created_at=job.created_at, started_at=job.started_at)
            job.steps[0] = job.steps[0].model_copy(update={"status": JobStepStatus.RUNNING})
            request = job.request
        self._persist_job(job_id)

        stage_timings: list[StageTiming] = []
        profile = self.get_profile(request.profile_id)

        self._raise_if_job_cancelled(job_id)
        stage_started = time.monotonic()
        rewritten = self._rewrite_question_preserving_empty_filter(request.question, profile)
        allowed = self._resolve_allowed_objects(request.profile_id, request.allowed_objects)
        row_limit = self._resolve_row_limit(request.profile_id, request.row_limit)
        ontology_context = self._job_ontology_context(
            request=request,
            question=rewritten,
            profile=profile,
            allowed=allowed,
            row_limit=row_limit,
        )
        stage_elapsed = _elapsed_ms(stage_started)
        stage_timings.append(StageTiming(stage="prepare_context", elapsed_ms=stage_elapsed))
        self._transition_job_steps(
            job_id,
            completed_stage="prepare_context",
            elapsed_ms=stage_elapsed,
            running_stage="generate_sql",
        )

        self._raise_if_job_cancelled(job_id)
        stage_started = time.monotonic()
        generated = self._generate_with_fallback(
            question=rewritten,
            engine=request.engine,
            profile=profile,
            allowed=allowed,
            row_limit=row_limit,
            select_ai_overrides=request.select_ai_overrides,
            ontology_context=ontology_context,
        )
        stage_elapsed = _elapsed_ms(stage_started)
        stage_timings.append(StageTiming(stage="generate_sql", elapsed_ms=stage_elapsed))
        self._transition_job_steps(
            job_id,
            completed_stage="generate_sql",
            elapsed_ms=stage_elapsed,
            running_stage="safety_check",
        )

        self._raise_if_job_cancelled(job_id)
        stage_started = time.monotonic()
        analysis = self.analyze_sql(
            generated.generated_sql,
            allowed,
            row_limit,
            catalog=generated.schema_catalog,
        )
        analysis = self._apply_empty_filter_generation_guard(rewritten, analysis)
        stage_elapsed = _elapsed_ms(stage_started)
        stage_timings.append(StageTiming(stage="safety_check", elapsed_ms=stage_elapsed))
        self._transition_job_steps(
            job_id,
            completed_stage="safety_check",
            completed_status=(
                JobStepStatus.DONE if analysis.safety.is_safe else JobStepStatus.ERROR
            ),
            elapsed_ms=stage_elapsed,
            running_stage="execute_sql" if analysis.safety.is_safe else None,
        )

        self._raise_if_job_cancelled(job_id)
        stage_started = time.monotonic()
        if analysis.safety.is_safe:
            safety, executable, results = self.execute_sql(
                generated.generated_sql, allowed, row_limit, analysis=analysis
            )
        else:
            safety = analysis.safety
            executable = analysis.executable_sql
            results = QueryResults(columns=[], rows=[], total=0)
        stage_elapsed = _elapsed_ms(stage_started)
        stage_timings.append(StageTiming(stage="execute_sql", elapsed_ms=stage_elapsed))
        self._transition_job_steps(
            job_id,
            completed_stage="execute_sql",
            completed_status=(
                JobStepStatus.DONE
                if safety.is_safe
                else (JobStepStatus.SKIPPED if not analysis.safety.is_safe else JobStepStatus.ERROR)
            ),
            elapsed_ms=stage_elapsed,
            running_stage="format_results",
        )

        self._raise_if_job_cancelled(job_id)
        stage_started = time.monotonic()
        timing = TimingEnvelope(
            created_at=job.created_at,
            started_at=job.started_at,
            stage_timings=stage_timings,
        )
        interpretation: Nl2SqlInterpretationArtifact | None = None
        # include_interpretation は処理手順、use_ontology_context は Ontology 接地確認を担う。
        # どちらかが要る場合に artifact を構築し、不要な部分は空にする。
        if request.include_interpretation or request.use_ontology_context:
            try:
                ontology_graph: Nl2SqlOntologyGraphSnapshot | None = None
                ontology_graph_warnings: list[str] = []
                if request.use_ontology_context:
                    ontology_graph, ontology_graph_warnings = (
                        self._build_interpretation_ontology_graph_snapshot(
                            profile=profile,
                            allowed=allowed,
                        )
                    )
                interpretation = self._build_interpretation_artifact(
                    request=request,
                    profile=profile,
                    rewritten_question=rewritten,
                    generated_sql=generated.generated_sql,
                    executable_sql=executable,
                    analysis=analysis,
                    safety=safety,
                    row_limit=row_limit,
                    ontology_graph=ontology_graph,
                    ontology_graph_warnings=ontology_graph_warnings,
                    include_logical_steps=request.include_interpretation,
                    ontology_grounding_enabled=request.use_ontology_context,
                )
            except Exception as exc:  # pragma: no cover - defensive artifact boundary
                logger.warning("nl2sql_interpretation_artifact_boundary_failed", exc_info=True)
                interpretation = Nl2SqlInterpretationArtifact(
                    available=False,
                    warnings=[f"解釈 artifact の生成に失敗しました: {exc}"],
                )
        show_prompt: Nl2SqlShowPromptArtifact | None = None
        if request.include_show_prompt:
            try:
                show_prompt = self._build_show_prompt_artifact(
                    request=request,
                    profile=profile,
                    generated=generated,
                    rewritten_question=rewritten,
                    ontology_context=ontology_context,
                )
            except Exception as exc:  # pragma: no cover - defensive artifact boundary
                logger.warning("nl2sql_showprompt_artifact_boundary_failed", exc_info=True)
                show_prompt = Nl2SqlShowPromptArtifact(
                    available=False,
                    engine=generated.engine,
                    unavailable_reason="Show Prompt の取得に失敗しました。",
                    warnings=[str(exc)],
                )
        history_id = str(uuid.uuid4())
        result = Nl2SqlResult(
            history_id=history_id,
            engine=generated.engine,
            engine_meta=generated.engine_meta,
            fallback_reason=generated.fallback_reason,
            original_question=request.question,
            rewritten_question=rewritten,
            generated_sql=generated.generated_sql,
            executable_sql=executable,
            explanation=generated.explanation,
            safety=safety,
            recommendations=analysis.recommendations,
            repaired_sql=analysis.repaired_sql,
            optimization_hints=analysis.optimization_hints,
            results=results,
            timing=timing,
            interpretation=interpretation,
            show_prompt=show_prompt,
        )
        stage_elapsed = _elapsed_ms(stage_started)
        stage_timings.append(StageTiming(stage="format_results", elapsed_ms=stage_elapsed))
        finished = _utc_now()
        timing = timing.model_copy(
            update={
                "finished_at": finished,
                "elapsed_ms": _elapsed_ms(total_started),
                "stage_timings": stage_timings,
            }
        )
        result = result.model_copy(update={"timing": timing})
        with self._lock:
            job = self._jobs[job_id]
            final_steps = [
                (
                    step.model_copy(
                        update={"status": JobStepStatus.DONE, "elapsed_ms": stage_elapsed}
                    )
                    if step.stage == "format_results"
                    else step
                )
                for step in job.steps
            ]
            actor_user_uuid = job.actor_user_uuid
        final_status = JobStatus.DONE if safety.is_safe else JobStatus.ERROR
        final_error_message = None if safety.is_safe else safety.blocked_reason
        history_item = HistoryItem(
            id=history_id,
            question=request.question,
            engine=result.engine,
            generated_sql=result.generated_sql,
            created_at=finished,
            elapsed_ms=timing.elapsed_ms,
            profile_id=profile.id,
            profile_name=profile.name,
            profile_category=profile.category,
            rewritten_question=rewritten,
            executable_sql=result.executable_sql,
            safety_is_safe=result.safety.is_safe,
            result_row_count=result.results.total,
            result_columns=result.results.columns,
            actor_user_uuid=actor_user_uuid,
        )
        # terminal 状態を公開する前に job snapshot と履歴を永続化する。先に公開すると、
        # ポーリングが DONE を見た直後の履歴取得(UI の履歴更新 / 他 worker)に新しい履歴が
        # まだ無い取りこぼしが起きる。永続化失敗は結果を捨てず warning として公開する。
        published = replace(
            job,
            steps=final_steps,
            status=final_status,
            error_message=final_error_message,
            warning_message=None,
            result=result,
            finished_at=finished,
            elapsed_ms=timing.elapsed_ms,
            timing=timing,
        )
        persistence_warning: str | None = None
        try:
            self._persist_entities(
                [
                    ("jobs", job_id, self._job_to_snapshot(published)),
                    ("history", history_item.id, history_item.model_dump(mode="json")),
                ]
            )
        except (Nl2SqlPersistenceUnavailable, Nl2SqlRepositoryOperationFailed) as exc:
            persistence_warning = _JOB_RESULT_PERSISTENCE_WARNING
            logger.exception(
                "nl2sql_job_result_persist_failed",
                extra={
                    "job_id": job_id,
                    "engine": request.engine.value,
                    "profile_id": request.profile_id or "",
                    "exception_type": type(exc).__name__,
                },
            )
        with self._lock:
            job = self._jobs[job_id]
            job.steps = final_steps
            job.status = final_status
            job.error_message = final_error_message
            job.warning_message = persistence_warning
            job.result = result
            job.finished_at = finished
            job.elapsed_ms = timing.elapsed_ms
            job.timing = timing
            self._history.append(history_item)
            self._prune_history_locked()

    def _generate_with_fallback(
        self,
        question: str,
        engine: Nl2SqlEngine,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
        select_ai_overrides: SelectAiRequestOverrides | None = None,
        ontology_context: Any | None = None,
    ) -> GeneratedSql:
        if (
            self._incremental_repository is None
            and not self._use_oracle_runtime()
            and not self._catalog.tables
        ):
            raise SchemaCatalogEmptyError(_SCHEMA_EMPTY_MESSAGE)
        candidates = (
            [
                Nl2SqlEngine.SELECT_AI_AGENT,
                Nl2SqlEngine.SELECT_AI,
                Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            ]
            if engine == Nl2SqlEngine.AUTO
            else [engine]
        )
        fallback_messages: list[str] = []
        for candidate in candidates:
            allow_deterministic_fallback = not (
                engine == Nl2SqlEngine.SELECT_AI_AGENT and candidate == Nl2SqlEngine.SELECT_AI_AGENT
            )
            try:
                return self._generate_sql(
                    candidate,
                    question,
                    profile,
                    allowed,
                    row_limit,
                    fallback_messages,
                    select_ai_overrides,
                    ontology_context,
                    allow_deterministic_fallback=allow_deterministic_fallback,
                )
            except RuntimeError as exc:
                fallback_messages.append(f"{candidate.value}: {exc}")
                if engine != Nl2SqlEngine.AUTO:
                    raise RuntimeError(str(exc)) from exc
        raise RuntimeError("すべての NL2SQL エンジンが失敗しました。")

    def _generate_sql(
        self,
        engine: Nl2SqlEngine,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
        fallback_messages: list[str],
        select_ai_overrides: SelectAiRequestOverrides | None = None,
        ontology_context: Any | None = None,
        *,
        allow_deterministic_fallback: bool = True,
        runtime_timeout_seconds: float | None = None,
        runtime_max_retries: int | None = None,
    ) -> GeneratedSql:
        # テスト/デモ用の明示的 failure trigger。deterministic runtime 限定で有効化し、
        # 本番(oracle runtime)ではユーザ入力に反応させない。
        if not self._use_oracle_runtime() and f"{engine.value}_fail" in question.lower():
            raise RuntimeError("明示的な fallback テスト要求")
        # 本番(oracle runtime)では、エンジン失敗時に質問を無視したテンプレート SQL を
        # 「生成結果」として返さない(deterministic fallback は local/CI デモ専用)。
        # これにより auto も失敗した候補から次のエンジンへ進める。
        allow_deterministic_fallback = (
            allow_deterministic_fallback and not self._use_oracle_runtime()
        )
        is_select_ai_engine = engine in {Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT}
        effective_question = (
            question.strip()
            if is_select_ai_engine
            else self._rewrite_question_preserving_empty_filter(question, profile)
        )
        runtime_question = _question_with_empty_filter_guard(effective_question)
        meta: dict[str, Any] = {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "row_limit": row_limit or 0,
            "allowed_tables": allowed.table_names or self.profile_allowed_object_names(profile),
        }
        learning_examples = (
            []
            if is_select_ai_engine
            else self._learning_examples_for_generation(
                question=effective_question,
                profile=profile,
            )
        )
        history_examples = [
            example for example in learning_examples if example.source == "similar_history"
        ]
        if learning_examples:
            meta["learning_example_count"] = len(learning_examples)
            meta["learning_examples"] = [
                self._learning_example_meta(example) for example in learning_examples
            ]
        if history_examples:
            meta["similar_history_source"] = (
                "oracle_vector"
                if history_examples[0].reason.startswith("Oracle 26ai")
                else "deterministic"
            )
            meta["similar_history_examples"] = [
                {
                    "question": example.question,
                    "sql": example.sql,
                    "history_id": example.history_id,
                    "score": example.score,
                    "feedback": example.feedback,
                }
                for example in history_examples
            ]
        if ontology_context is not None:
            meta.update(
                {
                    "ontology_context_hash": getattr(ontology_context, "context_hash", ""),
                    "ontology_context_applied": True,
                    "ontology_context_instruction_length": len(
                        self._ontology_generation_context_prompt(ontology_context)
                    ),
                }
            )
        if self._use_oracle_runtime() and engine in {
            Nl2SqlEngine.SELECT_AI,
            Nl2SqlEngine.SELECT_AI_AGENT,
        }:
            try:
                return self._generate_oracle_sql(
                    engine=engine,
                    question=runtime_question,
                    profile=profile,
                    fallback_messages=fallback_messages,
                    meta=dict(meta),
                    learning_examples=learning_examples,
                    select_ai_overrides=select_ai_overrides,
                    ontology_context=ontology_context,
                    runtime_timeout_seconds=runtime_timeout_seconds,
                )
            except OracleAdapterError as exc:
                fallback_messages.append(f"{engine.value}: {exc}")
                if not allow_deterministic_fallback:
                    raise RuntimeError(str(exc)) from exc
        elif engine in {Nl2SqlEngine.SELECT_AI, Nl2SqlEngine.SELECT_AI_AGENT} and not (
            allow_deterministic_fallback
        ):
            raise RuntimeError("Oracle runtime が構成されていません。")
        generation_catalog = self._generation_schema_catalog(profile, allowed)
        table = self._choose_table(effective_question, profile, allowed, generation_catalog)
        columns = self._choose_columns(table, allowed)
        direct_configured = self._enterprise_ai_client.is_configured()
        if engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT and direct_configured:
            try:
                return self._generate_enterprise_ai_direct_sql(
                    question=runtime_question,
                    profile=profile,
                    allowed=allowed,
                    row_limit=row_limit,
                    fallback_messages=fallback_messages,
                    meta=dict(meta),
                    learning_examples=learning_examples,
                    ontology_context=ontology_context,
                    catalog=generation_catalog,
                    runtime_timeout_seconds=runtime_timeout_seconds,
                    runtime_max_retries=runtime_max_retries,
                )
            except EnterpriseAiDirectError as exc:
                fallback_messages.append(f"{engine.value}: {exc}")
                if not allow_deterministic_fallback:
                    raise RuntimeError(str(exc)) from exc
        elif engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT and not (allow_deterministic_fallback):
            raise RuntimeError("OCI Enterprise AI Direct が構成されていません。")

        if not allow_deterministic_fallback:
            raise RuntimeError(f"{engine.value} の実行結果を取得できませんでした。")

        sql = self._compose_select_sql(self._catalog_qualified_name(table), columns)
        if engine == Nl2SqlEngine.SELECT_AI:
            meta.update({"select_ai_profile": self._select_ai_profile_name(profile)})
        elif engine == Nl2SqlEngine.SELECT_AI_AGENT:
            meta.update(
                {
                    "select_ai_profile": self._select_ai_profile_name(profile),
                    "team_name": self._select_ai_team_name(profile),
                    "conversation_id": str(uuid.uuid4()),
                }
            )
        else:
            meta.update({"provider": "oci_enterprise_ai", "mode": "direct"})
        return GeneratedSql(
            engine=engine,
            generated_sql=sql,
            explanation=f"{table.logical_name} を対象に、許可された列のみを取得します。",
            engine_meta=meta,
            fallback_reason="; ".join(fallback_messages),
            schema_catalog=generation_catalog,
        )

    def _quality_evaluation_profile(
        self,
        profile_id: str | None,
    ) -> tuple[Nl2SqlProfile | None, str]:
        if not profile_id:
            return None, ""
        try:
            return self.get_profile(profile_id), ""
        except ValueError as exc:
            return None, str(exc)

    def _select_ai_known_scope_blocking_reason(self, profile: Nl2SqlProfile | None) -> str:
        if profile is None:
            return ""
        profile_name = self._select_ai_profile_name(profile)
        scope_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI)
        if scope_meta is None:
            return ""
        raw_states = scope_meta.engine_meta.get("profile_scope_states", {})
        profile_state = (
            raw_states.get(profile_name.upper()) if isinstance(raw_states, dict) else None
        )
        if isinstance(profile_state, dict):
            if (
                not bool(profile_state.get("refreshed"))
                or str(profile_state.get("status") or "") != "ready"
            ):
                return (
                    "Oracle Select AI Profile の object scope が未同期です。"
                    "Profile を再同期してから実行してください。"
                )
            return ""
        if scope_meta.profile_name == profile_name and (
            not scope_meta.refreshed or scope_meta.status != "ready"
        ):
            return (
                "Oracle Select AI Profile の object scope が未同期です。"
                "Profile を再同期してから実行してください。"
            )
        return ""

    def _select_ai_agent_known_asset_blocking_reason(self, profile: Nl2SqlProfile | None) -> str:
        if profile is None:
            return ""
        profile_name = self._select_ai_profile_name(profile)
        asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        if (
            asset_meta is not None
            and asset_meta.profile_name == profile_name
            and (not asset_meta.refreshed or asset_meta.status != "ready")
        ):
            return (
                "Oracle Select AI Agent assets が未同期です。"
                "Profile を再同期してから実行してください。"
            )
        return ""

    def quality_evaluation_engine_readiness(
        self, profile_id: str | None = None
    ) -> dict[Nl2SqlEngine, tuple[bool, str]]:
        """SQL生成評価で strict 実行を試行できる engine を公開する。"""

        settings = get_settings()
        profile, profile_error = self._quality_evaluation_profile(profile_id)
        oracle_ready = bool(
            self._use_oracle_runtime()
            and self._oracle_adapter.is_configured()
            and settings.nl2sql_select_ai_provider.strip()
            and settings.nl2sql_select_ai_credential_name.strip()
        )
        select_ai_blocking_reason = self._select_ai_known_scope_blocking_reason(profile)
        agent_blocking_reason = self._select_ai_agent_known_asset_blocking_reason(profile)
        select_ai_ready = bool(
            not profile_error
            and settings.nl2sql_select_ai_enabled
            and oracle_ready
            and not select_ai_blocking_reason
        )
        agent_ready = bool(
            not profile_error
            and settings.nl2sql_select_ai_agent_enabled
            and oracle_ready
            and not select_ai_blocking_reason
            and not agent_blocking_reason
        )
        direct_ready = bool(
            not profile_error
            and settings.nl2sql_enterprise_ai_direct_enabled
            and self._enterprise_ai_client.is_configured()
        )
        return {
            Nl2SqlEngine.SELECT_AI: (
                select_ai_ready,
                (
                    ""
                    if select_ai_ready
                    else (
                        profile_error
                        or select_ai_blocking_reason
                        or "Oracle Select AI の接続・credential・profile が未構成です。"
                    )
                ),
            ),
            Nl2SqlEngine.SELECT_AI_AGENT: (
                agent_ready,
                (
                    ""
                    if agent_ready
                    else (
                        profile_error
                        or select_ai_blocking_reason
                        or agent_blocking_reason
                        or "Oracle Select AI Agent の接続・credential・team が未構成です。"
                    )
                ),
            ),
            Nl2SqlEngine.ENTERPRISE_AI_DIRECT: (
                direct_ready,
                (
                    ""
                    if direct_ready
                    else profile_error or "OCI Enterprise AI Direct が構成されていません。"
                ),
            ),
        }

    def generate_sql_strict_for_quality_evaluation(
        self,
        *,
        question: str,
        engine: Nl2SqlEngine,
        profile_id: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> GeneratedSql:
        """fallback を一切許さず、選択された engine だけで SQL を生成する。"""

        if engine == Nl2SqlEngine.AUTO:
            raise ValueError("SQL生成評価で auto engine は使用できません。")
        ready, reason = self.quality_evaluation_engine_readiness(profile_id=profile_id).get(
            engine, (False, "未対応の engine です。")
        )
        if not ready:
            raise RuntimeError(reason)
        profile = self.get_profile(profile_id)
        allowed = self._resolve_allowed_objects(profile_id, AllowedObjects())
        row_limit = self._resolve_row_limit(profile_id, profile.default_row_limit)
        return self._generate_sql(
            engine,
            question,
            profile,
            allowed,
            row_limit,
            [],
            allow_deterministic_fallback=False,
            runtime_timeout_seconds=timeout_seconds,
            runtime_max_retries=max_retries,
        )

    def _generate_enterprise_ai_direct_sql(
        self,
        *,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
        fallback_messages: list[str],
        meta: dict[str, Any],
        learning_examples: list[LearningExample],
        catalog: SchemaCatalog,
        ontology_context: Any | None = None,
        runtime_timeout_seconds: float | None = None,
        runtime_max_retries: int | None = None,
    ) -> GeneratedSql:
        context = self._enterprise_ai_schema_context(
            profile=profile,
            allowed=allowed,
            catalog=catalog,
            learning_examples=learning_examples,
        )
        if ontology_context is not None:
            context = "\n".join(
                [
                    context,
                    "ontology_generation_context:",
                    self._ontology_generation_context_prompt(ontology_context),
                ]
            )
        system_prompt = self._enterprise_ai_sql_system_prompt()
        generate_kwargs: dict[str, Any] = {
            "prompt": question,
            "context": context,
            "system_prompt": system_prompt,
        }
        if runtime_timeout_seconds is not None:
            generate_kwargs["timeout_seconds"] = runtime_timeout_seconds
        if runtime_max_retries is not None:
            generate_kwargs["max_retries"] = runtime_max_retries
        raw_text = self._enterprise_ai_client.generate(**generate_kwargs)
        sql, explanation = self._extract_enterprise_ai_sql(raw_text)
        if not sql:
            raise EnterpriseAiDirectError("OCI Enterprise AI response から SQL を抽出できません。")
        meta.update(
            {
                "provider": "oci_enterprise_ai",
                "mode": "direct",
                "runtime": "oci_enterprise_ai",
                "model": self._enterprise_ai_client.model_id(),
                "response_format": "json_or_sql_text",
            }
        )
        return GeneratedSql(
            engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
            generated_sql=sql,
            explanation=explanation or "OCI Enterprise AI Direct で SQL を生成しました。",
            engine_meta=meta,
            fallback_reason="; ".join(fallback_messages),
            schema_catalog=catalog,
        )

    def _enterprise_ai_schema_context(
        self,
        *,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        catalog: SchemaCatalog,
        learning_examples: list[LearningExample] | None = None,
        use_glossary: bool = True,
    ) -> str:
        allowed_tables = {
            self._resolve_profile_object_name(table)
            for table in (allowed.table_names or self.profile_allowed_object_names(profile))
        }
        allowed_columns = {
            self._resolve_profile_object_name(table): {
                _normalize_identifier(column) for column in columns
            }
            for table, columns in allowed.columns.items()
            if columns
        }
        lines = [
            f"profile: {profile.name}",
            "glossary:",
        ]
        if use_glossary:
            lines.extend(
                f"- {term}: {definition}"
                for term, definition in self._effective_glossary(profile).items()
            )
        rules = self._effective_sql_rules(profile)
        if rules:
            lines.append("sql_rules:")
            lines.extend(f"- {rule}" for rule in rules)
        additional_instructions = profile.select_ai_config.additional_instructions.strip()
        if additional_instructions:
            lines.append("additional_instructions:")
            lines.append(additional_instructions)
        lines.append("schema:")
        for table in catalog.tables:
            qualified_name = self._catalog_qualified_name(table)
            if allowed_tables and qualified_name not in allowed_tables:
                continue
            lines.append(
                f"- table {qualified_name} logical={table.logical_name} comment={table.comment}"
            )
            table_allowed_columns = allowed_columns.get(qualified_name, set())
            for column in table.columns:
                if table_allowed_columns and column.column_name not in table_allowed_columns:
                    continue
                lines.append(
                    "  - column "
                    f"{column.column_name} logical={column.logical_name} "
                    f"type={column.data_type} comment={column.comment}"
                )
        learning_context = self._learning_examples_context(learning_examples or [])
        if learning_context:
            lines.append(learning_context)
        return "\n".join(line for line in lines if line.strip())

    def _enterprise_ai_sql_system_prompt(self) -> str:
        return (
            "あなたは Oracle Database 26ai 向け NL2SQL エンジンです。"
            "与えられた schema/context の表と列だけを使用してください。"
            "FROM/JOIN の物理 object は必ず OWNER.OBJECT で修飾してください。"
            "DDL/DML/PLSQL/複数 statement/説明付き markdown は禁止です。"
            "必ず SELECT または WITH で始まる 1 つの Oracle SQL を生成してください。"
            "質問に件数の指定が無い限り、FETCH FIRST n ROWS ONLY などの行数制限を"
            "勝手に付けないでください。"
            '出力は JSON のみ: {"sql":"...", "explanation":"..."}。'
            "説明は日本語で簡潔にしてください。"
        )

    def _extract_enterprise_ai_sql(self, raw_text: str) -> tuple[str, str]:
        cleaned = raw_text.strip()
        fence_match = re.match(
            r"^\s*```(?:json|sql)?\s*(.*?)\s*```\s*$",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fence_match:
            cleaned = fence_match.group(1).strip()
        explanation = ""
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            explanation = str(payload.get("explanation") or "")
            for key in ("sql", "generated_sql", "query", "result"):
                candidate = str(payload.get(key) or "").strip()
                if candidate:
                    return self._extract_select_from_text(candidate), explanation
        return self._extract_select_from_text(cleaned), explanation

    def _extract_select_from_text(self, text: str) -> str:
        match = re.search(r"\b(with|select)\b.+", text.strip(), flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return match.group(0).split(";", 1)[0].strip()

    def _generate_oracle_sql(
        self,
        *,
        engine: Nl2SqlEngine,
        question: str,
        profile: Nl2SqlProfile,
        fallback_messages: list[str],
        meta: dict[str, Any],
        learning_examples: list[LearningExample],
        select_ai_overrides: SelectAiRequestOverrides | None = None,
        ontology_context: Any | None = None,
        runtime_timeout_seconds: float | None = None,
    ) -> GeneratedSql:
        self._assert_select_ai_scope_ready(profile)
        asset_meta = self._asset_meta.get(engine)
        expected_profile_name = self._select_ai_profile_name(profile)
        if (
            asset_meta is not None
            and asset_meta.profile_name == expected_profile_name
            and (not asset_meta.refreshed or asset_meta.status != "ready")
        ):
            raise OracleAdapterError(
                "Oracle Select AI asset の object scope が未同期です。"
                "Profile を再同期してから実行してください。"
            )
        runtime_question = question
        ontology_instructions = (
            self._ontology_generation_context_prompt(ontology_context)
            if ontology_context is not None
            else ""
        )
        if engine == Nl2SqlEngine.SELECT_AI:
            profile_name = self._select_ai_profile_name(profile)
            effective_overrides = self._select_ai_overrides_with_ontology_context(
                select_ai_overrides,
                ontology_context,
            )
            attributes = self._select_ai_generate_attributes(profile, effective_overrides)
            if attributes:
                select_ai_kwargs: dict[str, Any] = {
                    "profile_name": profile_name,
                    "question": runtime_question,
                    "attributes": attributes,
                }
            else:
                select_ai_kwargs = {
                    "profile_name": profile_name,
                    "question": runtime_question,
                }
            if runtime_timeout_seconds is not None:
                select_ai_kwargs["call_timeout_seconds"] = runtime_timeout_seconds
            sql = self._oracle_adapter.generate_select_ai_sql(**select_ai_kwargs)
            meta.update({"select_ai_profile": profile_name, "runtime": "oracle"})
            if attributes:
                meta.update(
                    {
                        "select_ai_role_applied": "role" in attributes,
                        "select_ai_role_length": len(attributes.get("role", "")),
                        "select_ai_additional_instructions_applied": (
                            "additional_instructions" in attributes
                        ),
                        "select_ai_additional_instructions_length": len(
                            attributes.get("additional_instructions", "")
                        ),
                    }
                )
        else:
            team_name = self._select_ai_runtime_team_name(profile)
            tool_name = self._select_ai_agent_asset_names(profile)["tool"]
            if ontology_instructions:
                runtime_question = "\n\n".join(
                    [
                        runtime_question,
                        "確認済み Ontology コンテキスト:",
                        ontology_instructions,
                    ]
                )
            agent_kwargs: dict[str, Any] = {
                "team_name": team_name,
                "question": runtime_question,
                "tool_name": tool_name,
            }
            if runtime_timeout_seconds is not None:
                agent_kwargs["call_timeout_seconds"] = runtime_timeout_seconds
            sql, conversation_id = self._oracle_adapter.run_select_ai_agent_team(**agent_kwargs)
            meta.update(
                {
                    "select_ai_profile": self._select_ai_profile_name(profile),
                    "team_name": team_name,
                    "conversation_id": conversation_id,
                    "runtime": "oracle",
                }
            )
        if not sql:
            raise OracleAdapterError("Oracle engine から SQL を取得できませんでした。")
        return GeneratedSql(
            engine=engine,
            generated_sql=sql,
            explanation="SQL を生成しました。",
            engine_meta=meta,
            fallback_reason="; ".join(fallback_messages),
        )

    def _ontology_generation_context_prompt(self, context: Any) -> str:
        """確認済み Ontology context を SQL 生成器向けの短い制約文へ変換する。"""

        if context is None:
            return ""
        lines = [
            f"context_hash: {getattr(context, 'context_hash', '')}",
            f"ontology_revision_id: {getattr(context, 'ontology_revision_id', '')}",
            f"profile_view_id: {getattr(context, 'profile_view_id', '')}",
            f"intent_version: {getattr(context, 'intent_version', '')}",
            f"question_effective: {getattr(context, 'question_effective', '')}",
        ]
        allowed_objects = list(getattr(context, "allowed_object_names", []) or [])
        if allowed_objects:
            lines.append("allowed_objects:")
            lines.extend(f"- {value}" for value in allowed_objects[:80])
        allowed_columns = dict(getattr(context, "allowed_column_names", {}) or {})
        if allowed_columns:
            lines.append("allowed_columns:")
            for object_name, columns in sorted(allowed_columns.items())[:80]:
                lines.append(f"- {object_name}: {', '.join(columns[:80])}")
        metric_definitions = list(getattr(context, "metric_definitions", []) or [])
        if metric_definitions:
            lines.append("metrics:")
            for metric in metric_definitions[:40]:
                name = getattr(metric, "metric_node_id", "")
                expression = getattr(metric, "expression_sql", "")
                aggregation = getattr(getattr(metric, "aggregation", ""), "value", "")
                lines.append(f"- {name}: aggregation={aggregation} expression={expression}")
        filters = list(getattr(context, "filter_summaries_ja", []) or [])
        if filters:
            lines.append("filters:")
            lines.extend(f"- {value}" for value in filters[:40])
        time_range = str(getattr(context, "time_range_summary_ja", "") or "").strip()
        if time_range:
            lines.append(f"time_range: {time_range}")
        granularity = str(getattr(context, "granularity", "") or "").strip()
        if granularity:
            lines.append(f"granularity: {granularity}")
        joins = list(getattr(context, "join_condition_summaries", []) or [])
        if joins:
            lines.append("approved_join_conditions:")
            lines.extend(f"- {value}" for value in joins[:80])
        published_markdown = str(getattr(context, "llm_markdown", "") or "").strip()
        if published_markdown:
            lines.append("published_markdown_ontology:")
            lines.append("```markdown")
            lines.append(
                published_markdown
                if len(published_markdown) <= 12000
                else f"{published_markdown[:12000]}\n... truncated ..."
            )
            lines.append("```")
        sorts = list(getattr(context, "sort_summaries_ja", []) or [])
        if sorts:
            lines.append("sorts:")
            lines.extend(f"- {value}" for value in sorts[:20])
        limit = getattr(context, "limit", None)
        if limit:
            lines.append(f"limit: {limit}")
        warnings = list(getattr(context, "warnings_ja", []) or [])
        if warnings:
            lines.append("warnings:")
            lines.extend(f"- {value}" for value in warnings[:20])
        mermaid_er = str(getattr(context, "mermaid_er", "") or "").strip()
        if mermaid_er:
            lines.append("er_diagram(mermaid):")
            lines.append("```mermaid")
            lines.append(mermaid_er)
            lines.append("```")
        lines.append("rules:")
        lines.append(
            "- 上記 allowed_objects / allowed_columns / approved_join_conditions だけを使う。"
        )
        lines.append("- published_markdown_ontology は業務語彙・指標説明の確認済み文脈として使う。")
        lines.append("- 未承認の JOIN、未確認の指標、未確認の filter を追加しない。")
        lines.append("- SQL は Oracle SELECT または WITH 1 statement のみ。")
        return "\n".join(lines)

    def _use_oracle_runtime(self) -> bool:
        return get_settings().nl2sql_runtime_mode.strip().lower() == "oracle"

    def _select_ai_feedback_showsql(self, question: str) -> str:
        cleaned = question.strip()
        if cleaned.endswith(";"):
            cleaned = cleaned[:-1].rstrip()
        return f"select ai showsql {cleaned}"

    def _select_ai_feedback_plsql_preview(
        self,
        *,
        profile_name: str,
        sql_text: str,
        feedback_type: str,
        response: str,
        feedback_content: str,
    ) -> str:
        response_expr = "NULL" if not response else _quote_sql_string(response)
        feedback_content_expr = (
            "NULL" if not feedback_content else _quote_sql_string(feedback_content)
        )
        return "\n".join(
            [
                "BEGIN",
                "  DBMS_CLOUD_AI.FEEDBACK(",
                f"    profile_name => {_quote_sql_string(profile_name)},",
                f"    sql_text => {_quote_sql_string(sql_text)},",
                f"    feedback_type => {_quote_sql_string(feedback_type)},",
                f"    response => {response_expr},",
                f"    feedback_content => {feedback_content_expr},",
                "    operation => 'ADD'",
                "  );",
                "END;",
            ]
        )

    def _select_ai_profile_name(self, profile: Nl2SqlProfile) -> str:
        configured = profile.select_ai_config.profile_name.strip()
        if configured:
            return configured
        prefix = get_settings().nl2sql_select_ai_profile_prefix.strip() or "NL2SQL"
        return f"{prefix}_{profile.id.upper()}_PROFILE"

    def _business_select_ai_profile_names(self, *, include_archived: bool) -> set[str]:
        profiles = self.list_profiles(include_archived=include_archived)
        names: set[str] = set()
        for profile in profiles:
            if profile.archived and not include_archived:
                continue
            profile_name = self._select_ai_profile_name(profile).strip()
            if profile_name:
                names.add(profile_name.upper())
        return names

    def _is_business_select_ai_profile(
        self, profile_name: str, business_profile_names: set[str] | None
    ) -> bool:
        if business_profile_names is None:
            return True
        return profile_name.strip().upper() in business_profile_names

    def _select_ai_team_name(self, profile: Nl2SqlProfile) -> str:
        prefix = get_settings().nl2sql_select_ai_profile_prefix.strip() or "NL2SQL"
        return _oracle_agent_asset_name(prefix=prefix, profile_key=profile.id, suffix="TEAM")

    def _select_ai_runtime_team_name(self, profile: Nl2SqlProfile) -> str:
        profile_name = self._select_ai_profile_name(profile)
        asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        if asset_meta and asset_meta.profile_name == profile_name and asset_meta.team_name:
            return asset_meta.team_name
        return self._select_ai_team_name(profile)

    def _versioned_select_ai_team_name(self, base_team_name: str) -> str:
        suffix = uuid.uuid4().hex[:8].upper()
        return f"{base_team_name[:118]}_V{suffix}"

    def _looks_like_agent_generated_profile_conflict(self, message: str) -> bool:
        normalized = message.upper()
        return "AGENT$" in normalized and "PROFILE" in normalized and "ALREADY EXISTS" in normalized

    def _select_ai_agent_asset_names(self, profile: Nl2SqlProfile) -> dict[str, str]:
        prefix = get_settings().nl2sql_select_ai_profile_prefix.strip() or "NL2SQL"
        return {
            "tool": _oracle_agent_asset_name(prefix=prefix, profile_key=profile.id, suffix="TOOL"),
            "agent": _oracle_agent_asset_name(
                prefix=prefix, profile_key=profile.id, suffix="AGENT"
            ),
            "task": _oracle_agent_asset_name(prefix=prefix, profile_key=profile.id, suffix="TASK"),
            "team": _oracle_agent_asset_name(prefix=prefix, profile_key=profile.id, suffix="TEAM"),
        }

    def _dedupe_object_names(self, names: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        objects: list[str] = []
        for name in names:
            if not str(name or "").strip():
                continue
            normalized = self._resolve_profile_object_name(str(name))
            if not is_user_visible_object_name(normalized):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            objects.append(normalized)
        return objects

    def _select_ai_object_list(self, object_names: Sequence[str]) -> list[dict[str, str]]:
        objects: list[dict[str, str]] = []
        for object_name in object_names:
            if not str(object_name or "").strip():
                continue
            identity = parse_object_identity(
                self._resolve_profile_object_name(str(object_name)),
            )
            objects.append({"owner": identity.owner, "name": identity.object_name})
        return objects

    def _resolve_allowed_objects(
        self, profile_id: str | None, requested: AllowedObjects
    ) -> AllowedObjects:
        profile = self.get_profile(profile_id)
        profile_names = self.profile_allowed_object_names(profile)
        profile_scope = set(profile_names)
        requested_names = self._dedupe_object_names(requested.table_names)
        resolved_names = (
            [name for name in requested_names if name in profile_scope]
            if requested_names
            else profile_names
        )
        resolved_scope = set(resolved_names)
        resolved_columns: dict[str, list[str]] = {}
        for table_name, columns in requested.columns.items():
            canonical = self._resolve_profile_object_name(table_name)
            if canonical in resolved_scope:
                resolved_columns[canonical] = [
                    _normalize_identifier(column) for column in columns if column.strip()
                ]
        return AllowedObjects(
            table_names=resolved_names,
            columns=resolved_columns,
            enforce_table_scope=True,
        )

    def resolve_allowed_objects(
        self, profile_id: str | None, requested: AllowedObjects
    ) -> AllowedObjects:
        """Profile view を越えない request scope を公開 API 用に解決する。"""

        return self._resolve_allowed_objects(profile_id, requested)

    def resolve_direct_sql_allowed_objects(
        self,
        requested: AllowedObjects,
        *,
        profile_ids: set[str] | None = None,
    ) -> AllowedObjects:
        """手書き SELECT SQL 実行用の scope を解決する。

        `profile_ids` が与えられた(= principal に業務プロファイル制限がある)場合は、
        その profile 群が許可する table/view の和集合を上限とし、request の table_names は
        和集合との積として解釈する(和集合が空なら全ての表参照が拒否される)。
        `None`(system admin / 認証無効)のときは従来どおり request scope だけを使う。
        """

        current_owner = self._current_schema_owner()
        requested_names: list[str] = []
        seen: set[str] = set()
        for name in requested.table_names:
            if not str(name or "").strip():
                continue
            normalized = parse_object_identity(
                str(name),
                default_owner=current_owner,
            ).qualified_name
            if not is_user_visible_object_name(normalized) or normalized in seen:
                continue
            seen.add(normalized)
            requested_names.append(normalized)
        profile_scope: set[str] | None = None
        if profile_ids is not None:
            profile_scope = set()
            for profile_id in profile_ids:
                try:
                    profile = self.get_profile(profile_id)
                except ValueError:
                    continue
                profile_scope.update(
                    parse_object_identity(name, default_owner=current_owner).qualified_name
                    for name in self.profile_allowed_object_names(profile)
                )
            requested_names = (
                [name for name in requested_names if name in profile_scope]
                if requested_names
                else sorted(profile_scope)
            )
        requested_scope = set(requested_names)
        resolved_columns: dict[str, list[str]] = {}
        for table_name, columns in requested.columns.items():
            canonical = parse_object_identity(
                table_name,
                default_owner=current_owner,
            ).qualified_name
            if not is_user_visible_object_name(canonical):
                continue
            if requested_names and canonical not in requested_scope:
                continue
            normalized_columns = [
                _normalize_identifier(column) for column in columns if column.strip()
            ]
            if normalized_columns:
                resolved_columns[canonical] = normalized_columns
        return AllowedObjects(
            table_names=requested_names,
            columns=resolved_columns,
            enforce_table_scope=requested.enforce_table_scope or profile_scope is not None,
        )

    def _resolve_row_limit(self, profile_id: str | None, requested: int | None) -> int:
        """request 明示 > profile 既定 > グローバル既定の順で row limit を解決する。

        None のまま返すと execute_select が無制限 fetchall になるため、必ず正の値へ落とす。
        """
        if requested:
            return requested
        try:
            profile_default = self.get_profile(profile_id).default_row_limit
        except ValueError:
            profile_default = None
        return profile_default or get_settings().nl2sql_default_row_limit

    def _generation_schema_catalog(
        self,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
    ) -> SchemaCatalog:
        """SQL 生成で使う Profile scope 限定の schema snapshot を構築する。"""

        repository = self._incremental_repository
        if repository is None:
            catalog = self.get_catalog()
            if not catalog.tables:
                raise SchemaCatalogEmptyError(_SCHEMA_EMPTY_MESSAGE)
            return catalog

        target_names = self._dedupe_object_names(
            allowed.table_names or self.profile_allowed_object_names(profile)
        )
        if not target_names:
            raise ValueError(_PROFILE_SCHEMA_SCOPE_EMPTY_MESSAGE)

        cached_head = self._schema_cache.get("head")
        self._refresh_cache_token(
            SCHEMA_NAMESPACE,
            allow_cached_on_failure=isinstance(cached_head, SchemaCatalogHead),
        )
        head = self.get_catalog_head()
        tables: list[SchemaTable] = []
        dependencies: list[SchemaViewDependency] = []
        seen: set[str] = set()
        current_owner = self._current_schema_owner()
        for object_name in target_names:
            identity = parse_object_identity(object_name, default_owner=current_owner)
            qualified = identity.qualified_name
            if qualified in seen:
                continue
            try:
                detail = repository.get_schema_object(identity.owner, identity.object_name)
            except Exception as exc:
                self._raise_incremental_repository_failure(
                    operation="schema_object_detail",
                    exc=exc,
                    operation_error_code="schema_object_detail_failed",
                )
            if detail is None:
                try:
                    page = repository.search_schema_objects(
                        cursor=None,
                        limit=3,
                        query=identity.object_name,
                        owner="",
                        object_type="",
                        allowed_names={identity.object_name},
                        row_state="",
                        include_counts=False,
                    )
                    matches = [
                        item
                        for item in page.items
                        if item.object_name.upper() == identity.object_name
                    ]
                    if len(matches) == 1:
                        detail = repository.get_schema_object(
                            matches[0].owner,
                            matches[0].object_name,
                        )
                except Exception as exc:
                    self._raise_incremental_repository_failure(
                        operation="schema_object_detail",
                        exc=exc,
                        operation_error_code="schema_object_detail_failed",
                    )
            if detail is None:
                continue
            table = detail.table.model_copy(deep=True)
            tables.append(table)
            dependencies.extend(detail.dependencies)
            seen.add(qualified)

        if not tables:
            raise ValueError(_PROFILE_SCHEMA_SCOPE_EMPTY_MESSAGE)

        return SchemaCatalog(
            refreshed_at=head.refreshed_at,
            tables=tables,
            schema_fingerprint=head.schema_fingerprint,
            view_dependencies=dependencies,
            current_owner=current_owner,
        )

    def _choose_table(
        self,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        catalog: SchemaCatalog,
    ) -> SchemaTable:
        allowed_names = {
            self._resolve_profile_object_name(name)
            for name in (allowed.table_names or self.profile_allowed_object_names(profile))
        }
        candidates = [
            table
            for table in catalog.tables
            if self._catalog_qualified_name(table) in allowed_names
        ]
        if not candidates:
            raise ValueError(_PROFILE_SCHEMA_SCOPE_EMPTY_MESSAGE)
        question_upper = question.upper()
        for table in candidates:
            if table.table_name in question_upper or table.logical_name in question:
                return table
        return candidates[0]

    def _choose_columns(self, table: SchemaTable, allowed: AllowedObjects) -> list[SchemaColumn]:
        qualified_name = self._catalog_qualified_name(table)
        allowed_columns = {
            _normalize_identifier(name)
            for name in (
                allowed.columns.get(qualified_name, []) or allowed.columns.get(table.table_name, [])
            )
        }
        if allowed_columns:
            selected = [column for column in table.columns if column.column_name in allowed_columns]
            if selected:
                return selected[:8]
        return table.columns[:6]

    def _compose_select_sql(self, table_name: str, columns: list[SchemaColumn]) -> str:
        column_sql = ", ".join(column.column_name for column in columns) or "*"
        # Safe: deterministic SQL from schema catalog metadata.
        return f"SELECT {column_sql} FROM {table_name}"  # nosec B608

    def _mock_execute(self, sql: str, row_limit: int | None) -> QueryResults:
        referenced = _extract_referenced_tables(sql, current_owner=self._current_schema_owner())
        table_name = referenced[0] if referenced else ""
        table = next(
            (
                candidate
                for candidate in self._catalog.tables
                if self._catalog_qualified_name(candidate) == table_name
            ),
            None,
        )
        if table is None:
            return QueryResults(columns=["MESSAGE"], rows=[{"MESSAGE": "mock result"}], total=1)
        columns = [column.column_name for column in table.columns[:4]]
        if not columns:
            return QueryResults(columns=["MESSAGE"], rows=[{"MESSAGE": "mock result"}], total=1)

        def column_value(column_index: int, row_index: int) -> object:
            if column_index == 0:
                return f"{table.table_name}-{row_index + 1}"
            if column_index == 1:
                column = table.columns[column_index]
                if column.sample_values:
                    return column.sample_values[row_index % len(column.sample_values)]
                return f"値{row_index + 1}"
            if column_index == 2:
                return (row_index + 1) * 1000
            if column_index == 3:
                return "2026-06-21"
            return ""

        rows = [
            {
                column: column_value(column_index, index)
                for column_index, column in enumerate(columns)
            }
            for index in range(min(row_limit or 5, 5))
        ]
        return QueryResults(columns=columns, rows=rows, total=len(rows))

    def _repair_sql(
        self,
        *,
        sql: str,
        safety: SafetyReport,
        allowed: AllowedObjects,
        referenced_tables: list[str],
        referenced_columns: list[str],
        has_wildcard: bool,
        catalog: SchemaCatalog | None = None,
    ) -> str:
        stripped = sql.strip().rstrip(";")
        if not stripped:
            return ""

        if not safety.is_select_only:
            for statement in [part.strip() for part in sql.split(";") if part.strip()]:
                if is_select_only(statement):
                    return normalize_executable_sql(statement)
            return ""

        current_owner = self._current_schema_owner()
        if not _table_allowed(
            referenced_tables,
            allowed,
            current_owner=current_owner,
        ):
            table_name = self._first_allowed_table(allowed, catalog)
            if not table_name:
                return ""
            select_list = self._allowed_select_list(table_name, allowed, catalog)
            return normalize_executable_sql(
                # Safe: table and columns are resolved from allowed_objects.
                f"SELECT {select_list} FROM {table_name}",  # nosec B608
            )

        if has_wildcard or not _column_allowed(
            referenced_columns,
            has_wildcard,
            referenced_tables,
            allowed,
            current_owner=current_owner,
        ):
            table_name = (
                referenced_tables[0]
                if referenced_tables
                else self._first_allowed_table(allowed, catalog)
            )
            if not table_name:
                return normalize_executable_sql(stripped)
            select_list = self._allowed_select_list(table_name, allowed, catalog)
            if _extract_select_list(stripped):
                repaired = re.sub(
                    r"\bselect\b.+?\bfrom\b",
                    f"SELECT {select_list} FROM",
                    stripped,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                return normalize_executable_sql(repaired)
            # Safe: repair fallback uses allowed table/column list.
            return normalize_executable_sql(
                f"SELECT {select_list} FROM {table_name}",  # nosec B608
            )

        executable = normalize_executable_sql(stripped)
        return executable if executable != stripped else ""

    def _first_allowed_table(
        self, allowed: AllowedObjects, catalog: SchemaCatalog | None = None
    ) -> str:
        if allowed.table_names:
            return self._resolve_profile_object_name(allowed.table_names[0])
        active_catalog = catalog or self._catalog
        return (
            self._catalog_qualified_name(active_catalog.tables[0]) if active_catalog.tables else ""
        )

    def _allowed_select_list(
        self,
        table_name: str,
        allowed: AllowedObjects,
        catalog: SchemaCatalog | None = None,
    ) -> str:
        normalized_table = self._resolve_profile_object_name(table_name)
        restricted_columns = {
            self._resolve_profile_object_name(candidate_table): columns
            for candidate_table, columns in allowed.columns.items()
        }
        allowed_columns = [
            _normalize_identifier(column)
            for column in restricted_columns.get(normalized_table, [])
            if column.strip()
        ]
        if allowed_columns:
            return ", ".join(allowed_columns)
        table = next(
            (
                candidate
                for candidate in (catalog or self._catalog).tables
                if self._catalog_qualified_name(candidate) == normalized_table
            ),
            None,
        )
        columns = [column.column_name for column in table.columns[:6]] if table else []
        return ", ".join(columns) or "*"

    def _optimization_hints(
        self, *, safety: SafetyReport, sql: str, row_limit: int | None
    ) -> list[str]:
        if not safety.is_select_only:
            return ["参照系 SQL に修正してから最適化を確認してください。"]
        hints: list[str] = []
        normalized = sql.lower()
        if safety.referenced_tables and " where " not in normalized:
            hints.append("大量データの表では WHERE 条件を追加すると応答時間が安定します。")
        if " join " in normalized:
            hints.append("JOIN 条件に主キー・外部キー列を使っているか確認してください。")
        if row_limit and row_limit > 1000:
            hints.append("画面確認用途では row limit を 1000 件以下にすると扱いやすくなります。")
        if not hints:
            hints.append(
                "現在の SQL は安全境界内で実行可能です。必要に応じて条件列を追加してください。"
            )
        return hints

    def _recommendations(
        self,
        safety: SafetyReport,
        repaired_sql: str = "",
        *,
        sql: str = "",
        allowed: AllowedObjects | None = None,
        catalog: SchemaCatalog | None = None,
    ) -> list[str]:
        if not safety.is_safe:
            recommendations = [
                "許可オブジェクトを見直すか、SELECT/WITH の単一 statement に修正してください。"
            ]
            if allowed and "許可されていない表" in safety.blocked_reason:
                active_catalog = catalog or self._catalog
                allowed_tables = allowed.table_names or [
                    table.table_name for table in active_catalog.tables[:5]
                ]
                recommendations.append(f"参照可能な表は {', '.join(allowed_tables[:5])} です。")
            if allowed and "許可されていない列" in safety.blocked_reason:
                allowed_columns = [
                    f"{_normalize_identifier(table)}.{_normalize_identifier(column)}"
                    for table, columns in allowed.columns.items()
                    for column in columns
                    if column.strip()
                ]
                if allowed_columns:
                    recommendations.append(
                        f"参照可能な列は {', '.join(allowed_columns[:8])} です。"
                    )
            if repaired_sql:
                recommendations.append("修復候補 SQL を確認してから再実行してください。")
            return recommendations
        recommendations = ["実行前に生成 SQL と対象表を確認してください。"]
        if re.search(r"\s+limit\s+\d+\s*;?\s*$", sql, flags=re.IGNORECASE):
            recommendations.append(
                "Oracle では LIMIT 句を使えないため、必要なら "
                "FETCH FIRST n ROWS ONLY に修正してください。"
            )
        if sql.strip().endswith(";") and ";" not in sql.strip().rstrip(";"):
            recommendations.append("API 実行前に末尾セミコロンを除去します。")
        if not safety.referenced_tables:
            recommendations.append("FROM/JOIN の対象表が検出できませんでした。")
        if repaired_sql:
            recommendations.append("実行時には修復候補 SQL を使用します。")
        return recommendations

    def _build_default_catalog(self) -> SchemaCatalog:
        return SchemaCatalog(refreshed_at=_utc_now(), tables=[])


nl2sql_service = Nl2SqlService()
