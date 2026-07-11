"""NL2SQL application service.

この実装は local / CI で外部 Oracle・OCI に依存せずに動く deterministic adapter を持つ。
実運用では `SelectAiAdapter` / `SelectAiAgentAdapter` / `EnterpriseAiDirectAdapter`
の generate 部分を Oracle / OCI 呼び出しに差し替える。
"""

from __future__ import annotations

import base64
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
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel
from pydantic import Field as PydanticField

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
    ClassifierImportData,
    ClassifierModelActivateData,
    ClassifierModelImportData,
    ClassifierModelInfo,
    ClassifierModelsData,
    ClassifierPredictionCandidate,
    ClassifierPredictionData,
    ClassifierPredictRequest,
    ClassifierStatusData,
    ClassifierTrainingDataData,
    ClassifierTrainingExample,
    ClassifierTrainRequest,
    CommentApplyData,
    CommentApplyItem,
    CommentApplyRequest,
    CommentApplyStatement,
    CommentSuggestion,
    CommentSuggestionData,
    CommentSuggestionRequest,
    CompareData,
    CompareExecutionData,
    CompareHistoryData,
    CompareRecord,
    CompareRequest,
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
    DbAdminObjectsData,
    DbAdminObjectSummary,
    DbAdminStatementResult,
    DbAdminStatementsRequest,
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
    FeedbackEntriesData,
    FeedbackIndexData,
    FeedbackIndexRequest,
    FeedbackRating,
    FeedbackSearchConfigData,
    FeedbackSearchConfigRequest,
    FeedbackVectorEntry,
    HistoryData,
    HistoryItem,
    JobCreateData,
    JobCreateRequest,
    JobData,
    JobStatus,
    LegacyLearningMaterialData,
    MetadataSqlGenerateData,
    MetadataSqlGenerateRequest,
    MetadataSqlSampleData,
    MetadataSqlSampleRequest,
    Nl2SqlEngine,
    Nl2SqlProfile,
    Nl2SqlResult,
    PreviewData,
    PreviewRequest,
    ProfileLearningMaterialImportData,
    ProfileRecommendationCandidate,
    ProfileRecommendationData,
    ProfileRecommendationRequest,
    ProfileSelectAiProfileRequest,
    QueryResults,
    RepairData,
    RepairRequest,
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
    SchemaColumn,
    SchemaTable,
    SelectAiAgentAsset,
    SelectAiAgentAssetsData,
    SelectAiDbProfile,
    SelectAiDbProfileDetailData,
    SelectAiDbProfileMutationData,
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
    SimilarHistoryRequest,
    StageTiming,
    SyntheticCase,
    SyntheticCasesData,
    SyntheticDataGenerateRequest,
    SyntheticDataOperationData,
    SyntheticDataOperationStatusData,
    SyntheticDataResultsData,
    TimingEnvelope,
)
from .oracle_adapter import OracleAdapterError, OracleNl2SqlAdapter
from .store import MemoryNl2SqlStore, Nl2SqlStore, OracleJsonNl2SqlStore

logger = logging.getLogger(__name__)

_JoinWherePromptProfile = Literal["join_where_strict", "sql_structure"]

_SAMPLE_PROFILE_ID = "sql_assist_sample"
_SAMPLE_CONFIRMATION = "SQL_ASSIST_SAMPLE"
_SAMPLE_OBJECTS = [
    "DEPARTMENT",
    "EMPLOYEE",
    "PROJECT",
    "V_EMP_DEPT",
    "V_DEPT_PROJECT",
]
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
_COMMENT_TARGET = re.compile(
    r"^comment\s+on\s+([a-zA-Z_]+(?:\s+[a-zA-Z_]+)?(?:\s+[a-zA-Z_]+)?)\b",
    re.IGNORECASE,
)
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
_JOIN_WHERE_STRICT_SYSTEM_PROMPT = (
    "You are a SQL parser. Output ONLY the requested format. No explanations."
)
_JOIN_WHERE_STRICT_PROMPT = (
    "Extract ONLY JOIN and WHERE conditions from the SQL query below.\n"
    "Output in STRICT format (no explanations, no markdown, no extra text):\n\n"
    "JOIN:\n"
    "[JOIN_TYPE] alias1(schema.table1).column1 = alias2(schema.table2).column2\n"
    "[JOIN_TYPE] alias3(schema.table3).column3 = alias4(schema.table4).column4\n\n"
    "WHERE:\n"
    "alias(schema.table).column operator value\n\n"
    "Rules:\n"
    "- Format: alias(schema.table_name).column or schema.table_name.column (if no alias)\n"
    "- JOIN_TYPE must be one of: INNER JOIN, LEFT JOIN, RIGHT JOIN, FULL JOIN, CROSS JOIN, JOIN\n"
    "- Include schema name if present (e.g., ADMIN.USER_ROLE)\n"
    "- One condition per line\n"
    "- Keep original operators (=, >, <, LIKE, IN, etc.)\n"
    "- Preserve exact column names and values with quotes\n"
    "- If no JOIN/WHERE exists, output 'JOIN:\\nNone' or 'WHERE:\\nNone'\n\n"
    "SQL:\n```sql\n{sql}\n```"
)
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


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL while keeping quoted strings and PL/SQL blocks intact enough for admin use."""
    text = str(sql or "")
    statements: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    block_depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_single:
            buffer.append(char)
            if char == "'" and next_char == "'":
                buffer.append(next_char)
                index += 2
                continue
            if char == "'":
                in_single = False
            index += 1
            continue
        if in_double:
            buffer.append(char)
            if char == '"':
                in_double = False
            index += 1
            continue
        if char == "'":
            in_single = True
            buffer.append(char)
            index += 1
            continue
        if char == '"':
            in_double = True
            buffer.append(char)
            index += 1
            continue
        ahead = text[index:].lower()
        if re.match(r"^\s*(begin|declare)\b", ahead):
            block_depth = max(block_depth, 1)
        if char == ";" and block_depth > 0:
            joined = "".join(buffer).lower()
            if re.search(r"\bend\s*$", joined):
                block_depth = 0
                statement = "".join(buffer).strip()
                if statement:
                    statements.append(statement)
                buffer = []
                index += 1
                continue
        if char == ";" and block_depth == 0:
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
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
        "grant",
        "revoke",
    ):
        if re.match(rf"^{keyword}\b", stripped, flags=re.IGNORECASE):
            return keyword.upper()
    return "UNKNOWN"


def _normalize_admin_statement(sql: str) -> str:
    stripped = str(sql or "").strip()
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
        re.compile(
            r"^comment\s+on\s+(table|column|materialized\s+view|view)\b",
            re.IGNORECASE,
        ),
    ),
    "annotation_sql": (
        re.compile(r"^alter\s+(table|view)\b", re.IGNORECASE),
    ),
}

_DB_ADMIN_POLICY_LABELS = {
    "table_ddl": "CREATE TABLE / COMMENT ON / DROP TABLE",
    "view_ddl": "CREATE [OR REPLACE] VIEW / COMMENT ON / DROP VIEW",
    "data_dml": "INSERT / UPDATE / DELETE / MERGE / TRUNCATE",
    "comment_sql": "COMMENT ON TABLE/COLUMN/MATERIALIZED VIEW/VIEW",
    "annotation_sql": (
        "ALTER TABLE MODIFY ... ANNOTATIONS / ALTER TABLE ANNOTATIONS / ALTER VIEW ANNOTATIONS"
    ),
}


def _db_admin_policy_error(statement: str, policy: str) -> str:
    """policy に反する statement なら日本語エラーを返す(許可なら空文字)。"""
    stripped = _strip_leading_sql_comments(statement).strip()
    if policy == "annotation_sql":
        return _annotation_statement_error(stripped)
    for pattern in _DB_ADMIN_STATEMENT_POLICIES[policy]:
        if pattern.match(stripped):
            return ""
    return f"禁止された操作です。{_DB_ADMIN_POLICY_LABELS[policy]} のみ実行できます。"


def _annotation_statement_error(statement: str) -> str:
    norm = re.sub(r"\s+", " ", statement.strip())
    object_ref = _SQL_OBJECT_REF
    allowed_patterns = (
        rf"^alter\s+table\s+{object_ref}\s+annotations\s*\(.+\)\s*$",
        rf"^alter\s+table\s+{object_ref}\s+modify\s*\(.+\s+annotations\s*\(.+\)\s*\)\s*$",
        rf"^alter\s+table\s+{object_ref}\s+modify\s+.+\s+annotations\s*\(.+\)\s*$",
        rf"^alter\s+view\s+{object_ref}\s+annotations\s*\(.+\)\s*$",
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
                    '\"COMMENT\" と二重引用符で囲んでください。'
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


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
                name="標準プロファイル",
                description=(
                    "業務表は未固定です。schema refresh または明示 import 後に"
                    "対象表を選択します。"
                ),
                allowed_tables=[],
                glossary={},
                sql_rules=["SELECT/WITH のみ", "FETCH FIRST で行数制限"],
                default_row_limit=settings.nl2sql_default_row_limit,
                few_shot_examples=[],
            )
        }
        self._jobs: dict[str, StoredJob] = {}
        self._history: list[HistoryItem] = []
        self._compare_records: list[CompareRecord] = []
        self._evaluation_sets: list[EvaluationSet] = []
        self._evaluation_runs: list[EvaluationRunRecord] = []
        self._feedback: dict[str, FeedbackRating] = {}
        self._feedback_indexed_ids: set[str] = set()
        self._feedback_similarity_threshold = 0.0
        self._feedback_match_limit = 3
        self._classifier_examples: list[ClassifierTrainingExample] = []
        self._classifier_artifact: dict[str, Any] | None = None
        self._classifier_model_registry: dict[str, dict[str, Any]] = {}
        self._asset_meta: dict[Nl2SqlEngine, AssetRefreshData] = {}
        self._admin_audit: list[dict[str, Any]] = []
        self._legacy_learning_material = LegacyLearningMaterialData()
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
            feedback_config = snapshot.get("feedback_search_config", {})
            classifier_examples = [
                ClassifierTrainingExample.model_validate(item)
                for item in snapshot.get("classifier_examples", [])
            ]
            classifier_artifact = snapshot.get("classifier_artifact")
            if classifier_artifact is not None and not isinstance(classifier_artifact, dict):
                classifier_artifact = None
            classifier_model_registry = {
                str(version): dict(data)
                for version, data in snapshot.get("classifier_model_registry", {}).items()
                if isinstance(data, dict)
            }
            admin_audit = [
                dict(item) for item in snapshot.get("admin_audit", []) if isinstance(item, dict)
            ]
            legacy_learning_material = LegacyLearningMaterialData.model_validate(
                snapshot.get("legacy_learning_material", {})
            )
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
            self._feedback_similarity_threshold = float(
                feedback_config.get("similarity_threshold", 0.0)
            )
            self._feedback_match_limit = int(feedback_config.get("match_limit", 3))
            self._classifier_examples = classifier_examples
            self._classifier_artifact = classifier_artifact
            self._classifier_model_registry = classifier_model_registry
            self._asset_meta = asset_meta
            self._admin_audit = admin_audit[-200:]
            self._legacy_learning_material = legacy_learning_material

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
            "feedback_search_config": {
                "similarity_threshold": self._feedback_similarity_threshold,
                "match_limit": self._feedback_match_limit,
            },
            "classifier_examples": [
                item.model_dump(mode="json") for item in self._classifier_examples
            ],
            "classifier_artifact": self._classifier_artifact,
            "classifier_model_registry": self._classifier_model_registry,
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

    def sample_data_info(self) -> SampleDataInfo:
        sql = self._sample_sql_sections()
        imported = self._sample_imported_objects()
        return SampleDataInfo(
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            profile_id=_SAMPLE_PROFILE_ID,
            confirmation=_SAMPLE_CONFIRMATION,
            objects=list(_SAMPLE_OBJECTS),
            imported_objects=imported,
            sql=sql,
        )

    def import_sample_data(self, request: SampleDataMutationRequest) -> SampleDataMutationData:
        started = time.monotonic()
        created_at = _utc_now()
        step = request.step
        sql_sections = self._sample_sql_sections()
        statements = self._sample_import_statements(step, sql_sections)
        warnings: list[str] = []
        executed = False
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
            execution = self.execute_db_admin_sql(
                DbAdminExecuteRequest(
                    sql=";\n".join(statements),
                    confirmation="ADMIN_EXECUTE",
                    reason=request.reason or "sql-assist-sample-import",
                )
            )
            results = execution.statements
            warnings.extend(execution.warnings)
            executed = execution.executed
            if executed:
                self._ensure_sample_profile()
        else:
            self._apply_sample_import_to_catalog(step)
            self._ensure_sample_profile()
            results = self._statement_results(statements, status="applied_to_local_state")
            executed = True
            self._persist_state()
        return SampleDataMutationData(
            operation="import",
            step=step,
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            executed=executed,
            objects=list(_SAMPLE_OBJECTS),
            statements=results,
            warnings=warnings,
            profile_id=_SAMPLE_PROFILE_ID,
            timing=self._timing(created_at, started, "sample_data_import"),
        )

    def delete_sample_data(self, request: SampleDataMutationRequest) -> SampleDataMutationData:
        started = time.monotonic()
        created_at = _utc_now()
        statements = self._sample_sql_sections()["delete"]
        warnings: list[str] = []
        executed = False
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
            execution = self.execute_db_admin_sql(
                DbAdminExecuteRequest(
                    sql=";\n".join(statements),
                    confirmation="ADMIN_EXECUTE",
                    reason=request.reason or "sql-assist-sample-delete",
                )
            )
            warnings.extend(execution.warnings)
            results = []
            for index, item in enumerate(execution.statements):
                if item.status == "error" and self._is_missing_object_error(
                    item.error_message
                ):
                    statement = item.sql or (
                        statements[index] if index < len(statements) else ""
                    )
                    warnings.append(f"{statement}: 対象が存在しないため skip しました。")
                    item = item.model_copy(update={"status": "skipped_missing_object"})
                results.append(item)
            executed = bool(results) and all(
                item.status in {"success", "skipped_missing_object"} for item in results
            )
            if executed:
                self._remove_sample_from_state()
        else:
            self._remove_sample_from_state()
            results = self._statement_results(statements, status="applied_to_local_state")
            executed = True
            self._persist_state()
        return SampleDataMutationData(
            operation="delete",
            step=SampleDataStep.ALL,
            runtime="oracle" if self._use_oracle_runtime() else "deterministic",
            executed=executed,
            objects=list(_SAMPLE_OBJECTS),
            statements=results,
            warnings=warnings,
            profile_id=_SAMPLE_PROFILE_ID,
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

    def _sample_imported_objects(self) -> list[str]:
        existing = {table.table_name for table in self._catalog.tables}
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
        current = {table.table_name: table for table in self._catalog.tables}
        sample = {table.table_name: table for table in self._sample_schema_tables(step)}
        current.update(sample)
        ordered = [name for name in _SAMPLE_OBJECTS if name in current]
        ordered.extend(name for name in current if name not in ordered)
        self._catalog = SchemaCatalog(
            refreshed_at=_utc_now(),
            tables=[current[name] for name in ordered],
        )

    def _remove_sample_from_state(self) -> None:
        sample_objects = set(_SAMPLE_OBJECTS)
        self._catalog = SchemaCatalog(
            refreshed_at=_utc_now(),
            tables=[
                table
                for table in self._catalog.tables
                if table.table_name not in sample_objects
            ],
        )
        profile = self._profiles.get(_SAMPLE_PROFILE_ID)
        if profile is not None:
            self._profiles[_SAMPLE_PROFILE_ID] = profile.model_copy(update={"archived": True})
        self._persist_state()

    def _ensure_sample_profile(self) -> None:
        self._profiles[_SAMPLE_PROFILE_ID] = Nl2SqlProfile(
            id=_SAMPLE_PROFILE_ID,
            name="SQL Assist サンプル",
            description="DEPARTMENT / EMPLOYEE / PROJECT の明示 import sample profile。",
            allowed_tables=self._sample_imported_objects(),
            glossary={},
            sql_rules=["SELECT/WITH のみ", "sample data は明示 import 後のみ利用"],
            default_row_limit=get_settings().nl2sql_default_row_limit,
            few_shot_examples=[],
            archived=False,
        )

    def _sample_schema_tables(self, step: SampleDataStep) -> list[SchemaTable]:
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
                    if table.table_name in _SAMPLE_TABLES
                )
            tables = [
                table.model_copy(
                    update={"row_count": row_counts.get(table.table_name, table.row_count)}
                )
                for table in tables
            ]
        return tables

    def _sample_tables_from_ddl(self) -> list[SchemaTable]:
        sql = "\n".join(self._sample_sql_sections()["tables"])
        row_counts = self._sample_row_counts()
        result: list[SchemaTable] = []
        for match in re.finditer(
            r"CREATE\s+TABLE\s+([A-Z0-9_]+)\s*\((.*?)\)\s*$",
            sql,
            flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ):
            table_name = _normalize_identifier(match.group(1))
            body = match.group(2)
            columns: list[SchemaColumn] = []
            constraints: list[str] = []
            for raw_line in body.splitlines():
                line = raw_line.strip().rstrip(",")
                if not line:
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
                            "NOT NULL" not in line.upper()
                            and "PRIMARY KEY" not in line.upper()
                        ),
                    )
                )
                if "PRIMARY KEY" in line.upper():
                    constraints.append(f"PK_{table_name} P({column_name})")
            result.append(
                SchemaTable(
                    table_name=table_name,
                    logical_name=table_name,
                    comment="SQL Assist sample table",
                    row_count=row_counts.get(table_name),
                    constraints=constraints,
                    columns=columns,
                )
            )
        return result

    def _sample_views_from_ddl(self) -> list[SchemaTable]:
        views: list[SchemaTable] = []
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
        with self._lock:
            return [
                profile
                for profile in self._profiles.values()
                if include_archived or not profile.archived
            ]

    def profile_allowed_object_names(self, profile: Nl2SqlProfile) -> list[str]:
        """Profile が検索・Select AI で参照できる table/view 名を返す。"""
        return self._dedupe_object_names([*profile.allowed_tables, *profile.allowed_views])

    def build_select_ai_additional_instructions(
        self,
        profile: Nl2SqlProfile,
        request_instructions: str = "",
    ) -> str:
        """業務 profile の文脈を Select AI 用の決定論的な指示へまとめる。"""
        sections: list[str] = []
        description = profile.description.strip()
        if description:
            sections.append(f"## 業務説明\n{description}")

        glossary_lines = [
            f"- {term.strip()}: {definition.strip()}"
            for term, definition in sorted(
                self._effective_glossary(profile).items(), key=lambda item: item[0]
            )
            if term.strip() and definition.strip()
        ]
        if glossary_lines:
            sections.append("## 業務用語集\n" + "\n".join(glossary_lines))

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

    def _redact_select_ai_context_attributes(
        self, attributes: dict[str, Any]
    ) -> dict[str, Any]:
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
        region = config.region.strip() or settings.oci_region.strip()
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

    def import_profile_learning_material(
        self,
        *,
        profile_id: str,
        filename: str,
        content: bytes,
        mode: str = "merge",
    ) -> ProfileLearningMaterialImportData:
        warnings: list[str] = []
        normalized_mode = mode.strip().lower() or "merge"
        if normalized_mode not in {"merge", "replace"}:
            warnings.append(f"{mode}: 未対応の import mode のため merge として扱いました。")
            normalized_mode = "merge"
        parsed, skipped = self._parse_profile_learning_material_file(
            filename,
            content,
            warnings,
        )

        def patch(current: Nl2SqlProfile) -> Nl2SqlProfile:
            if normalized_mode == "replace":
                glossary = parsed["terms"]
                rules = parsed["rules"]
                examples = parsed["examples"]
            else:
                glossary = {**current.glossary, **parsed["terms"]}
                rules = self._merge_unique_strings(current.sql_rules, parsed["rules"])
                examples = self._merge_few_shot_examples(
                    current.few_shot_examples,
                    parsed["examples"],
                )
            return current.model_copy(
                update={
                    "glossary": glossary,
                    "sql_rules": rules,
                    "few_shot_examples": examples,
                }
            )

        updated = self.update_profile(profile_id, patch)
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
        terms_sheet.append(["TERM", "DEFINITION"])
        for term, definition in profile.glossary.items():
            terms_sheet.append([term, definition])
        rules_sheet = workbook.create_sheet("rules")
        rules_sheet.append(["CATEGORY", "RULE"])
        for rule in profile.sql_rules:
            rules_sheet.append([profile.name, rule])
        examples_sheet = workbook.create_sheet("few_shot")
        examples_sheet.append(["QUESTION", "SQL"])
        for example in profile.few_shot_examples:
            examples_sheet.append([example.get("question", ""), example.get("sql", "")])
        buffer = io.BytesIO()
        workbook.save(buffer)
        safe_profile = _csv_identifier(profile.id or profile.name, "PROFILE").lower()
        return f"nl2sql_{safe_profile}_learning_material.xlsx", buffer.getvalue()

    def get_legacy_learning_material(self) -> LegacyLearningMaterialData:
        with self._lock:
            return self._legacy_learning_material.model_copy(deep=True)

    def import_legacy_terms(self, *, filename: str, content: bytes) -> LegacyLearningMaterialData:
        warnings: list[str] = []
        glossary = self._parse_legacy_terms_file(filename, content, warnings)
        with self._lock:
            self._legacy_learning_material = self._legacy_learning_material.model_copy(
                update={"glossary": glossary}
            )
        self._persist_state()
        return self.get_legacy_learning_material()

    def import_legacy_rules(self, *, filename: str, content: bytes) -> LegacyLearningMaterialData:
        warnings: list[str] = []
        rules = self._parse_legacy_rules_file(filename, content, warnings)
        with self._lock:
            self._legacy_learning_material = self._legacy_learning_material.model_copy(
                update={"rules": rules}
            )
        self._persist_state()
        return self.get_legacy_learning_material()

    def export_legacy_terms_xlsx(self) -> tuple[str, bytes]:
        material = self.get_legacy_learning_material()
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "terms"
        sheet.append(["TERM", "DEFINITION"])
        for term, definition in material.glossary.items():
            sheet.append([term, definition])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "terms.xlsx", buffer.getvalue()

    def export_legacy_rules_xlsx(self) -> tuple[str, bytes]:
        material = self.get_legacy_learning_material()
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "rules"
        sheet.append(["RULE"])
        for rule in material.rules:
            sheet.append([rule])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "rules.xlsx", buffer.getvalue()

    def archive_profile(self, profile_id: str) -> Nl2SqlProfile:
        return self.update_profile(profile_id, lambda p: p.model_copy(update={"archived": True}))

    def restore_profile(self, profile_id: str) -> Nl2SqlProfile:
        return self.update_profile(profile_id, lambda p: p.model_copy(update={"archived": False}))

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
            select_ai_overrides=request.select_ai_overrides,
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
        if not self._use_oracle_runtime() and not self._catalog.tables:
            return (
                SafetyReport(
                    is_safe=False,
                    is_select_only=is_select_only(sql),
                    row_limit_applied=row_limit,
                    blocked_reason=_SCHEMA_EMPTY_MESSAGE,
                ),
                executable,
                QueryResults(columns=[], rows=[], total=0),
            )
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

    def analyze_sql(
        self,
        sql: str,
        allowed: AllowedObjects,
        row_limit: int,
        *,
        use_llm: bool = False,
    ) -> AnalyzeData:
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
        structure = self._sql_structure(sql, referenced)
        risk_findings = [
            item
            for item in [
                blocked_reason,
                *warnings,
                *self._optimization_hints(safety=safety, sql=sql, row_limit=row_limit),
            ]
            if item
        ]
        repair_candidates = [repaired_sql] if repaired_sql else []
        data = AnalyzeData(
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
            return self._enhance_sql_analysis_with_llm(data, sql, allowed)
        return data

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

    def feedback_index_status(self) -> FeedbackIndexData:
        return self._feedback_index_data(operation="status", include_bad=False)

    def rebuild_feedback_index(self, request: FeedbackIndexRequest) -> FeedbackIndexData:
        return self._feedback_index_data(operation="rebuild", include_bad=request.include_bad)

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
        limit = request.limit or self._feedback_match_limit
        threshold = self._feedback_similarity_threshold
        filtered = [item for item in ranked if item.score >= threshold]
        return SimilarHistoryData(items=filtered[:limit])

    def list_feedback_entries(self) -> FeedbackEntriesData:
        with self._lock:
            items = [
                FeedbackVectorEntry(
                    history_id=item.id,
                    question=item.question,
                    generated_sql=item.generated_sql,
                    profile_id=item.profile_id,
                    profile_name=item.profile_name,
                    feedback_rating=item.feedback_rating,
                    feedback_comment=item.feedback_comment,
                    indexed=item.id in self._feedback_indexed_ids,
                    created_at=item.created_at,
                )
                for item in reversed(self._history)
            ]
            indexed_count = sum(1 for item in items if item.indexed)
        return FeedbackEntriesData(items=items, total=len(items), indexed_count=indexed_count)

    def delete_feedback_entries(self, history_ids: list[str]) -> FeedbackEntriesData:
        ids = {item.strip() for item in history_ids if item.strip()}
        if not ids:
            return self.list_feedback_entries()
        with self._lock:
            self._history = [item for item in self._history if item.id not in ids]
            for item_id in ids:
                self._feedback.pop(item_id, None)
            self._feedback_indexed_ids.difference_update(ids)
        self._persist_state()
        return self.list_feedback_entries()

    def feedback_search_config(self) -> FeedbackSearchConfigData:
        with self._lock:
            return FeedbackSearchConfigData(
                similarity_threshold=self._feedback_similarity_threshold,
                match_limit=self._feedback_match_limit,
            )

    def update_feedback_search_config(
        self, request: FeedbackSearchConfigRequest
    ) -> FeedbackSearchConfigData:
        with self._lock:
            self._feedback_similarity_threshold = request.similarity_threshold
            self._feedback_match_limit = request.match_limit
        self._persist_state()
        return self.feedback_search_config()

    def classifier_status(self) -> ClassifierStatusData:
        with self._lock:
            artifact = dict(self._classifier_artifact or {})
            examples = list(self._classifier_examples)
        categories = sorted({item.category for item in examples})
        ready = bool(artifact.get("model_base64") and artifact.get("categories"))
        warnings: list[str] = []
        if not examples:
            warnings.append("分類器の training data が未登録です。")
        if not ready:
            warnings.append("LogisticRegression classifier は未学習です。")
        return ClassifierStatusData(
            ready=ready,
            trained=ready,
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
            vector_dimension=1536,
            persistence_mode=self._store.mode,
            recommendation_source="classifier" if ready else "deterministic",
            metrics=dict(artifact.get("metrics") or {}),
            warnings=warnings,
        )

    def import_classifier_training_data(
        self,
        *,
        filename: str,
        content: bytes,
        replace: bool = False,
        profile_id: str | None = None,
    ) -> ClassifierImportData:
        warnings: list[str] = []
        parsed, skipped = self._parse_classifier_training_file(filename, content, warnings)
        examples = [
            ClassifierTrainingExample(
                id=str(uuid.uuid4()),
                category=category,
                text=text,
                profile_id=profile_id or self._profile_id_for_classifier_category(category),
                source=filename,
            )
            for category, text in parsed
        ]
        with self._lock:
            if replace:
                self._classifier_examples = examples
                self._classifier_artifact = None
            else:
                self._classifier_examples.extend(examples)
                if examples:
                    self._classifier_artifact = None
            total_examples = len(self._classifier_examples)
            all_categories = sorted({item.category for item in self._classifier_examples})
        self._persist_state()
        return ClassifierImportData(
            imported_count=len(examples),
            skipped_count=skipped,
            total_examples=total_examples,
            categories=all_categories,
            warnings=warnings,
            examples=examples[:50],
        )

    def classifier_training_data(self) -> ClassifierTrainingDataData:
        with self._lock:
            examples = list(self._classifier_examples)
        categories = sorted({item.category for item in examples})
        warnings = [] if examples else ["分類器の training data が未登録です。"]
        return ClassifierTrainingDataData(
            total_examples=len(examples),
            categories=categories,
            warnings=warnings,
            examples=examples,
        )

    def train_classifier(self, request: ClassifierTrainRequest) -> ClassifierStatusData:
        with self._lock:
            examples = list(self._classifier_examples)
        warnings: list[str] = []
        if not examples:
            return ClassifierStatusData(
                ready=False,
                trained=False,
                example_count=0,
                category_count=0,
                persistence_mode=self._store.mode,
                warnings=["分類器の training data が未登録です。"],
            )

        counts: dict[str, int] = {}
        for item in examples:
            counts[item.category] = counts.get(item.category, 0) + 1
        eligible = [
            item
            for item in examples
            if counts.get(item.category, 0) >= request.min_examples_per_category
        ]
        categories = sorted({item.category for item in eligible})
        if len(categories) < 2:
            return ClassifierStatusData(
                ready=False,
                trained=False,
                example_count=len(examples),
                category_count=len(categories),
                categories=categories,
                persistence_mode=self._store.mode,
                warnings=["LogisticRegression には 2 category 以上の training data が必要です。"],
            )

        try:
            vectors, embedding_warnings, embedding_model = self._classifier_vectors(
                [item.text for item in eligible]
            )
            warnings.extend(embedding_warnings)
            linear_model = importlib.import_module("sklearn.linear_model")
            joblib = importlib.import_module("joblib")
            model = linear_model.LogisticRegression(max_iter=1000, random_state=42)
            labels = [item.category for item in eligible]
            model.fit(vectors, labels)
            score = float(model.score(vectors, labels))
            buffer = io.BytesIO()
            joblib.dump(model, buffer)
        except Exception as exc:
            return ClassifierStatusData(
                ready=False,
                trained=False,
                example_count=len(examples),
                category_count=len(categories),
                categories=categories,
                persistence_mode=self._store.mode,
                warnings=[f"分類器の学習に失敗しました: {exc}"],
            )

        now = _utc_now()
        artifact = {
            "version": str(uuid.uuid4()),
            "updated_at": now,
            "model_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "categories": categories,
            "embedding_model": embedding_model,
            "vector_dimension": 1536,
            "metrics": {
                "training_examples": len(eligible),
                "category_count": len(categories),
                "training_accuracy": round(score, 4),
            },
        }
        with self._lock:
            self._classifier_artifact = artifact
            self._classifier_model_registry[str(artifact["version"])] = dict(artifact)
        self._persist_state()
        return self.classifier_status().model_copy(update={"warnings": warnings})

    def list_classifier_models(self) -> ClassifierModelsData:
        with self._lock:
            active = dict(self._classifier_artifact or {})
            registry = {
                str(version): dict(data)
                for version, data in self._classifier_model_registry.items()
            }
            if active.get("version"):
                registry[str(active["version"])] = active
        active_version = str(active.get("version") or "")
        models = [
            self._classifier_model_info(version, artifact, active_version=active_version)
            for version, artifact in registry.items()
        ]
        models.sort(key=lambda item: item.updated_at, reverse=True)
        return ClassifierModelsData(active_version=active_version, models=models)

    def activate_classifier_model(self, version: str) -> ClassifierModelActivateData:
        with self._lock:
            artifact = self._classifier_model_registry.get(version)
            if artifact is None and self._classifier_artifact:
                current_version = str(self._classifier_artifact.get("version") or "")
                if current_version == version:
                    artifact = dict(self._classifier_artifact)
            if artifact is None:
                return ClassifierModelActivateData(
                    active_version=str((self._classifier_artifact or {}).get("version") or ""),
                    warnings=[f"{version}: classifier model が見つかりません。"],
                )
            self._classifier_artifact = dict(artifact)
            self._classifier_model_registry[version] = dict(artifact)
        self._persist_state()
        return ClassifierModelActivateData(
            active_version=version,
            model=self._classifier_model_info(version, artifact, active_version=version),
        )

    def delete_classifier_model(self, version: str) -> ClassifierModelsData:
        with self._lock:
            self._classifier_model_registry.pop(version, None)
            if self._classifier_artifact and self._classifier_artifact.get("version") == version:
                self._classifier_artifact = None
        self._persist_state()
        return self.list_classifier_models()

    def import_classifier_model_artifact(
        self, *, filename: str, content: bytes, activate: bool = True
    ) -> ClassifierModelImportData:
        warnings: list[str] = []
        suffix = Path(filename).suffix.lower()
        raw_model: bytes
        meta: dict[str, Any] = {}
        try:
            if suffix == ".json":
                payload = json.loads(content.decode("utf-8-sig"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object ではありません。")
                model_base64 = str(payload.get("model_base64") or "")
                if not model_base64:
                    raise ValueError("model_base64 がありません。")
                raw_model = base64.b64decode(model_base64)
                meta = dict(payload)
            elif suffix == ".joblib":
                raw_model = content
            else:
                raise ValueError("joblib または JSON artifact を指定してください。")
            joblib = importlib.import_module("joblib")
            model = joblib.load(io.BytesIO(raw_model))
            categories = [str(item) for item in getattr(model, "classes_", [])]
            if not categories:
                warnings.append("model.classes_ が空です。legacy meta の category を使用します。")
                categories = [str(item) for item in meta.get("categories", [])]
            version = str(meta.get("version") or uuid.uuid4())
            now = _utc_now()
            artifact = {
                "version": version,
                "updated_at": str(meta.get("updated_at") or now),
                "model_base64": base64.b64encode(raw_model).decode("ascii"),
                "categories": categories,
                "embedding_model": str(
                    meta.get("embedding_model")
                    or meta.get("embed_model")
                    or get_settings().oci_genai_embed_model_id
                    or "deterministic-hash-1536"
                ),
                "vector_dimension": int(meta.get("vector_dimension") or 1536),
                "metrics": dict(meta.get("metrics") or {}),
                "source": f"legacy:{filename}",
            }
        except Exception as exc:
            return ClassifierModelImportData(
                imported=False,
                active_version=str((self._classifier_artifact or {}).get("version") or ""),
                warnings=[f"classifier model artifact の import に失敗しました: {exc}"],
            )
        with self._lock:
            self._classifier_model_registry[version] = artifact
            if activate:
                self._classifier_artifact = artifact
        self._persist_state()
        active_version = (
            version if activate else str((self._classifier_artifact or {}).get("version") or "")
        )
        return ClassifierModelImportData(
            imported=True,
            active_version=active_version,
            model=self._classifier_model_info(version, artifact, active_version=active_version),
            warnings=warnings,
        )

    def export_classifier_training_data_xlsx(self) -> tuple[str, bytes]:
        with self._lock:
            examples = list(self._classifier_examples)
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "training_data"
        sheet.append(["CATEGORY", "TEXT", "PROFILE_ID", "SOURCE"])
        for item in examples:
            sheet.append([item.category, item.text, item.profile_id, item.source])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return "nl2sql_classifier_training_data.xlsx", buffer.getvalue()

    def export_classifier_training_data_jsonl(self) -> tuple[str, bytes]:
        with self._lock:
            lines = [
                json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
                for item in self._classifier_examples
            ]
        return "nl2sql_classifier_training_data.jsonl", ("\n".join(lines) + "\n").encode("utf-8")

    def _classifier_model_info(
        self, version: str, artifact: dict[str, Any], *, active_version: str
    ) -> ClassifierModelInfo:
        categories = [str(item) for item in artifact.get("categories", [])]
        return ClassifierModelInfo(
            version=version,
            active=version == active_version,
            updated_at=str(artifact.get("updated_at") or ""),
            category_count=len(categories),
            categories=categories,
            embedding_model=str(artifact.get("embedding_model") or ""),
            vector_dimension=int(artifact.get("vector_dimension") or 1536),
            metrics=dict(artifact.get("metrics") or {}),
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
        with self._lock:
            artifact = dict(self._classifier_artifact or {})
        if not artifact.get("model_base64"):
            return None, []
        warnings: list[str] = []
        try:
            joblib = importlib.import_module("joblib")
            raw = base64.b64decode(str(artifact["model_base64"]))
            model = joblib.load(io.BytesIO(raw))
            vectors, embedding_warnings, _embedding_model = self._classifier_vectors([question])
            warnings.extend(embedding_warnings)
            probabilities = model.predict_proba(vectors)[0]
            classes = [str(item) for item in model.classes_]
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
    ) -> tuple[list[tuple[str, str]], int]:
        suffix = Path(filename).suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return self._parse_classifier_training_xlsx(content, warnings)
        if suffix in {".csv", ".txt", ""}:
            text = content.decode("utf-8-sig", errors="replace")
            return self._parse_classifier_training_csv(text, warnings)
        warnings.append(f"{suffix} は未対応の形式です。CSV または XLSX を指定してください。")
        return [], 0

    def _parse_classifier_training_csv(
        self, text: str, warnings: list[str]
    ) -> tuple[list[tuple[str, str]], int]:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            warnings.append("CSV header が見つかりません。")
            return [], 0
        category_key, text_key = self._classifier_header_keys(reader.fieldnames)
        if not category_key or not text_key:
            warnings.append("CSV は CATEGORY と TEXT/QUESTION 列が必要です。")
            return [], 0
        rows: list[tuple[str, str]] = []
        skipped = 0
        for row in reader:
            category = str(row.get(category_key) or "").strip()
            value = str(row.get(text_key) or "").strip()
            if not category or not value:
                skipped += 1
                continue
            rows.append((category, value))
        return rows, skipped

    def _parse_classifier_training_xlsx(
        self, content: bytes, warnings: list[str]
    ) -> tuple[list[tuple[str, str]], int]:
        try:
            openpyxl = importlib.import_module("openpyxl")
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            warnings.append(f"XLSX の読込に失敗しました: {exc}")
            return [], 0
        sheet = workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        headers = [str(value or "").strip() for value in next(rows_iter, [])]
        category_key, text_key = self._classifier_header_keys(headers)
        if not category_key or not text_key:
            warnings.append("XLSX は CATEGORY と TEXT/QUESTION 列が必要です。")
            return [], 0
        category_index = headers.index(category_key)
        text_index = headers.index(text_key)
        rows: list[tuple[str, str]] = []
        skipped = 0
        for raw_row in rows_iter:
            category = (
                str(raw_row[category_index] or "").strip() if len(raw_row) > category_index else ""
            )
            value = str(raw_row[text_index] or "").strip() if len(raw_row) > text_index else ""
            if not category or not value:
                skipped += 1
                continue
            rows.append((category, value))
        return rows, skipped

    def _classifier_header_keys(self, headers: Sequence[str]) -> tuple[str, str]:
        normalized = {self._normalize_training_header(header): header for header in headers}
        category = (
            normalized.get("CATEGORY") or normalized.get("PROFILE") or normalized.get("LABEL")
        )
        text = (
            normalized.get("TEXT")
            or normalized.get("QUESTION")
            or normalized.get("PROMPT")
            or normalized.get("UTTERANCE")
        )
        return category or "", text or ""

    def _normalize_training_header(self, value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "_", value.strip().upper()).strip("_")

    def _parse_profile_learning_material_file(
        self,
        filename: str,
        content: bytes,
        warnings: list[str],
    ) -> tuple[dict[str, Any], int]:
        suffix = Path(filename).suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            return self._parse_profile_learning_material_xlsx(content, warnings)
        if suffix in {".csv", ".tsv", ".txt", ""}:
            text = content.decode("utf-8-sig", errors="replace")
            first_line = text.splitlines()[0] if text.splitlines() else ""
            delimiter = "\t" if suffix == ".tsv" or "\t" in first_line else ","
            return self._parse_profile_learning_material_csv(
                text,
                warnings,
                kind_hint=self._learning_material_kind_hint(filename),
                delimiter=delimiter,
            )
        warnings.append(f"{suffix} は未対応の形式です。CSV または XLSX を指定してください。")
        return self._empty_learning_material(), 0

    def _legacy_material_rows(
        self, filename: str, content: bytes, warnings: list[str]
    ) -> list[tuple[list[str], list[Sequence[Any]]]]:
        suffix = Path(filename).suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            try:
                openpyxl = importlib.import_module("openpyxl")
                workbook = openpyxl.load_workbook(
                    io.BytesIO(content), read_only=True, data_only=True
                )
            except Exception as exc:
                warnings.append(f"XLSX の読込に失敗しました: {exc}")
                return []
            sheets: list[tuple[list[str], list[Sequence[Any]]]] = []
            for sheet in workbook.worksheets:
                rows_iter = sheet.iter_rows(values_only=True)
                headers = [str(value or "").strip() for value in next(rows_iter, [])]
                if any(headers):
                    sheets.append((headers, list(rows_iter)))
            return sheets
        if suffix in {".csv", ".tsv", ".txt", ""}:
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
        warnings.append(f"{suffix} は未対応の形式です。CSV または XLSX を指定してください。")
        return []

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

    def _parse_profile_learning_material_xlsx(
        self,
        content: bytes,
        warnings: list[str],
    ) -> tuple[dict[str, Any], int]:
        try:
            openpyxl = importlib.import_module("openpyxl")
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            warnings.append(f"XLSX の読込に失敗しました: {exc}")
            return self._empty_learning_material(), 0
        merged = self._empty_learning_material()
        skipped = 0
        for sheet in workbook.worksheets:
            rows_iter = sheet.iter_rows(values_only=True)
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
        normalized_names = {self._normalize_training_header(name) for name in names}
        raw_names = {name.strip().upper() for name in names}
        for index, header in enumerate(headers):
            raw = header.strip()
            normalized = self._normalize_training_header(raw)
            if normalized in normalized_names or raw.upper() in raw_names:
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
        profile = self._profile_for_classifier_category(category)
        return profile.id if profile else ""

    def _profile_for_classifier_category(self, category: str) -> Nl2SqlProfile | None:
        normalized = category.strip().lower()
        profiles = self.list_profiles()
        for profile in profiles:
            if normalized in {profile.id.lower(), profile.name.lower()}:
                return profile
        scored = [
            (
                len(
                    _similarity_tokens(category)
                    & _similarity_tokens(f"{profile.name} {profile.description}")
                ),
                profile,
            )
            for profile in profiles
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
        return profiles[0] if profiles else None

    def _feedback_index_data(self, *, operation: str, include_bad: bool) -> FeedbackIndexData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        with self._lock:
            indexable = self._feedback_indexable_history(include_bad)
            source_count = len(self._history)
            indexed_count = len(self._feedback_indexed_ids)
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
        classifier_prediction, classifier_warnings = self._classifier_prediction(
            request.question, top_k=3
        )
        if classifier_prediction and classifier_prediction.candidates:
            mapped_candidates: list[ProfileRecommendationCandidate] = []
            for candidate in classifier_prediction.candidates:
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
                        category=candidate.category,
                    )
                )
            if mapped_candidates:
                best = mapped_candidates[0]
                profile = self.get_profile(best.profile_id)
                reason = (
                    f"LogisticRegression classifier が category "
                    f"{best.category or classifier_prediction.predicted_category} を予測しました。"
                )
                if classifier_warnings:
                    reason = f"{reason} {' '.join(classifier_warnings)}"
                return self._recommendation_from_profile(
                    profile=profile,
                    question=request.question,
                    score=best.score,
                    matched_terms=best.matched_terms,
                    candidates=mapped_candidates,
                ).model_copy(
                    update={
                        "reason": reason,
                        "recommendation_source": "classifier",
                        "classifier_version": classifier_prediction.classifier_version,
                        "category_scores": {
                            candidate.category: candidate.score
                            for candidate in classifier_prediction.candidates
                        },
                    }
                )

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
                allowed_tables=self.profile_allowed_object_names(profile),
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

    def rewrite(self, request: RewriteRequest) -> RewriteData:
        profile = self.get_profile(request.profile_id)
        warnings: list[str] = []
        deterministic = self.rewrite_question(request.question, profile)
        if not self._enterprise_ai_client.is_configured():
            return RewriteData(
                original_question=request.question,
                rewritten_question=deterministic,
                source="deterministic",
                warnings=[
                    "OCI Enterprise AI が未設定のため deterministic rewrite を使用しました。"
                ],
            )
        try:
            context = self._rewrite_context(
                profile=profile,
                use_glossary=request.use_glossary,
                use_schema=request.use_schema,
                extra_prompt=request.extra_prompt,
            )
            rewritten = self._enterprise_ai_client.generate(
                prompt=request.question,
                context=context,
                system_prompt=(
                    "あなたは日本語の NL2SQL 入力を業務語彙と Oracle schema に合わせて"
                    "検索意図が保たれるように書き換えるアシスタントです。"
                    "SQL は生成せず、書き換え後の自然言語質問だけを返してください。"
                ),
            ).strip()
            rewritten = self._strip_code_fence(rewritten).splitlines()[0].strip()
            if not rewritten:
                rewritten = deterministic
                warnings.append("Enterprise AI rewrite が空だったため fallback しました。")
            return RewriteData(
                original_question=request.question,
                rewritten_question=rewritten,
                source="oci_enterprise_ai",
                model=self._enterprise_ai_client.model_id(),
                warnings=warnings,
            )
        except EnterpriseAiDirectError as exc:
            return RewriteData(
                original_question=request.question,
                rewritten_question=deterministic,
                source="deterministic",
                warnings=[f"Enterprise AI rewrite に失敗したため fallback しました: {exc}"],
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
        structure = self._sql_structure(request.sql, referenced)
        profile = self.get_profile(request.profile_id)
        question = self._apply_reverse_glossary(
            f"{table_names} のデータを条件に沿って確認したい",
            profile=profile,
            enabled=request.use_glossary,
        )
        logical_structure = self._reverse_logical_structure(structure)
        if request.use_glossary:
            logical_structure = self._apply_reverse_glossary(
                logical_structure,
                profile=profile,
                enabled=True,
            )
        logical_steps = [
            structure["summary"],
            *[f"条件: {item}" for item in structure["filters"][:3]],
            *[f"結合: {item}" for item in structure["joins"][:3]],
            *[f"集計: {item}" for item in structure["aggregations"][:3]],
        ]
        if request.use_glossary:
            logical_steps = [
                self._apply_reverse_glossary(step, profile=profile, enabled=True)
                for step in logical_steps
            ]
        return ReverseSqlData(
            question=question,
            explanation=("SELECT 句・FROM/JOIN 句・条件・集計をもとに自然言語説明を生成しました。"),
            referenced_tables=referenced,
            logical_structure=logical_structure,
            logical_steps=logical_steps,
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
            raw = self._enterprise_ai_client.generate(
                prompt=request.sql,
                context=self._enterprise_ai_schema_context(
                    profile=context_profile,
                    allowed=AllowedObjects(),
                    use_glossary=request.use_glossary,
                ),
                system_prompt=(
                    "Oracle SQL を日本語の業務質問へ逆生成してください。"
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
            steps_raw = payload.get("logical_steps")
            steps = [str(item) for item in steps_raw] if isinstance(steps_raw, list) else []
            return deterministic.model_copy(
                update={
                    "question": question,
                    "explanation": explanation,
                    "logical_structure": logical_structure,
                    "logical_steps": steps or deterministic.logical_steps,
                    "source": "oci_enterprise_ai",
                }
            )
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": [f"Enterprise AI reverse に失敗したため fallback しました: {exc}"]
                }
            )

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

    def _apply_reverse_glossary(
        self, text: str, *, profile: Nl2SqlProfile, enabled: bool
    ) -> str:
        glossary = self._effective_glossary(profile)
        if not enabled or not glossary:
            return text
        result = text
        for term, definition in glossary.items():
            normalized_definition = str(definition).strip()
            candidates = [normalized_definition]
            if "." in normalized_definition:
                candidates.append(normalized_definition.rsplit(".", 1)[-1])
            for candidate in candidates:
                if candidate:
                    result = result.replace(candidate, term)
        return result

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
        filters = self._extract_sql_clauses(normalized, "where", ["group by", "order by", "fetch"])
        joins = [
            match.group(0).strip()
            for match in re.finditer(
                rf"\b(?:left|right|inner|outer|cross)?\s*join\s+{_SQL_OBJECT_REF}(?:\s+on\s+.*?)(?=\s+(?:left|right|inner|outer|cross)?\s*join\s+|\s+where\s+|\s+group\s+by\s+|\s+order\s+by\s+|$)",
                normalized,
                re.IGNORECASE,
            )
        ]
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
            raw = self._enterprise_ai_client.generate(
                prompt=sql,
                context=self._enterprise_ai_schema_context(
                    profile=self.get_profile(None),
                    allowed=allowed,
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
                prompt="表・列・ビューの COMMENT ON 候補を日本語で生成してください。",
                context=self._comment_generation_context(options.max_items),
                system_prompt=(
                    "Oracle schema metadata を読み、業務利用者が理解しやすい日本語 comment "
                    "を生成してください。JSON object で suggestions 配列だけを返してください。"
                    "各要素は object_name, object_type, suggested_comment を持ち、"
                    "object_type は table/view/column のいずれかです。"
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
        return suggestions[:max_items]

    def _comment_generation_context(self, max_items: int) -> str:
        lines = [f"max_items: {max_items}", "schema:"]
        for table in self._catalog.tables:
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
            if not object_name or object_type not in {"table", "view", "column"} or not comment:
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
        for table in self._catalog.tables:
            table_value = table.comment or table.logical_name or table.table_name
            suggestions.append(
                AnnotationSuggestion(
                    object_name=table.table_name,
                    object_type=table.table_type or "table",
                    annotation_name="Display",
                    annotation_value=table_value,
                )
            )
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
        self, request: MetadataSqlGenerateRequest,
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
                    "TABLE/COLUMN/VIEW/MATERIALIZED VIEW ステートメントのみを出力してください。"
                    "表・ビューはA-Z順、列は定義順、各説明文は200字以内です。"
                ),
            )
            sql = self._clean_generated_metadata_sql(raw, "comment_sql")
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
            table = self._find_catalog_table(target.object_name)
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
                samples[table.table_name.upper()] = values
        return samples

    def _format_metadata_samples(
        self,
        request: MetadataSqlSampleRequest,
        samples: dict[str, dict[str, list[str]]],
    ) -> tuple[str, int]:
        blocks: list[str] = []
        sample_count = 0
        for target in request.targets:
            object_name = _normalize_identifier(target.object_name)
            column_samples = samples.get(object_name, {})
            lines: list[str] = []
            for column in target.columns:
                values = column_samples.get(_normalize_identifier(column), [])
                if values:
                    lines.append(f"{column}: {', '.join(values)}")
                    sample_count += len(values)
            if lines:
                blocks.append(f"OBJECT: {target.object_name}\n" + "\n".join(lines))
        return "\n\n".join(blocks), sample_count

    def generate_annotation_sql(
        self, request: MetadataSqlGenerateRequest,
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
                    "以下の情報に基づき、Oracle ALTER TABLE/ALTER VIEW の ANNOTATIONS 文のみを"
                    "生成してください。\n\n"
                    "出力ルール:\n"
                    "- 純粋な ALTER TABLE/ALTER VIEW ANNOTATIONS ステートメントのみを出力\n"
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
                    "ALTER TABLE T1 ANNOTATIONS (ADD IF NOT EXISTS UI_Display 'Table 1');\n"
                    "ALTER TABLE T1 MODIFY (ID ANNOTATIONS "
                    "(ADD IF NOT EXISTS UI_Display 'ID', data_type 'NUMBER', nullable 'N'));\n"
                    "ALTER VIEW SALES_V ANNOTATIONS (ADD IF NOT EXISTS UI_Display 'Sales View');"
                ),
                context=self._metadata_generation_context(request),
                system_prompt=(
                    "あなたは Oracle Database の専門家です。純粋な annotation SQL のみを"
                    "出力してください。\n"
                    "テーブル: ALTER TABLE <表> ANNOTATIONS (<annotation>);\n"
                    "列: ALTER TABLE <表> MODIFY (<列> ANNOTATIONS (<annotation>));\n"
                    "ビュー: ALTER VIEW <ビュー> ANNOTATIONS (<annotation>);\n"
                    "ADD / DROP / REPLACE を使用できます。再実行可能な追加には "
                    "ADD IF NOT EXISTS を使用してください。annotation 名は Oracle 識別子です。"
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
            f"{target.object_type}:{target.object_name}" for target in request.targets
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

    def _selected_metadata_tables(self, request: MetadataSqlGenerateRequest) -> list[SchemaTable]:
        if not request.targets:
            return list(self._catalog.tables)
        selected: list[SchemaTable] = []
        for target in request.targets:
            table = self._find_catalog_table(target.object_name)
            if table is not None:
                selected.append(table)
        return selected

    def _metadata_target_types(self, request: MetadataSqlGenerateRequest) -> dict[str, str]:
        return {
            _normalize_identifier(target.object_name): target.object_type
            for target in request.targets
        }

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
            object_kind = (
                "VIEW"
                if target_types.get(_normalize_identifier(table.table_name)) == "view"
                or table.table_type.lower() == "view"
                else "TABLE"
            )
            object_comment = table.comment or table.logical_name or table.table_name
            if object_comment:
                statements.append(
                    f"COMMENT ON {object_kind} {_quote_identifier(table.table_name)} "
                    f"IS {_quote_sql_string(object_comment)};"
                )
            for column in table.columns:
                column_comment = column.comment or column.logical_name or column.column_name
                if column_comment:
                    statements.append(
                        f"COMMENT ON COLUMN {_quote_identifier(table.table_name)}."
                        f"{_quote_identifier(column.column_name)} IS "
                        f"{_quote_sql_string(column_comment)};"
                    )
        if selected_tables:
            return "\n".join(statements)
        for item in self._metadata_input_objects(request):
            object_kind = "VIEW" if item["type"] == "view" else "TABLE"
            object_comment = item["comment"] or item["name"]
            statements.append(
                f"COMMENT ON {object_kind} {_quote_identifier(item['name'])} "
                f"IS {_quote_sql_string(object_comment)};"
            )
            for column in item["columns"]:
                column_comment = column["comment"] or column["name"]
                statements.append(
                    f"COMMENT ON COLUMN {_quote_identifier(item['name'])}."
                    f"{_quote_identifier(column['name'])} IS "
                    f"{_quote_sql_string(column_comment)};"
                )
        return "\n".join(statements)

    def _deterministic_annotation_sql(self, request: MetadataSqlGenerateRequest) -> str:
        statements: list[str] = []
        target_types = self._metadata_target_types(request)
        selected_tables = sorted(
            self._selected_metadata_tables(request),
            key=lambda table: table.table_name.upper(),
        )
        for table in selected_tables:
            object_value = table.comment or table.logical_name or table.table_name
            if (
                target_types.get(_normalize_identifier(table.table_name)) == "view"
                or table.table_type.lower() == "view"
            ):
                statements.append(
                    f"ALTER VIEW {_quote_identifier(table.table_name)} "
                    "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
                    f"{_quote_sql_string(object_value)});"
                )
                continue
            statements.append(
                f"ALTER TABLE {_quote_identifier(table.table_name)} "
                "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
                f"{_quote_sql_string(object_value)});"
            )
            for column in table.columns:
                column_value = column.comment or column.logical_name or column.column_name
                statements.append(
                    f"ALTER TABLE {_quote_identifier(table.table_name)} "
                    f"MODIFY ({_quote_identifier(column.column_name)} "
                    "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
                    f"{_quote_sql_string(column_value)}));"
                )
        if selected_tables:
            return "\n".join(statements)
        for item in sorted(
            self._metadata_input_objects(request),
            key=lambda value: str(value["name"]).upper(),
        ):
            object_value = item["comment"] or item["name"]
            if item["type"] == "view":
                statements.append(
                    f"ALTER VIEW {_quote_identifier(item['name'])} "
                    "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
                    f"{_quote_sql_string(object_value)});"
                )
                continue
            statements.append(
                f"ALTER TABLE {_quote_identifier(item['name'])} "
                "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
                f"{_quote_sql_string(object_value)});"
            )
            for column in item["columns"]:
                column_value = column["comment"] or column["name"]
                statements.append(
                    f"ALTER TABLE {_quote_identifier(item['name'])} "
                    f"MODIFY ({_quote_identifier(column['name'])} "
                    "ANNOTATIONS (ADD IF NOT EXISTS UI_Display "
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
            if policy == "annotation_sql" and not has_annotation_samples:
                candidate = _without_sample_annotations(candidate)
                if not candidate:
                    continue
            policy_error = _db_admin_policy_error(candidate, policy)
            if policy_error:
                if policy == "annotation_sql":
                    raise ValueError(policy_error)
                continue
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
                    self._oracle_adapter.apply_comment_statements(
                        [statement.sql for statement in statements]
                    )
                    executed = True
                    statements = [
                        statement.model_copy(update={"status": "applied"})
                        for statement in statements
                    ]
                    self._record_admin_audit(
                        operation="comments_apply",
                        target="ADMIN_EXECUTE",
                        executed=True,
                        reason=request.reason,
                        detail={"statement_count": len(statements)},
                    )
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
        else:
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
                    self._oracle_adapter.apply_safe_statements(
                        [statement.sql for statement in statements]
                    )
                    executed = True
                    statements = [
                        statement.model_copy(update={"status": "applied"})
                        for statement in statements
                    ]
                    self._record_admin_audit(
                        operation="annotations_apply",
                        target="ADMIN_EXECUTE",
                        executed=True,
                        reason=request.reason,
                        detail={"statement_count": len(statements)},
                    )
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
        if object_type in {"table", "view"}:
            table = self._find_catalog_table(item.object_name)
            if table is None:
                raise ValueError(f"{item.object_name}: catalog に存在しない object です。")
            ddl_kind = (
                "VIEW" if object_type == "view" or table.table_type.lower() == "view" else "TABLE"
            )
            return AnnotationApplyStatement(
                object_name=table.table_name,
                object_type=object_type,
                annotation_name=annotation_name,
                annotation_value=annotation_value,
                sql=(
                    f"ALTER {ddl_kind} {_quote_identifier(table.table_name)} "
                    f"ANNOTATIONS ({annotation_name} {_quote_sql_string(annotation_value)});"
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
            return AnnotationApplyStatement(
                object_name=f"{table.table_name}.{column.column_name}",
                object_type="column",
                annotation_name=annotation_name,
                annotation_value=annotation_value,
                sql=(
                    f"ALTER TABLE {_quote_identifier(table.table_name)} "
                    f"MODIFY {_quote_identifier(column.column_name)} "
                    f"ANNOTATIONS ({annotation_name} {_quote_sql_string(annotation_value)});"
                ),
            )
        raise ValueError(f"{item.object_name}: object_type は table/view/column のみ指定できます。")

    def _annotation_name(self, value: str) -> str:
        normalized = value.strip().replace('"', "").upper()
        if not _STRICT_IDENTIFIER.fullmatch(normalized):
            raise ValueError(f"{value}: annotation name が不正です。")
        return normalized

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

    def _synthetic_unsupported_columns(self, table: SchemaTable) -> list[SchemaColumn]:
        return [
            column
            for column in table.columns
            if _synthetic_data_type_key(column.data_type)
            in _SYNTHETIC_DATA_UNSUPPORTED_DATA_TYPES
        ]

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

    def generate_synthetic_data(
        self, request: SyntheticDataGenerateRequest
    ) -> SyntheticDataOperationData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        requested_source = (
            [request.table_name] if request.table_name.strip() else request.object_list
        )
        requested_objects = [
            _normalize_identifier(item) for item in requested_source if item.strip()
        ]
        safe_objects: list[str] = []
        for object_name in requested_objects:
            table = self._find_catalog_table(object_name)
            if table is None:
                warnings.append(f"{object_name}: catalog に存在しない table です。")
                safe_objects.append(object_name)
                continue
            unsupported_columns = self._synthetic_unsupported_columns(table)
            if unsupported_columns:
                column_text = ", ".join(
                    f"{column.column_name}({column.data_type})"
                    for column in unsupported_columns
                )
                warnings.append(
                    f"{table.table_name}: DBMS_CLOUD_AI.GENERATE_SYNTHETIC_DATA は "
                    f"{column_text} を含む table をサポートしません。"
                    "BLOB/CLOB/RAW/VECTOR などを除いた view または別 table を対象にしてください。"
                )
                continue
            safe_objects.append(table.table_name)
        safe_table_name = safe_objects[0] if safe_objects else ""
        if not safe_objects:
            warnings.append("synthetic data の対象にできる table がありません。")
        executed = False
        status = "error"
        operation_id = ""
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
            confirmation_error = self._admin_confirmation_error(
                confirmation=request.confirmation,
                target=safe_table_name or "ADMIN_EXECUTE",
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
            else:
                try:
                    engine_meta.update(
                        self._oracle_adapter.generate_synthetic_data(
                            table_name=safe_table_name,
                            object_list=safe_objects if len(safe_objects) > 1 else [],
                            row_count=row_count,
                            profile_name=profile_name,
                            user_prompt=prompt,
                            sample_rows=request.sample_rows,
                            use_comments=request.use_comments,
                        )
                    )
                    operation_id = str(engine_meta.get("operation_id") or "")
                    executed = True
                    status = "submitted" if operation_id else "executed"
                    message = "DBMS_CLOUD_AI synthetic data generation を開始しました。"
                    self._record_admin_audit(
                        operation="synthetic_data_generate",
                        target=",".join(safe_objects),
                        executed=True,
                        reason=request.reason,
                        detail={
                            "profile_name": profile_name,
                            "row_count": row_count,
                            "operation_id": operation_id,
                        },
                    )
                except OracleAdapterError as exc:
                    status = "error"
                    warnings.append(str(exc))
        return SyntheticDataOperationData(
            operation_id=operation_id,
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
        safe_table_name = self._sanitize_import_table_name(table_name)
        sql = enforce_row_limit(f"SELECT * FROM {_quote_identifier(safe_table_name)}", limit)
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
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        return SyntheticDataResultsData(
            table_name=safe_table_name,
            runtime="deterministic",
            results=self._mock_execute(sql, min(limit, 20)),
            warnings=warnings
            or ["Oracle runtime ではないため deterministic result preview を返しました。"],
        )

    def synthetic_data_operation_status(
        self, operation_id: str
    ) -> SyntheticDataOperationStatusData:
        if not self._use_oracle_runtime():
            return SyntheticDataOperationStatusData(
                operation_id=operation_id,
                runtime="deterministic",
                status="requires_oracle",
                message="operation status の取得には NL2SQL_RUNTIME_MODE=oracle が必要です。",
            )
        try:
            result = self._oracle_adapter.synthetic_data_operation_status(operation_id=operation_id)
            return SyntheticDataOperationStatusData(
                operation_id=operation_id,
                runtime=str(result.get("runtime") or "oracle"),
                status=str(result.get("status") or "unknown"),
                message=str(result.get("message") or ""),
                result=dict(result.get("result") or {}),
            )
        except OracleAdapterError as exc:
            return SyntheticDataOperationStatusData(
                operation_id=operation_id,
                runtime="oracle",
                status="error",
                message=str(exc),
                warnings=[str(exc)],
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
                request_hint='{"engine":"select_ai_agent","question":"登録済みの表から主要な列を一覧したい"}',
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

    def list_db_admin_tables(self) -> DbAdminObjectsData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return DbAdminObjectsData(
                    runtime="oracle",
                    items=[
                        DbAdminObjectSummary.model_validate(item)
                        for item in self._oracle_adapter.list_db_admin_objects("table")
                    ],
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        return DbAdminObjectsData(
            runtime="deterministic",
            items=[
                DbAdminObjectSummary(
                    name=table.table_name,
                    owner=table.owner,
                    object_type="table",
                    row_count=table.row_count,
                    comment=table.comment,
                )
                for table in self._catalog.tables
                if table.table_type.lower() != "view"
            ],
            warnings=warnings,
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
                    ],
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        return DbAdminObjectsData(
            runtime="deterministic",
            items=[
                DbAdminObjectSummary(
                    name=table.table_name,
                    owner=table.owner,
                    object_type="view",
                    row_count=table.row_count,
                    comment=table.comment,
                )
                for table in self._catalog.tables
                if table.table_type.lower() == "view"
            ],
            warnings=warnings,
        )

    def get_db_admin_object(self, object_name: str, object_type: str) -> DbAdminObjectDetail:
        normalized_type = "view" if object_type.lower() == "view" else "table"
        if self._use_oracle_runtime():
            try:
                return DbAdminObjectDetail.model_validate(
                    self._oracle_adapter.get_db_admin_object_detail(
                        object_name=object_name,
                        object_type=normalized_type,
                    )
                )
            except OracleAdapterError as exc:
                fallback = self._catalog_object_detail(object_name, normalized_type)
                return fallback.model_copy(update={"warnings": [str(exc)]})
        return self._catalog_object_detail(object_name, normalized_type)

    def drop_db_admin_table(self, request: DbAdminDropTableRequest) -> DbAdminExecuteData:
        table_name = self._sanitize_import_table_name(request.table_name)
        sql = f"DROP TABLE {_quote_identifier(table_name)}" f"{' PURGE' if request.purge else ''}"
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

    def execute_db_admin_sql(self, request: DbAdminExecuteRequest) -> DbAdminExecuteData:
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        statements = _split_sql_statements(request.sql)
        if not statements:
            warnings.append("SQL statement がありません。")
        statement_types = [_admin_statement_type(statement) for statement in statements]
        select_count = sum(1 for kind in statement_types if kind == "SELECT")
        if len(statements) > 1 and select_count > 0:
            warnings.append("複数 statement 実行に SELECT は含められません。")
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
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
        if len(statements) == 1 and statement_types == ["SELECT"]:
            sql = enforce_row_limit(statements[0], request.row_limit)
            results = (
                self._oracle_adapter.execute_select(sql, request.row_limit)
                if self._use_oracle_runtime()
                else self._mock_execute(sql, request.row_limit)
            )
            return DbAdminExecuteData(
                executed=True,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
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
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target="ADMIN_EXECUTE",
        )
        if confirmation_error:
            warnings.append(confirmation_error)
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle" if self._use_oracle_runtime() else "deterministic",
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
            self._record_admin_audit(
                operation="db_admin_execute",
                target="ADMIN_EXECUTE",
                executed=ok,
                reason=request.reason,
                detail={"statement_count": len(statements), "types": statement_types},
            )
            if ok:
                try:
                    self._catalog = self._oracle_adapter.fetch_catalog()
                    self._persist_state()
                except OracleAdapterError as exc:
                    warnings.append(f"Admin SQL 後の schema refresh に失敗しました: {exc}")
            return DbAdminExecuteData(
                executed=ok,
                runtime="oracle",
                statements=statement_results,
                committed=ok,
                rolled_back=not ok,
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_execute"),
            )
        except OracleAdapterError as exc:
            warnings.append(str(exc))
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle",
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
        try:
            content = base64.b64decode(request.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 の decode に失敗しました: {exc}") from exc
        csv_text, sheet_name, sheet_warnings = self._tabular_content_to_csv_text(
            filename=request.filename,
            content=content,
            sheet_name=request.sheet_name,
        )
        warnings.extend(sheet_warnings)
        settings = get_settings()
        row_limit = request.max_rows or settings.nl2sql_csv_import_max_rows
        columns, rows, parse_warnings = self._parse_csv_sample(
            table_name=request.table_name,
            csv_text=csv_text,
            max_rows=min(row_limit, settings.nl2sql_csv_import_max_rows),
            max_columns=settings.nl2sql_csv_import_max_columns,
        )
        warnings.extend(parse_warnings)
        table_name = self._sanitize_import_table_name(request.table_name)
        mode = request.mode.strip().lower() or "create"
        if mode not in {"create", "replace", "append", "truncate"}:
            warnings.append(f"{request.mode}: 未対応 mode のため create として扱いました。")
            mode = "create"
        ddl = self._csv_import_ddl(table_name, columns)
        insert_sql = self._csv_import_insert_sql(table_name, columns)
        executed = False
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target="ADMIN_EXECUTE",
        )
        if confirmation_error:
            warnings.append(confirmation_error)
        elif self._use_oracle_runtime():
            self._oracle_adapter.import_tabular_table(
                table_name=table_name,
                columns=columns,
                rows=rows,
                mode=mode,
            )
            executed = True
            self._record_admin_audit(
                operation="db_admin_import_tabular",
                target=table_name,
                executed=True,
                reason=request.reason,
                detail={"mode": mode, "row_count": len(rows), "filename": request.filename},
            )
            try:
                self._catalog = self._oracle_adapter.fetch_catalog()
                self._persist_state()
            except OracleAdapterError as exc:
                warnings.append(f"import 後の schema refresh に失敗しました: {exc}")
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
            warnings=warnings,
            sample_rows=rows[:5],
            timing=self._timing(created_at, started, "db_admin_import_tabular"),
        )

    def export_db_admin_table_xlsx(self, table_name: str, limit: int = 1000) -> tuple[str, bytes]:
        _ = limit
        detail = self.get_db_admin_object(table_name, "table")
        safe_table = self._sanitize_import_table_name(detail.name)
        catalog_table = self._find_catalog_table(safe_table)
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
        headers = ["物理名", "論理名", "型", "NULL 可", "サンプル"]
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
                    column.logical_name,
                    column.data_type,
                    "YES" if column.nullable else "NO",
                    sample_text or "-",
                ]
            )
        for column_letter, width in {"A": 28, "B": 32, "C": 22, "D": 12, "E": 48}.items():
            columns_sheet.column_dimensions[column_letter].width = width
        buffer = io.BytesIO()
        workbook.save(buffer)
        return f"{safe_table.lower()}_columns.xlsx", buffer.getvalue()

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
                warnings=warnings,
                timing=self._timing(created_at, started, "db_admin_statements"),
            )
        statement_types = [_admin_statement_type(statement) for statement in statements]
        policy_errors = [
            _db_admin_policy_error(statement, request.policy) for statement in statements
        ]
        if any(policy_errors):
            warnings.append("禁止された操作が含まれるため実行しませんでした。")
            return DbAdminExecuteData(
                executed=False,
                runtime=runtime,
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
                for item in self._oracle_adapter.execute_admin_statements(
                    statements, atomic=False
                )
            ]
        except OracleAdapterError as exc:
            warnings.append(str(exc))
            return DbAdminExecuteData(
                executed=False,
                runtime="oracle",
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
        if committed:
            try:
                self._catalog = self._oracle_adapter.fetch_catalog()
                self._persist_state()
            except OracleAdapterError as exc:
                warnings.append(f"実行後の schema refresh に失敗しました: {exc}")
        if 0 < success_count < len(statement_results):
            warnings.append(
                f"部分的に成功しました({success_count}/{len(statement_results)} 件)。"
            )
        return DbAdminExecuteData(
            executed=committed,
            runtime="oracle",
            statements=statement_results,
            committed=committed,
            rolled_back=not committed,
            warnings=warnings,
            timing=self._timing(created_at, started, "db_admin_statements"),
        )

    def drop_db_admin_view(self, request: DbAdminDropViewRequest) -> DbAdminExecuteData:
        view_name = self._sanitize_import_table_name(request.view_name)
        sql = f"DROP VIEW {_quote_identifier(view_name)}"
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
            return DbAdminDataPreviewData(runtime="oracle", sql=sql, results=results)
        return DbAdminDataPreviewData(
            runtime="deterministic",
            sql=sql,
            results=self._mock_execute(sql, min(request.limit, 20)),
        )

    def export_db_admin_preview_xlsx(self, request: DbAdminDataPreviewRequest) -> tuple[str, bytes]:
        """テーブル/ビュープレビュー結果を Excel workbook として出力する。"""
        data = self.preview_db_admin_data(request)
        openpyxl = importlib.import_module("openpyxl")
        workbook = openpyxl.Workbook()
        data_sheet = workbook.active
        data_sheet.title = "data"
        data_sheet.append(data.results.columns)
        for row in data.results.rows:
            data_sheet.append([row.get(column) for column in data.results.columns])
        query_sheet = workbook.create_sheet("query")
        query_sheet.append(["SQL"])
        query_sheet.append([data.sql])
        buffer = io.BytesIO()
        workbook.save(buffer)
        object_name = self._sanitize_import_table_name(request.object_name)
        return f"{object_name.lower()}_preview.xlsx", buffer.getvalue()

    def _build_db_admin_preview_sql(self, request: DbAdminDataPreviewRequest) -> str:
        object_name = self._sanitize_import_table_name(request.object_name)
        sql = f"SELECT * FROM {_quote_identifier(object_name)}"  # nosec B608
        where_clause = request.where_clause.strip()
        if where_clause:
            if ";" in where_clause:
                raise ValueError("WHERE 句に複数 statement は指定できません。")
            where_body = re.sub(r"^where\s+", "", where_clause, flags=re.IGNORECASE)
            sql += f" WHERE {where_body}"
        if len(_split_sql_statements(sql)) != 1 or not is_select_only(sql):
            raise ValueError("WHERE 句が不正です。単一の SELECT になる条件のみ指定できます。")
        sql += f" FETCH FIRST {int(request.limit)} ROWS ONLY"
        return sql

    def upload_db_admin_csv(self, request: DbAdminCsvUploadRequest) -> DbAdminCsvUploadData:
        """既存テーブルへの CSV アップロード(SQL Assist upload_csv_data の再マップ)。"""
        started = time.monotonic()
        created_at = _utc_now()
        warnings: list[str] = []
        try:
            content = base64.b64decode(request.content_base64)
        except Exception as exc:
            raise ValueError(f"content_base64 の decode に失敗しました: {exc}") from exc
        csv_text = content.decode("utf-8-sig", errors="replace")
        settings = get_settings()
        row_limit = request.max_rows or settings.nl2sql_csv_import_max_rows
        columns, rows, parse_warnings = self._parse_csv_sample(
            table_name=request.table_name,
            csv_text=csv_text,
            max_rows=min(row_limit, settings.nl2sql_csv_import_max_rows),
            max_columns=settings.nl2sql_csv_import_max_columns,
        )
        warnings.extend(parse_warnings)
        table_name = self._sanitize_import_table_name(request.table_name)
        truncate = request.mode == "truncate_insert"
        matched_columns, unmatched_csv = self._match_csv_columns_to_catalog(table_name, columns)
        executed = False
        success_count = 0
        error_count = 0
        row_errors: list[str] = []
        hint = ""
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        confirmation_error = self._admin_confirmation_error(
            confirmation=request.confirmation,
            target=table_name,
        )
        if confirmation_error:
            warnings.append(confirmation_error)
        elif self._use_oracle_runtime():
            try:
                result = self._oracle_adapter.upload_csv_to_existing_table(
                    table_name=table_name,
                    columns=columns,
                    rows=rows,
                    truncate=truncate,
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
            else:
                executed = True
                matched_columns = list(result.get("matched_columns") or matched_columns)
                unmatched_csv = list(result.get("unmatched_csv_columns") or unmatched_csv)
                success_count = int(result.get("success_count") or 0)
                error_count = int(result.get("error_count") or 0)
                row_errors = list(result.get("row_errors") or [])
                hint = str(result.get("hint") or "")
                self._record_admin_audit(
                    operation="db_admin_upload_csv",
                    target=table_name,
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
                if success_count > 0:
                    try:
                        self._catalog = self._oracle_adapter.fetch_catalog()
                        self._persist_state()
                    except OracleAdapterError as exc:
                        warnings.append(f"upload 後の schema refresh に失敗しました: {exc}")
        else:
            warnings.append("CSV アップロード実行には NL2SQL_RUNTIME_MODE=oracle が必要です。")
        return DbAdminCsvUploadData(
            table_name=table_name,
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
                update={
                    "warnings": [f"Enterprise AI 分析に失敗したため fallback しました: {exc}"]
                }
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

    def extract_db_admin_join_where(
        self, request: DbAdminJoinWhereRequest
    ) -> DbAdminJoinWhereData:
        """ビュー DDL から JOIN/WHERE 条件を抽出する(SQL Assist の AI 抽出再マップ)。"""
        match = re.search(r"\b(SELECT|WITH)\b[\s\S]*", request.ddl, re.IGNORECASE)
        view_sql = match.group(0) if match else request.ddl
        prompt_profile: _JoinWherePromptProfile = request.prompt_profile
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
            prompt, system_prompt = self._join_where_prompt(view_sql, prompt_profile)
            raw = self._enterprise_ai_client.generate(
                prompt=prompt,
                context="",
                system_prompt=system_prompt,
            )
            if prompt_profile == "sql_structure":
                return self._parse_structure_join_where(raw, deterministic, prompt_profile)
            return self._parse_strict_join_where(raw, prompt_profile)
        except (EnterpriseAiDirectError, ValueError) as exc:
            return deterministic.model_copy(
                update={
                    "warnings": [
                        f"{prompt_profile}: Enterprise AI 抽出に失敗したため "
                        f"fallback しました: {exc}"
                    ]
                }
            )

    def _join_where_prompt(
        self, view_sql: str, prompt_profile: _JoinWherePromptProfile
    ) -> tuple[str, str]:
        if prompt_profile == "sql_structure":
            return (
                _SQL_STRUCTURE_ANALYSIS_PROMPT.format(sql=view_sql),
                _SQL_STRUCTURE_SYSTEM_PROMPT,
            )
        return (
            _JOIN_WHERE_STRICT_PROMPT.format(sql=view_sql),
            _JOIN_WHERE_STRICT_SYSTEM_PROMPT,
        )

    def _parse_strict_join_where(
        self, raw: str, prompt_profile: _JoinWherePromptProfile
    ) -> DbAdminJoinWhereData:
        cleaned = self._clean_join_where_ai_text(raw)
        parsed = re.search(
            r"JOIN:\s*([\s\S]*?)\n\s*WHERE:\s*([\s\S]*)$", cleaned, re.IGNORECASE
        )
        if not parsed:
            raise ValueError("JOIN:/WHERE: フォーマットを解析できませんでした。")
        return DbAdminJoinWhereData(
            join_text=parsed.group(1).strip() or "None",
            where_text=parsed.group(2).strip() or "None",
            source="oci_enterprise_ai",
            prompt_profile=prompt_profile,
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
        prompt_profile: _JoinWherePromptProfile = "join_where_strict",
    ) -> DbAdminJoinWhereData:
        structure = self._sql_structure(view_sql, [])
        joins = structure.get("joins") or []
        filters = structure.get("filters") or []
        return DbAdminJoinWhereData(
            join_text="\n".join(joins) if joins else "None",
            where_text="\n".join(filters) if filters else "None",
            prompt_profile=prompt_profile,
        )

    def _catalog_object_detail(self, object_name: str, object_type: str) -> DbAdminObjectDetail:
        table = self._find_catalog_table(object_name)
        if table is None:
            return DbAdminObjectDetail(
                name=_normalize_identifier(object_name),
                object_type=object_type,
                warnings=[f"{object_name}: catalog に存在しません。"],
            )
        column_defs = ", ".join(
            f"{_quote_identifier(column.column_name)} {column.data_type}"
            for column in table.columns
        )
        ddl_kind = "VIEW" if object_type == "view" else "TABLE"
        ddl = f"CREATE {ddl_kind} {_quote_identifier(table.table_name)} ({column_defs});"
        if table.comment:
            ddl += (
                f"\nCOMMENT ON TABLE {_quote_identifier(table.table_name)} "
                f"IS {_quote_sql_string(table.comment)};"
            )
        for column in table.columns:
            if column.comment:
                column_comment = _quote_sql_string(column.comment)
                ddl += (
                    f"\nCOMMENT ON COLUMN {_quote_identifier(table.table_name)}."
                    f"{_quote_identifier(column.column_name)} IS {column_comment};"
                )
        return DbAdminObjectDetail(
            name=table.table_name,
            owner=table.owner,
            object_type=object_type,
            row_count=table.row_count,
            comment=table.comment,
            columns=table.columns,
            ddl=ddl,
        )

    def _tabular_content_to_csv_text(
        self, *, filename: str, content: bytes, sheet_name: str = ""
    ) -> tuple[str, str, list[str]]:
        suffix = Path(filename).suffix.lower()
        warnings: list[str] = []
        if suffix in {".xlsx", ".xlsm"}:
            openpyxl = importlib.import_module("openpyxl")
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            sheet = (
                workbook[sheet_name]
                if sheet_name and sheet_name in workbook.sheetnames
                else workbook.active
            )
            if sheet_name and sheet_name not in workbook.sheetnames:
                warnings.append(
                    f"{sheet_name}: sheet が見つからないため active sheet を使用しました。"
                )
            output = io.StringIO()
            writer = csv.writer(output)
            for row in sheet.iter_rows(values_only=True):
                writer.writerow(["" if value is None else value for value in row])
            return output.getvalue(), str(sheet.title), warnings
        return content.decode("utf-8-sig", errors="replace"), "", warnings

    def _admin_confirmation_error(self, *, confirmation: str, target: str) -> str:
        normalized = confirmation.strip()
        if normalized in {target, "ADMIN_EXECUTE"}:
            return ""
        if target == "ADMIN_EXECUTE":
            return "実行には confirmation=ADMIN_EXECUTE が必要です。"
        return f"実行には confirmation={target} または ADMIN_EXECUTE が必要です。"

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
        self._persist_state()

    def refresh_select_ai_profile(self, profile_id: str | None) -> AssetRefreshData:
        profile = self.get_profile(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        attributes = self.build_select_ai_profile_attributes(profile)
        warning = ""
        refreshed = True
        status = "ready"
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
                    description=profile.description,
                    attributes=attributes,
                )
                if isinstance(oracle_meta.get("attributes"), dict):
                    oracle_meta["attributes"] = self._redact_select_ai_context_attributes(
                        oracle_meta["attributes"]
                    )
                engine_meta.update(oracle_meta)
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

    def upsert_profile_select_ai_profile(
        self,
        profile_id: str,
        request: ProfileSelectAiProfileRequest,
    ) -> SelectAiDbProfileMutationData:
        with self._lock:
            profile = self._profiles[profile_id]
        attributes = self.build_select_ai_profile_attributes(profile)
        if request.attributes_override:
            attributes = {**attributes, **request.attributes_override}
        profile_name = self._select_ai_profile_name(profile)
        return self.upsert_select_ai_db_profile(
            SelectAiDbProfileUpsertRequest(
                profile_name=profile_name,
                attributes=attributes,
                description=profile.description,
                category=profile.category or profile.name,
                confirmation=request.confirmation,
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
            warnings.append("データ行がありません。取込対象を確認してください。")
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
        # Safe: generated SQL uses sanitized CSV identifiers; execution path uses Oracle binds.
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
            "allowed_tables": profile.allowed_tables,
            "allowed_views": profile.allowed_views,
            "allowed_objects": self.profile_allowed_object_names(profile),
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
        self._persist_state()
        return cleaned

    def list_select_ai_db_profiles(self, include_detail: bool = False) -> SelectAiDbProfilesData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                profiles: list[SelectAiDbProfile] = []
                for item in self._oracle_adapter.list_select_ai_profiles():
                    profile = SelectAiDbProfile.model_validate(item)
                    if include_detail:
                        try:
                            detail = self._oracle_adapter.get_select_ai_profile_detail(
                                profile_name=profile.name
                            )
                            profile = SelectAiDbProfile.model_validate(
                                {**profile.model_dump(), **detail}
                            )
                        except OracleAdapterError as exc:
                            warnings.append(f"{profile.name}: {exc}")
                    profiles.append(self._enrich_select_ai_db_profile(profile))
                return SelectAiDbProfilesData(
                    runtime="oracle",
                    profiles=profiles,
                    warnings=warnings,
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
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
            ]
        runtime = "oracle" if self._use_oracle_runtime() else "deterministic"
        if not self._use_oracle_runtime():
            warnings.append("Oracle runtime ではないため保存済み asset metadata を表示しています。")
        return SelectAiDbProfilesData(runtime=runtime, profiles=profiles, warnings=warnings)

    def get_select_ai_db_profile(self, profile_name: str) -> SelectAiDbProfileDetailData:
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
                return SelectAiDbProfileDetailData(
                    runtime="oracle",
                    profile=self._enrich_select_ai_db_profile(
                        SelectAiDbProfile.model_validate(
                            self._oracle_adapter.get_select_ai_profile_detail(
                                profile_name=profile_name
                            )
                        )
                    ),
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        profiles = self.list_select_ai_db_profiles(include_detail=True)
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
            object_list = [
                item
                for item in raw_object_list
                if isinstance(item, dict)
            ]
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
            table.table_name.upper(): table.table_type.lower()
            for table in self._catalog.tables
        }
        tables: list[str] = []
        views: list[str] = []
        for item in object_list:
            name = _normalize_identifier(str(item.get("name") or ""))
            if not name:
                continue
            object_type = catalog_types.get(name, "")
            if "view" in object_type or name.startswith("V_"):
                views.append(name)
            else:
                tables.append(name)
        return self._dedupe_object_names(tables), self._dedupe_object_names(views)

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
                        SelectAiFeedbackEntry.model_validate(item)
                        for item in data.get("items", [])
                    ],
                    total=int(data.get("total") or 0),
                )
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        else:
            warnings.append(
                "Select AI feedback 管理には NL2SQL_RUNTIME_MODE=oracle が必要です。"
            )
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
                warnings=[
                    "Select AI feedback 削除には NL2SQL_RUNTIME_MODE=oracle が必要です。"
                ],
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
                warnings=[
                    "Select AI feedback 追加には NL2SQL_RUNTIME_MODE=oracle が必要です。"
                ],
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
                    "DBMS_CLOUD_AI profile の作成/更新には "
                    "NL2SQL_RUNTIME_MODE=oracle が必要です。"
                ],
            )
        try:
            meta = self._oracle_adapter.upsert_select_ai_profile_low_level(
                profile_name=profile_name,
                attributes=request.attributes,
                description=request.description,
                original_name=original_name,
            )
            detail = self.get_select_ai_db_profile(profile_name).profile
            self._record_admin_audit(
                operation="select_ai_profile_upsert",
                target=profile_name,
                executed=True,
                reason=request.reason,
                detail={
                    "original_name": original_name,
                    "category": request.category,
                    "attributes": self._redact_select_ai_context_attributes(
                        request.attributes
                    ),
                },
            )
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
                    "attributes": self._redact_select_ai_context_attributes(
                        request.attributes
                    ),
                },
            )
        except OracleAdapterError as exc:
            return SelectAiDbProfileMutationData(
                runtime="oracle",
                executed=False,
                status="error",
                profile_name=profile_name,
                original_name=original_name,
                ddl=ddl,
                warnings=[str(exc)],
            )

    def export_select_ai_profiles_json(self) -> SelectAiProfilesExportData:
        return SelectAiProfilesExportData(
            profiles=self.list_select_ai_db_profiles().profiles,
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
        )

    def run_select_ai_agent_team(self, request: AgentTeamRunRequest) -> AgentTeamRunData:
        profile = self.get_profile(request.profile_id)
        team_name = request.team_name.strip() or self._select_ai_runtime_team_name(profile)
        warnings: list[str] = []
        if self._use_oracle_runtime():
            try:
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
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        generated = self._generate_sql(
            Nl2SqlEngine.SELECT_AI_AGENT,
            request.prompt,
            profile,
            AllowedObjects(),
            profile.default_row_limit,
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
        if self._use_oracle_runtime():
            try:
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
            except OracleAdapterError as exc:
                warnings.append(str(exc))
        generated = self._generate_sql(
            Nl2SqlEngine.SELECT_AI_AGENT,
            request.prompt,
            self.get_profile(None),
            AllowedObjects(),
            self.get_profile(None).default_row_limit,
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

    def _cleanup_select_ai_profile(self, profile_id: str | None) -> AssetCleanupData:
        profile = self._cleanup_profile_target(profile_id)
        profile_name = self._select_ai_profile_name(profile)
        warning = ""
        status = "error"
        executed = False
        engine_meta: dict[str, Any] = {"runtime": "deterministic"}
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
                warning = str(exc)
        else:
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

    def _cleanup_select_ai_agent_assets(self, profile_id: str | None) -> AssetCleanupData:
        profile = self._cleanup_profile_target(profile_id)
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

    def _effective_glossary(self, profile: Nl2SqlProfile) -> dict[str, str]:
        with self._lock:
            legacy = dict(self._legacy_learning_material.glossary)
        return {**legacy, **profile.glossary}

    def _effective_sql_rules(self, profile: Nl2SqlProfile) -> list[str]:
        with self._lock:
            global_rules = list(self._legacy_learning_material.rules)
        return self._merge_unique_strings(global_rules, profile.sql_rules)

    def _append_rules_to_question(self, question: str, profile: Nl2SqlProfile) -> str:
        rules = self._effective_sql_rules(profile)
        if not rules:
            return question
        return f"{question.rstrip()}\n\n=== Rules ===\n" + "\n\n".join(rules)

    def rewrite_question(self, question: str, profile: Nl2SqlProfile) -> str:
        rewritten = question.strip()
        for term, replacement in self._effective_glossary(profile).items():
            if term in rewritten and replacement not in rewritten:
                rewritten = f"{rewritten}（{term}={replacement}）"
        return rewritten

    def _rewrite_context(
        self,
        *,
        profile: Nl2SqlProfile,
        use_glossary: bool,
        use_schema: bool,
        extra_prompt: str,
    ) -> str:
        lines = [f"profile: {profile.name}", f"description: {profile.description}"]
        glossary = self._effective_glossary(profile)
        if use_glossary and glossary:
            lines.append("glossary:")
            for term, replacement in list(glossary.items())[:40]:
                lines.append(f"- {term}: {replacement}")
        rules = self._effective_sql_rules(profile)
        if rules:
            lines.append("sql_rules:")
            for rule in rules[:20]:
                lines.append(f"- {rule}")
        if use_schema:
            allowed = {name.upper() for name in self.profile_allowed_object_names(profile)}
            lines.append("schema:")
            for table in self._catalog.tables:
                if allowed and table.table_name not in allowed:
                    continue
                columns = ", ".join(column.column_name for column in table.columns[:20])
                lines.append(f"- {table.table_name} ({table.logical_name}): {columns}")
        if extra_prompt.strip():
            lines.append("extra_instruction:")
            lines.append(extra_prompt.strip())
        return "\n".join(lines)

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
        allowed_tables = self.profile_allowed_object_names(profile) or [
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

        for term, replacement in self._effective_glossary(profile).items():
            add_match(term, 2.0)
            add_match(replacement, 1.0)
        for token in re.split(r"[\s、。・/]+", f"{profile.name} {profile.description}"):
            add_match(token.strip(), 0.6)
        for example in profile.few_shot_examples:
            add_match(example.get("question", ""), 1.2)

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
            select_ai_overrides=request.select_ai_overrides,
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
        select_ai_overrides: SelectAiRequestOverrides | None = None,
    ) -> GeneratedSql:
        if not self._use_oracle_runtime() and not self._catalog.tables:
            raise ValueError(_SCHEMA_EMPTY_MESSAGE)
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
                    candidate,
                    question,
                    profile,
                    allowed,
                    row_limit,
                    fallback_messages,
                    select_ai_overrides,
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
        select_ai_overrides: SelectAiRequestOverrides | None = None,
    ) -> GeneratedSql:
        # テスト/デモ用の明示的 failure trigger。実 adapter では不要。
        if f"{engine.value}_fail" in question.lower():
            raise RuntimeError("明示的な fallback テスト要求")
        effective_question = self.rewrite_question(question, profile)
        meta: dict[str, Any] = {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "row_limit": row_limit or profile.default_row_limit,
            "allowed_tables": allowed.table_names or self.profile_allowed_object_names(profile),
        }
        learning_examples = self._learning_examples_for_generation(
            question=effective_question,
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
                    question=effective_question,
                    profile=profile,
                    fallback_messages=fallback_messages,
                    meta=dict(meta),
                    learning_examples=learning_examples,
                    select_ai_overrides=select_ai_overrides,
                )
            except OracleAdapterError as exc:
                fallback_messages.append(f"{engine.value}: {exc}")
        if not self._catalog.tables:
            raise ValueError(_SCHEMA_EMPTY_MESSAGE)
        table = self._choose_table(effective_question, profile, allowed)
        columns = self._choose_columns(table, allowed)
        direct_configured = self._enterprise_ai_client.is_configured()
        if engine == Nl2SqlEngine.ENTERPRISE_AI_DIRECT and direct_configured:
            try:
                return self._generate_enterprise_ai_direct_sql(
                    question=effective_question,
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
        use_glossary: bool = True,
    ) -> str:
        allowed_tables = {
            _normalize_identifier(table)
            for table in (allowed.table_names or self.profile_allowed_object_names(profile))
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
        if use_glossary:
            lines.extend(
                f"- {term}: {definition}"
                for term, definition in self._effective_glossary(profile).items()
            )
        lines.append("sql_rules:")
        lines.extend(f"- {rule}" for rule in self._effective_sql_rules(profile))
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
        select_ai_overrides: SelectAiRequestOverrides | None = None,
    ) -> GeneratedSql:
        runtime_question = self._augment_question_with_learning_examples(
            question,
            learning_examples,
        )
        runtime_question = self._append_rules_to_question(runtime_question, profile)
        if engine == Nl2SqlEngine.SELECT_AI:
            profile_name = self._select_ai_profile_name(profile)
            attributes = self._select_ai_generate_attributes(profile, select_ai_overrides)
            if attributes:
                sql = self._oracle_adapter.generate_select_ai_sql(
                    profile_name=profile_name,
                    question=runtime_question,
                    attributes=attributes,
                )
            else:
                sql = self._oracle_adapter.generate_select_ai_sql(
                    profile_name=profile_name,
                    question=runtime_question,
                )
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

    def _dedupe_object_names(self, names: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        objects: list[str] = []
        for name in names:
            normalized = _normalize_identifier(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            objects.append(normalized)
        return objects

    def _select_ai_object_list(self, object_names: Sequence[str]) -> list[dict[str, str]]:
        owner = get_settings().oracle_user.strip().upper()
        objects: list[dict[str, str]] = []
        for object_name in object_names:
            normalized = _normalize_identifier(object_name)
            if not normalized:
                continue
            if "." in normalized:
                object_owner, name = normalized.split(".", 1)
                objects.append({"owner": object_owner, "name": name})
            elif owner:
                objects.append({"owner": owner, "name": normalized})
            else:
                objects.append({"name": normalized})
        return objects

    def _resolve_allowed_objects(
        self, profile_id: str | None, requested: AllowedObjects
    ) -> AllowedObjects:
        if requested.table_names:
            return requested
        profile = self.get_profile(profile_id)
        return AllowedObjects(
            table_names=self.profile_allowed_object_names(profile),
            columns=requested.columns,
        )

    def _resolve_row_limit(self, profile_id: str | None, requested: int | None) -> int:
        if requested:
            return requested
        return self.get_profile(profile_id).default_row_limit

    def _choose_table(
        self, question: str, profile: Nl2SqlProfile, allowed: AllowedObjects
    ) -> SchemaTable:
        allowed_names = {
            _normalize_identifier(name)
            for name in (allowed.table_names or self.profile_allowed_object_names(profile))
        }
        candidates = [
            table
            for table in self._catalog.tables
            if not allowed_names or table.table_name in allowed_names
        ]
        if not candidates:
            candidates = self._catalog.tables
        if not candidates:
            raise ValueError(_SCHEMA_EMPTY_MESSAGE)
        question_upper = question.upper()
        for table in candidates:
            if table.table_name in question_upper or table.logical_name in question:
                return table
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
        table_name = referenced[0] if referenced else ""
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
        return SchemaCatalog(refreshed_at=_utc_now(), tables=[])


nl2sql_service = Nl2SqlService()
