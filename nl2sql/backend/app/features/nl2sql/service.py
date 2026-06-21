"""NL2SQL application service.

この実装は local / CI で外部 Oracle・OCI に依存せずに動く deterministic adapter を持つ。
実運用では `SelectAiAdapter` / `SelectAiAgentAdapter` / `EnterpriseAiDirectAdapter`
の generate 部分を Oracle / OCI 呼び出しに差し替える。
"""

from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.settings import get_settings

from .models import (
    AllowedObjects,
    AnalyzeData,
    AssetCleanupData,
    AssetRefreshData,
    CommentSuggestion,
    CommentSuggestionData,
    CompareData,
    CompareExecutionData,
    CompareRequest,
    CsvImportColumn,
    CsvImportData,
    CsvImportRequest,
    DiagnosticCheck,
    DiagnosticsData,
    EvaluateData,
    EvaluateRequest,
    FeedbackData,
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
_FROM_JOIN_TABLE = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][\w$#]*(?:\.[a-zA-Z_][\w$#]*)?)", re.IGNORECASE
)
_FROM_JOIN_WITH_ALIAS = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][\w$#]*(?:\.[a-zA-Z_][\w$#]*)?)"
    r"(?:\s+(?:as\s+)?([a-zA-Z_][\w$#]*))?",
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
    return value.strip().strip('"').split(".")[-1].upper()


def _csv_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip().upper()).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"C_{normalized}"
    return normalized[:128]


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
        self._feedback: dict[str, FeedbackRating] = {}
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
            asset_meta = {
                Nl2SqlEngine(engine): AssetRefreshData.model_validate(data)
                for engine, data in snapshot.get("asset_meta", {}).items()
            }
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
            self._feedback = {
                item.id: item.feedback_rating
                for item in history
                if item.feedback_rating is not None
            }
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

    def similar_history(self, request: SimilarHistoryRequest) -> SimilarHistoryData:
        with self._lock:
            history = list(self._history)
        return SimilarHistoryData(
            items=self._rank_similar_history(
                question=request.question,
                profile_id=request.profile_id,
                history=history,
                include_bad=False,
            )[: request.limit]
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
        total = len(request.cases)
        if total == 0:
            return EvaluateData(
                evaluation_suite="deterministic_mock",
                total_cases=0,
                executable_rate=0.0,
                select_only_rate=0.0,
                findings=["評価ケースがありません。"],
            )
        select_only = 0
        executable = 0
        for case in request.cases:
            preview = self.preview(
                PreviewRequest(
                    question=case.get("question", ""),
                    engine=request.engine,
                    allowed_objects=AllowedObjects(),
                )
            )
            if preview.safety and preview.safety.is_select_only:
                select_only += 1
            if preview.is_safe:
                executable += 1
        return EvaluateData(
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
        return CompareData(
            question=request.question,
            results=results,
            execution_results=execution_results,
            error_rate=error_rate,
            recommendation=recommendation,
        )

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
        uses_oracle_runtime = self._use_oracle_runtime()
        oracle_live_ok = False
        oracle_live_message = "deterministic runtime のため live 接続は未確認です。"
        if uses_oracle_runtime:
            oracle_live_ok, oracle_live_message = self._oracle_adapter.test_connection()
        persistence_ready, persistence_message = self._store.check()
        checks = [
            check_present("ORACLE_DSN", "Oracle DSN"),
            check_present("ORACLE_USER", "Oracle user"),
            check_present("OCI_REGION", "OCI region"),
            check_present("OCI_COMPARTMENT_ID", "OCI compartment"),
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
                name="NL2SQL_SELECT_AI_AGENT_ENABLED",
                status="ok" if settings.nl2sql_select_ai_agent_enabled else "warning",
                message=(
                    "Select AI Agent engine は有効です。"
                    if settings.nl2sql_select_ai_agent_enabled
                    else "Select AI Agent engine は無効です。"
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
        ]
        return DiagnosticsData(checks=checks)

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
                engine_meta.update(
                    self._oracle_adapter.refresh_select_ai_agent_assets(
                        profile_name=profile_name,
                        tool_name=tool_name,
                        agent_name=agent_name,
                        task_name=task_name,
                        team_name=team_name,
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
        learned_examples = self._rank_similar_history(
            question=question,
            profile_id=profile.id,
            history=list(self._history),
            include_bad=False,
        )[:3]
        if learned_examples:
            meta["similar_history_examples"] = [
                {
                    "history_id": example.item.id,
                    "question": example.item.question,
                    "sql": example.item.generated_sql,
                    "score": example.score,
                    "feedback": (
                        example.item.feedback_rating.value if example.item.feedback_rating else None
                    ),
                }
                for example in learned_examples
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
                )
            except OracleAdapterError as exc:
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

    def _generate_oracle_sql(
        self,
        *,
        engine: Nl2SqlEngine,
        question: str,
        profile: Nl2SqlProfile,
        fallback_messages: list[str],
        meta: dict[str, Any],
    ) -> GeneratedSql:
        if engine == Nl2SqlEngine.SELECT_AI:
            profile_name = self._select_ai_profile_name(profile)
            sql = self._oracle_adapter.generate_select_ai_sql(
                profile_name=profile_name, question=question
            )
            meta.update({"select_ai_profile": profile_name, "runtime": "oracle"})
        else:
            team_name = self._select_ai_team_name(profile)
            sql, conversation_id = self._oracle_adapter.run_select_ai_agent_team(
                team_name=team_name, question=question
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
