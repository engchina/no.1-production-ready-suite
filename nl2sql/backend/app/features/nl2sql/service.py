"""NL2SQL application service.

この実装は local / CI で外部 Oracle・OCI に依存せずに動く deterministic adapter を持つ。
実運用では `SelectAiAdapter` / `SelectAiAgentAdapter` / `EnterpriseAiDirectAdapter`
の generate 部分を Oracle / OCI 呼び出しに差し替える。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

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
from .models import (
    AllowedObjects,
    AnalyzeData,
    AssetCleanupData,
    AssetRefreshData,
    CommentApplyData,
    CommentApplyItem,
    CommentApplyRequest,
    CommentApplyStatement,
    CommentSuggestion,
    CommentSuggestionData,
    CompareData,
    CompareExecutionData,
    CompareHistoryData,
    CompareRecord,
    CompareRequest,
    CsvImportColumn,
    CsvImportData,
    CsvImportRequest,
    DemoLearningData,
    DiagnosticCheck,
    DiagnosticConfigGuide,
    DiagnosticConfigVar,
    DiagnosticReadiness,
    DiagnosticsData,
    DiagnosticSmokeCheck,
    EvaluateData,
    EvaluateRequest,
    EvaluationRunRecord,
    EvaluationRunsData,
    EvaluationSet,
    EvaluationSetsData,
    EvaluationSetUpsertRequest,
    FeedbackData,
    FeedbackIndexData,
    FeedbackIndexRequest,
    FeedbackRating,
    HistoryData,
    HistoryItem,
    JobCreateData,
    JobCreateRequest,
    JobData,
    JobStatus,
    Nl2SqlEngine,
    Nl2SqlProfile,
    Nl2SqlResult,
    PreviewData,
    PreviewRequest,
    ProfileRecommendationCandidate,
    ProfileRecommendationData,
    ProfileRecommendationRequest,
    QueryResults,
    RepairData,
    RepairRequest,
    ReverseSqlData,
    ReverseSqlRequest,
    SafetyReport,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
    SimilarHistoryData,
    SimilarHistoryItem,
    SimilarHistoryRequest,
    StageTiming,
    SyntheticCase,
    SyntheticCasesData,
    TimingEnvelope,
)
from .oracle_adapter import OracleAdapterError, OracleNl2SqlAdapter
from .store import MemoryNl2SqlStore, Nl2SqlStore, OracleJsonNl2SqlStore

logger = logging.getLogger(__name__)

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
_SELECT_TOKEN = re.compile(r"\bselect\b", re.IGNORECASE)
_SQL_IDENTIFIER = re.compile(r"[a-zA-Z_][\w$#]*")
_STRICT_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_QUALIFIED_COLUMN = re.compile(r"([a-zA-Z_][\w$#]*)\s*\.\s*([a-zA-Z_*][\w$#*]*)", re.IGNORECASE)
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _normalize_identifier(value: str) -> str:
    parts = [part.strip().strip('"') for part in value.strip().split(".")]
    return (parts[-1] if parts else "").upper()


def _csv_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().upper()).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"C_{normalized}"
    return normalized[:128]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _quote_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _similarity_tokens(value: str) -> set[str]:
    normalized = value.upper()
    tokens = {match.group(0) for match in re.finditer(r"[A-Z0-9_]{2,}", normalized)}
    cjk = [char for char in value if "\u3040" <= char <= "\u9fff"]
    tokens.update(cjk)
    tokens.update("".join(cjk[index : index + 2]) for index in range(max(len(cjk) - 1, 0)))
    return {token for token in tokens if token.strip()}


def is_select_only(sql: str) -> bool:
    """SELECT/WITH のみを許可し、DDL/DML/PLSQL と複数 statement を拒否する。"""
    stripped = sql.strip()
    if not stripped:
        return False
    head = stripped.lstrip("(").lower()
    if head.startswith(_FORBIDDEN_PREFIXES):
        return False
    if ";" in stripped.rstrip(";"):
        return False
    if _DANGEROUS_TOKENS.search(stripped):
        return False
    return head.startswith("select") or head.startswith("with")


def _extract_referenced_tables(sql: str) -> list[str]:
    seen: set[str] = set()
    tables: list[str] = []
    for match in _FROM_JOIN_TABLE.finditer(sql):
        normalized = _normalize_identifier(match.group(1))
        if normalized and normalized not in seen:
            seen.add(normalized)
            tables.append(normalized)
    return tables


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


def _table_allowed(referenced_tables: list[str], allowed: AllowedObjects) -> bool:
    if not allowed.table_names:
        return True
    allowed_set = {_normalize_identifier(table) for table in allowed.table_names}
    return all(table in allowed_set for table in referenced_tables)


def _column_allowed(
    referenced_columns: list[str],
    has_wildcard: bool,
    referenced_tables: list[str],
    allowed: AllowedObjects,
) -> bool:
    restrictions = {
        _normalize_identifier(table): {_normalize_identifier(column) for column in columns}
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
            table, column = column_ref.split(".", 1)
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


def _strip_row_limit(sql: str) -> str:
    without_fetch = re.sub(
        r"\s+fetch\s+first\s+\d+\s+rows\s+only\s*;?\s*$",
        "",
        sql,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+limit\s+\d+\s*;?\s*$", "", without_fetch, flags=re.IGNORECASE)


def one_line_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def enforce_row_limit(sql: str, row_limit: int) -> str:
    """Oracle 向けに row limit を明示する。すでに FETCH FIRST があれば置換する。"""
    normalized = _strip_row_limit(sql.strip().rstrip(";"))
    return f"{normalized} FETCH FIRST {row_limit} ROWS ONLY"


@dataclass
class GeneratedSql:
    engine: Nl2SqlEngine
    generated_sql: str
    explanation: str
    engine_meta: dict[str, Any]
    fallback_reason: str = ""


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
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=_utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_ms: int | None = None
    result: Nl2SqlResult | None = None
    error_message: str | None = None
    timing: TimingEnvelope | None = None


class Nl2SqlService:
    """NL2SQL orchestration with pluggable state store."""

    def __init__(self, store: Nl2SqlStore | None = None) -> None:
        settings = get_settings()
        self._lock = threading.RLock()
        self._catalog = self._build_default_catalog()
        self._oracle_adapter = OracleNl2SqlAdapter(settings)
        self._embedding_client: FeedbackEmbeddingClient = OciGenAiEmbeddingClient(settings)
        self._enterprise_ai_client: EnterpriseAiDirectClient = OciEnterpriseAiDirectClient(settings)
        self._store = store or self._build_store(settings)
        self._profiles: dict[str, Nl2SqlProfile] = {
            "default": Nl2SqlProfile(
                id="default",
                name="標準業務プロファイル",
                description="売上・請求・顧客データを対象にした標準 NL2SQL profile。",
                allowed_tables=["INVOICES", "CUSTOMERS", "PAYMENTS"],
                glossary={"売上": "INVOICES.TOTAL_AMOUNT", "取引先": "CUSTOMERS.CUSTOMER_NAME"},
                sql_rules=["SELECT/WITH のみ", "FETCH FIRST で行数制限", "許可テーブルのみ参照"],
                default_row_limit=settings.nl2sql_default_row_limit,
                few_shot_examples=[
                    {
                        "question": "今月の請求金額が大きい取引先を見たい",
                        "sql": "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
                    }
                ],
            )
        }
        self._jobs: dict[str, StoredJob] = {}
        self._history: list[HistoryItem] = []
        self._compare_records: list[CompareRecord] = []
        self._evaluation_sets: list[EvaluationSet] = []
        self._evaluation_runs: list[EvaluationRunRecord] = []
        self._feedback: dict[str, FeedbackRating] = {}
        self._feedback_indexed_ids: set[str] = set()
        self._asset_meta: dict[Nl2SqlEngine, AssetRefreshData] = {}
        self._load_snapshot()
        self._persist_state()

    def _build_store(self, settings: Any) -> Nl2SqlStore:
        mode = settings.nl2sql_persistence_mode.strip().lower()
        if mode == "oracle":
            return OracleJsonNl2SqlStore(
                connection_factory=self._oracle_adapter.connection,
                table_name=settings.nl2sql_oracle_state_table,
            )
        if mode not in {"memory", "in_memory", "deterministic"}:
            logger.warning("Unsupported NL2SQL persistence mode %s; using memory.", mode)
        return MemoryNl2SqlStore()

    def _load_snapshot(self) -> None:
        try:
            snapshot = self._store.load_snapshot()
        except Exception as exc:  # pragma: no cover - live store defensive boundary
            logger.warning("NL2SQL store snapshot load failed: %s", exc)
            return
        if not snapshot:
            return
        try:
            catalog = SchemaCatalog.model_validate(snapshot.get("catalog", self._catalog))
            profiles = [Nl2SqlProfile.model_validate(item) for item in snapshot.get("profiles", [])]
            jobs = {
                item["job_id"]: self._job_from_snapshot(item)
                for item in snapshot.get("jobs", [])
                if item.get("job_id")
            }
            history = [HistoryItem.model_validate(item) for item in snapshot.get("history", [])]
            compare_records = [
                CompareRecord.model_validate(item) for item in snapshot.get("compare_records", [])
            ]
            evaluation_sets = [
                EvaluationSet.model_validate(item) for item in snapshot.get("evaluation_sets", [])
            ]
            evaluation_runs = [
                EvaluationRunRecord.model_validate(item)
                for item in snapshot.get("evaluation_runs", [])
            ]
            asset_meta = {
                Nl2SqlEngine(engine): AssetRefreshData.model_validate(data)
                for engine, data in snapshot.get("asset_meta", {}).items()
            }
            feedback_indexed_ids = {str(item) for item in snapshot.get("feedback_indexed_ids", [])}
        except Exception as exc:
            logger.warning("NL2SQL store snapshot restore failed: %s", exc)
            return
        with self._lock:
            self._catalog = catalog
            if profiles:
                self._profiles = {profile.id: profile for profile in profiles}
            self._jobs = jobs
            self._recover_interrupted_jobs()
            self._history = history
            self._compare_records = compare_records
            self._evaluation_sets = evaluation_sets
            self._evaluation_runs = evaluation_runs
            self._feedback = {
                item.id: item.feedback_rating
                for item in history
                if item.feedback_rating is not None
            }
            self._feedback_indexed_ids = feedback_indexed_ids
            self._asset_meta = asset_meta

    def _recover_interrupted_jobs(self) -> None:
        now = _utc_now()
        for job in self._jobs.values():
            if job.status in {JobStatus.PENDING, JobStatus.RUNNING}:
                job.status = JobStatus.ERROR
                job.finished_at = job.finished_at or now
                job.error_message = (
                    "サーバ再起動前に完了しなかったため、ジョブを終了扱いにしました。"
                )

    def _persist_state(self) -> None:
        with self._lock:
            snapshot = self._snapshot_locked()
        try:
            self._store.save_snapshot(snapshot)
        except Exception as exc:  # pragma: no cover - live store defensive boundary
            logger.warning("NL2SQL store snapshot save failed: %s", exc)

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "catalog": self._catalog.model_dump(mode="json"),
            "profiles": [profile.model_dump(mode="json") for profile in self._profiles.values()],
            "jobs": [self._job_to_snapshot(job) for job in self._jobs.values()],
            "history": [item.model_dump(mode="json") for item in self._history],
            "compare_records": [item.model_dump(mode="json") for item in self._compare_records],
            "evaluation_sets": [item.model_dump(mode="json") for item in self._evaluation_sets],
            "evaluation_runs": [item.model_dump(mode="json") for item in self._evaluation_runs],
            "feedback_indexed_ids": sorted(self._feedback_indexed_ids),
            "asset_meta": {
                engine.value: data.model_dump(mode="json")
                for engine, data in self._asset_meta.items()
            },
            "saved_at": _utc_now(),
        }

    def _job_to_snapshot(self, job: StoredJob) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "request": job.request.model_dump(mode="json"),
            "status": job.status.value,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "elapsed_ms": job.elapsed_ms,
            "result": job.result.model_dump(mode="json") if job.result else None,
            "error_message": job.error_message,
            "timing": job.timing.model_dump(mode="json") if job.timing else None,
        }

    def _job_from_snapshot(self, data: dict[str, Any]) -> StoredJob:
        return StoredJob(
            job_id=str(data["job_id"]),
            request=JobCreateRequest.model_validate(data["request"]),
            status=JobStatus(data.get("status", JobStatus.PENDING)),
            created_at=str(data.get("created_at") or _utc_now()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            elapsed_ms=data.get("elapsed_ms"),
            result=Nl2SqlResult.model_validate(data["result"]) if data.get("result") else None,
            error_message=data.get("error_message"),
            timing=TimingEnvelope.model_validate(data["timing"]) if data.get("timing") else None,
        )

    def get_catalog(self) -> SchemaCatalog:
        return self._catalog

    def refresh_catalog(self) -> SchemaCatalog:
        if self._use_oracle_runtime():
            self._catalog = self._oracle_adapter.fetch_catalog()
            self._persist_state()
            return self._catalog
        self._catalog = self._build_default_catalog()
        self._persist_state()
        return self._catalog

    def list_profiles(self) -> list[Nl2SqlProfile]:
        with self._lock:
            return [profile for profile in self._profiles.values() if not profile.archived]

    def create_profile(self, profile: Nl2SqlProfile) -> Nl2SqlProfile:
        with self._lock:
            self._profiles[profile.id] = profile
        self._persist_state()
        return profile

    def update_profile(
        self, profile_id: str, patcher: Callable[[Nl2SqlProfile], Nl2SqlProfile]
    ) -> Nl2SqlProfile:
        with self._lock:
            current = self._profiles[profile_id]
            updated = patcher(current)
            self._profiles[profile_id] = updated
        self._persist_state()
        return updated

    def archive_profile(self, profile_id: str) -> Nl2SqlProfile:
        return self.update_profile(profile_id, lambda p: p.model_copy(update={"archived": True}))

    def get_profile(self, profile_id: str | None) -> Nl2SqlProfile:
        with self._lock:
            if profile_id and profile_id in self._profiles:
                return self._profiles[profile_id]
            return self._profiles["default"]

    def start_job(self, request: JobCreateRequest) -> JobCreateData:
        job_id = str(uuid.uuid4())
        job = StoredJob(job_id=job_id, request=request)
        with self._lock:
            self._jobs[job_id] = job
        self._persist_state()
        thread = threading.Thread(target=self._run_job_safely, args=(job_id,), daemon=True)
        thread.start()
        return JobCreateData(job_id=job_id, status=job.status, created_at=job.created_at)

    def get_job(self, job_id: str) -> JobData | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return JobData(
                job_id=job.job_id,
                status=job.status,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                elapsed_ms=job.elapsed_ms,
                result=job.result,
                error_message=job.error_message,
                timing=job.timing,
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
        )
        row_limit = self._resolve_row_limit(request.profile_id, request.row_limit)
        analysis = self.analyze_sql(generated.generated_sql, allowed, row_limit)
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
            row_limit=row_limit,
            note=f"質問を受領しました: {request.question[:80]}",
            engine=generated.engine,
            engine_meta=generated.engine_meta,
            fallback_reason=generated.fallback_reason,
            rewritten_question=self.rewrite_question(
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
        row_limit: int,
    ) -> tuple[SafetyReport, str, QueryResults]:
        executable = enforce_row_limit(sql, row_limit)
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

    def analyze_sql(self, sql: str, allowed: AllowedObjects, row_limit: int) -> AnalyzeData:
        referenced = _extract_referenced_tables(sql)
        referenced_columns, has_wildcard = _extract_referenced_columns(sql, referenced)
        select_only = is_select_only(sql)
        warnings: list[str] = []
        blocked_reason = ""
        if not select_only:
            blocked_reason = (
                "SELECT/WITH 以外、複数 statement、または危険語を含む SQL は実行できません。"
            )
        if not _table_allowed(referenced, allowed):
            blocked_reason = "許可されていない表を参照しています。"
        if not _column_allowed(referenced_columns, has_wildcard, referenced, allowed):
            blocked_reason = "許可されていない列を参照しています。"
        if re.search(r"\s+limit\s+\d+\s*;?\s*$", sql, flags=re.IGNORECASE):
            warnings.append("Oracle では LIMIT ではなく FETCH FIRST n ROWS ONLY を使用します。")
        elif "fetch first" not in sql.lower():
            warnings.append("行数制限が見つからないため実行時に FETCH FIRST を付与します。")
        if sql.strip().endswith(";") and ";" not in sql.strip().rstrip(";"):
            warnings.append("API 実行時は末尾のセミコロンを除去します。")
        if has_wildcard and allowed.columns:
            warnings.append("列選択が制限されているため、SELECT * は実行できません。")
        safety = SafetyReport(
            is_safe=not blocked_reason,
            is_select_only=select_only,
            row_limit_applied=row_limit,
            blocked_reason=blocked_reason,
            warnings=warnings,
            referenced_tables=referenced,
            referenced_columns=referenced_columns,
        )
        executable_sql = enforce_row_limit(sql, row_limit) if select_only else ""
        repaired_sql = self._repair_sql(
            sql=sql,
            safety=safety,
            allowed=allowed,
            row_limit=row_limit,
            referenced_tables=referenced,
            referenced_columns=referenced_columns,
            has_wildcard=has_wildcard,
        )
        return AnalyzeData(
            safety=safety,
            explanation=(
                "SQL は参照系クエリとして解析されました。" if safety.is_safe else blocked_reason
            ),
            recommendations=self._recommendations(safety, repaired_sql, sql=sql, allowed=allowed),
            executable_sql=executable_sql,
            repaired_sql=repaired_sql,
            optimization_hints=self._optimization_hints(
                safety=safety, sql=sql, row_limit=row_limit
            ),
        )

    def repair_oracle_error(self, request: RepairRequest, row_limit: int) -> RepairData:
        """Oracle error message をヒントに SELECT SQL の修復候補を返す。"""
        error_code = self._oracle_error_code(request.error_message)
        base = self.analyze_sql(request.sql, request.allowed_objects, row_limit)
        referenced = base.safety.referenced_tables
        repaired_sql = self._repair_sql_for_oracle_error(
            sql=request.sql,
            error_code=error_code,
            allowed=request.allowed_objects,
            row_limit=row_limit,
            referenced_tables=referenced,
        )
        if not repaired_sql:
            repaired_sql = base.repaired_sql or base.executable_sql
        if repaired_sql:
            repaired = self.analyze_sql(repaired_sql, request.allowed_objects, row_limit)
            safety = repaired.safety
            executable_sql = repaired.executable_sql
            recommendations = self._oracle_error_recommendations(
                error_code=error_code,
                fallback_recommendations=repaired.recommendations,
            )
        else:
            safety = base.safety
            executable_sql = ""
            recommendations = self._oracle_error_recommendations(
                error_code=error_code,
                fallback_recommendations=base.recommendations,
            )
        return RepairData(
            error_code=error_code,
            repaired_sql=repaired_sql,
            explanation=self._oracle_error_explanation(error_code),
            recommendations=recommendations,
            safety=safety,
            executable_sql=executable_sql,
        )

    def list_history(self) -> HistoryData:
        with self._lock:
            return HistoryData(items=list(reversed(self._history[-50:])))

    def save_feedback(
        self, history_id: str, rating: FeedbackRating, comment: str = ""
    ) -> FeedbackData:
        with self._lock:
            self._feedback[history_id] = rating
            self._history = [
                (
                    item.model_copy(update={"feedback_rating": rating, "feedback_comment": comment})
                    if item.id == history_id
                    else item
                )
                for item in self._history
            ]
        self._persist_state()
        return FeedbackData(history_id=history_id, rating=rating, saved=True, comment=comment)

    def seed_demo_learning_data(self) -> DemoLearningData:
        """学習/feedback 機能をすぐ検証できる demo 履歴を投入する。"""
        now = _utc_now()
        profile = self.get_profile("default")
        demo_items = [
            HistoryItem(
                id="demo-learning-invoice-total",
                question="今月の請求金額が大きい取引先を見たい",
                engine=Nl2SqlEngine.SELECT_AI_AGENT,
                generated_sql=(
                    "SELECT c.CUSTOMER_NAME, SUM(i.TOTAL_AMOUNT) AS TOTAL_AMOUNT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "GROUP BY c.CUSTOMER_NAME ORDER BY TOTAL_AMOUNT DESC"
                ),
                created_at=now,
                elapsed_ms=842,
                feedback_rating=FeedbackRating.GOOD,
                profile_id=profile.id,
                profile_name=profile.name,
                rewritten_question="今月の請求金額合計が大きい取引先を降順で確認する",
                executable_sql=(
                    "SELECT c.CUSTOMER_NAME, SUM(i.TOTAL_AMOUNT) AS TOTAL_AMOUNT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "GROUP BY c.CUSTOMER_NAME ORDER BY TOTAL_AMOUNT DESC "
                    "FETCH FIRST 100 ROWS ONLY"
                ),
                result_row_count=5,
                result_columns=["CUSTOMER_NAME", "TOTAL_AMOUNT"],
                feedback_comment="集計軸と並び順が期待通り。few-shot に利用できる。",
            ),
            HistoryItem(
                id="demo-learning-customer-sales",
                question="顧客別の売上推移を確認したい",
                engine=Nl2SqlEngine.SELECT_AI,
                generated_sql=(
                    "SELECT c.CUSTOMER_NAME, TRUNC(i.INVOICE_DATE, 'MM') AS SALES_MONTH, "
                    "SUM(i.TOTAL_AMOUNT) AS SALES_AMOUNT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "GROUP BY c.CUSTOMER_NAME, TRUNC(i.INVOICE_DATE, 'MM')"
                ),
                created_at=now,
                elapsed_ms=706,
                feedback_rating=FeedbackRating.GOOD,
                profile_id=profile.id,
                profile_name=profile.name,
                rewritten_question="顧客別・月別に請求金額合計を集計する",
                executable_sql=(
                    "SELECT c.CUSTOMER_NAME, TRUNC(i.INVOICE_DATE, 'MM') AS SALES_MONTH, "
                    "SUM(i.TOTAL_AMOUNT) AS SALES_AMOUNT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "GROUP BY c.CUSTOMER_NAME, TRUNC(i.INVOICE_DATE, 'MM') "
                    "FETCH FIRST 100 ROWS ONLY"
                ),
                result_row_count=12,
                result_columns=["CUSTOMER_NAME", "SALES_MONTH", "SALES_AMOUNT"],
                feedback_comment="顧客別・月別の粒度が正しい。",
            ),
            HistoryItem(
                id="demo-learning-payment-delay",
                question="入金が遅れている請求を確認したい",
                engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
                generated_sql=(
                    "SELECT i.INVOICE_ID, c.CUSTOMER_NAME, i.DUE_DATE, p.PAID_AT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "LEFT JOIN PAYMENTS p ON p.INVOICE_ID = i.INVOICE_ID "
                    "WHERE p.PAID_AT IS NULL OR p.PAID_AT > i.DUE_DATE"
                ),
                created_at=now,
                elapsed_ms=918,
                feedback_rating=FeedbackRating.NEEDS_REVIEW,
                profile_id=profile.id,
                profile_name=profile.name,
                rewritten_question="支払期日を過ぎた未入金または遅延入金の請求を確認する",
                executable_sql=(
                    "SELECT i.INVOICE_ID, c.CUSTOMER_NAME, i.DUE_DATE, p.PAID_AT "
                    "FROM INVOICES i JOIN CUSTOMERS c ON c.CUSTOMER_ID = i.CUSTOMER_ID "
                    "LEFT JOIN PAYMENTS p ON p.INVOICE_ID = i.INVOICE_ID "
                    "WHERE p.PAID_AT IS NULL OR p.PAID_AT > i.DUE_DATE "
                    "FETCH FIRST 100 ROWS ONLY"
                ),
                result_row_count=3,
                result_columns=["INVOICE_ID", "CUSTOMER_NAME", "DUE_DATE", "PAID_AT"],
                feedback_comment="遅延条件は妥当。業務上の猶予日数があれば追加したい。",
            ),
        ]
        with self._lock:
            existing_ids = {item.id for item in self._history}
            new_items = [item for item in demo_items if item.id not in existing_ids]
            self._history.extend(new_items)
            for item in demo_items:
                if item.feedback_rating is not None:
                    self._feedback[item.id] = item.feedback_rating
            if new_items:
                self._feedback_indexed_ids.difference_update(item.id for item in new_items)
        if new_items:
            self._persist_state()
        return DemoLearningData(
            seeded_history_count=len(new_items),
            seeded_feedback_count=sum(1 for item in new_items if item.feedback_rating is not None),
            history_ids=[item.id for item in new_items],
            profile_ids=[profile.id],
            message=(
                "Demo 学習データを投入しました。"
                if new_items
                else "Demo 学習データは投入済みです。"
            ),
        )

    def feedback_index_status(self) -> FeedbackIndexData:
        return self._feedback_index_data(operation="status", execute=False, include_bad=False)

    def rebuild_feedback_index(self, request: FeedbackIndexRequest) -> FeedbackIndexData:
        return self._feedback_index_data(
            operation="rebuild", execute=request.execute, include_bad=request.include_bad
        )

    def clear_feedback_index(self, request: FeedbackIndexRequest) -> FeedbackIndexData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        executed = False
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        with self._lock:
            source_count = len(self._history)
            indexable_count = len(self._feedback_indexable_history(request.include_bad))
            current_indexed = len(self._feedback_indexed_ids)
        if request.execute:
            if not self._use_oracle_runtime():
                warnings.append(
                    "Feedback vector index の clear 実行には "
                    "NL2SQL_RUNTIME_MODE=oracle が必要です。"
                )
            else:
                try:
                    settings = get_settings()
                    self._oracle_adapter.clear_feedback_vector_index(
                        table_name=settings.nl2sql_feedback_vector_table,
                        index_name=settings.nl2sql_feedback_vector_index,
                    )
                    with self._lock:
                        self._feedback_indexed_ids = set()
                    executed = True
                    self._persist_state()
                except OracleAdapterError as exc:
                    warnings.append(str(exc))
        else:
            warnings.append("Dry-run のため feedback index は削除していません。")
        embedding_configured = self._embedding_client.is_configured()
        settings = get_settings()
        return FeedbackIndexData(
            operation="clear",
            status=(
                "empty"
                if executed
                else self._feedback_index_status(current_indexed, indexable_count)
            ),
            executed=executed,
            runtime=runtime,
            source_history_count=source_count,
            indexable_count=indexable_count,
            indexed_count=0 if executed else current_indexed,
            ddl=self._feedback_index_ddl(),
            embedding_model=settings.oci_genai_embed_model_id,
            embedding_configured=embedding_configured,
            warnings=warnings,
            timing=self._timing(created_at, started, "feedback_index"),
        )

    def similar_history(self, request: SimilarHistoryRequest) -> SimilarHistoryData:
        ranked = self._similar_history_candidates(
            question=request.question,
            profile_id=request.profile_id,
            include_bad=False,
        )
        return SimilarHistoryData(items=ranked[: request.limit])

    def _feedback_index_data(
        self, *, operation: str, execute: bool, include_bad: bool
    ) -> FeedbackIndexData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        with self._lock:
            indexable = self._feedback_indexable_history(include_bad)
            source_count = len(self._history)
            indexed_count = len(self._feedback_indexed_ids)
        executed = False
        if operation == "rebuild" and execute:
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
                                item.feedback_rating.value if item.feedback_rating else ""
                            ),
                            "embedding": vector,
                        }
                        for item, vector in zip(indexable, vectors, strict=True)
                    ]
                    self._oracle_adapter.rebuild_feedback_vector_index(
                        table_name=settings.nl2sql_feedback_vector_table,
                        index_name=settings.nl2sql_feedback_vector_index,
                        rows=rows,
                    )
                    with self._lock:
                        self._feedback_indexed_ids = {item.id for item in indexable}
                        indexed_count = len(self._feedback_indexed_ids)
                    executed = True
                    self._persist_state()
                except (EmbeddingClientError, OracleAdapterError, ValueError) as exc:
                    warnings.append(str(exc))
        elif operation == "rebuild":
            warnings.append("Dry-run のため feedback index は再構築していません。")
            indexed_count = len(indexable)
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

    def _feedback_indexable_history(self, include_bad: bool) -> list[HistoryItem]:
        return [
            item
            for item in self._history
            if item.feedback_rating and (include_bad or item.feedback_rating != FeedbackRating.BAD)
        ]

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
        return "\n".join(
            [
                f"question: {item.question}",
                f"rewritten_question: {item.rewritten_question}",
                f"sql: {item.generated_sql}",
                f"feedback: {item.feedback_rating.value if item.feedback_rating else ''}",
                f"comment: {item.feedback_comment}",
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

    def recommend_profile(self, request: ProfileRecommendationRequest) -> ProfileRecommendationData:
        profiles = self.list_profiles()
        if not profiles:
            profile = self.get_profile(request.current_profile_id)
            return self._recommendation_from_profile(
                profile=profile,
                question=request.question,
                score=0.0,
                matched_terms=[],
                candidates=[],
            )

        scored: list[tuple[float, Nl2SqlProfile, list[str]]] = []
        for profile in profiles:
            score, matched_terms = self._score_profile_for_question(profile, request.question)
            if profile.id == request.current_profile_id:
                score += 0.2
            scored.append((score, profile, matched_terms))
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_profile, best_terms = scored[0]
        candidates = [
            ProfileRecommendationCandidate(
                profile_id=profile.id,
                profile_name=profile.name,
                score=round(score, 3),
                matched_terms=terms[:8],
                allowed_tables=profile.allowed_tables,
            )
            for score, profile, terms in scored[:3]
        ]
        return self._recommendation_from_profile(
            profile=best_profile,
            question=request.question,
            score=best_score,
            matched_terms=best_terms,
            candidates=candidates,
        )

    def evaluate(self, request: EvaluateRequest) -> EvaluateData:
        profile, evaluation_set_id, evaluation_set_name = self._evaluation_context(request)
        cases = self._evaluation_cases_from_request(request, profile.id)
        total = len(cases)
        if total == 0:
            data = EvaluateData(
                evaluation_suite="deterministic_mock",
                total_cases=0,
                executable_rate=0.0,
                select_only_rate=0.0,
                findings=["評価ケースがありません。"],
            )
            self._save_evaluation_run(
                request=request,
                data=data,
                cases=cases,
                profile=profile,
                evaluation_set_id=evaluation_set_id,
                evaluation_set_name=evaluation_set_name,
            )
            return data
        select_only = 0
        executable = 0
        for case in cases:
            preview = self.preview(
                PreviewRequest(
                    question=case.question,
                    engine=request.engine,
                    allowed_objects=AllowedObjects(),
                )
            )
            if preview.safety and preview.safety.is_select_only:
                select_only += 1
            if preview.is_safe:
                executable += 1
        data = EvaluateData(
            evaluation_suite="deterministic_mock",
            total_cases=total,
            executable_rate=round(executable / total, 3),
            select_only_rate=round(select_only / total, 3),
            findings=(
                []
                if executable == total
                else ["一部のケースで安全境界により実行不可になりました。"]
            ),
        )
        self._save_evaluation_run(
            request=request,
            data=data,
            cases=cases,
            profile=profile,
            evaluation_set_id=evaluation_set_id,
            evaluation_set_name=evaluation_set_name,
        )
        return data

    def list_evaluation_runs(self, limit: int = 20) -> EvaluationRunsData:
        with self._lock:
            items = list(reversed(self._evaluation_runs[-limit:]))
        return EvaluationRunsData(items=items)

    def _evaluation_context(self, request: EvaluateRequest) -> tuple[Nl2SqlProfile, str, str]:
        evaluation_set = self._find_evaluation_set(request.evaluation_set_id)
        profile = self.get_profile(
            request.profile_id or (evaluation_set.profile_id if evaluation_set else None)
        )
        return (
            profile,
            evaluation_set.id if evaluation_set else request.evaluation_set_id or "",
            evaluation_set.name if evaluation_set else "",
        )

    def _find_evaluation_set(self, evaluation_set_id: str | None) -> EvaluationSet | None:
        if not evaluation_set_id:
            return None
        with self._lock:
            return next(
                (item for item in self._evaluation_sets if item.id == evaluation_set_id),
                None,
            )

    def _evaluation_cases_from_request(
        self, request: EvaluateRequest, profile_id: str
    ) -> list[SyntheticCase]:
        cases: list[SyntheticCase] = []
        for case in request.cases:
            question = str(case.get("question") or "").strip()
            expected_sql = str(case.get("expected_sql") or case.get("sql") or "").strip()
            if not question and not expected_sql:
                continue
            cases.append(
                SyntheticCase(
                    question=question,
                    expected_sql=expected_sql,
                    profile_id=profile_id,
                )
            )
        return cases

    def _save_evaluation_run(
        self,
        *,
        request: EvaluateRequest,
        data: EvaluateData,
        cases: list[SyntheticCase],
        profile: Nl2SqlProfile,
        evaluation_set_id: str,
        evaluation_set_name: str,
    ) -> None:
        record = EvaluationRunRecord(
            id=str(uuid.uuid4()),
            created_at=_utc_now(),
            evaluation_set_id=evaluation_set_id,
            evaluation_set_name=evaluation_set_name,
            profile_id=profile.id,
            profile_name=profile.name,
            engine=request.engine,
            cases=cases,
            result=data,
            report=self._evaluation_report_text(
                data=data,
                engine=request.engine,
                profile=profile,
                evaluation_set_name=evaluation_set_name,
            ),
        )
        with self._lock:
            self._evaluation_runs.append(record)
            self._evaluation_runs = self._evaluation_runs[-100:]
        self._persist_state()

    def _evaluation_report_text(
        self,
        *,
        data: EvaluateData,
        engine: Nl2SqlEngine,
        profile: Nl2SqlProfile,
        evaluation_set_name: str,
    ) -> str:
        lines = [
            "NL2SQL deterministic evaluation",
            f"Suite: {data.evaluation_suite}",
            f"Evaluation set: {evaluation_set_name or '-'}",
            f"Profile: {profile.name}",
            f"Engine: {engine.value}",
            f"Cases: {data.total_cases}",
            f"Executable rate: {round(data.executable_rate * 100)}%",
            f"SELECT-only rate: {round(data.select_only_rate * 100)}%",
        ]
        if data.findings:
            lines.extend(["", "Findings:", *[f"- {item}" for item in data.findings]])
        return "\n".join(lines)

    def list_evaluation_sets(self, include_archived: bool = False) -> EvaluationSetsData:
        with self._lock:
            items = [
                item for item in self._evaluation_sets if include_archived or not item.archived
            ]
        return EvaluationSetsData(items=list(reversed(items)))

    def create_evaluation_set(self, request: EvaluationSetUpsertRequest) -> EvaluationSet:
        now = _utc_now()
        evaluation_set = self._evaluation_set_from_request(
            evaluation_set_id=str(uuid.uuid4()),
            request=request,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        with self._lock:
            self._evaluation_sets.append(evaluation_set)
        self._persist_state()
        return evaluation_set

    def update_evaluation_set(
        self, evaluation_set_id: str, request: EvaluationSetUpsertRequest
    ) -> EvaluationSet:
        with self._lock:
            current = next(
                (item for item in self._evaluation_sets if item.id == evaluation_set_id),
                None,
            )
            if current is None:
                raise KeyError(evaluation_set_id)
            updated = self._evaluation_set_from_request(
                evaluation_set_id=evaluation_set_id,
                request=request,
                created_at=current.created_at,
                updated_at=_utc_now(),
                archived=current.archived,
            )
            self._evaluation_sets = [
                updated if item.id == evaluation_set_id else item for item in self._evaluation_sets
            ]
        self._persist_state()
        return updated

    def archive_evaluation_set(self, evaluation_set_id: str) -> EvaluationSet:
        with self._lock:
            current = next(
                (item for item in self._evaluation_sets if item.id == evaluation_set_id),
                None,
            )
            if current is None:
                raise KeyError(evaluation_set_id)
            archived = current.model_copy(update={"archived": True, "updated_at": _utc_now()})
            self._evaluation_sets = [
                archived if item.id == evaluation_set_id else item for item in self._evaluation_sets
            ]
        self._persist_state()
        return archived

    def _evaluation_set_from_request(
        self,
        *,
        evaluation_set_id: str,
        request: EvaluationSetUpsertRequest,
        created_at: str,
        updated_at: str,
        archived: bool,
    ) -> EvaluationSet:
        profile_id = (
            request.profile_id
            or next((case.profile_id for case in request.cases if case.profile_id), None)
            or "default"
        )
        profile = self.get_profile(profile_id)
        cases = [
            case.model_copy(update={"profile_id": profile.id})
            for case in request.cases
            if case.question.strip() and case.expected_sql.strip()
        ]
        return EvaluationSet(
            id=evaluation_set_id,
            name=request.name.strip(),
            description=request.description.strip(),
            profile_id=profile.id,
            profile_name=profile.name,
            engine=request.engine,
            cases=cases,
            created_at=created_at,
            updated_at=updated_at,
            archived=archived,
        )

    def compare_engines(self, request: CompareRequest) -> CompareData:
        results: list[PreviewData] = []
        execution_results: list[CompareExecutionData] = []
        engines = [engine for engine in request.engines if engine != Nl2SqlEngine.AUTO]
        if not engines:
            engines = [Nl2SqlEngine.SELECT_AI_AGENT, Nl2SqlEngine.SELECT_AI]
        allowed = self._resolve_allowed_objects(request.profile_id, request.allowed_objects)
        row_limit = self._resolve_row_limit(request.profile_id, request.row_limit)
        for engine in engines[:3]:
            results.append(
                self.preview(
                    PreviewRequest(
                        question=request.question,
                        engine=engine,
                        profile_id=request.profile_id,
                        allowed_objects=request.allowed_objects,
                        row_limit=request.row_limit,
                    )
                )
            )
        if request.execute:
            for result in results:
                started = time.monotonic()
                if not result.is_safe or not result.executable_sql:
                    execution_results.append(
                        CompareExecutionData(
                            engine=result.engine,
                            executed=False,
                            row_count=0,
                            error_message=(
                                result.safety.blocked_reason
                                if result.safety and result.safety.blocked_reason
                                else "安全境界により実行しませんでした。"
                            ),
                            elapsed_ms=_elapsed_ms(started),
                        )
                    )
                    continue
                try:
                    safety, _executable, query_results = self.execute_sql(
                        result.executable_sql, allowed, row_limit
                    )
                    if not safety.is_safe:
                        execution_results.append(
                            CompareExecutionData(
                                engine=result.engine,
                                executed=False,
                                row_count=0,
                                error_message=safety.blocked_reason,
                                elapsed_ms=_elapsed_ms(started),
                            )
                        )
                        continue
                    execution_results.append(
                        CompareExecutionData(
                            engine=result.engine,
                            executed=True,
                            row_count=query_results.total,
                            results=query_results,
                            elapsed_ms=_elapsed_ms(started),
                        )
                    )
                except Exception as exc:  # pragma: no cover - Oracle 実行時の安全網
                    logger.warning(
                        "NL2SQL compare execution failed",
                        extra={"engine": result.engine.value},
                        exc_info=True,
                    )
                    execution_results.append(
                        CompareExecutionData(
                            engine=result.engine,
                            executed=False,
                            row_count=0,
                            error_message=str(exc),
                            elapsed_ms=_elapsed_ms(started),
                        )
                    )
        safe_results = [result for result in results if result.is_safe]
        fastest = min(
            safe_results,
            key=lambda result: (
                result.timing.elapsed_ms
                if result.timing and result.timing.elapsed_ms is not None
                else 999_999
            ),
            default=None,
        )
        recommendation = (
            f"{fastest.engine.value} は安全に生成でき、処理時間が最短でした。"
            if fastest
            else "安全に生成できたエンジンがありません。"
        )
        execution_errors = [item for item in execution_results if not item.executed]
        error_rate = (
            round(len(execution_errors) / len(execution_results), 3) if execution_results else 0.0
        )
        data = CompareData(
            question=request.question,
            results=results,
            execution_results=execution_results,
            error_rate=error_rate,
            recommendation=recommendation,
        )
        self._save_compare_record(request=request, data=data, engines=engines)
        return data

    def list_compare_records(self, limit: int = 20) -> CompareHistoryData:
        with self._lock:
            items = list(reversed(self._compare_records[-limit:]))
        return CompareHistoryData(items=items)

    def _save_compare_record(
        self, *, request: CompareRequest, data: CompareData, engines: Sequence[Nl2SqlEngine]
    ) -> None:
        profile = self.get_profile(request.profile_id)
        record = CompareRecord(
            id=str(uuid.uuid4()),
            created_at=_utc_now(),
            profile_id=profile.id,
            profile_name=profile.name,
            question=request.question,
            engines=list(engines),
            execute=request.execute,
            report=self._compare_report_text(data),
            comparison=data,
        )
        with self._lock:
            self._compare_records.append(record)
            self._compare_records = self._compare_records[-50:]
        self._persist_state()

    def _compare_report_text(self, data: CompareData) -> str:
        lines = [
            "NL2SQL engine comparison",
            f"Question: {data.question}",
            f"Recommendation: {data.recommendation}",
            f"Error rate: {round(data.error_rate * 100)}%",
            "",
        ]
        for result in data.results:
            execution = next(
                (item for item in data.execution_results if item.engine == result.engine),
                None,
            )
            execution_text = "not executed"
            if execution:
                execution_text = (
                    f"{execution.row_count} rows"
                    if execution.executed
                    else execution.error_message or "not executed"
                )
            elapsed = (
                f"{result.timing.elapsed_ms}ms"
                if result.timing and result.timing.elapsed_ms is not None
                else "-"
            )
            safety = result.safety
            lines.extend(
                [
                    f"## {result.engine.value}",
                    f"Safe: {'yes' if result.is_safe else 'no'}",
                    f"Elapsed: {elapsed}",
                    f"Row limit: {result.row_limit}",
                    "Tables: " + (", ".join(safety.referenced_tables) if safety else "-"),
                    "Columns: " + (", ".join(safety.referenced_columns) if safety else "-"),
                    f"Execution: {execution_text}",
                    f"SQL: {one_line_sql(result.executable_sql or result.sql)}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def reverse_sql(self, request: ReverseSqlRequest) -> ReverseSqlData:
        referenced = _extract_referenced_tables(request.sql)
        table_names = ", ".join(referenced) if referenced else "指定表"
        return ReverseSqlData(
            question=f"{table_names} のデータを条件に沿って確認したい",
            explanation="SELECT 句・FROM/JOIN 句・行数制限をもとに自然言語説明を生成しました。",
            referenced_tables=referenced,
        )

    def suggest_comments(self) -> CommentSuggestionData:
        suggestions: list[CommentSuggestion] = []
        for table in self._catalog.tables:
            suggestions.append(
                CommentSuggestion(
                    object_name=table.table_name,
                    object_type="table",
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
        return CommentSuggestionData(suggestions=suggestions)

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
        runtime = "deterministic"
        if request.execute and statements:
            if not self._use_oracle_runtime():
                warnings.append("COMMENT ON の実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
                statements = [
                    statement.model_copy(update={"status": "requires_oracle"})
                    for statement in statements
                ]
            else:
                runtime = "oracle"
                try:
                    self._oracle_adapter.apply_comment_statements(
                        [statement.sql for statement in statements]
                    )
                    executed = True
                    statements = [
                        statement.model_copy(update={"status": "applied"})
                        for statement in statements
                    ]
                    try:
                        self._catalog = self._oracle_adapter.fetch_catalog()
                    except OracleAdapterError as exc:
                        warnings.append(f"COMMENT 適用後の catalog refresh に失敗しました: {exc}")
                except OracleAdapterError as exc:
                    warnings.append(str(exc))
                    statements = [
                        statement.model_copy(update={"status": "error", "error_message": str(exc)})
                        for statement in statements
                    ]
        elif request.execute and not statements:
            warnings.append("適用対象の COMMENT がありません。")

        if not request.items:
            warnings.append("COMMENT 対象が指定されていません。")

        finished_at = _utc_now()
        return CommentApplyData(
            executed=executed,
            runtime=runtime,
            statements=statements,
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
        if object_type == "table":
            table = self._find_catalog_table(item.object_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない table です。")
            return CommentApplyStatement(
                object_name=table.table_name,
                object_type="table",
                comment=comment,
                sql=(
                    f"COMMENT ON TABLE {_quote_identifier(table.table_name)} "
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
            return CommentApplyStatement(
                object_name=f"{table.table_name}.{column.column_name}",
                object_type="column",
                comment=comment,
                sql=(
                    f"COMMENT ON COLUMN {_quote_identifier(table.table_name)}."
                    f"{_quote_identifier(column.column_name)} IS {_quote_sql_string(comment)};"
                ),
            )
        raise ValueError(
            f"{item.object_name}: object_type は table または column のみ指定できます。"
        )

    def _split_comment_column_name(self, object_name: str) -> tuple[str, str]:
        parts = [part.strip() for part in object_name.split(".") if part.strip()]
        if len(parts) != 2:
            raise ValueError(f"{object_name}: column は TABLE.COLUMN 形式で指定してください。")
        return parts[0], parts[1]

    def _find_catalog_table(self, table_name: str) -> SchemaTable | None:
        normalized = _normalize_identifier(table_name)
        return next(
            (table for table in self._catalog.tables if table.table_name == normalized),
            None,
        )

    def _find_catalog_column(self, table: SchemaTable, column_name: str) -> SchemaColumn | None:
        normalized = _normalize_identifier(column_name)
        return next(
            (column for column in table.columns if column.column_name == normalized),
            None,
        )

    def synthetic_cases(self, profile_id: str | None = None, limit: int = 6) -> SyntheticCasesData:
        profile = self.get_profile(profile_id)
        cases: list[SyntheticCase] = []
        for table in self._catalog.tables:
            if profile.allowed_tables and table.table_name not in profile.allowed_tables:
                continue
            amount_column = next(
                (column for column in table.columns if "AMOUNT" in column.column_name),
                table.columns[0],
            )
            cases.append(
                SyntheticCase(
                    question=f"{table.logical_name} の {amount_column.logical_name} を確認したい",
                    # Safe: synthetic example SQL is generated for evaluation display, not executed.
                    expected_sql=f"SELECT {amount_column.column_name} FROM {table.table_name}",  # nosec B608
                    profile_id=profile.id,
                )
            )
            if len(cases) >= limit:
                break
        return SyntheticCasesData(cases=cases)

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
                endpoint="/api/nl2sql/select-ai/profiles/refresh?profile_id=default",
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
                endpoint="/api/nl2sql/select-ai-agent/assets/refresh?profile_id=default",
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
                request_hint='{"engine":"select_ai","question":"請求金額を確認したい"}',
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
                request_hint='{"engine":"select_ai_agent","question":"請求金額を確認したい"}',
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
                    '{"engine":"enterprise_ai_direct","question":"請求金額を確認したい"}'
                ),
                expected=(
                    "engine=enterprise_ai_direct, provider=enterprise_ai_direct, "
                    "SQL が返ること。"
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
                    "executed=true, VECTOR(1536, FLOAT32) index が Oracle 26ai に"
                    "作成されること。"
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

    def import_csv_sample(self, request: CsvImportRequest) -> CsvImportData:
        started = time.monotonic()
        created_at = _utc_now()
        settings = get_settings()
        row_limit = request.max_rows or settings.nl2sql_csv_import_max_rows
        columns, rows, warnings = self._parse_csv_sample(
            table_name=request.table_name,
            csv_text=request.csv_text,
            max_rows=min(row_limit, settings.nl2sql_csv_import_max_rows),
            max_columns=settings.nl2sql_csv_import_max_columns,
        )
        table_name = self._sanitize_import_table_name(request.table_name)
        ddl = self._csv_import_ddl(table_name, columns)
        insert_sql = self._csv_import_insert_sql(table_name, columns)
        executed = False
        if request.execute:
            if self._use_oracle_runtime():
                self._oracle_adapter.import_csv_table(
                    table_name=table_name,
                    columns=columns,
                    rows=rows,
                    replace_existing=request.replace_existing,
                )
                executed = True
                try:
                    self._catalog = self._oracle_adapter.fetch_catalog()
                    self._persist_state()
                except OracleAdapterError as exc:
                    warnings.append(f"import 後の schema refresh に失敗しました: {exc}")
            else:
                warnings.append(
                    "deterministic runtime のため dry-run として返しました。"
                    "実投入には NL2SQL_RUNTIME_MODE=oracle が必要です。"
                )
        finished_at = _utc_now()
        return CsvImportData(
            table_name=table_name,
            columns=columns,
            row_count=len(rows),
            dry_run=not executed,
            executed=executed,
            ddl=ddl,
            insert_sql=insert_sql,
            warnings=warnings,
            sample_rows=rows[:5],
            timing=TimingEnvelope(
                created_at=created_at,
                started_at=created_at,
                finished_at=finished_at,
                elapsed_ms=_elapsed_ms(started),
                stage_timings=[StageTiming(stage="parse_csv", elapsed_ms=_elapsed_ms(started))],
            ),
        )

    def refresh_select_ai_profile(self, profile_id: str | None) -> AssetRefreshData:
        profile = self.get_profile(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        warning = ""
        refreshed = True
        status = "ready"
        engine_meta: dict[str, Any] = {
            "allowed_tables": profile.allowed_tables,
            "use_comments": True,
            "use_constraints": True,
            "runtime": "deterministic",
        }
        if self._use_oracle_runtime():
            try:
                engine_meta.update(
                    self._oracle_adapter.refresh_select_ai_profile(
                        profile_name=profile_name,
                        allowed_tables=profile.allowed_tables,
                        row_limit=profile.default_row_limit,
                        description=profile.description,
                    )
                )
            except OracleAdapterError as exc:
                refreshed = False
                status = "error"
                warning = str(exc)
        data = AssetRefreshData(
            engine=Nl2SqlEngine.SELECT_AI,
            refreshed=refreshed,
            status=status,
            refreshed_at=_utc_now(),
            profile_name=profile_name,
            warning=warning,
            asset_names={"profile": profile_name},
            engine_meta=engine_meta,
        )
        with self._lock:
            self._asset_meta[Nl2SqlEngine.SELECT_AI] = data
        self._persist_state()
        return data

    def _parse_csv_sample(
        self,
        *,
        table_name: str,
        csv_text: str,
        max_rows: int,
        max_columns: int,
    ) -> tuple[list[CsvImportColumn], list[dict[str, str | None]], list[str]]:
        self._sanitize_import_table_name(table_name)
        warnings: list[str] = []
        text = csv_text.lstrip("\ufeff")
        try:
            dialect = csv.Sniffer().sniff(text[:2048])
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV header が見つかりません。") from exc
        if not raw_header or all(not cell.strip() for cell in raw_header):
            raise ValueError("CSV header が空です。")
        if len(raw_header) > max_columns:
            warnings.append(
                f"列数が上限 {max_columns} を超えたため、先頭 {max_columns} 列だけを使用します。"
            )
            raw_header = raw_header[:max_columns]
        column_names = self._dedupe_csv_column_names(raw_header)
        raw_rows: list[list[str]] = []
        truncated = False
        for index, row in enumerate(reader):
            if index >= max_rows:
                truncated = True
                break
            raw_rows.append(row[: len(column_names)])
        if truncated:
            warnings.append(
                f"行数が上限 {max_rows} を超えたため、先頭 {max_rows} 行だけを使用します。"
            )
        columns = [
            CsvImportColumn(
                source_name=raw_header[index].strip() or f"column_{index + 1}",
                column_name=column_name,
                data_type=self._infer_csv_data_type(
                    [row[index] if index < len(row) else "" for row in raw_rows]
                ),
                nullable=any(
                    (row[index] if index < len(row) else "").strip() == "" for row in raw_rows
                ),
            )
            for index, column_name in enumerate(column_names)
        ]
        rows = [
            {
                column.column_name: self._normalize_csv_cell(row[index] if index < len(row) else "")
                for index, column in enumerate(columns)
            }
            for row in raw_rows
            if any(cell.strip() for cell in row)
        ]
        if not rows:
            warnings.append("データ行がありません。DDL の dry-run のみ生成しました。")
        return columns, rows, warnings

    def _sanitize_import_table_name(self, table_name: str) -> str:
        normalized = _csv_identifier(table_name, "CSV_IMPORT")
        if not _STRICT_IDENTIFIER.fullmatch(normalized):
            raise ValueError("table_name は英数字と underscore の Oracle 識別子へ変換できません。")
        return normalized

    def _dedupe_csv_column_names(self, raw_header: list[str]) -> list[str]:
        seen: dict[str, int] = {}
        names: list[str] = []
        for index, source_name in enumerate(raw_header):
            base = _csv_identifier(source_name, f"COLUMN_{index + 1}")
            count = seen.get(base, 0)
            seen[base] = count + 1
            names.append(base if count == 0 else f"{base}_{count + 1}"[:128])
        return names

    def _infer_csv_data_type(self, values: list[str]) -> str:
        normalized = [value.strip() for value in values if value.strip()]
        if normalized and all(self._is_csv_number(value) for value in normalized):
            return "NUMBER"
        max_len = max((len(value) for value in normalized), default=1)
        return f"VARCHAR2({min(max(max_len, 1), 4000)})"

    def _is_csv_number(self, value: str) -> bool:
        return bool(re.fullmatch(r"[-+]?(?:\d+\.?\d*|\.\d+)", value.strip()))

    def _normalize_csv_cell(self, value: str) -> str | None:
        stripped = value.strip()
        return stripped or None

    def _csv_import_ddl(self, table_name: str, columns: list[CsvImportColumn]) -> str:
        column_defs = ", ".join(f'"{column.column_name}" {column.data_type}' for column in columns)
        return f'CREATE TABLE "{table_name}" ({column_defs})'

    def _csv_import_insert_sql(self, table_name: str, columns: list[CsvImportColumn]) -> str:
        column_names = ", ".join(f'"{column.column_name}"' for column in columns)
        binds = ", ".join(f":c{index}" for index, _column in enumerate(columns))
        # Safe: dry-run SQL from sanitized CSV identifiers; execution path uses Oracle binds.
        return f'INSERT INTO "{table_name}" ({column_names}) VALUES ({binds})'  # nosec B608

    def refresh_select_ai_agent_assets(self, profile_id: str | None) -> AssetRefreshData:
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
            "runtime": "deterministic",
        }
        if self._use_oracle_runtime():
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
        self._persist_state()
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
        self, profile_id: str | None, engines: list[Nl2SqlEngine], execute: bool
    ) -> list[AssetCleanupData]:
        """Select AI / Agent assets の dry-run / 明示 cleanup を行う。"""
        cleaned: list[AssetCleanupData] = []
        for engine in engines:
            if engine == Nl2SqlEngine.AUTO:
                continue
            if engine == Nl2SqlEngine.SELECT_AI:
                cleaned.append(self._cleanup_select_ai_profile(profile_id, execute))
            elif engine == Nl2SqlEngine.SELECT_AI_AGENT:
                cleaned.append(self._cleanup_select_ai_agent_assets(profile_id, execute))
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
        self._persist_state()
        return cleaned

    def _cleanup_select_ai_profile(self, profile_id: str | None, execute: bool) -> AssetCleanupData:
        profile = self._cleanup_profile_target(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        warning = ""
        status = "dry_run"
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        if execute:
            if self._use_oracle_runtime():
                try:
                    engine_meta.update(
                        self._oracle_adapter.drop_select_ai_profile(profile_name=profile_name)
                    )
                    status = "cleaned"
                    executed = True
                    with self._lock:
                        self._asset_meta.pop(Nl2SqlEngine.SELECT_AI, None)
                except OracleAdapterError as exc:
                    status = "error"
                    warning = str(exc)
            else:
                status = "error"
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
        )

    def _cleanup_select_ai_agent_assets(
        self, profile_id: str | None, execute: bool
    ) -> AssetCleanupData:
        profile = self._cleanup_profile_target(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        asset_names = self._select_ai_agent_asset_names(profile)
        asset_meta = self._asset_meta.get(Nl2SqlEngine.SELECT_AI_AGENT)
        if asset_meta and asset_meta.profile_name == profile_name and asset_meta.team_name:
            asset_names["team"] = asset_meta.team_name
        warning = ""
        status = "dry_run"
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
        if execute:
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
                    status = "error"
                    warning = str(exc)
            else:
                status = "error"
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
            allowed_tables=profile.allowed_tables,
            row_limit=profile.default_row_limit,
            description=profile.description,
        )

    def _cleanup_profile_target(self, profile_id: str | None) -> Nl2SqlProfile:
        if not profile_id:
            return self.get_profile(None)
        with self._lock:
            existing = self._profiles.get(profile_id)
        if existing:
            return existing
        return Nl2SqlProfile(
            id=profile_id,
            name=profile_id,
            description="Cleanup target profile",
            default_row_limit=get_settings().nl2sql_default_row_limit,
        )

    def rewrite_question(self, question: str, profile: Nl2SqlProfile) -> str:
        rewritten = question.strip()
        for term, replacement in profile.glossary.items():
            if term in rewritten and replacement not in rewritten:
                rewritten = f"{rewritten}（{term}={replacement}）"
        return rewritten

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

    def _recommendation_from_profile(
        self,
        *,
        profile: Nl2SqlProfile,
        question: str,
        score: float,
        matched_terms: list[str],
        candidates: list[ProfileRecommendationCandidate],
    ) -> ProfileRecommendationData:
        confidence = min(round(score / 6, 3), 1.0)
        allowed_tables = profile.allowed_tables or [
            table.table_name for table in self._catalog.tables
        ]
        reason_terms = "、".join(matched_terms[:4]) if matched_terms else profile.name
        return ProfileRecommendationData(
            recommended_profile_id=profile.id,
            recommended_profile_name=profile.name,
            confidence=confidence,
            reason=f"{reason_terms} に一致したため、この profile を推薦しました。",
            rewritten_question=self.rewrite_question(question, profile),
            recommended_allowed_objects=AllowedObjects(table_names=allowed_tables, columns={}),
            candidates=candidates,
        )

    def _score_profile_for_question(
        self, profile: Nl2SqlProfile, question: str
    ) -> tuple[float, list[str]]:
        normalized_question = question.upper()
        matched_terms: list[str] = []
        score = 0.0

        def add_match(term: str, weight: float) -> None:
            nonlocal score
            if not term:
                return
            if term.upper() in normalized_question or term in question:
                score += weight
                if term not in matched_terms:
                    matched_terms.append(term)

        for term, replacement in profile.glossary.items():
            add_match(term, 2.0)
            add_match(replacement, 1.0)
        for token in re.split(r"[\s、。・/]+", f"{profile.name} {profile.description}"):
            add_match(token.strip(), 0.6)
        for example in profile.few_shot_examples:
            add_match(example.get("question", ""), 1.2)

        allowed_tables = {_normalize_identifier(table) for table in profile.allowed_tables}
        for table in self._catalog.tables:
            if allowed_tables and table.table_name not in allowed_tables:
                continue
            add_match(table.table_name, 1.6)
            add_match(table.logical_name, 1.6)
            add_match(table.comment, 0.8)
            for column in table.columns:
                add_match(column.column_name, 0.9)
                add_match(column.logical_name, 0.9)

        if not matched_terms and profile.id == "default":
            score += 0.5
        return score, matched_terms

    def _similar_history_candidates(
        self,
        *,
        question: str,
        profile_id: str | None,
        include_bad: bool,
    ) -> list[SimilarHistoryItem]:
        with self._lock:
            history = list(self._history)
        vector_ranked = self._rank_oracle_vector_history(
            question=question,
            profile_id=profile_id,
            history=history,
            include_bad=include_bad,
            limit=10,
        )
        if vector_ranked:
            return vector_ranked
        return self._rank_similar_history(
            question=question,
            profile_id=profile_id,
            history=history,
            include_bad=include_bad,
        )

    def _rank_oracle_vector_history(
        self,
        *,
        question: str,
        profile_id: str | None,
        history: list[HistoryItem],
        include_bad: bool,
        limit: int,
    ) -> list[SimilarHistoryItem]:
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
                include_bad=include_bad,
                limit=limit,
            )
        except (EmbeddingClientError, OracleAdapterError, IndexError, ValueError) as exc:
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
                item = HistoryItem(
                    id=history_id,
                    question=str(row.get("question") or ""),
                    engine=Nl2SqlEngine.ENTERPRISE_AI_DIRECT,
                    generated_sql=str(row.get("generated_sql") or ""),
                    created_at=_utc_now(),
                    feedback_rating=self._feedback_rating_from_text(
                        str(row.get("feedback_rating") or "")
                    ),
                    profile_id=str(row.get("profile_id") or ""),
                    profile_name=str(row.get("profile_id") or ""),
                )
            if item.feedback_rating == FeedbackRating.BAD and not include_bad:
                continue
            if not item.safety_is_safe:
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
        history: list[HistoryItem],
        include_bad: bool,
    ) -> list[SimilarHistoryItem]:
        query_tokens = _similarity_tokens(question)
        if not query_tokens:
            return []
        scored: list[SimilarHistoryItem] = []
        for item in history:
            if item.feedback_rating == FeedbackRating.BAD and not include_bad:
                continue
            if not item.safety_is_safe:
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
            base_score = len(overlap) / max(len(query_tokens), 1)
            if profile_id and item.profile_id == profile_id:
                base_score += 0.15
            if item.feedback_rating == FeedbackRating.GOOD:
                base_score += 0.25
            elif item.feedback_rating == FeedbackRating.NEEDS_REVIEW:
                base_score += 0.05
            score = round(min(base_score, 1.0), 3)
            visible_terms = self._visible_similarity_terms(question, item, overlap)
            reason_terms = "、".join(visible_terms[:4] or overlap[:4])
            reason = (
                f"{reason_terms} が一致し、良い feedback が付いています。"
                if item.feedback_rating == FeedbackRating.GOOD
                else f"{reason_terms} が一致しました。"
            )
            scored.append(SimilarHistoryItem(item=item, score=score, reason=reason))
        scored.sort(
            key=lambda candidate: (
                candidate.score,
                candidate.item.feedback_rating == FeedbackRating.GOOD,
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
        for profile in self._profiles.values():
            candidates.extend(profile.glossary.keys())
            candidates.extend(profile.glossary.values())
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
            self._run_job(job_id)
        except Exception as exc:  # pragma: no cover - defensive boundary
            with self._lock:
                job = self._jobs[job_id]
                job.status = JobStatus.ERROR
                job.error_message = f"NL2SQL ジョブに失敗しました: {exc}"
                job.finished_at = _utc_now()
            self._persist_state()

    def _run_job(self, job_id: str) -> None:
        total_started = time.monotonic()
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.RUNNING
            job.started_at = _utc_now()
            job.timing = TimingEnvelope(created_at=job.created_at, started_at=job.started_at)
            request = job.request
        self._persist_state()

        stage_timings: list[StageTiming] = []
        profile = self.get_profile(request.profile_id)

        stage_started = time.monotonic()
        rewritten = self.rewrite_question(request.question, profile)
        allowed = self._resolve_allowed_objects(request.profile_id, request.allowed_objects)
        row_limit = self._resolve_row_limit(request.profile_id, request.row_limit)
        stage_timings.append(
            StageTiming(stage="prepare_context", elapsed_ms=_elapsed_ms(stage_started))
        )

        stage_started = time.monotonic()
        generated = self._generate_with_fallback(
            question=rewritten,
            engine=request.engine,
            profile=profile,
            allowed=allowed,
            row_limit=row_limit,
        )
        stage_timings.append(
            StageTiming(stage="generate_sql", elapsed_ms=_elapsed_ms(stage_started))
        )

        stage_started = time.monotonic()
        analysis = self.analyze_sql(generated.generated_sql, allowed, row_limit)
        safety, executable, results = self.execute_sql(generated.generated_sql, allowed, row_limit)
        stage_timings.append(
            StageTiming(stage="safety_and_execute", elapsed_ms=_elapsed_ms(stage_started))
        )

        finished = _utc_now()
        timing = TimingEnvelope(
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=finished,
            elapsed_ms=_elapsed_ms(total_started),
            stage_timings=stage_timings,
        )
        result = Nl2SqlResult(
            engine=generated.engine,
            engine_meta=generated.engine_meta,
            fallback_reason=generated.fallback_reason,
            original_question=request.question,
            rewritten_question=rewritten,
            generated_sql=generated.generated_sql,
            executable_sql=executable,
            explanation=generated.explanation,
            safety=analysis.safety,
            recommendations=analysis.recommendations,
            repaired_sql=analysis.repaired_sql,
            optimization_hints=analysis.optimization_hints,
            results=results,
            timing=timing,
        )
        history_id = str(uuid.uuid4())
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.DONE if analysis.safety.is_safe else JobStatus.ERROR
            job.error_message = None if analysis.safety.is_safe else analysis.safety.blocked_reason
            job.result = result
            job.finished_at = finished
            job.elapsed_ms = timing.elapsed_ms
            job.timing = timing
            self._history.append(
                HistoryItem(
                    id=history_id,
                    question=request.question,
                    engine=result.engine,
                    generated_sql=result.generated_sql,
                    created_at=finished,
                    elapsed_ms=timing.elapsed_ms,
                    profile_id=profile.id,
                    profile_name=profile.name,
                    rewritten_question=rewritten,
                    executable_sql=result.executable_sql,
                    safety_is_safe=result.safety.is_safe,
                    result_row_count=result.results.total,
                    result_columns=result.results.columns,
                )
            )
        self._persist_state()

    def _generate_with_fallback(
        self,
        question: str,
        engine: Nl2SqlEngine,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
    ) -> GeneratedSql:
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
            try:
                return self._generate_sql(
                    candidate, question, profile, allowed, row_limit, fallback_messages
                )
            except RuntimeError as exc:
                fallback_messages.append(f"{candidate.value}: {exc}")
        raise RuntimeError("すべての NL2SQL エンジンが失敗しました。")

    def _generate_sql(
        self,
        engine: Nl2SqlEngine,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int | None,
        fallback_messages: list[str],
    ) -> GeneratedSql:
        # テスト/デモ用の明示的 failure trigger。実 adapter では不要。
        if f"{engine.value}_fail" in question.lower():
            raise RuntimeError("明示的な fallback テスト要求")
        table = self._choose_table(question, profile, allowed)
        columns = self._choose_columns(table, allowed)
        meta: dict[str, Any] = {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "row_limit": row_limit or profile.default_row_limit,
            "allowed_tables": allowed.table_names or profile.allowed_tables,
        }
        learning_examples = self._learning_examples_for_generation(
            question=question,
            profile=profile,
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
        if self._use_oracle_runtime() and engine in {
            Nl2SqlEngine.SELECT_AI,
            Nl2SqlEngine.SELECT_AI_AGENT,
        }:
            try:
                return self._generate_oracle_sql(
                    engine=engine,
                    question=question,
                    profile=profile,
                    fallback_messages=fallback_messages,
                    meta=dict(meta),
                    learning_examples=learning_examples,
                )
            except OracleAdapterError as exc:
                fallback_messages.append(f"{engine.value}: {exc}")
        direct_configured = self._enterprise_ai_client.is_configured()
        if engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT and direct_configured:
            try:
                return self._generate_enterprise_ai_direct_sql(
                    question=question,
                    profile=profile,
                    allowed=allowed,
                    row_limit=row_limit or profile.default_row_limit,
                    fallback_messages=fallback_messages,
                    meta=dict(meta),
                    learning_examples=learning_examples,
                )
            except EnterpriseAiDirectError as exc:
                fallback_messages.append(f"{engine.value}: {exc}")

        sql = self._compose_select_sql(table.table_name, columns)
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
        )

    def _generate_enterprise_ai_direct_sql(
        self,
        *,
        question: str,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        row_limit: int,
        fallback_messages: list[str],
        meta: dict[str, Any],
        learning_examples: list[LearningExample],
    ) -> GeneratedSql:
        context = self._enterprise_ai_schema_context(
            profile=profile,
            allowed=allowed,
            learning_examples=learning_examples,
        )
        system_prompt = self._enterprise_ai_sql_system_prompt(row_limit)
        raw_text = self._enterprise_ai_client.generate(
            prompt=question,
            context=context,
            system_prompt=system_prompt,
        )
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
        )

    def _enterprise_ai_schema_context(
        self,
        *,
        profile: Nl2SqlProfile,
        allowed: AllowedObjects,
        learning_examples: list[LearningExample] | None = None,
    ) -> str:
        allowed_tables = {
            _normalize_identifier(table)
            for table in (allowed.table_names or profile.allowed_tables)
        }
        allowed_columns = {
            _normalize_identifier(table): {_normalize_identifier(column) for column in columns}
            for table, columns in allowed.columns.items()
            if columns
        }
        lines = [
            f"profile: {profile.name}",
            f"description: {profile.description}",
            "glossary:",
        ]
        lines.extend(f"- {term}: {definition}" for term, definition in profile.glossary.items())
        lines.append("sql_rules:")
        lines.extend(f"- {rule}" for rule in profile.sql_rules)
        lines.append("schema:")
        for table in self._catalog.tables:
            if allowed_tables and table.table_name not in allowed_tables:
                continue
            lines.append(
                f"- table {table.table_name} logical={table.logical_name} comment={table.comment}"
            )
            table_allowed_columns = allowed_columns.get(table.table_name, set())
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

    def _enterprise_ai_sql_system_prompt(self, row_limit: int) -> str:
        return (
            "あなたは Oracle Database 26ai 向け NL2SQL エンジンです。"
            "与えられた schema/context の表と列だけを使用してください。"
            "DDL/DML/PLSQL/複数 statement/説明付き markdown は禁止です。"
            "必ず SELECT または WITH で始まる 1 つの Oracle SQL を生成してください。"
            f"必要に応じて FETCH FIRST {row_limit} ROWS ONLY を使ってください。"
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
    ) -> GeneratedSql:
        runtime_question = self._augment_question_with_learning_examples(
            question,
            learning_examples,
        )
        if engine == Nl2SqlEngine.SELECT_AI:
            profile_name = self._select_ai_profile_name(profile)
            sql = self._oracle_adapter.generate_select_ai_sql(
                profile_name=profile_name, question=runtime_question
            )
            meta.update({"select_ai_profile": profile_name, "runtime": "oracle"})
        else:
            team_name = self._select_ai_runtime_team_name(profile)
            tool_name = self._select_ai_agent_asset_names(profile)["tool"]
            sql, conversation_id = self._oracle_adapter.run_select_ai_agent_team(
                team_name=team_name, question=runtime_question, tool_name=tool_name
            )
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
            explanation="Oracle runtime で SQL を生成しました。",
            engine_meta=meta,
            fallback_reason="; ".join(fallback_messages),
        )

    def _use_oracle_runtime(self) -> bool:
        return get_settings().nl2sql_runtime_mode.strip().lower() == "oracle"

    def _select_ai_profile_name(self, profile: Nl2SqlProfile) -> str:
        prefix = get_settings().nl2sql_select_ai_profile_prefix.strip() or "NL2SQL"
        return f"{prefix}_{profile.id.upper()}_PROFILE"

    def _select_ai_team_name(self, profile: Nl2SqlProfile) -> str:
        prefix = get_settings().nl2sql_select_ai_profile_prefix.strip() or "NL2SQL"
        return f"{prefix}_{profile.id.upper()}_TEAM"

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
        profile_key = profile.id.upper()
        return {
            "tool": f"{prefix}_{profile_key}_TOOL",
            "agent": f"{prefix}_{profile_key}_AGENT",
            "task": f"{prefix}_{profile_key}_TASK",
            "team": f"{prefix}_{profile_key}_TEAM",
        }

    def _resolve_allowed_objects(
        self, profile_id: str | None, requested: AllowedObjects
    ) -> AllowedObjects:
        if requested.table_names:
            return requested
        profile = self.get_profile(profile_id)
        return AllowedObjects(table_names=profile.allowed_tables, columns=requested.columns)

    def _resolve_row_limit(self, profile_id: str | None, requested: int | None) -> int:
        if requested:
            return requested
        return self.get_profile(profile_id).default_row_limit

    def _choose_table(
        self, question: str, profile: Nl2SqlProfile, allowed: AllowedObjects
    ) -> SchemaTable:
        allowed_names = {
            _normalize_identifier(name) for name in (allowed.table_names or profile.allowed_tables)
        }
        candidates = [
            table
            for table in self._catalog.tables
            if not allowed_names or table.table_name in allowed_names
        ]
        if not candidates:
            candidates = self._catalog.tables
        question_upper = question.upper()
        for table in candidates:
            if table.table_name in question_upper or table.logical_name in question:
                return table
        if "顧客" in question or "取引先" in question:
            return next(
                (table for table in candidates if table.table_name == "CUSTOMERS"), candidates[0]
            )
        if "入金" in question or "支払" in question:
            return next(
                (table for table in candidates if table.table_name == "PAYMENTS"), candidates[0]
            )
        return candidates[0]

    def _choose_columns(self, table: SchemaTable, allowed: AllowedObjects) -> list[SchemaColumn]:
        allowed_columns = {
            _normalize_identifier(name) for name in allowed.columns.get(table.table_name, [])
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

    def _mock_execute(self, sql: str, row_limit: int) -> QueryResults:
        referenced = _extract_referenced_tables(sql)
        table_name = referenced[0] if referenced else "INVOICES"
        table = next(
            (candidate for candidate in self._catalog.tables if candidate.table_name == table_name),
            None,
        )
        if table is None:
            return QueryResults(columns=["MESSAGE"], rows=[{"MESSAGE": "mock result"}], total=1)
        columns = [column.column_name for column in table.columns[:4]]
        rows = [
            {
                columns[0]: f"{table.table_name}-{index + 1}",
                columns[1]: (
                    table.columns[1].sample_values[index % len(table.columns[1].sample_values)]
                    if len(table.columns) > 1 and table.columns[1].sample_values
                    else f"値{index + 1}"
                ),
                columns[2]: (index + 1) * 1000 if len(columns) > 2 else "",
                columns[3]: "2026-06-21" if len(columns) > 3 else "",
            }
            for index in range(min(row_limit, 5))
        ]
        return QueryResults(columns=columns, rows=rows, total=len(rows))

    def _repair_sql(
        self,
        *,
        sql: str,
        safety: SafetyReport,
        allowed: AllowedObjects,
        row_limit: int,
        referenced_tables: list[str],
        referenced_columns: list[str],
        has_wildcard: bool,
    ) -> str:
        stripped = sql.strip().rstrip(";")
        if not stripped:
            return ""

        if not safety.is_select_only:
            for statement in [part.strip() for part in sql.split(";") if part.strip()]:
                if is_select_only(statement):
                    return enforce_row_limit(statement, row_limit)
            return ""

        if not _table_allowed(referenced_tables, allowed):
            table_name = self._first_allowed_table(allowed)
            if not table_name:
                return ""
            return enforce_row_limit(
                # Safe: table and columns are resolved from allowed_objects.
                f"SELECT {self._allowed_select_list(table_name, allowed)} FROM {table_name}",  # nosec B608
                row_limit,
            )

        if has_wildcard or not _column_allowed(
            referenced_columns, has_wildcard, referenced_tables, allowed
        ):
            table_name = (
                referenced_tables[0] if referenced_tables else self._first_allowed_table(allowed)
            )
            if not table_name:
                return enforce_row_limit(stripped, row_limit)
            select_list = self._allowed_select_list(table_name, allowed)
            if _extract_select_list(stripped):
                repaired = re.sub(
                    r"\bselect\b.+?\bfrom\b",
                    f"SELECT {select_list} FROM",
                    stripped,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                return enforce_row_limit(repaired, row_limit)
            # Safe: repair fallback uses allowed table/column list.
            return enforce_row_limit(
                f"SELECT {select_list} FROM {table_name}",  # nosec B608
                row_limit,
            )

        executable = enforce_row_limit(stripped, row_limit)
        return executable if executable != stripped else ""

    def _repair_sql_for_oracle_error(
        self,
        *,
        sql: str,
        error_code: str,
        allowed: AllowedObjects,
        row_limit: int,
        referenced_tables: list[str],
    ) -> str:
        stripped = sql.strip().rstrip(";")
        if not stripped:
            return ""
        table_name = (
            referenced_tables[0] if referenced_tables else self._first_allowed_table(allowed)
        )
        if error_code in {"ORA-00933", "ORA-00911"}:
            first_select = next(
                (part.strip() for part in sql.split(";") if is_select_only(part.strip())),
                stripped,
            )
            first_select = re.sub(
                r"\s+limit\s+(\d+)\s*$",
                r" FETCH FIRST \1 ROWS ONLY",
                first_select,
                flags=re.IGNORECASE,
            )
            return (
                enforce_row_limit(first_select, row_limit) if is_select_only(first_select) else ""
            )
        if error_code == "ORA-00942":
            replacement_table = self._first_allowed_table(allowed)
            if not replacement_table:
                return ""
            return enforce_row_limit(
                f"SELECT {self._allowed_select_list(replacement_table, allowed)} "  # nosec B608
                f"FROM {replacement_table}",
                row_limit,
            )
        if error_code in {"ORA-00904", "ORA-00918", "ORA-00979"}:
            if not table_name:
                return ""
            select_list = self._allowed_select_list(table_name, allowed)
            from_match = re.search(r"\bfrom\b\s+.+", stripped, flags=re.IGNORECASE | re.DOTALL)
            if from_match:
                return enforce_row_limit(
                    f"SELECT {select_list} {from_match.group(0)}",  # nosec B608
                    row_limit,
                )
            return enforce_row_limit(
                f"SELECT {select_list} FROM {table_name}",  # nosec B608
                row_limit,
            )
        if error_code == "ORA-01722":
            return enforce_row_limit(stripped, row_limit) if is_select_only(stripped) else ""
        return ""

    def _oracle_error_code(self, message: str) -> str:
        match = re.search(r"\bORA-\d{5}\b", message.upper())
        return match.group(0) if match else ""

    def _oracle_error_explanation(self, error_code: str) -> str:
        explanations = {
            "ORA-00904": "存在しない列名または alias を参照している可能性があります。",
            "ORA-00911": "SQL に無効な文字が含まれている可能性があります。",
            "ORA-00918": "結合時に列名が曖昧になっている可能性があります。",
            "ORA-00933": (
                "Oracle 構文に合わない句、末尾セミコロン、LIMIT 句が"
                "含まれている可能性があります。"
            ),
            "ORA-00942": (
                "参照表または view が存在しない、または権限が不足している可能性があります。"
            ),
            "ORA-00979": "GROUP BY に含めるべき非集計列が SELECT に残っている可能性があります。",
            "ORA-01722": "文字列列を数値として比較している可能性があります。",
        }
        return explanations.get(
            error_code,
            "Oracle error message をもとに安全な修復候補を生成しました。",
        )

    def _oracle_error_recommendations(
        self, *, error_code: str, fallback_recommendations: list[str]
    ) -> list[str]:
        recommendations = {
            "ORA-00904": ["Schema catalog の列名・alias を確認してください。"],
            "ORA-00911": ["末尾セミコロンや不可視文字を削除してください。"],
            "ORA-00918": ["結合 SQL では table alias を付けて列を明示してください。"],
            "ORA-00933": [
                "Oracle では LIMIT ではなく FETCH FIRST n ROWS ONLY を使用してください。"
            ],
            "ORA-00942": ["許可 table / schema owner / 権限を確認してください。"],
            "ORA-00979": ["非集計列を GROUP BY に追加するか、SELECT から外してください。"],
            "ORA-01722": [
                "数値比較対象の列型を確認し、必要なら文字列比較または明示変換を使ってください。"
            ],
        }
        merged = [*recommendations.get(error_code, []), *fallback_recommendations]
        seen: set[str] = set()
        unique: list[str] = []
        for item in merged:
            if item and item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    def _first_allowed_table(self, allowed: AllowedObjects) -> str:
        if allowed.table_names:
            return _normalize_identifier(allowed.table_names[0])
        return self._catalog.tables[0].table_name if self._catalog.tables else ""

    def _allowed_select_list(self, table_name: str, allowed: AllowedObjects) -> str:
        normalized_table = _normalize_identifier(table_name)
        restricted_columns = {
            _normalize_identifier(candidate_table): columns
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
                for candidate in self._catalog.tables
                if candidate.table_name == normalized_table
            ),
            None,
        )
        columns = [column.column_name for column in table.columns[:6]] if table else []
        return ", ".join(columns) or "*"

    def _optimization_hints(self, *, safety: SafetyReport, sql: str, row_limit: int) -> list[str]:
        if not safety.is_select_only:
            return ["参照系 SQL に修正してから最適化を確認してください。"]
        hints: list[str] = []
        normalized = sql.lower()
        if safety.referenced_tables and " where " not in normalized:
            hints.append("大量データの表では WHERE 条件を追加すると応答時間が安定します。")
        if " join " in normalized:
            hints.append("JOIN 条件に主キー・外部キー列を使っているか確認してください。")
        if " order by " in normalized and "fetch first" not in normalized:
            hints.append("ORDER BY と行数制限を組み合わせると結果確認が速くなります。")
        if row_limit > 1000:
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
    ) -> list[str]:
        if not safety.is_safe:
            recommendations = [
                "許可オブジェクトを見直すか、SELECT/WITH の単一 statement に修正してください。"
            ]
            if allowed and "許可されていない表" in safety.blocked_reason:
                allowed_tables = allowed.table_names or [
                    table.table_name for table in self._catalog.tables[:5]
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
                "Oracle では LIMIT 句を FETCH FIRST n ROWS ONLY に置き換えて実行します。"
            )
        if sql.strip().endswith(";") and ";" not in sql.strip().rstrip(";"):
            recommendations.append("API 実行前に末尾セミコロンを除去します。")
        if not safety.referenced_tables:
            recommendations.append("FROM/JOIN の対象表が検出できませんでした。")
        if repaired_sql:
            recommendations.append("実行時には行数制限付き SQL を使用します。")
        return recommendations

    def _build_default_catalog(self) -> SchemaCatalog:
        return SchemaCatalog(
            refreshed_at=_utc_now(),
            tables=[
                SchemaTable(
                    table_name="INVOICES",
                    logical_name="請求",
                    comment="請求書ヘッダーと金額情報",
                    row_count=1280,
                    constraints=["PK_INVOICES", "FK_INVOICES_CUSTOMER"],
                    columns=[
                        SchemaColumn(
                            column_name="INVOICE_ID",
                            logical_name="請求ID",
                            data_type="VARCHAR2",
                            nullable=False,
                        ),
                        SchemaColumn(
                            column_name="CUSTOMER_NAME",
                            logical_name="取引先名",
                            data_type="VARCHAR2",
                            sample_values=["青山商事", "東京製作所", "大阪物流"],
                        ),
                        SchemaColumn(
                            column_name="TOTAL_AMOUNT", logical_name="請求金額", data_type="NUMBER"
                        ),
                        SchemaColumn(
                            column_name="INVOICE_DATE", logical_name="請求日", data_type="DATE"
                        ),
                        SchemaColumn(
                            column_name="STATUS", logical_name="ステータス", data_type="VARCHAR2"
                        ),
                        SchemaColumn(
                            column_name="DUE_DATE", logical_name="支払期限", data_type="DATE"
                        ),
                    ],
                ),
                SchemaTable(
                    table_name="CUSTOMERS",
                    logical_name="顧客",
                    comment="顧客・取引先マスター",
                    row_count=320,
                    constraints=["PK_CUSTOMERS"],
                    columns=[
                        SchemaColumn(
                            column_name="CUSTOMER_ID",
                            logical_name="顧客ID",
                            data_type="VARCHAR2",
                            nullable=False,
                        ),
                        SchemaColumn(
                            column_name="CUSTOMER_NAME",
                            logical_name="取引先名",
                            data_type="VARCHAR2",
                            sample_values=["青山商事", "東京製作所", "大阪物流"],
                        ),
                        SchemaColumn(
                            column_name="INDUSTRY", logical_name="業種", data_type="VARCHAR2"
                        ),
                        SchemaColumn(
                            column_name="REGION", logical_name="地域", data_type="VARCHAR2"
                        ),
                    ],
                ),
                SchemaTable(
                    table_name="PAYMENTS",
                    logical_name="入金",
                    comment="入金消込と支払状況",
                    row_count=980,
                    constraints=["PK_PAYMENTS", "FK_PAYMENTS_INVOICE"],
                    columns=[
                        SchemaColumn(
                            column_name="PAYMENT_ID",
                            logical_name="入金ID",
                            data_type="VARCHAR2",
                            nullable=False,
                        ),
                        SchemaColumn(
                            column_name="INVOICE_ID", logical_name="請求ID", data_type="VARCHAR2"
                        ),
                        SchemaColumn(
                            column_name="PAID_AMOUNT", logical_name="入金額", data_type="NUMBER"
                        ),
                        SchemaColumn(
                            column_name="PAID_AT", logical_name="入金日", data_type="DATE"
                        ),
                        SchemaColumn(
                            column_name="PAYMENT_METHOD",
                            logical_name="入金方法",
                            data_type="VARCHAR2",
                        ),
                    ],
                ),
            ],
        )


nl2sql_service = Nl2SqlService()
