"""AI要件確認: SQL の要件の下書きを作り、足りない・明確でない情報を確かめる（#1439）。

規格の原則（Issue #1439）:

1. 利用者の質問と、Schema・業務プロファイル・用語集・オントロジー（**表のデータは使わない**）を
   合わせ、SQL を作るのに足りない情報・明確でない情報はすべて確認する。
2. 全部の質問に答えた後、要件のまとめを LLM でもう一度検査し、まだ足りない・明確でない情報が
   あれば、さらに確認する（検査の回数の上限は設定）。
3. 最低の基準: SELECT（何を出すか）と FROM（どの表から）が明確でない限り、SQL の生成に進めない。
4. 表・列は Schema の物理名で持ち、画面の文言・要件のまとめにも物理名を書く。

LLM は 1 回の呼び出しで、欄（SELECT・FROM・結合・WHERE・期間・GROUP BY・ORDER BY・件数・
WITH / サブクエリ・そのほか）ごとの項目を構造化出力で返す。このモジュールはその出力を確定的に
検査する（業務プロファイルの範囲・Schema に実在する物理名・承認済みの関係・マスクの値・候補の
根拠）。相対的な期間の表現は、プログラムが基準日（実行日）から具体的な日付の範囲を計算して
候補にする（LLM に日付の計算をさせない）。
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from .models import (
    AllowedObjects,
    Nl2SqlProfile,
    SchemaTable,
    SqlRequirementSpec,
    SqlRequirementSpecItem,
)
from .object_identity import catalog_match_key, object_match_key
from .ontology_catalog import SchemaOntology
from .ontology_models import (
    ColumnQueryPolicy,
    OntologyNodeKind,
    ProfileOntologyView,
    QuerySession,
    RequirementAnswerKind,
    RequirementAnswerRecord,
    RequirementCandidate,
    RequirementItem,
    RequirementPeriod,
    RequirementPhase,
    RequirementReferenceBasis,
    RequirementSection,
    RequirementSource,
    RequirementState,
    RequirementStatus,
    SqlRequirementDraft,
)
from .ontology_service import OntologyServiceError
from .ontology_store import stable_ontology_id
from .structured_outputs import response_format

REQUIREMENT_SCHEMA_VERSION = "sql_requirement_v1"
REQUIREMENT_PROMPT_VERSION = "sql_requirement_v1"

# LLM に渡す Schema の上限（Schema が大きいときは質問との関連で絞る。
# 絞り込みはあいまいさを作らない）。
_MAX_CONTEXT_TABLES = 16
_MAX_CONTEXT_COLUMNS_PER_TABLE = 60
_MAX_ONTOLOGY_CONCEPTS = 60
_MAX_ITEMS = 40
_MAX_CANDIDATES = 8

# 質問の順番（FROM → SELECT → 結合 → 期間 → WHERE → …）。
_SECTION_ORDER: dict[RequirementSection, int] = {
    RequirementSection.FROM: 0,
    RequirementSection.SELECT: 1,
    RequirementSection.JOIN: 2,
    RequirementSection.PERIOD: 3,
    RequirementSection.WHERE: 4,
    RequirementSection.GROUP_BY: 5,
    RequirementSection.ORDER_BY: 6,
    RequirementSection.LIMIT: 7,
    RequirementSection.SUBQUERY: 8,
    RequirementSection.OTHER: 9,
}

SECTION_LABELS_JA: dict[RequirementSection, str] = {
    RequirementSection.SELECT: "表示する項目（SELECT）",
    RequirementSection.FROM: "対象の表（FROM）",
    RequirementSection.JOIN: "表の結合（JOIN）",
    RequirementSection.WHERE: "条件（WHERE）",
    RequirementSection.PERIOD: "期間（WHERE）",
    RequirementSection.GROUP_BY: "集計の単位（GROUP BY）",
    RequirementSection.ORDER_BY: "並び順（ORDER BY）",
    RequirementSection.LIMIT: "件数の上限",
    RequirementSection.SUBQUERY: "WITH・サブクエリ",
    RequirementSection.OTHER: "そのほか",
}

_UNRESOLVED = {RequirementStatus.MISSING, RequirementStatus.AMBIGUOUS}
_CLEAR = {RequirementStatus.CONFIRMED, RequirementStatus.INFERRED}

# 相対的な期間の表現の種類（LLM は種類と件数だけを返し、日付はプログラムが計算する）。
RELATIVE_PERIOD_KINDS = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "last_year",
    "this_fiscal_year",
    "last_fiscal_year",
    "year_to_date",
    "fiscal_year_to_date",
    "last_n_days",
    "last_n_weeks",
    "last_n_months",
    "last_n_years",
    "same_period_last_year",
)
_FISCAL_KINDS = {"this_fiscal_year", "last_fiscal_year", "fiscal_year_to_date"}
_DATE_TYPES = ("DATE", "TIMESTAMP")


class RequirementAiUnavailableError(OntologyServiceError):
    """LLM が使えない・応答を検査できないときのエラー（規則の下書きへ切り替えない）。"""


class RequirementAnswerInvalidError(ValueError):
    pass


# ---------------------------------------------------------------------------
# LLM の構造化出力
# ---------------------------------------------------------------------------

_SourceLiteral = Literal["question", "schema", "profile", "glossary", "ontology", "calendar"]
_SectionLiteral = Literal[
    "select",
    "from",
    "join",
    "where",
    "period",
    "group_by",
    "order_by",
    "limit",
    "subquery",
    "other",
]


class _LlmCandidate(BaseModel):
    label_ja: str
    description_ja: str
    evidence_ja: str
    source: _SourceLiteral
    tables: list[str]
    columns: list[str]
    relationship_ids: list[str]
    sql_hint: str


class _LlmPeriod(BaseModel):
    expression_ja: str
    relative_kind: str
    count: int
    fiscal_year_start_month: int
    date_column: str


class _LlmItem(BaseModel):
    key: str
    section: _SectionLiteral
    label_ja: str
    value_ja: str
    status: Literal["confirmed", "inferred", "missing", "ambiguous"]
    reason_ja: str
    question_ja: str
    answer_kind: Literal["single_select", "multi_select", "free_text"]
    source: _SourceLiteral
    tables: list[str]
    columns: list[str]
    relationship_ids: list[str]
    sql_hint: str
    period: _LlmPeriod | None
    candidates: list[_LlmCandidate]


class LlmRequirementOutput(BaseModel):
    clarified_question_ja: str
    items: list[_LlmItem]
    unanswerable_reason_ja: str


def requirement_response_format() -> dict[str, Any]:
    return response_format(LlmRequirementOutput, name=REQUIREMENT_SCHEMA_VERSION)


SYSTEM_PROMPT = (
    "あなたは Oracle AI Database の NL2SQL の要件確認器です。利用者の質問と、入力の schema・"
    "relationships・profile・ontology だけを使い、1 本の SQL を作るための「SQL の要件の下書き」を、"
    "指定の JSON Schema の JSON オブジェクトだけで返してください（説明文や Markdown を付けない）。"
    "\n"
    "規則:\n"
    "1. 表のデータは渡していません。値の候補は、列のコメント・用語集・オントロジー・"
    "業務プロファイルに"
    "書かれた値だけにし、データの値を推測・生成しないでください。masked=true "
    "の列の値は候補にしないでください。\n"
    "2. 表・列は必ず schema にある物理名で書いてください（tables は `OWNER.TABLE`、columns は"
    " `OWNER.TABLE.COLUMN`）。label_ja・value_ja・question_ja・候補の label_ja・"
    "clarified_question_ja でも、"
    "表・列は物理名（`TABLE.COLUMN` など）で書き、論理名は括弧で添えるだけにしてください。"
    "schema に無い名前を作らないでください。\n"
    "3. 表の結合は relationships の id だけを使ってください（relationship_ids）。承認済みの関係で"
    "つなげない表を組み合わせないでください。\n"
    "4. 欄（section）: select（表示する列・式・集計関数・重複の除去）、from（使う表。必須）、"
    "join（複数の表の結合の経路・種類（INNER / LEFT など）・1 対多の結合で集計が膨らむおそれ）、"
    "where（絞り込みの列・演算子・値、状態・区分の値、NULL の扱い）、"
    "period（相対的な期間の表現）、group_by（集計の単位）、order_by（並び順）、limit（件数の上限）、"
    "subquery（WITH・サブクエリが要るか・どう組むか。例: 部門ごとの平均を超える社員、前月比）、"
    "other（単位・通貨、「最新」「上位」の定義、指標の計算式の選択など、SQL "
    "の生成に要るそのほかの情報）。"
    "SQL の生成に要る情報は、欄の例に限らずすべて項目にしてください。select と from "
    "の項目は必ず出してください。\n"
    "5. status: confirmed=質問に明示されている、inferred=明示されていないが schema・profile・"
    "ontology から"
    "1 つに決められる、missing=情報が無い、ambiguous=複数の解釈がある。missing / ambiguous には"
    " question_ja（利用者への 1 つの質問）と reason_ja を書き、ambiguous には根拠のある候補を "
    "candidates に"
    "入れてください（evidence_ja に根拠を書く。根拠の無い候補は出さない）。\n"
    "6. 「今年度」「今月」「先月」「直近 3 か月」「昨年同期」などの相対的な期間の表現は、必ず "
    "section=period の"
    "項目にし、period.relative_kind に次のどれかを入れてください: "
    + ", ".join(RELATIVE_PERIOD_KINDS)
    + "（当てはまらなければ other）。period.count は件数（直近 3 か月なら 3、無ければ 0）、"
    "period.date_column はその期間で絞る日付の列（物理名。決められなければ空）、"
    "period.fiscal_year_start_month は年度の開始月が profile・用語集・ontology "
    "に書かれているときだけ"
    " 1〜12（無ければ 0）。日付の計算はしないでください（プログラムが計算します）。\n"
    "7. sql_hint には、その項目を SQL にした断片（例: `SUM(ORDERS.AMOUNT)`、"
    "`ORDERS.STATUS = 'SHIPPED'`）を物理名で書いてください（決められなければ空）。"
    "句のキーワード（SELECT・FROM・WHERE・GROUP BY・ORDER BY）は付けないでください。\n"
    "8. clarified_question_ja は、すべての項目を反映した、自然な日本語の 1 "
    "つの質問の文にしてください"
    "（表・列は物理名）。\n"
    "9. 業務プロファイルの範囲の表だけでは答えられない質問なら、unanswerable_reason_ja "
    "に理由を書いてください。\n"
    "10. mode=review のときは、answered_items（利用者が答えた項目）を変えずに、ほかの項目と"
    " clarified_question_ja を見直し、まだ足りない・明確でない情報があれば missing / ambiguous "
    "の項目として"
    "挙げてください。無ければすべての項目を confirmed / inferred にしてください。"
    "answered_items と同じ key の項目は出力しないでください。"
)


# ---------------------------------------------------------------------------
# 入力の文脈（表のデータは含めない）
# ---------------------------------------------------------------------------


@dataclass
class ColumnInfo:
    key: str  # OWNER.TABLE.COLUMN（catalog_match_key）
    display: str  # TABLE.COLUMN
    name: str
    logical_name: str
    data_type: str
    nullable: bool
    comment: str
    domain_name: str
    masked: bool = False
    filterable: bool = True
    queryable: bool = True

    @property
    def is_date(self) -> bool:
        return self.data_type.upper().startswith(_DATE_TYPES)


@dataclass
class TableInfo:
    key: str  # OWNER.TABLE
    owner: str
    name: str
    logical_name: str
    comment: str
    table_type: str
    columns: dict[str, ColumnInfo] = field(default_factory=dict)
    primary_key: list[str] = field(default_factory=list)
    # 外部キーで別の表を参照する（明細・取引の表になりやすい。件数は読まないため Schema
    # だけで判断する）。
    references_other_tables: bool = False


@dataclass
class RelationshipInfo:
    id: str
    left_table: str
    right_table: str
    condition: str
    cardinality: str
    name_ja: str
    source: str


@dataclass
class RequirementContext:
    question: str
    tables: dict[str, TableInfo]
    relationships: list[RelationshipInfo]
    profile_context: dict[str, Any]
    ontology_concepts: list[dict[str, Any]]
    evidence_corpus: str
    fiscal_year_start: tuple[int, str] | None
    today: date
    table_lookup: dict[str, str] = field(default_factory=dict)
    column_lookup: dict[str, str] = field(default_factory=dict)

    def column(self, key: str) -> ColumnInfo | None:
        parts = key.rsplit(".", 1)
        if len(parts) != 2:
            return None
        table_key = self.column_lookup.get(key, "")
        if not table_key:
            return None
        table = self.tables.get(table_key)
        if table is None:
            return None
        return table.columns.get(key)

    def resolve_table(self, value: str) -> str:
        return self.table_lookup.get(object_match_key(value), "")

    def resolve_column(self, value: str) -> str:
        normalized = object_match_key(value)
        table_key = self.column_lookup.get(normalized, "")
        if not table_key:
            return ""
        table = self.tables[table_key]
        column_name = normalized.rsplit(".", 1)[-1]
        for column in table.columns.values():
            if catalog_match_key(column.name) == column_name:
                return column.key
        return ""

    def table_display(self, key: str) -> str:
        table = self.tables.get(key)
        if table is None:
            return key
        return f"{table.key}（{table.logical_name}）" if _has_label(table) else table.key

    def column_display(self, key: str) -> str:
        table_key = self.column_lookup.get(key, "")
        table = self.tables.get(table_key)
        column = table.columns.get(key) if table else None
        if column is None:
            return key
        logical = column.logical_name.strip()
        if logical and logical.casefold() != column.name.casefold():
            return f"{column.display}（{logical}）"
        return column.display

    def date_columns(self, table_keys: Iterable[str]) -> list[ColumnInfo]:
        result: list[ColumnInfo] = []
        for key in table_keys:
            table = self.tables.get(key)
            if table is None:
                continue
            result.extend(column for column in table.columns.values() if column.is_date)
        return result


def _has_label(table: TableInfo) -> bool:
    logical = table.logical_name.strip()
    return bool(logical) and logical.casefold() != table.name.casefold()


def build_requirement_context(
    *,
    question: str,
    profile: Nl2SqlProfile,
    catalog_tables: Sequence[SchemaTable],
    allowed: AllowedObjects,
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
    today: date,
) -> RequirementContext:
    """業務プロファイルの範囲の Schema・関係・業務の文脈を集める（代表値・件数は入れない）。"""

    scope = {
        object_match_key(value)
        for value in [*profile.allowed_tables, *profile.allowed_views]
        if value.strip()
    }
    requested = {object_match_key(value) for value in allowed.table_names if value.strip()}
    requested_columns = {
        object_match_key(table): {object_match_key(column) for column in columns}
        for table, columns in allowed.columns.items()
        if columns
    }
    policies = _column_policies(ontology, view)

    tables: dict[str, TableInfo] = {}
    short_names: dict[str, list[str]] = {}
    for table in catalog_tables:
        qualified = catalog_match_key(table.owner, table.table_name)
        short = catalog_match_key(table.table_name)
        if scope and qualified not in scope and short not in scope:
            continue
        if requested and qualified not in requested and short not in requested:
            continue
        restricted = requested_columns.get(qualified) or requested_columns.get(short) or set()
        info = TableInfo(
            key=qualified,
            owner=table.owner,
            name=table.table_name,
            logical_name=table.logical_name,
            comment=table.comment,
            table_type=table.table_type,
        )
        for column in table.columns:
            column_token = catalog_match_key(column.column_name)
            if restricted and column_token not in restricted:
                continue
            policy = policies.get((table.owner, table.table_name, column.column_name))
            if policy is not None and not policy.queryable:
                continue
            column_key = catalog_match_key(table.owner, table.table_name, column.column_name)
            info.columns[column_key] = ColumnInfo(
                key=column_key,
                display=catalog_match_key(table.table_name, column.column_name),
                name=column.column_name,
                logical_name=column.logical_name,
                data_type=column.data_type,
                nullable=column.nullable,
                comment=column.comment,
                domain_name=column.domain_name,
                masked=bool(policy and policy.masked),
                filterable=bool(policy is None or policy.filterable),
                queryable=True,
            )
        for constraint in table.constraint_details:
            if constraint.constraint_type == "P":
                info.primary_key = list(constraint.columns)
            if constraint.constraint_type == "R":
                info.references_other_tables = True
        tables[qualified] = info
        short_names.setdefault(short, []).append(qualified)

    table_lookup: dict[str, str] = {}
    column_lookup: dict[str, str] = {}
    for key, info in tables.items():
        table_lookup[key] = key
        short = catalog_match_key(info.name)
        if len(short_names.get(short, [])) == 1:
            table_lookup[short] = key
        for column_info in info.columns.values():
            column_lookup[column_info.key] = key
            if len(short_names.get(short, [])) == 1:
                column_lookup[catalog_match_key(info.name, column_info.name)] = key

    relationships = _approved_relationships(catalog_tables, tables, ontology, view)
    profile_context = {
        "name": profile.name,
        "description": profile.description,
        "glossary": profile.glossary,
        "sql_rules": profile.sql_rules,
        "few_shot_examples": profile.few_shot_examples[:5],
        "default_row_limit": profile.default_row_limit,
    }
    concepts = _ontology_concepts(ontology, view, tables)
    corpus_parts: list[str] = [profile.description, *profile.sql_rules]
    corpus_parts.extend(f"{term} {definition}" for term, definition in profile.glossary.items())
    for example in profile.few_shot_examples[:5]:
        corpus_parts.extend(str(value) for value in example.values())
    for info in tables.values():
        corpus_parts.extend([info.logical_name, info.comment])
        for column_info in info.columns.values():
            corpus_parts.extend(
                [column_info.logical_name, column_info.comment, column_info.domain_name]
            )
    for concept in concepts:
        corpus_parts.extend(
            str(concept.get(key) or "") for key in ("name_ja", "description_ja", "definition_ja")
        )
        corpus_parts.extend(str(value) for value in concept.get("aliases", []))
        corpus_parts.extend(str(value) for value in concept.get("values", []))
    corpus = "\n".join(part for part in corpus_parts if part).casefold()
    return RequirementContext(
        question=question,
        tables=_relevant_tables(tables, question, concepts),
        relationships=relationships,
        profile_context=profile_context,
        ontology_concepts=concepts,
        evidence_corpus=corpus,
        fiscal_year_start=detect_fiscal_year_start(profile, concepts),
        today=today,
        table_lookup=table_lookup,
        column_lookup=column_lookup,
    )


def _column_policies(
    ontology: SchemaOntology | None, view: ProfileOntologyView | None
) -> dict[tuple[str, str, str], ColumnQueryPolicy]:
    if ontology is None or view is None:
        return {}
    node_by_id = {node.id: node for node in ontology.nodes}
    node_by_technical = {object_match_key(node.technical_name): node for node in ontology.nodes}
    result: dict[tuple[str, str, str], ColumnQueryPolicy] = {}
    for key, policy in view.column_policies.items():
        node = node_by_id.get(key) or node_by_technical.get(object_match_key(key))
        if node is None:
            continue
        for mapping in node.physical_mappings:
            for column in mapping.column_refs:
                result[(column.owner, column.object_name, column.column_name)] = policy
    return result


def _approved_relationships(
    catalog_tables: Sequence[SchemaTable],
    tables: Mapping[str, TableInfo],
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
) -> list[RelationshipInfo]:
    """承認済みの関係（業務プロファイルの範囲のオントロジーの関係と、Schema の外部キー）。"""

    result: list[RelationshipInfo] = []
    seen: set[tuple[str, str, str]] = set()

    def add(item: RelationshipInfo) -> None:
        left, right = sorted((item.left_table, item.right_table))
        signature = (left, right, item.condition.casefold())
        if signature in seen:
            return
        seen.add(signature)
        result.append(item)

    for table in catalog_tables:
        left = catalog_match_key(table.owner, table.table_name)
        if left not in tables:
            continue
        for constraint in table.constraint_details:
            if constraint.constraint_type != "R" or not constraint.referenced_table:
                continue
            if constraint.status and constraint.status.upper() != "ENABLED":
                continue
            right_owner = constraint.referenced_owner or table.owner
            right = catalog_match_key(right_owner, constraint.referenced_table)
            if right not in tables:
                continue
            pairs = zip(constraint.columns, constraint.referenced_columns, strict=False)
            condition = " AND ".join(
                f"{catalog_match_key(table.table_name, left_column)} = "
                f"{catalog_match_key(constraint.referenced_table, right_column)}"
                for left_column, right_column in pairs
            )
            if not condition:
                continue
            add(
                RelationshipInfo(
                    id=f"fk:{catalog_match_key(table.owner, constraint.constraint_name)}",
                    left_table=left,
                    right_table=right,
                    condition=condition,
                    cardinality="many_to_one",
                    name_ja=f"外部キー {constraint.constraint_name}",
                    source="foreign_key",
                )
            )
    if ontology is not None and view is not None:
        approved = set(view.allowed_path_ids)
        for edge in ontology.edges:
            if edge.id not in approved or edge.id not in set(view.edge_ids):
                continue
            if not edge.join_conditions:
                continue
            first = edge.join_conditions[0]
            left = catalog_match_key(first.left.owner, first.left.object_name)
            right = catalog_match_key(first.right.owner, first.right.object_name)
            if left not in tables or right not in tables:
                continue
            condition = " AND ".join(
                f"{catalog_match_key(item.left.object_name, item.left.column_name)} "
                f"{item.operator} "
                f"{catalog_match_key(item.right.object_name, item.right.column_name)}"
                for item in sorted(edge.join_conditions, key=lambda value: value.ordinal)
            )
            add(
                RelationshipInfo(
                    id=edge.id,
                    left_table=left,
                    right_table=right,
                    condition=condition,
                    cardinality=edge.cardinality.value,
                    name_ja=edge.relationship_name_ja,
                    source="ontology",
                )
            )
    return result


_CONCEPT_KINDS = {
    OntologyNodeKind.BUSINESS_ENTITY,
    OntologyNodeKind.BUSINESS_EVENT,
    OntologyNodeKind.PROPERTY,
    OntologyNodeKind.METRIC,
    OntologyNodeKind.BUSINESS_TERM,
    OntologyNodeKind.BUSINESS_RULE,
    OntologyNodeKind.ENUM_VALUE,
    OntologyNodeKind.ENUMERATION,
}


def _ontology_concepts(
    ontology: SchemaOntology | None,
    view: ProfileOntologyView | None,
    tables: Mapping[str, TableInfo],
) -> list[dict[str, Any]]:
    """オントロジーの業務の概念（用語・指標・規則・取りうる値）。物理名の対応を添える。"""

    if ontology is None or view is None:
        return []
    visible = set(view.node_ids)
    result: list[dict[str, Any]] = []
    for node in ontology.nodes:
        if node.id not in visible or node.kind not in _CONCEPT_KINDS:
            continue
        physical: list[str] = []
        for mapping in node.physical_mappings:
            table_key = catalog_match_key(mapping.object_ref.owner, mapping.object_ref.object_name)
            if table_key not in tables:
                continue
            if mapping.column_refs:
                physical.extend(
                    catalog_match_key(item.object_name, item.column_name)
                    for item in mapping.column_refs
                )
            else:
                physical.append(table_key)
            if mapping.expression_sql:
                physical.append(mapping.expression_sql)
        concept: dict[str, Any] = {
            "kind": node.kind.value,
            "name_ja": node.business_name_ja,
            "description_ja": node.description_ja,
            "aliases": node.aliases[:8],
            "physical": physical[:8],
        }
        if node.business_rule_definition is not None:
            concept["definition_ja"] = str(node.business_rule_definition.model_dump(mode="json"))[
                :400
            ]
        if node.enum_value_definition is not None:
            payload = node.enum_value_definition.model_dump(mode="json")
            concept["values"] = [
                str(value) for value in payload.values() if isinstance(value, str | int)
            ][:8]
        result.append(concept)
        if len(result) >= _MAX_ONTOLOGY_CONCEPTS:
            break
    return result


def _relevant_tables(
    tables: dict[str, TableInfo], question: str, concepts: Sequence[Mapping[str, Any]]
) -> dict[str, TableInfo]:
    """Schema が大きいときは、質問との関連で上位の表に絞る（範囲の外の表は元から入れない）。"""

    if len(tables) <= _MAX_CONTEXT_TABLES:
        return tables
    normalized = question.casefold()

    def score(info: TableInfo) -> int:
        terms = [info.name, info.logical_name, info.comment]
        for column in info.columns.values():
            terms.extend([column.name, column.logical_name])
        value = sum(
            1 for term in terms if term and len(term) >= 2 and term.casefold() in normalized
        )
        for concept in concepts:
            names = [str(concept.get("name_ja") or ""), *map(str, concept.get("aliases", []))]
            if any(name and name.casefold() in normalized for name in names) and any(
                str(ref).startswith(info.name) or str(ref) == info.key
                for ref in concept.get("physical", [])
            ):
                value += 2
        return value

    ranked = sorted(tables.values(), key=lambda info: (-score(info), info.key))
    return {info.key: info for info in ranked[:_MAX_CONTEXT_TABLES]}


_FISCAL_PATTERNS = (
    re.compile(r"年度[^。\n]{0,24}?(1[0-2]|[1-9])\s*月"),
    re.compile(r"(1[0-2]|[1-9])\s*月\s*(?:1\s*日)?\s*(?:から|始まり|開始|に始ま)[^。\n]{0,12}年度"),
    re.compile(r"fiscal year[^.\n]{0,24}?(?:starts?|begins?)[^.\n]{0,12}?(1[0-2]|[1-9])\b", re.I),
)


def detect_fiscal_year_start(
    profile: Nl2SqlProfile, concepts: Sequence[Mapping[str, Any]]
) -> tuple[int, str] | None:
    """年度の開始月を業務プロファイル・用語集・オントロジーの記述から見つける（無ければ None）。"""

    sources: list[tuple[str, str]] = [("profile", profile.description)]
    sources.extend(("profile", rule) for rule in profile.sql_rules)
    sources.extend(("glossary", f"{term} {value}") for term, value in profile.glossary.items())
    for concept in concepts:
        text = " ".join(
            str(concept.get(key) or "") for key in ("name_ja", "description_ja", "definition_ja")
        )
        sources.append(("ontology", text))
    for source, text in sources:
        for pattern in _FISCAL_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return int(match.group(1)), source
    return None


def context_payload(
    context: RequirementContext,
    *,
    mode: Literal["draft", "review"],
    draft: SqlRequirementDraft | None = None,
) -> dict[str, Any]:
    """LLM に渡す JSON（表のデータ・代表値・件数は入れない）。"""

    payload: dict[str, Any] = {
        "mode": mode,
        "reference_date": context.today.isoformat(),
        "schema": [
            {
                "table": info.key,
                "logical_name": info.logical_name,
                "comment": info.comment,
                "type": info.table_type,
                "primary_key": [catalog_match_key(info.name, name) for name in info.primary_key],
                "columns": [
                    {
                        "name": column.key,
                        "logical_name": column.logical_name,
                        "data_type": column.data_type,
                        "nullable": column.nullable,
                        "comment": column.comment,
                        "domain": column.domain_name,
                        "masked": column.masked,
                        "filterable": column.filterable,
                    }
                    for column in list(info.columns.values())[:_MAX_CONTEXT_COLUMNS_PER_TABLE]
                ],
            }
            for info in context.tables.values()
        ],
        "relationships": [
            {
                "id": item.id,
                "tables": [item.left_table, item.right_table],
                "condition": item.condition,
                "cardinality": item.cardinality,
                "name_ja": item.name_ja,
            }
            for item in context.relationships
        ],
        "profile": context.profile_context,
        "ontology": context.ontology_concepts,
        "fiscal_year_start": (
            {"month": context.fiscal_year_start[0], "source": context.fiscal_year_start[1]}
            if context.fiscal_year_start
            else None
        ),
    }
    if draft is not None:
        payload["current_draft"] = {
            "clarified_question_ja": draft.clarified_question_ja,
            "items": [_item_for_llm(item) for item in draft.items if not item.answered],
        }
        payload["answered_items"] = [_item_for_llm(item) for item in draft.items if item.answered]
    return payload


def _item_for_llm(item: RequirementItem) -> dict[str, Any]:
    return {
        "key": item.key,
        "section": item.section.value,
        "label_ja": item.label_ja,
        "value_ja": item.value_ja,
        "status": item.status.value,
        "tables": item.tables,
        "columns": item.columns,
        "relationship_ids": item.relationship_ids,
        "sql_hint": item.sql_hint,
        "period": item.period.model_dump(mode="json") if item.period else None,
        "answer_ja": item.answer_ja,
    }


# ---------------------------------------------------------------------------
# LLM の呼び出し
# ---------------------------------------------------------------------------


def call_requirement_llm(
    client: Any,
    *,
    question: str,
    context: RequirementContext,
    mode: Literal["draft", "review"],
    draft: SqlRequirementDraft | None = None,
) -> tuple[LlmRequirementOutput, str]:
    configured = getattr(client, "is_configured", None)
    generate = getattr(client, "generate", None)
    if not callable(configured) or not configured() or not callable(generate):
        raise RequirementAiUnavailableError(
            "REQUIREMENT_AI_NOT_CONFIGURED",
            "OCI Enterprise AI が設定されていないため、AI要件確認を使えません。",
        )
    model_id = ""
    model_id_fn = getattr(client, "model_id", None)
    if callable(model_id_fn):
        try:
            model_id = str(model_id_fn())
        except Exception:
            model_id = ""
    prompt = question
    if mode == "review" and draft is not None:
        prompt = (
            f"元の質問: {draft.question_original}\n"
            f"今の要件のまとめ（書き直した質問）: {draft.clarified_question_ja}\n"
            "このまとめと answered_items を検査し、SQL を作るのにまだ足りない・"
            "明確でない情報を挙げてください。"
        )
    try:
        raw = generate(
            prompt=prompt,
            context=json.dumps(
                context_payload(context, mode=mode, draft=draft),
                ensure_ascii=False,
                sort_keys=True,
            ),
            system_prompt=SYSTEM_PROMPT,
            response_format=requirement_response_format(),
        )
    except Exception as exc:
        raise RequirementAiUnavailableError(
            "REQUIREMENT_AI_FAILED",
            "AI要件確認の LLM の呼び出しに失敗しました。もう一度お試しください。",
        ) from exc
    try:
        parsed = LlmRequirementOutput.model_validate_json(str(raw).strip())
    except (ValidationError, ValueError) as exc:
        raise RequirementAiUnavailableError(
            "REQUIREMENT_AI_INVALID_OUTPUT",
            "AI要件確認の LLM の応答が指定の形式ではありませんでした。もう一度お試しください。",
        ) from exc
    return parsed, model_id


# ---------------------------------------------------------------------------
# 下書きの検査（確定的）
# ---------------------------------------------------------------------------


def build_draft(
    output: LlmRequirementOutput,
    context: RequirementContext,
    *,
    question: str,
    model: str = "",
    previous: SqlRequirementDraft | None = None,
) -> SqlRequirementDraft:
    """LLM の出力を検査して下書きにする。答えた項目は前の下書きのまま保つ。"""

    warnings: list[str] = []
    # 前の下書きの項目は保つ。答えた項目は変えず、LLM が同じ key で出し直した未回答の項目だけを
    # 置き換え、新しい key の項目を足す（LLM が出し忘れた項目を消さない）。
    items: list[RequirementItem] = [
        item.model_copy(deep=True) for item in (previous.items if previous else [])
    ]
    answered_keys = {item.key for item in items if item.answered}
    position = {item.key: index for index, item in enumerate(items)}
    seen_keys: set[str] = set()
    for raw in output.items[:_MAX_ITEMS]:
        key = _normalize_key(raw.key) or _normalize_key(f"{raw.section}_{len(items)}")
        if key in answered_keys:
            continue
        while key in seen_keys:
            key = f"{key}_2"
        seen_keys.add(key)
        validated = _validated_item(raw, key, context, warnings)
        if key in position:
            items[position[key]] = validated
        else:
            position[key] = len(items)
            items.append(validated)
    draft = SqlRequirementDraft(
        version=(previous.version + 1) if previous else 1,
        question_original=question,
        clarified_question_ja=output.clarified_question_ja.strip(),
        items=items,
        unanswerable_reason_ja=output.unanswerable_reason_ja.strip(),
        warnings_ja=_unique(warnings),
        reference_date=context.today.isoformat(),
        model=model,
    )
    return enforce_requirement_rules(draft, context)


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().casefold()).strip("_")[:64]


def _validated_item(
    raw: _LlmItem,
    key: str,
    context: RequirementContext,
    warnings: list[str],
) -> RequirementItem:
    section = RequirementSection(raw.section)
    status = RequirementStatus(raw.status)
    reasons: list[str] = [raw.reason_ja.strip()] if raw.reason_ja.strip() else []
    tables, bad_tables = _resolve_tables(raw.tables, context)
    columns, bad_columns = _resolve_columns(raw.columns, context)
    relationship_ids, bad_relationships = _resolve_relationships(raw.relationship_ids, context)
    rejected = [*bad_tables, *bad_columns]
    if rejected:
        warnings.append(
            f"Schema に無い・業務プロファイルの範囲の外の名前を除きました: {'、'.join(rejected)}"
        )
        reasons.append(
            f"Schema に実在しない、または業務プロファイルの範囲の外の名前（{'、'.join(rejected)}）"
            "を除きました。"
        )
        if status in _CLEAR:
            status = RequirementStatus.AMBIGUOUS
    if bad_relationships:
        warnings.append("承認済みでない結合の関係を除きました。")
        reasons.append("承認済みの関係・外部キーではない結合を除きました。")
        if status in _CLEAR:
            status = RequirementStatus.AMBIGUOUS
    sql_hint = strip_clause_keyword(raw.sql_hint)
    if rejected:
        sql_hint = ""
    masked = [column for column in columns if _column_masked(context, column)]
    candidates = [
        candidate
        for index, value in enumerate(raw.candidates[:_MAX_CANDIDATES])
        if (candidate := _validated_candidate(value, index, key, section, context)) is not None
    ]
    if section in {RequirementSection.WHERE, RequirementSection.PERIOD} and masked:
        # マスク・非公開の値を推測・生成しない（候補も値の入った断片も出さない）。
        candidates = []
        if _has_literal(sql_hint):
            sql_hint = ""
            if status in _CLEAR:
                status = RequirementStatus.AMBIGUOUS
        reasons.append("マスクされた列の値は AI が推測しません。値を入力してください。")
    period: RequirementPeriod | None = None
    if raw.period is not None:
        date_column = (
            context.resolve_column(raw.period.date_column) if raw.period.date_column else ""
        )
        if raw.period.date_column and not date_column:
            warnings.append(f"Schema に無い日付の列を除きました: {raw.period.date_column}")
        column_info = context.column(date_column) if date_column else None
        if column_info is not None and not column_info.is_date:
            date_column = ""
        period = RequirementPeriod(
            expression_ja=raw.period.expression_ja.strip() or raw.value_ja.strip(),
            relative_kind=raw.period.relative_kind.strip().casefold(),
            count=raw.period.count if raw.period.count > 0 else None,
            fiscal_year_start_month=None,
            date_column=date_column,
        )
    question = raw.question_ja.strip()
    if status in _UNRESOLVED and not question:
        question = f"{raw.label_ja.strip() or SECTION_LABELS_JA[section]}を教えてください。"
    answer_kind = RequirementAnswerKind(raw.answer_kind)
    if not candidates:
        answer_kind = RequirementAnswerKind.FREE_TEXT
    elif answer_kind == RequirementAnswerKind.FREE_TEXT:
        answer_kind = RequirementAnswerKind.SINGLE_SELECT
    if status == RequirementStatus.AMBIGUOUS and not candidates and not question:
        question = f"{raw.label_ja.strip()}を教えてください。"
    item = RequirementItem(
        id=stable_ontology_id("requirement_item", key),
        key=key,
        section=section,
        label_ja=raw.label_ja.strip() or SECTION_LABELS_JA[section],
        value_ja=raw.value_ja.strip(),
        status=status,
        reason_ja=" ".join(_unique(reasons)),
        question_ja=question if status in _UNRESOLVED else "",
        answer_kind=answer_kind,
        candidates=candidates if status in _UNRESOLVED else [],
        source=RequirementSource(raw.source),
        tables=tables,
        columns=columns,
        relationship_ids=relationship_ids,
        sql_hint=sql_hint,
        period=period,
    )
    item.refs_ja = refs_text(context, tables=item.tables, columns=item.columns)
    return item


def _resolve_tables(
    values: Iterable[str], context: RequirementContext
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    rejected: list[str] = []
    for value in values:
        if not str(value).strip():
            continue
        key = context.resolve_table(str(value))
        if key:
            if key not in resolved:
                resolved.append(key)
        else:
            rejected.append(str(value).strip())
    return resolved, rejected


def _resolve_columns(
    values: Iterable[str], context: RequirementContext
) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    rejected: list[str] = []
    for value in values:
        if not str(value).strip():
            continue
        key = context.resolve_column(str(value))
        if key:
            if key not in resolved:
                resolved.append(key)
        else:
            rejected.append(str(value).strip())
    return resolved, rejected


def _resolve_relationships(
    values: Iterable[str], context: RequirementContext
) -> tuple[list[str], list[str]]:
    approved = {item.id for item in context.relationships}
    resolved = [value for value in values if value in approved]
    rejected = [value for value in values if value and value not in approved]
    return _unique(resolved), rejected


def _column_masked(context: RequirementContext, column_key: str) -> bool:
    info = context.column(column_key)
    return bool(info and (info.masked or not info.filterable))


_LITERAL = re.compile(r"'((?:[^']|'')*)'")


def _has_literal(value: str) -> bool:
    return bool(_LITERAL.search(value or ""))


_CLAUSE_KEYWORD = re.compile(r"^\s*(?:SELECT|FROM|WHERE|AND|GROUP\s+BY|ORDER\s+BY|HAVING)\s+", re.I)


def strip_clause_keyword(value: str) -> str:
    """sql_hint の先頭の句のキーワード（`SELECT` など）を外す（骨組みで二重にしない）。"""

    # `SELECT DISTINCT a` は `DISTINCT a` にする（骨組みは `SELECT DISTINCT a`）。
    return _CLAUSE_KEYWORD.sub("", (value or "").strip(), count=1).strip()


def _validated_candidate(
    raw: _LlmCandidate,
    index: int,
    item_key: str,
    section: RequirementSection,
    context: RequirementContext,
) -> RequirementCandidate | None:
    """根拠の無い候補・範囲の外の名前・承認されていない結合・根拠の無い値は出さない。"""

    if not raw.label_ja.strip() or not raw.evidence_ja.strip():
        return None
    tables, bad_tables = _resolve_tables(raw.tables, context)
    columns, bad_columns = _resolve_columns(raw.columns, context)
    relationships, bad_relationships = _resolve_relationships(raw.relationship_ids, context)
    if bad_tables or bad_columns or bad_relationships:
        return None
    if section in {RequirementSection.WHERE, RequirementSection.PERIOD}:
        if any(_column_masked(context, column) for column in columns):
            return None
        for literal in _LITERAL.findall(raw.sql_hint):
            value = literal.replace("''", "'").strip().casefold()
            if value and value not in context.evidence_corpus:
                # 値がコメント・用語集・オントロジーに書かれていない（データからの推測）。
                return None
    if raw.source == "calendar":
        return None
    candidate = RequirementCandidate(
        id=stable_ontology_id("requirement_candidate", item_key, str(index), raw.label_ja),
        label_ja=raw.label_ja.strip(),
        description_ja=raw.description_ja.strip(),
        evidence_ja=raw.evidence_ja.strip(),
        source=RequirementSource(raw.source),
        tables=tables,
        columns=columns,
        relationship_ids=relationships,
        sql_hint=strip_clause_keyword(raw.sql_hint),
    )
    candidate.refs_ja = refs_text(context, tables=tables, columns=columns)
    return candidate


def refs_text(context: RequirementContext, *, tables: Sequence[str], columns: Sequence[str]) -> str:
    """画面に出す物理名（論理名は括弧で添える）。"""

    parts = [context.table_display(key) for key in tables]
    parts.extend(context.column_display(key) for key in columns)
    return "、".join(_unique(parts))


def enforce_requirement_rules(
    draft: SqlRequirementDraft, context: RequirementContext
) -> SqlRequirementDraft:
    """最低の基準（SELECT・FROM）・承認済みの結合・相対的な期間の確認をプログラムで強制する。"""

    updated = draft.model_copy(deep=True)
    items = updated.items
    _ensure_from(items, context)
    _ensure_select(items)
    _include_referenced_tables(items, context)
    _enforce_joins(updated, context)
    _enforce_periods(items, context)
    _enforce_risks(items, context)
    for item in items:
        item.refs_ja = refs_text(context, tables=item.tables, columns=item.columns)
        for candidate in item.candidates:
            if not candidate.refs_ja:
                candidate.refs_ja = refs_text(
                    context, tables=candidate.tables, columns=candidate.columns
                )
    updated.items = sorted(
        items, key=lambda item: (_SECTION_ORDER[item.section], items.index(item))
    )
    updated.clarified_question_ja = _ensure_physical_names(updated, context)
    updated.sql_skeleton = build_sql_skeleton(updated, context)
    return updated


def from_tables(items: Iterable[RequirementItem]) -> list[str]:
    tables: list[str] = []
    for item in items:
        if item.section == RequirementSection.FROM and item.status in _CLEAR:
            tables.extend(table for table in item.tables if table not in tables)
    return tables


def _ensure_from(items: list[RequirementItem], context: RequirementContext) -> None:
    from_items = [item for item in items if item.section == RequirementSection.FROM]
    for item in from_items:
        if item.status in _CLEAR and not item.tables:
            item.status = (
                RequirementStatus.AMBIGUOUS if item.candidates else RequirementStatus.MISSING
            )
            item.reason_ja = item.reason_ja or "使う表が Schema の物理名で決まっていません。"
            item.question_ja = item.question_ja or "どの表のデータを使いますか？"
    if any(item.status in _CLEAR and item.tables for item in from_items):
        return
    if any(item.status in _UNRESOLVED for item in from_items):
        target = next(item for item in from_items if item.status in _UNRESOLVED)
    else:
        target = RequirementItem(
            id=stable_ontology_id("requirement_item", "from_tables"),
            key="from_tables",
            section=RequirementSection.FROM,
            label_ja="使う表",
            status=RequirementStatus.MISSING,
            reason_ja="SQL を作るには、どの表から（FROM）が明確である必要があります。",
            question_ja="どの表のデータを使いますか？",
        )
        items.append(target)
    if not target.candidates:
        target.candidates = [
            RequirementCandidate(
                id=stable_ontology_id("requirement_candidate", target.key, info.key),
                label_ja=context.table_display(info.key),
                description_ja=info.comment,
                evidence_ja="業務プロファイルの対象の表",
                source=RequirementSource.SCHEMA,
                tables=[info.key],
                refs_ja=context.table_display(info.key),
            )
            for info in list(context.tables.values())[:_MAX_CANDIDATES]
        ]
    if target.candidates:
        target.status = RequirementStatus.AMBIGUOUS
        target.answer_kind = RequirementAnswerKind.MULTI_SELECT


def _include_referenced_tables(items: list[RequirementItem], context: RequirementContext) -> None:
    """SELECT・結合・WHERE などで使う表を FROM に足す（FROM と食い違うと SQL が作れない）。

    WITH・サブクエリの項目の表は、外側の FROM に入れない（サブクエリの中で使う）。
    """

    target = next(
        (
            item
            for item in items
            if item.section == RequirementSection.FROM
            and (item.status in _CLEAR or item.answered)
            and item.tables
        ),
        None,
    )
    if target is None:
        return
    current = set(from_tables(items))
    referenced: list[str] = []
    for item in items:
        if item.section in {RequirementSection.FROM, RequirementSection.SUBQUERY}:
            continue
        if not (item.status in _CLEAR or item.answered):
            continue
        tables = [*item.tables]
        tables.extend(
            table for column in item.columns if (table := context.column_lookup.get(column, ""))
        )
        referenced.extend(table for table in tables if table and table not in current)
    added = _unique(referenced)
    if not added:
        return
    target.tables = _unique([*target.tables, *added])
    names = "、".join(context.table_display(table) for table in added)
    note = f"SELECT・条件などで使う {names} を FROM に足しました。"
    if note not in target.reason_ja:
        target.reason_ja = f"{target.reason_ja} {note}".strip()
    if target.value_ja and not all(table in target.value_ja for table in added):
        target.value_ja = f"{target.value_ja}、{'、'.join(added)}"


def _ensure_select(items: list[RequirementItem]) -> None:
    select_items = [item for item in items if item.section == RequirementSection.SELECT]
    if any(item.status in _CLEAR for item in select_items):
        return
    if select_items:
        return
    items.append(
        RequirementItem(
            id=stable_ontology_id("requirement_item", "select_output"),
            key="select_output",
            section=RequirementSection.SELECT,
            label_ja="表示する項目",
            status=RequirementStatus.MISSING,
            reason_ja="SQL を作るには、何を表示するか（SELECT）が明確である必要があります。",
            question_ja="結果に表示する列・集計を、Schema の列名で教えてください。",
        )
    )


def _enforce_joins(draft: SqlRequirementDraft, context: RequirementContext) -> None:
    items = draft.items
    tables = from_tables(items)
    join_items = [item for item in items if item.section == RequirementSection.JOIN]
    if len(tables) < 2:
        return
    relations = [
        relation
        for relation in context.relationships
        if relation.left_table in tables and relation.right_table in tables
    ]
    if not _connected(tables, relations):
        path = _path_through_scope(tables, context.relationships)
        target = next((item for item in join_items if not item.answered), None)
        if target is None:
            target = RequirementItem(
                id=stable_ontology_id("requirement_item", "join_path"),
                key="join_path",
                section=RequirementSection.JOIN,
                label_ja="表の結合の経路",
                status=RequirementStatus.MISSING,
            )
            items.append(target)
        if path is None:
            target.status = RequirementStatus.MISSING
            target.candidates = []
            target.reason_ja = f"{'、'.join(tables)} を、承認済みの関係・外部キーでつなげません。"
            target.question_ja = "使う表を直すか、管理者に関係の承認を依頼してください。"
            if not any(
                item.section == RequirementSection.FROM and item.status in _UNRESOLVED
                for item in items
            ):
                draft.unanswerable_reason_ja = draft.unanswerable_reason_ja or (
                    "この業務プロファイルでは答えられません。"
                    f"{'、'.join(tables)} をつなぐ承認済みの関係・外部キーがありません。"
                )
        else:
            extra_tables, relation_ids = path
            target.status = RequirementStatus.AMBIGUOUS
            target.answer_kind = RequirementAnswerKind.SINGLE_SELECT
            target.reason_ja = "使う表は、間の表を経由しないとつなげません。"
            target.question_ja = f"{'、'.join(extra_tables)} を経由して表をつなぎますか？"
            conditions = [
                relation.condition
                for relation in context.relationships
                if relation.id in relation_ids
            ]
            target.candidates = [
                RequirementCandidate(
                    id=stable_ontology_id("requirement_candidate", target.key, *relation_ids),
                    label_ja=f"{'、'.join(extra_tables)} を経由する: {' / '.join(conditions)}",
                    evidence_ja="承認済みの関係・外部キー",
                    source=RequirementSource.SCHEMA,
                    tables=extra_tables,
                    relationship_ids=relation_ids,
                    sql_hint=" AND ".join(conditions),
                )
            ]
        return
    # 同じ 2 つの表の間に承認済みの関係が複数あるときは、利用者に選んでもらう。
    pairs: dict[tuple[str, str], list[RelationshipInfo]] = {}
    for relation in relations:
        pair = (
            min(relation.left_table, relation.right_table),
            max(relation.left_table, relation.right_table),
        )
        pairs.setdefault(pair, []).append(relation)
    chosen = {
        relation_id
        for item in join_items
        if item.answered or item.status in _CLEAR
        for relation_id in item.relationship_ids
    }
    for pair, options in pairs.items():
        if len(options) < 2:
            continue
        if any(option.id in chosen for option in options) and any(
            item.answered
            for item in join_items
            if set(item.relationship_ids) & {option.id for option in options}
        ):
            continue
        key = _normalize_key(f"join_{pair[0]}_{pair[1]}")
        target = next(
            (
                item
                for item in join_items
                if set(item.relationship_ids) & {option.id for option in options}
            ),
            None,
        )
        if target is None or target.answered:
            target = RequirementItem(
                id=stable_ontology_id("requirement_item", key),
                key=key,
                section=RequirementSection.JOIN,
                label_ja="表の結合の条件",
                status=RequirementStatus.AMBIGUOUS,
            )
            items.append(target)
        target.status = RequirementStatus.AMBIGUOUS
        target.answer_kind = RequirementAnswerKind.SINGLE_SELECT
        target.reason_ja = f"{pair[0]} と {pair[1]} の間に、承認済みの関係が複数あります。"
        target.question_ja = f"{pair[0]} と {pair[1]} を、どの条件で結合しますか？"
        target.candidates = [
            RequirementCandidate(
                id=stable_ontology_id("requirement_candidate", key, option.id),
                label_ja=option.condition,
                description_ja=option.name_ja,
                evidence_ja="承認済みの関係・外部キー",
                source=RequirementSource.SCHEMA
                if option.source == "foreign_key"
                else RequirementSource.ONTOLOGY,
                relationship_ids=[option.id],
                sql_hint=option.condition,
            )
            for option in options
        ]
    if not join_items and len(relations) >= len(tables) - 1:
        unique_ids = [relation.id for relation in relations]
        items.append(
            RequirementItem(
                id=stable_ontology_id("requirement_item", "join_relationships"),
                key="join_relationships",
                section=RequirementSection.JOIN,
                label_ja="表の結合の条件",
                value_ja=" / ".join(relation.condition for relation in relations),
                status=RequirementStatus.INFERRED,
                source=RequirementSource.SCHEMA,
                relationship_ids=unique_ids,
                sql_hint=" AND ".join(relation.condition for relation in relations),
            )
        )


def _connected(tables: Sequence[str], relations: Sequence[RelationshipInfo]) -> bool:
    if not tables:
        return True
    adjacency: dict[str, set[str]] = {table: set() for table in tables}
    for relation in relations:
        adjacency.setdefault(relation.left_table, set()).add(relation.right_table)
        adjacency.setdefault(relation.right_table, set()).add(relation.left_table)
    seen = {tables[0]}
    frontier = [tables[0]]
    while frontier:
        current = frontier.pop()
        for target in adjacency.get(current, set()):
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return all(table in seen for table in tables)


def _path_through_scope(
    tables: Sequence[str], relations: Sequence[RelationshipInfo]
) -> tuple[list[str], list[str]] | None:
    """範囲の中の別の表を経由してつなげるなら、経由する表と関係を返す（最短の経路）。"""

    adjacency: dict[str, list[RelationshipInfo]] = {}
    for relation in relations:
        adjacency.setdefault(relation.left_table, []).append(relation)
        adjacency.setdefault(relation.right_table, []).append(relation)
    start = tables[0]
    extra: list[str] = []
    relation_ids: list[str] = []
    for goal in tables[1:]:
        previous: dict[str, tuple[str, str]] = {start: ("", "")}
        frontier = [start]
        while frontier and goal not in previous:
            next_frontier: list[str] = []
            for current in frontier:
                for relation in adjacency.get(current, []):
                    other = (
                        relation.right_table
                        if relation.left_table == current
                        else relation.left_table
                    )
                    if other not in previous:
                        previous[other] = (current, relation.id)
                        next_frontier.append(other)
            frontier = next_frontier
        if goal not in previous:
            return None
        node = goal
        while node != start:
            parent, relation_id = previous[node]
            if relation_id not in relation_ids:
                relation_ids.append(relation_id)
            if parent and parent != start and parent not in tables and parent not in extra:
                extra.append(parent)
            node = parent
    return extra, relation_ids


def _enforce_periods(items: list[RequirementItem], context: RequirementContext) -> None:
    """相対的な期間の表現は、具体的な日付の範囲として必ず利用者に確かめる。"""

    period_items = [
        item
        for item in items
        if item.section == RequirementSection.PERIOD and item.period is not None
    ]
    if not period_items:
        return
    tables = from_tables(items)
    date_columns = context.date_columns(tables)
    column_item = next((item for item in items if item.key == "period_date_column"), None)
    chosen_column = ""
    if column_item is not None and column_item.answered and column_item.columns:
        chosen_column = column_item.columns[0]
    elif len(date_columns) == 1:
        chosen_column = date_columns[0].key
    llm_column = next(
        (
            item.period.date_column
            for item in period_items
            if item.period and item.period.date_column
        ),
        "",
    )
    first_period = period_items[0].period
    first_expression = first_period.expression_ja if first_period else ""
    if not chosen_column and len(date_columns) > 1:
        if column_item is None:
            column_item = RequirementItem(
                id=stable_ontology_id("requirement_item", "period_date_column"),
                key="period_date_column",
                section=RequirementSection.PERIOD,
                label_ja="期間で絞る日付の列",
                status=RequirementStatus.AMBIGUOUS,
            )
            items.append(column_item)
        if not column_item.answered:
            ordered = sorted(date_columns, key=lambda column: column.key != llm_column)
            column_item.status = RequirementStatus.AMBIGUOUS
            column_item.answer_kind = RequirementAnswerKind.SINGLE_SELECT
            column_item.reason_ja = "期間の条件に使える日付の列が複数あります。"
            column_item.question_ja = f"「{first_expression}」の期間は、どの日付の列で絞りますか？"
            column_item.candidates = [
                RequirementCandidate(
                    id=stable_ontology_id(
                        "requirement_candidate", "period_date_column", column.key
                    ),
                    label_ja=context.column_display(column.key),
                    description_ja=(
                        ("AI の推測。" if column.key == llm_column else "")
                        + (column.comment or column.data_type)
                    ),
                    evidence_ja=f"Schema の日付の列（{column.data_type}）",
                    source=RequirementSource.SCHEMA,
                    columns=[column.key],
                    refs_ja=context.column_display(column.key),
                )
                for column in ordered[:_MAX_CANDIDATES]
            ]
    elif not chosen_column and not date_columns and tables:
        if column_item is None:
            column_item = RequirementItem(
                id=stable_ontology_id("requirement_item", "period_date_column"),
                key="period_date_column",
                section=RequirementSection.PERIOD,
                label_ja="期間で絞る日付の列",
                status=RequirementStatus.MISSING,
            )
            items.append(column_item)
        if not column_item.answered:
            column_item.status = RequirementStatus.MISSING
            column_item.reason_ja = "使う表に日付の列（DATE / TIMESTAMP）がありません。"
            column_item.question_ja = "期間で絞る列を、Schema の列名で教えてください。"
    elif chosen_column and column_item is not None and not column_item.answered:
        column_item.status = RequirementStatus.INFERRED
        column_item.columns = [chosen_column]
        column_item.value_ja = context.column_display(chosen_column)
        column_item.question_ja = ""
        column_item.candidates = []
    for item in period_items:
        period = item.period
        if period is None:
            continue
        if chosen_column:
            period.date_column = chosen_column
            if chosen_column not in item.columns:
                item.columns = [chosen_column]
        if item.answered:
            if period.start_date and period.date_column:
                period.sql_condition = period_sql_condition(period)
                item.sql_hint = period.sql_condition
            continue
        candidates = period_candidates(period, context)
        if not candidates:
            if period.relative_kind in RELATIVE_PERIOD_KINDS or period.relative_kind == "other":
                item.status = RequirementStatus.AMBIGUOUS
                item.answer_kind = RequirementAnswerKind.FREE_TEXT
                item.question_ja = (
                    f"「{period.expression_ja}」は、いつからいつまでですか？"
                    "具体的な日付（例: 2026-04-01〜2027-03-31）で教えてください。"
                )
                item.reason_ja = "相対的な期間の表現は、具体的な日付の範囲で確かめます。"
                item.candidates = []
            continue
        item.status = RequirementStatus.AMBIGUOUS
        item.answer_kind = RequirementAnswerKind.SINGLE_SELECT
        item.candidates = candidates
        fy_note = ""
        if period.relative_kind in _FISCAL_KINDS:
            if context.fiscal_year_start:
                fy_note = (
                    f"年度の開始月は{_source_label(context.fiscal_year_start[1])}の定義"
                    f"（{context.fiscal_year_start[0]}月）を候補にしました。"
                )
            else:
                fy_note = (
                    "年度の開始月が業務プロファイル・用語集・オントロジーに定義されていません。"
                )
        column_note = (
            f"日付の列は {context.column_display(period.date_column)} です。"
            if period.date_column
            else ""
        )
        item.reason_ja = (
            "相対的な期間の表現は、具体的な日付の範囲で確かめます。"
            f"基準日は実行日（{context.today.isoformat()}）を既定にしています（表のデータは読みません）。"
            f"範囲は開始日と終了日を含みます。{fy_note}{column_note}"
        )
        item.question_ja = f"「{period.expression_ja}」は、どの期間ですか？"


def _source_label(source: str) -> str:
    return {"profile": "業務プロファイル", "glossary": "用語集", "ontology": "オントロジー"}.get(
        source, source
    )


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _fiscal_year_start(today: date, start_month: int) -> date:
    year = today.year if today.month >= start_month else today.year - 1
    return date(year, start_month, 1)


def period_ranges(
    kind: str, count: int | None, today: date, fiscal_start_month: int | None
) -> list[tuple[str, date, date]]:
    """相対的な期間の種類から、候補の範囲（説明・開始日・終了日。両端を含む）を計算する。"""

    n = count or 1
    if kind == "today":
        return [("今日", today, today)]
    if kind == "yesterday":
        day = today - timedelta(days=1)
        return [("昨日", day, day)]
    if kind == "this_week":
        start = today - timedelta(days=today.weekday())
        return [
            ("今週（月曜始まり、今日まで）", start, today),
            ("今週（月曜〜日曜）", start, start + timedelta(days=6)),
        ]
    if kind == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return [("先週（月曜〜日曜）", start, start + timedelta(days=6))]
    if kind == "this_month":
        start = _month_start(today)
        return [
            ("今月（月初〜今日）", start, today),
            ("今月（月初〜月末）", start, _month_end(today)),
        ]
    if kind == "last_month":
        start = _add_months(_month_start(today), -1)
        return [("先月（月初〜月末）", start, _month_end(start))]
    if kind in {"this_quarter", "last_quarter"}:
        quarter_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
        if kind == "last_quarter":
            quarter_start = _add_months(quarter_start, -3)
            return [
                (
                    "前の四半期（暦の四半期）",
                    quarter_start,
                    _month_end(_add_months(quarter_start, 2)),
                )
            ]
        return [
            ("今の四半期（暦の四半期、今日まで）", quarter_start, today),
            (
                "今の四半期（暦の四半期の全体）",
                quarter_start,
                _month_end(_add_months(quarter_start, 2)),
            ),
        ]
    if kind == "this_year":
        return [
            ("今年（1 月 1 日〜今日）", date(today.year, 1, 1), today),
            ("今年（1 月 1 日〜12 月 31 日）", date(today.year, 1, 1), date(today.year, 12, 31)),
        ]
    if kind == "last_year":
        return [
            (
                "昨年（1 月 1 日〜12 月 31 日）",
                date(today.year - 1, 1, 1),
                date(today.year - 1, 12, 31),
            )
        ]
    if kind == "year_to_date":
        return [("今年の 1 月 1 日〜今日", date(today.year, 1, 1), today)]
    if kind in _FISCAL_KINDS:
        months = [fiscal_start_month] if fiscal_start_month else [4, 1]
        result: list[tuple[str, date, date]] = []
        for month in months:
            start = _fiscal_year_start(today, month)
            label = f"{month} 月始まりの年度"
            if kind == "last_fiscal_year":
                start = _add_months(start, -12)
                result.append(
                    (f"前の年度（{label}）", start, _add_months(start, 12) - timedelta(days=1))
                )
            elif kind == "fiscal_year_to_date":
                result.append((f"今年度の初め〜今日（{label}）", start, today))
            else:
                result.append(
                    (
                        f"今年度（{label}、年度の全体）",
                        start,
                        _add_months(start, 12) - timedelta(days=1),
                    )
                )
                result.append((f"今年度（{label}、今日まで）", start, today))
        return result
    if kind == "last_n_days":
        return [(f"直近 {n} 日（今日を含む）", today - timedelta(days=n - 1), today)]
    if kind == "last_n_weeks":
        return [(f"直近 {n} 週間（今日を含む）", today - timedelta(days=7 * n - 1), today)]
    if kind == "last_n_months":
        rolling_start = _add_months(today, -n) + timedelta(days=1)
        full_end = _month_start(today) - timedelta(days=1)
        full_start = _add_months(_month_start(today), -n)
        current_start = _add_months(_month_start(today), -(n - 1))
        return [
            (f"直近 {n} か月（今日から遡る）", rolling_start, today),
            (f"前月までの {n} か月（月単位）", full_start, full_end),
            (f"今月を含む {n} か月（今月は今日まで）", current_start, today),
        ]
    if kind == "last_n_years":
        rolling_start = (
            date(today.year - n, today.month, today.day) + timedelta(days=1)
            if not (today.month == 2 and today.day == 29)
            else date(today.year - n, 3, 1)
        )
        return [
            (f"直近 {n} 年（今日から遡る）", rolling_start, today),
            (
                f"前年までの {n} 年（暦年）",
                date(today.year - n, 1, 1),
                date(today.year - 1, 12, 31),
            ),
        ]
    if kind == "same_period_last_year":
        month_start = _add_months(_month_start(today), -12)
        same_day = _add_months(today, -12)
        return [
            ("昨年の同じ月（月初〜月末）", month_start, _month_end(month_start)),
            ("昨年の同じ月の月初〜同じ日", month_start, same_day),
            ("昨年の 1 月 1 日〜同じ日", date(today.year - 1, 1, 1), same_day),
        ]
    return []


def period_candidates(
    period: RequirementPeriod, context: RequirementContext
) -> list[RequirementCandidate]:
    fiscal = context.fiscal_year_start
    fiscal_month = fiscal[0] if fiscal else None
    ranges = period_ranges(period.relative_kind, period.count, context.today, fiscal_month)
    if not ranges:
        return []
    source = RequirementSource.CALENDAR
    evidence = f"基準日（実行日）{context.today.isoformat()} から計算"
    if period.relative_kind in _FISCAL_KINDS and fiscal:
        source = (
            RequirementSource(fiscal[1])
            if fiscal[1] in {"profile", "glossary", "ontology"}
            else source
        )
        evidence = (
            f"{_source_label(fiscal[1])}の年度の定義（{fiscal[0]} 月始まり）"
            f"と基準日 {context.today.isoformat()}"
        )
    candidates: list[RequirementCandidate] = []
    for label, start, end in ranges:
        value = period.model_copy(
            update={
                "reference_basis": RequirementReferenceBasis.EXECUTION_DATE,
                "reference_date": context.today.isoformat(),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "fiscal_year_start_month": fiscal_month
                if period.relative_kind in _FISCAL_KINDS and fiscal_month
                else _fiscal_month_from_label(label),
                "fiscal_year_start_source": fiscal[1]
                if fiscal and period.relative_kind in _FISCAL_KINDS
                else "",
            }
        )
        value.range_ja = f"{start.isoformat()}〜{end.isoformat()}（開始日と終了日を含む）"
        value.sql_condition = period_sql_condition(value)
        candidates.append(
            RequirementCandidate(
                id=stable_ontology_id(
                    "requirement_candidate",
                    "period",
                    period.relative_kind,
                    label,
                    start.isoformat(),
                ),
                label_ja=f"{start.isoformat()}〜{end.isoformat()}",
                description_ja=f"{label}。開始日と終了日を含みます。",
                evidence_ja=evidence,
                source=source,
                columns=[period.date_column] if period.date_column else [],
                sql_hint=value.sql_condition,
                period=value,
            )
        )
    latest = period.model_copy(
        update={
            "reference_basis": RequirementReferenceBasis.LATEST_DATA_DATE,
            "reference_date": "",
            "start_date": "",
            "end_date": "",
        }
    )
    column_text = context.column_display(period.date_column) if period.date_column else "日付の列"
    latest.range_ja = f"データの最新日（SQL の中で MAX({column_text}) を求める）を基準に数える"
    latest.sql_condition = ""
    candidates.append(
        RequirementCandidate(
            id=stable_ontology_id(
                "requirement_candidate", "period", period.relative_kind, "latest"
            ),
            label_ja="データの最新日を基準にする",
            description_ja=(
                f"「{period.expression_ja}」を、実行日ではなくデータの最新日から数えます。"
                "表のデータは確認の段階では読まないため、日付は SQL の中で求めます。"
            ),
            evidence_ja="基準日の選択（データの最新日）",
            source=RequirementSource.CALENDAR,
            columns=[period.date_column] if period.date_column else [],
            period=latest,
        )
    )
    return candidates


def _fiscal_month_from_label(label: str) -> int | None:
    match = re.search(r"(\d{1,2}) 月始まり", label)
    return int(match.group(1)) if match else None


def period_sql_condition(period: RequirementPeriod) -> str:
    if not (period.start_date and period.end_date and period.date_column):
        return ""
    column = ".".join(period.date_column.split(".")[-2:])
    end_exclusive = date.fromisoformat(period.end_date) + timedelta(days=1)
    return (
        f"{column} >= DATE '{period.start_date}' AND {column} < DATE '{end_exclusive.isoformat()}'"
    )


_AGGREGATE = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX|LISTAGG|MEDIAN|STDDEV|VARIANCE)\s*\(", re.I)


def _is_aggregate_query(items: Sequence[RequirementItem]) -> bool:
    if any(
        item.section == RequirementSection.GROUP_BY and (item.status in _CLEAR or item.answered)
        for item in items
    ):
        return True
    return any(
        item.section == RequirementSection.SELECT and bool(_AGGREGATE.search(item.sql_hint or ""))
        for item in items
    )


def _has_clear(items: Sequence[RequirementItem], *sections: RequirementSection) -> bool:
    return any(
        item.section in sections
        and (item.status in _CLEAR or item.answered)
        and not item.key.startswith("risk_")
        for item in items
    )


def _has_unresolved(items: Sequence[RequirementItem], *sections: RequirementSection) -> bool:
    return any(
        item.section in sections and item.status in _UNRESOLVED and not item.key.startswith("risk_")
        for item in items
    )


def _risk_item(
    items: list[RequirementItem],
    *,
    key: str,
    section: RequirementSection,
    label_ja: str,
    reason_ja: str,
    question_ja: str,
    candidates: list[RequirementCandidate],
) -> None:
    existing = next((item for item in items if item.key == key), None)
    if existing is not None and existing.answered:
        return
    if existing is None:
        existing = RequirementItem(
            id=stable_ontology_id("requirement_item", key),
            key=key,
            section=section,
            label_ja=label_ja,
            status=RequirementStatus.AMBIGUOUS,
        )
        items.append(existing)
    existing.status = RequirementStatus.AMBIGUOUS
    existing.reason_ja = reason_ja
    existing.question_ja = question_ja
    existing.candidates = candidates
    existing.answer_kind = (
        RequirementAnswerKind.SINGLE_SELECT if candidates else RequirementAnswerKind.FREE_TEXT
    )
    existing.source = RequirementSource.SCHEMA


def _drop_risk(items: list[RequirementItem], key: str) -> None:
    for item in list(items):
        if item.key == key and not item.answered:
            items.remove(item)


def _enforce_risks(items: list[RequirementItem], context: RequirementContext) -> None:
    """危険・重い問い合わせを Schema（PK・FK・結合の条件）と要件だけで見つけ、確認の質問にする。

    表の件数などのデータは読まない。外部キーで別の表を参照する表・日付の列を持つ表は、明細・取引の
    表（件数が増え続ける表）として扱う。
    """

    tables = from_tables(items)
    if not tables:
        return
    # 1. 絞り込みの条件が無い大きな表の全件の集計・明細。
    large = [
        key
        for key in tables
        if key in context.tables
        and (
            context.tables[key].references_other_tables
            or any(column.is_date for column in context.tables[key].columns.values())
        )
    ]
    if (
        large
        and not _has_clear(items, RequirementSection.WHERE, RequirementSection.PERIOD)
        and not _has_unresolved(items, RequirementSection.WHERE, RequirementSection.PERIOD)
    ):
        names = "、".join(context.table_display(key) for key in large)
        kind = "集計" if _is_aggregate_query(items) else "明細"
        _risk_item(
            items,
            key="risk_no_filter",
            section=RequirementSection.WHERE,
            label_ja="絞り込みの条件",
            reason_ja=(
                f"{names} は明細・取引の表（外部キー・日付の列を持つ表）で、"
                "絞り込みの条件がありません。"
                f"全件の{kind}は重い問い合わせになるおそれがあります"
                "（表の件数は読まずに Schema だけで判断しています）。"
            ),
            question_ja="絞り込みの条件（期間・状態など）を付けますか？付けるなら、条件を入力してください。",
            candidates=[
                RequirementCandidate(
                    id=stable_ontology_id("requirement_candidate", "risk_no_filter", "all"),
                    label_ja="絞り込まずに全件を対象にする",
                    description_ja="重い問い合わせになることを承知で、全件を対象にします。",
                    evidence_ja="要件に絞り込みの条件が無いこと",
                    source=RequirementSource.SCHEMA,
                )
            ],
        )
    else:
        _drop_risk(items, "risk_no_filter")
    # 2. デカルト積になりうる結合（結合の条件が無い表）。
    if len(tables) >= 2 and not _has_unresolved(items, RequirementSection.JOIN):
        chosen_ids = {
            relation_id
            for item in items
            if item.section == RequirementSection.JOIN and (item.status in _CLEAR or item.answered)
            for relation_id in item.relationship_ids
        }
        chosen = [relation for relation in context.relationships if relation.id in chosen_ids]
        if not _connected(tables, chosen):
            available = [
                relation
                for relation in context.relationships
                if relation.left_table in tables and relation.right_table in tables
            ]
            _risk_item(
                items,
                key="risk_cartesian_join",
                section=RequirementSection.JOIN,
                label_ja="結合の条件の無い表",
                reason_ja=(
                    "結合の条件が無い表があり、すべての行の組み合わせ（デカルト積）になるおそれがあります。"
                ),
                question_ja="表をどの条件で結合しますか？",
                candidates=[
                    RequirementCandidate(
                        id=stable_ontology_id(
                            "requirement_candidate", "risk_cartesian_join", relation.id
                        ),
                        label_ja=relation.condition,
                        description_ja=relation.name_ja,
                        evidence_ja="承認済みの関係・外部キー",
                        source=RequirementSource.SCHEMA,
                        relationship_ids=[relation.id],
                        sql_hint=relation.condition,
                    )
                    for relation in available[:_MAX_CANDIDATES]
                ],
            )
        else:
            _drop_risk(items, "risk_cartesian_join")
    else:
        _drop_risk(items, "risk_cartesian_join")
    # 3. 件数の上限が無い明細の出力。
    if (
        not _is_aggregate_query(items)
        and not _has_clear(items, RequirementSection.LIMIT)
        and not _has_unresolved(items, RequirementSection.LIMIT)
    ):
        default_limit = int(context.profile_context.get("default_row_limit") or 0)
        candidates: list[RequirementCandidate] = []
        if default_limit > 0:
            candidates.append(
                RequirementCandidate(
                    id=stable_ontology_id(
                        "requirement_candidate", "risk_no_limit", str(default_limit)
                    ),
                    label_ja=f"{default_limit} 件まで",
                    description_ja="業務プロファイルの既定の行数の上限です。",
                    evidence_ja="業務プロファイルの既定の行数の上限",
                    source=RequirementSource.PROFILE,
                    sql_hint=f"FETCH FIRST {default_limit} ROWS ONLY",
                )
            )
        candidates.append(
            RequirementCandidate(
                id=stable_ontology_id("requirement_candidate", "risk_no_limit", "none"),
                label_ja="上限を付けない（全件）",
                description_ja="明細のすべての行を出します。",
                evidence_ja="要件に件数の上限が無いこと",
                source=RequirementSource.SCHEMA,
            )
        )
        _risk_item(
            items,
            key="risk_no_limit",
            section=RequirementSection.LIMIT,
            label_ja="明細の件数の上限",
            reason_ja="集計ではない明細の出力に、件数の上限がありません。",
            question_ja="明細を何件まで出しますか？",
            candidates=candidates,
        )
    else:
        _drop_risk(items, "risk_no_limit")


UNDECIDED_MARK = "⟨未確定: {}⟩"


def build_sql_skeleton(draft: SqlRequirementDraft, context: RequirementContext) -> str:
    """確定した要件から SQL の骨組みを決定的に組み立てる（LLM を使わない。実行しない）。

    要件をそのまま写すだけで、SQL の生成そのものではない。値が未確定の所は ⟨未確定: …⟩ にする。
    """

    items = draft.items

    def clear(item: RequirementItem) -> bool:
        return item.status in _CLEAR or item.answered

    def fragments(*sections: RequirementSection) -> list[str]:
        result: list[str] = []
        for item in items:
            if item.section not in sections or item.key == "period_date_column":
                continue
            if item.key.startswith("risk_") and clear(item) and "全件" in item.value_ja:
                continue
            if clear(item) and item.sql_hint:
                result.append(item.sql_hint)
            elif (
                clear(item)
                and item.period
                and item.period.reference_basis == (RequirementReferenceBasis.LATEST_DATA_DATE)
            ):
                result.append(
                    UNDECIDED_MARK.format(f"{item.label_ja}: データの最新日を基準に SQL で求める")
                )
            elif clear(item) and item.value_ja:
                result.append(UNDECIDED_MARK.format(f"{item.label_ja}: {item.value_ja}"))
            elif not clear(item):
                result.append(UNDECIDED_MARK.format(item.label_ja))
        return result

    lines: list[str] = []
    for item in items:
        if item.section != RequirementSection.SUBQUERY:
            continue
        if clear(item) and item.sql_hint.upper().startswith("WITH"):
            lines.append(item.sql_hint)
        elif clear(item):
            lines.append(f"-- WITH・サブクエリ: {item.value_ja or item.label_ja}")
        else:
            lines.append(f"-- WITH・サブクエリ: {UNDECIDED_MARK.format(item.label_ja)}")
    select = fragments(RequirementSection.SELECT)
    lines.append(
        "SELECT " + (",\n       ".join(select) if select else UNDECIDED_MARK.format("表示する項目"))
    )
    tables = from_tables(items)
    if not tables:
        lines.append("FROM " + UNDECIDED_MARK.format("対象の表"))
    else:
        chosen_ids = {
            relation_id
            for item in items
            if item.section == RequirementSection.JOIN and clear(item)
            for relation_id in item.relationship_ids
        }
        relations = [relation for relation in context.relationships if relation.id in chosen_ids]
        join_text = " ".join(
            f"{item.value_ja} {item.sql_hint}"
            for item in items
            if item.section == RequirementSection.JOIN and clear(item)
        ).upper()
        join_keyword = "LEFT JOIN" if "LEFT" in join_text else "JOIN"
        lines.append(f"FROM {tables[0]}")
        placed = {tables[0]}
        pending = list(tables[1:])
        progress = True
        while pending and progress:
            progress = False
            for table in list(pending):
                conditions = [
                    relation.condition
                    for relation in relations
                    if (relation.left_table == table and relation.right_table in placed)
                    or (relation.right_table == table and relation.left_table in placed)
                ]
                if conditions:
                    lines.append(f"  {join_keyword} {table} ON {' AND '.join(conditions)}")
                    placed.add(table)
                    pending.remove(table)
                    progress = True
        for table in pending:
            lines.append(f"  {join_keyword} {table} ON {UNDECIDED_MARK.format('結合の条件')}")
    where = fragments(RequirementSection.WHERE, RequirementSection.PERIOD)
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    group_by = fragments(RequirementSection.GROUP_BY)
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    order_by = fragments(RequirementSection.ORDER_BY)
    if order_by:
        lines.append("ORDER BY " + ", ".join(order_by))
    for item in items:
        if item.section != RequirementSection.LIMIT:
            continue
        if not clear(item):
            lines.append("FETCH FIRST " + UNDECIDED_MARK.format("件数") + " ROWS ONLY")
            break
        text = f"{item.sql_hint} {item.value_ja}"
        match = re.search(r"(\d+)", text)
        if match and "上限を付けない" not in text:
            lines.append(f"FETCH FIRST {int(match.group(1))} ROWS ONLY")
            break
    return "\n".join(lines)


def _ensure_physical_names(draft: SqlRequirementDraft, context: RequirementContext) -> str:
    """書き直した質問の文に、使う表の物理名が無ければ添える（業務名だけで書かない）。"""

    text = draft.clarified_question_ja.strip() or draft.question_original
    lowered = text.casefold()
    missing_tables = [
        context.tables[key].name
        for key in from_tables(draft.items)
        if key in context.tables and context.tables[key].name.casefold() not in lowered
    ]
    columns = [
        column
        for item in draft.items
        if item.status in _CLEAR or item.answered
        for column in item.columns
    ]
    missing_columns = [
        context.column_display(column).split("（", 1)[0]
        for column in _unique(columns)
        if column.rsplit(".", 1)[-1].casefold().strip('"') not in lowered
    ]
    notes: list[str] = []
    # 相対的な期間は、確かめた具体的な日付の範囲を質問の文にも書く（「今年度」のままにしない）。
    for item in draft.items:
        period = item.period
        if (
            item.section == RequirementSection.PERIOD
            and (item.answered or item.status in _CLEAR)
            and period is not None
            and period.start_date
            and period.end_date
            and period.start_date not in text
        ):
            column = period.date_column.split(".", 1)[-1] if period.date_column else ""
            target = f"{column} が " if column else ""
            notes.append(
                f"期間: {target}{period.start_date}〜{period.end_date}（開始日と終了日を含む）"
            )
    if missing_tables:
        notes.append(f"使う表: {'、'.join(missing_tables)}")
    if missing_columns:
        notes.append(f"使う列: {'、'.join(missing_columns)}")
    if notes:
        text = f"{text.rstrip()}（{'。'.join(notes)}）"
    return text


# ---------------------------------------------------------------------------
# 回答の反映
# ---------------------------------------------------------------------------


def apply_answer(
    draft: SqlRequirementDraft,
    *,
    item_id: str,
    selected_candidate_ids: Sequence[str],
    free_text: str,
    context: RequirementContext,
) -> SqlRequirementDraft:
    """1 つの項目への回答（選択・複数選択・自由入力）を反映する。ほかの項目は変えない。"""

    updated = draft.model_copy(deep=True)
    item = next((value for value in updated.items if value.id == item_id), None)
    if item is None:
        raise RequirementAnswerInvalidError(
            "確認の項目が見つかりません。最新の状態を読み込み直してください。"
        )
    text = free_text.strip()
    if len(selected_candidate_ids) != len(set(selected_candidate_ids)):
        raise RequirementAnswerInvalidError("同じ選択肢を重複して指定できません。")
    by_id = {candidate.id: candidate for candidate in item.candidates}
    unknown = set(selected_candidate_ids) - by_id.keys()
    if unknown:
        raise RequirementAnswerInvalidError("表示されていない選択肢は回答に使えません。")
    selected = [by_id[value] for value in selected_candidate_ids]
    if selected and text:
        raise RequirementAnswerInvalidError("選択肢か自由入力のどちらか 1 つで答えてください。")
    if not selected and not text:
        raise RequirementAnswerInvalidError("選択肢を選ぶか、自由入力を記入してください。")
    if item.answer_kind != RequirementAnswerKind.MULTI_SELECT and len(selected) > 1:
        raise RequirementAnswerInvalidError("この質問では選択肢を 1 つだけ選んでください。")

    if selected:
        item.value_ja = "、".join(
            f"{candidate.label_ja}"
            + (
                f"（{candidate.period.range_ja}）"
                if candidate.period
                and candidate.period.range_ja
                and candidate.label_ja not in candidate.period.range_ja
                else ""
            )
            for candidate in selected
        )
        item.tables = _unique(table for candidate in selected for table in candidate.tables)
        item.columns = _unique(column for candidate in selected for column in candidate.columns)
        item.relationship_ids = _unique(
            relation for candidate in selected for relation in candidate.relationship_ids
        )
        item.sql_hint = " / ".join(
            candidate.sql_hint for candidate in selected if candidate.sql_hint
        )
        period = next((candidate.period for candidate in selected if candidate.period), None)
        if period is not None:
            item.period = period.model_copy(deep=True)
        item.answer_ja = "、".join(candidate.label_ja for candidate in selected)
        if item.section == RequirementSection.JOIN and item.tables:
            _merge_join_tables(updated.items, item.tables)
    else:
        item.value_ja = text
        item.answer_ja = text
        tables, columns = detect_physical_refs(text, context)
        if item.section == RequirementSection.FROM:
            item.tables = tables
        elif tables or columns:
            item.tables = tables
            item.columns = columns
        item.sql_hint = ""
        item.relationship_ids = []
        if item.period is not None:
            item.period = item.period.model_copy(
                update={
                    "start_date": "",
                    "end_date": "",
                    "sql_condition": "",
                    "range_ja": text,
                    "reference_basis": RequirementReferenceBasis.EXECUTION_DATE,
                    "reference_date": context.today.isoformat(),
                }
            )
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
            if len(dates) >= 2:
                item.period.start_date, item.period.end_date = dates[0], dates[1]
            if columns and context.column(columns[0]) and context.column(columns[0]).is_date:  # type: ignore[union-attr]
                item.period.date_column = columns[0]
    item.status = RequirementStatus.CONFIRMED
    item.source = RequirementSource.USER
    item.answered = True
    item.question_ja = ""
    item.reason_ja = ""
    item.candidates = []
    if item.section == RequirementSection.FROM:
        updated.unanswerable_reason_ja = ""
    updated.version = draft.version + 1
    return enforce_requirement_rules(updated, context)


def _merge_join_tables(items: list[RequirementItem], tables: Sequence[str]) -> None:
    target = next(
        (
            item
            for item in items
            if item.section == RequirementSection.FROM and item.status in _CLEAR
        ),
        None,
    )
    if target is None:
        return
    target.tables = _unique([*target.tables, *tables])


def detect_physical_refs(text: str, context: RequirementContext) -> tuple[list[str], list[str]]:
    """自由入力の中の物理名（表・`TABLE.COLUMN`）を Schema から見つける。"""

    tokens = re.findall(
        r'"[^"]+"(?:\."[^"]+")*|[A-Za-z][A-Za-z0-9_$#]*(?:\.[A-Za-z][A-Za-z0-9_$#]*)*', text
    )
    tables: list[str] = []
    columns: list[str] = []
    for token in tokens:
        table = context.resolve_table(token)
        if table:
            tables.append(table)
            continue
        column = context.resolve_column(token)
        if column:
            columns.append(column)
    return _unique(tables), _unique(columns)


# ---------------------------------------------------------------------------
# 状態・まとめ・SQL の生成に渡す要件
# ---------------------------------------------------------------------------


def unresolved_items(draft: SqlRequirementDraft) -> list[RequirementItem]:
    return [item for item in draft.items if item.status in _UNRESOLVED and not item.answered]


def minimum_missing(draft: SqlRequirementDraft) -> list[str]:
    """最低の基準（SELECT と FROM が明確）を満たさない理由。"""

    missing: list[str] = []
    if not from_tables(draft.items):
        missing.append("どの表から（FROM）が明確ではありません。")
    if not any(
        item.section == RequirementSection.SELECT and item.status in _CLEAR for item in draft.items
    ):
        missing.append("何を表示するか（SELECT）が明確ではありません。")
    return missing


def build_requirement_state(session: QuerySession, *, max_review_rounds: int) -> RequirementState:
    draft = session.requirement
    if draft is None:
        return RequirementState(
            status=RequirementPhase.UNANSWERABLE,
            max_review_rounds=max_review_rounds,
            message_ja="AI要件確認の下書きがありません。",
        )
    unresolved = unresolved_items(draft)
    missing_minimum = minimum_missing(draft)
    limit_reached = session.requirement_review_round >= max_review_rounds
    if draft.unanswerable_reason_ja:
        status = RequirementPhase.UNANSWERABLE
        message = draft.unanswerable_reason_ja
    elif unresolved:
        status = RequirementPhase.NEEDS_ANSWER
        message = "SQL を正しく作るため、足りない情報・明確でない情報を 1 つずつ確認します。"
        if limit_reached:
            message = (
                f"AI の再検査は上限（{max_review_rounds} 回）に達しました。"
                f"まだ確認の要る項目が {len(unresolved)} 件あります。答えるか、"
                "このまま SQL の生成に進むかを選んでください（残った項目は AI が判断します）。"
            )
    else:
        status = RequirementPhase.READY
        message = "SQL の要件を確認できました。まとめを確かめてから反映してください。"
        if limit_reached and session.requirement_answers:
            message = (
                "SQL の要件を確認できました。AI の再検査は上限"
                f"（{max_review_rounds} 回）に達したため、最後の回答の後は再検査していません。"
            )
    if missing_minimum and status != RequirementPhase.UNANSWERABLE:
        message = (
            f"{message} {' '.join(missing_minimum)}"
            "SELECT と FROM が明確になるまで SQL の生成に進めません。"
        )
    can_apply = (
        status != RequirementPhase.UNANSWERABLE
        and not missing_minimum
        and (not unresolved or limit_reached)
    )
    return RequirementState(
        status=status,
        current_item=unresolved[0] if unresolved else None,
        unresolved_items=unresolved,
        answered_count=sum(1 for item in draft.items if item.answered),
        review_round=session.requirement_review_round,
        max_review_rounds=max_review_rounds,
        review_limit_reached=limit_reached,
        select_from_clear=not missing_minimum,
        minimum_missing_ja=missing_minimum,
        can_apply=can_apply,
        summary_ja=requirement_summary(draft),
        sql_skeleton=draft.sql_skeleton,
        message_ja=message,
    )


def requirement_summary(draft: SqlRequirementDraft) -> str:
    """要件のまとめ（物理名で書く）。画面の確認済みの一覧と SQL の生成に渡す内容の元。"""

    lines: list[str] = []
    for section in sorted(RequirementSection, key=lambda value: _SECTION_ORDER[value]):
        section_items = [item for item in draft.items if item.section == section]
        for item in section_items:
            status = _STATUS_LABELS[item.status] if not item.answered else "確定（回答）"
            value = item.value_ja or "（未確認）"
            detail: list[str] = []
            if item.refs_ja and item.refs_ja not in value:
                detail.append(item.refs_ja)
            if item.period and item.period.range_ja and item.period.range_ja not in value:
                detail.append(item.period.range_ja)
            if item.sql_hint:
                detail.append(f"SQL: {item.sql_hint}")
            suffix = f"（{' / '.join(detail)}）" if detail else ""
            lines.append(
                f"- {SECTION_LABELS_JA[section]} {item.label_ja}: {value}{suffix} [{status}]"
            )
    return "\n".join(lines)


_STATUS_LABELS = {
    RequirementStatus.CONFIRMED: "確定",
    RequirementStatus.INFERRED: "AI の推測",
    RequirementStatus.MISSING: "不足",
    RequirementStatus.AMBIGUOUS: "あいまい",
}


def generation_spec(
    session: QuerySession, context_relationships: Mapping[str, str] | None = None
) -> SqlRequirementSpec:
    """SQL の生成に渡す構造化した要件（反映した session から作る）。"""

    draft = session.requirement
    if draft is None:
        raise RequirementAnswerInvalidError("AI要件確認の要件がありません。")
    tables = from_tables(draft.items)
    joins = [
        item.sql_hint or item.value_ja
        for item in draft.items
        if item.section == RequirementSection.JOIN
        and (item.status in _CLEAR or item.answered)
        and (item.sql_hint or item.value_ja)
    ]
    items = [
        SqlRequirementSpecItem(
            section=item.section.value,
            section_label_ja=SECTION_LABELS_JA[item.section],
            label_ja=item.label_ja,
            value_ja=item.value_ja,
            status=item.status.value if not item.answered else "answered",
            tables=item.tables,
            columns=[".".join(column.split(".")[-2:]) for column in item.columns],
            sql_hint=item.sql_hint,
            period_range_ja=item.period.range_ja if item.period else "",
        )
        for item in draft.items
        if item.status in _CLEAR or item.answered
    ]
    unresolved = [
        f"{SECTION_LABELS_JA[item.section]} {item.label_ja}: {item.question_ja or item.reason_ja}"
        for item in unresolved_items(draft)
    ]
    return SqlRequirementSpec(
        session_id=session.id,
        profile_id=session.profile_id,
        clarified_question=draft.clarified_question_ja,
        tables=tables,
        joins=joins,
        items=items,
        unresolved_ja=unresolved,
        summary_ja=requirement_summary(draft),
    )


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


RequirementLlmCallback = Callable[..., tuple[LlmRequirementOutput, str]]


__all__ = [
    "REQUIREMENT_PROMPT_VERSION",
    "REQUIREMENT_SCHEMA_VERSION",
    "RequirementAiUnavailableError",
    "RequirementAnswerInvalidError",
    "RequirementAnswerRecord",
    "RequirementContext",
    "apply_answer",
    "build_draft",
    "build_requirement_context",
    "build_requirement_state",
    "call_requirement_llm",
    "generation_spec",
    "requirement_summary",
    "unresolved_items",
]
