"""検索（RAG）関連スキーマ。"""

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from app.schemas.classification import normalize_category_value
from app.schemas.common import JsonValue

# PoweRAG 由来の scalar / 日付 / カテゴリ pre-filter。Oracle AI Database の JSON_VALUE 数値述語・
# TIMESTAMP 範囲・IN 述語へ再マップし、ベクトル/hybrid 検索の候補集合を事前に絞り込む。
SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS = {
    "page_number_min",
    "page_number_max",
}
SUPPORTED_SEARCH_DATE_RANGE_FILTERS = {
    "uploaded_from",
    "uploaded_to",
    "indexed_from",
    "indexed_to",
}
# 文書の分類(表記の正規化・番号の接頭辞を除いた一致。#547)と有効期間の基準日
# (rag_poc の ClassificationFilter)。
SUPPORTED_SEARCH_CLASSIFICATION_FILTERS = {
    "large_category",
    "middle_category",
    "small_category",
}
SUPPORTED_SEARCH_LIST_FILTERS = {
    "content_kinds",
}
SUPPORTED_SCALAR_SEARCH_FILTER_KEYS = (
    SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS
    | SUPPORTED_SEARCH_DATE_RANGE_FILTERS
    | SUPPORTED_SEARCH_LIST_FILTERS
)

# 項目抽出の値の条件(#549)。値は条件の JSON 配列の文字列(filters は dict[str, str] のため)。
EXTRACTION_FIELD_FILTER_KEY = "extraction_fields"
MAX_EXTRACTION_FIELD_CONDITIONS = 10

SUPPORTED_SEARCH_FILTER_KEYS = {
    EXTRACTION_FIELD_FILTER_KEY,
    "document_id",
    "knowledge_base_id",
    "chunk_set_id",
    "file_name",
    "category_name",
    "status",
    "content_kind",
    "section_title",
    "section_path",
    "source_acl",
    "document_version",
    "as_of",
    *SUPPORTED_SEARCH_CLASSIFICATION_FILTERS,
    *SUPPORTED_SCALAR_SEARCH_FILTER_KEYS,
}
SUPPORTED_SEARCH_STATUS_FILTERS = {
    "UPLOADED",
    "INGESTING",
    "REVIEW",
    "INDEXING",
    "INDEXED",
    "ERROR",
}
SUPPORTED_CONTENT_KIND_FILTERS = {
    "text",
    "list",
    "table",
    "figure",
    "equation",
    "code",
    "email",
    "slide",
    "sheet",
    # 取込機能が生む合成 chunk(項目抽出 / 章節ナビゲーション要約)
    "field",
    "section_summary",
}


# 型ごとに使える演算子(string / bool は一致だけ、number / date は一致と範囲)。
_EXTRACTION_FIELD_OPERATORS: dict[str, frozenset[str]] = {
    "string": frozenset({"eq"}),
    "bool": frozenset({"eq"}),
    "number": frozenset({"eq", "gte", "lte"}),
    "date": frozenset({"eq", "gte", "lte"}),
}


class ExtractionFieldCondition(BaseModel):
    """項目抽出の値の条件 1 件(#549)。`op` は eq(一致)・gte(以上)・lte(以下)。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    value_type: Literal["string", "number", "date", "bool"]
    op: Literal["eq", "gte", "lte"] = "eq"
    value: str = Field(min_length=1, max_length=500)

    @field_validator("name", "value")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("項目の条件の項目名と値は空にできません。")
        return cleaned

    @model_validator(mode="after")
    def _validate_value(self) -> Self:
        """型に合う演算子と値だけを受け付け、値を比較する形へそろえる。"""
        if self.op not in _EXTRACTION_FIELD_OPERATORS[self.value_type]:
            raise ValueError(f"項目の条件の演算子が型に合いません: {self.value_type} {self.op}")
        if self.value_type == "number":
            try:
                number = Decimal(self.value)
            except InvalidOperation as exc:
                raise ValueError(f"項目の条件の値は数値にしてください: {self.value}") from exc
            if not number.is_finite():
                raise ValueError(f"項目の条件の値は数値にしてください: {self.value}")
            self.value = format(number, "f")
        elif self.value_type == "date":
            try:
                self.value = date.fromisoformat(self.value).isoformat()
            except ValueError as exc:
                raise ValueError(
                    f"項目の条件の日付は YYYY-MM-DD で指定してください: {self.value}"
                ) from exc
        elif self.value_type == "bool":
            if self.value.casefold() not in {"true", "false"}:
                raise ValueError(f"項目の条件の値は true か false にしてください: {self.value}")
            self.value = self.value.casefold()
        return self


_EXTRACTION_FIELD_CONDITIONS = TypeAdapter(
    list[ExtractionFieldCondition],
)


def parse_extraction_field_filter(value: str) -> list[ExtractionFieldCondition]:
    """`filters.extraction_fields`(条件の JSON 配列)を検証して条件にする(#549)。"""
    try:
        conditions = _EXTRACTION_FIELD_CONDITIONS.validate_json(value)
    except ValidationError as exc:
        messages = [str(error.get("msg", "")) for error in exc.errors()]
        raise ValueError(
            "項目の条件の形式が不正です: " + "; ".join(message for message in messages if message)
        ) from exc
    if len(conditions) > MAX_EXTRACTION_FIELD_CONDITIONS:
        raise ValueError(f"項目の条件は {MAX_EXTRACTION_FIELD_CONDITIONS} 件までです。")
    return conditions


def _normalize_extraction_field_filter(value: str) -> str:
    """項目の条件を検証し、そろえた JSON 文字列にする(空の配列は条件なし)。"""
    conditions = parse_extraction_field_filter(value)
    if not conditions:
        return ""
    return json.dumps(
        [condition.model_dump() for condition in conditions],
        ensure_ascii=False,
        separators=(",", ":"),
    )


class SearchMode(StrEnum):
    """Oracle AI Database の検索の種類(``OracleClient.hybrid_search`` の ``mode``)。

    回答は DocRAG の回答フローが hybrid(RRF)と vector を内部で使い分ける。利用者が選ぶ
    検索モードは #595 で削除した。
    """

    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


class SearchRequest(BaseModel):
    """RAG 検索リクエスト。

    旧 standard の回答エンジンの指定(``mode``・``strategy``・``rerank_top_n``・
    ``generation_profile``)は #595 で削除した。旧クライアントが送っても 422 にせず、
    未定義の項目として読み捨てる(pydantic の既定 ``extra="ignore"``)。
    """

    model_config = ConfigDict(extra="ignore")

    query: str = Field(..., min_length=1)
    top_k: int = Field(default=20, ge=1, le=100)
    filters: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=200)
    business_view_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "業務ビュー(Business View)ID。business_view_ids が無い旧クライアント向け互換値。"
            "指定時は参照 KB 群を検索対象へ展開し、業務ビューの回答の設定を適用する。"
        ),
    )
    business_view_ids: list[str] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "検索対象にする業務ビュー(Business View)ID。複数指定時は参照 KB 群を union し、"
            "回答の設定は先頭の業務ビューを代表として適用する。"
        ),
    )
    retrieval_only: bool = Field(
        default=False,
        description=(
            "回答を作らずに検索だけを行う(LLM を呼ばない)。KB の検索テストとレシピの検索比較が"
            "使う(#593)。"
        ),
    )

    @field_validator("business_view_id")
    @classmethod
    def validate_business_view_id(cls, value: str | None) -> str | None:
        """空文字は未指定として扱う。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("business_view_ids")
    @classmethod
    def validate_business_view_ids(cls, values: list[str]) -> list[str]:
        """業務ビュー ID を重複排除する。"""
        return normalize_search_id_list(values)

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        """空白だけのクエリを拒否し、前後空白を落とす。"""
        return normalize_query_text(query)

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, filters: dict[str, str]) -> dict[str, str]:
        """対応済み filter key のみ許可し、値を正規化する。"""
        return normalize_search_filters(filters)

    @field_validator("knowledge_base_ids")
    @classmethod
    def validate_knowledge_base_ids(cls, values: list[str]) -> list[str]:
        """検索対象のナレッジベース ID を重複排除する。"""
        return normalize_search_id_list(values)

    @model_validator(mode="after")
    def validate_search_options(self) -> Self:
        """ナレッジベース指定の整合性を検証する。"""
        filter_knowledge_base_ids = parse_search_id_filter(self.filters.get("knowledge_base_id"))
        if (
            self.knowledge_base_ids
            and filter_knowledge_base_ids
            and self.knowledge_base_ids != filter_knowledge_base_ids
        ):
            raise ValueError(
                "knowledge_base_ids と filters.knowledge_base_id は同じ値を指定してください。"
            )
        resolved_knowledge_base_ids = self.knowledge_base_ids or filter_knowledge_base_ids
        if resolved_knowledge_base_ids:
            self.knowledge_base_ids = resolved_knowledge_base_ids
            self.filters = {
                **self.filters,
                "knowledge_base_id": format_search_id_filter(resolved_knowledge_base_ids),
            }
        if self.business_view_id:
            self.business_view_ids = normalize_search_id_list(
                [self.business_view_id, *self.business_view_ids]
            )
        elif self.business_view_ids:
            self.business_view_id = self.business_view_ids[0]
        return self


class RetrievedChunk(BaseModel):
    """検索でヒットしたチャンク。"""

    document_id: str
    chunk_id: str
    text: str
    score: float
    rerank_score: float | None = None
    file_name: str | None = None
    category_name: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SearchDiagnostics(BaseModel):
    """検索・回答の再現と調査に使う非機密の診断。

    旧 standard の回答エンジンの診断(検索の内訳・候補・context の件数・回答スタイルなど)は
    #595 で削除した。回答の中身の診断は ``docrag`` にある。
    """

    # 回答の経路は常に DocRAG。保存済みの評価結果などの古い値も読めるよう str のままにする。
    retrieval_strategy: str = "docrag"
    # docrag_grounded(根拠付き回答)/ docrag_retrieval_only(検索だけ)/ blocked(質問の安全チェック)
    retrieval_strategy_adapter: str = "docrag_grounded"
    docrag: dict[str, JsonValue] | None = None
    guardrail_policy: str = "standard"
    guardrail_backend: str = "local"
    guardrail_degraded: bool = False
    filter_keys: list[str] = Field(default_factory=list)
    knowledge_base_count: int = 0
    kb_adapter_config_applied: str | None = None
    business_view_applied: str | None = None
    config_fingerprint: str = ""


class SearchResponse(BaseModel):
    """RAG 検索レスポンス。"""

    answer: str
    citations: list[RetrievedChunk] = Field(default_factory=list)
    trace_id: str
    guardrail_warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float
    diagnostics: SearchDiagnostics = Field(default_factory=SearchDiagnostics)
    # 回答側ガードレールが本文をマスク/差し替えしたか。realtime stream 時に
    # マスク済み本文を再送(置換)するかの判定に使う内部フラグ。
    answer_replaced: bool = False
    # 標準回答による評価の入力(回答の記録に保存するものと同じ。#591)。応答には出さず、
    # 品質評価が同じプロセスの中で回答の根拠・引用・実行記録を受け取るためだけに使う。
    _evaluation_input: dict[str, object] | None = PrivateAttr(default=None)

    @property
    def evaluation_input(self) -> dict[str, object] | None:
        """標準回答による評価の入力(回答エンジンが作らなかったときは None)。"""
        return self._evaluation_input

    def with_evaluation_input(self, value: Mapping[str, object] | None) -> Self:
        """標準回答による評価の入力を持たせて返す(同じ object を変更する)。"""
        self._evaluation_input = dict(value) if value is not None else None
        return self

    @field_validator("guardrail_warnings")
    @classmethod
    def _dedup_warnings(cls, value: list[str]) -> list[str]:
        """クエリ側/回答側で同一文言が重複するため順序保持で重複除去する。"""
        return list(dict.fromkeys(value))


def normalize_search_filters(filters: dict[str, str]) -> dict[str, str]:
    """検索 filter key/value を検証・正規化する。"""
    unsupported = sorted(set(filters) - SUPPORTED_SEARCH_FILTER_KEYS)
    if unsupported:
        raise ValueError(f"未対応の検索フィルターです: {', '.join(unsupported)}")

    normalized: dict[str, str] = {}
    for key, value in filters.items():
        cleaned = value.strip()
        if not cleaned:
            continue
        if key == "status":
            normalized[key] = cleaned.upper()
        elif key == "content_kind":
            normalized[key] = cleaned.casefold()
        elif key == "knowledge_base_id":
            formatted_ids = format_search_id_filter(parse_search_id_filter(cleaned))
            if formatted_ids:
                normalized[key] = formatted_ids
        elif key in SUPPORTED_SEARCH_NUMERIC_RANGE_FILTERS:
            normalized[key] = _normalize_filter_integer(key, cleaned)
        elif key in SUPPORTED_SEARCH_DATE_RANGE_FILTERS:
            normalized[key] = _normalize_filter_date(key, cleaned)
        elif key == "as_of":
            normalized[key] = _normalize_as_of(cleaned)
        elif key in SUPPORTED_SEARCH_LIST_FILTERS:
            if formatted_kinds := _normalize_content_kind_list(cleaned):
                normalized[key] = formatted_kinds
        elif key == EXTRACTION_FIELD_FILTER_KEY:
            if formatted_conditions := _normalize_extraction_field_filter(cleaned):
                normalized[key] = formatted_conditions
        elif key in SUPPORTED_SEARCH_CLASSIFICATION_FILTERS:
            # 保存時と同じ表記の正規化(NFKC・空白)。番号の接頭辞は残し、比較の側で外す(#547)。
            if category := normalize_category_value(cleaned):
                normalized[key] = category
        else:
            normalized[key] = cleaned

    if (status := normalized.get("status")) and status not in SUPPORTED_SEARCH_STATUS_FILTERS:
        raise ValueError(f"未対応のファイル状態フィルターです: {status}")
    if (content_kind := normalized.get("content_kind")) and (
        content_kind not in SUPPORTED_CONTENT_KIND_FILTERS
    ):
        raise ValueError(f"未対応の内容種別フィルターです: {content_kind}")
    _validate_filter_range_consistency(normalized)
    return normalized


def _normalize_as_of(value: str) -> str:
    """有効期間の基準日を YYYY-MM-DD として検証する。"""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"基準日は YYYY-MM-DD で指定してください: {value}") from exc


def _normalize_filter_integer(key: str, value: str) -> str:
    """数値 range filter を整数として検証し、正規化した文字列を返す。"""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"数値フィルターの形式が不正です: {key}={value}") from exc
    if parsed < 0:
        raise ValueError(f"数値フィルターは 0 以上にしてください: {key}={value}")
    return str(parsed)


def _normalize_filter_date(key: str, value: str) -> str:
    """日付 range filter を ISO 8601 として検証し、入力表現を保持して返す。

    Oracle 側で date-only か datetime かを判別して TIMESTAMP へ束ねるため、ここでは
    解析可能性のみ検証し、trim した入力をそのまま保持する。
    """
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"日付フィルターの形式が不正です: {key}={value}") from exc
    return value


def _normalize_content_kind_list(value: str) -> str:
    """content_kinds の list membership filter を正規化する。"""
    seen: set[str] = set()
    kinds: list[str] = []
    for part in value.split(","):
        cleaned = part.strip().casefold()
        if not cleaned or cleaned in seen:
            continue
        if cleaned not in SUPPORTED_CONTENT_KIND_FILTERS:
            raise ValueError(f"未対応の内容種別フィルターです: {cleaned}")
        seen.add(cleaned)
        kinds.append(cleaned)
    return ",".join(kinds)


def _validate_filter_range_consistency(filters: dict[str, str]) -> None:
    """min/max・from/to の範囲が逆転していないか検証する。"""
    low = filters.get("page_number_min")
    high = filters.get("page_number_max")
    if low and high and int(low) > int(high):
        raise ValueError("page_number_min は page_number_max 以下にしてください。")
    for from_key, to_key in (
        ("uploaded_from", "uploaded_to"),
        ("indexed_from", "indexed_to"),
    ):
        start = filters.get(from_key)
        end = filters.get(to_key)
        if start and end and _filter_date_sort_key(start) > _filter_date_sort_key(end):
            raise ValueError(f"{from_key} は {to_key} 以前にしてください。")


def _filter_date_sort_key(value: str) -> datetime:
    """検証済み日付文字列を比較用の tz-aware datetime へ変換する。"""
    candidate = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def normalize_search_id_list(values: Sequence[str]) -> list[str]:
    """ID リストの前後空白と重複を取り除く。"""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def parse_search_id_filter(value: str | None) -> list[str]:
    """カンマ区切り filter 値を ID リストへ戻す。"""
    if value is None:
        return []
    return normalize_search_id_list(value.split(","))


def format_search_id_filter(values: Sequence[str]) -> str:
    """ID リストを既存 filters 経路へ渡すための表現へ変換する。"""
    return ",".join(normalize_search_id_list(values))


def normalize_query_text(query: str) -> str:
    """検索・評価に使う自然言語クエリを正規化する。"""
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("クエリを入力してください。")
    return cleaned


class AnswerRecordSummary(BaseModel):
    """保存済み DocRAG 回答の一覧行(本文・根拠は含めない)。"""

    trace_id: str
    business_view_id: str | None = None
    surface: Literal["search", "chat"]
    answer_engine: str
    question: str
    rewritten_question: str | None = None
    confidence: str | None = None
    created_at: datetime


class AnswerRecordDetail(AnswerRecordSummary):
    """保存済み DocRAG 回答(本文・引用・根拠と実行記録)。"""

    answer: str
    citations: list[RetrievedChunk] = Field(default_factory=list)
    docrag: dict[str, JsonValue] = Field(default_factory=dict)
    # 標準回答で評価できるか(この機能より前の回答は評価の入力を持たない)。
    evaluation_available: bool = False
    evaluation: dict[str, JsonValue] | None = None


class AnswerEvaluationRequest(BaseModel):
    """保存済み DocRAG 回答を評価する標準回答。"""

    standard_answer: str = Field(min_length=1, max_length=20000)

    @field_validator("standard_answer")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("標準回答を入力してください。")
        return value


class AnswerRecordDeleteResult(BaseModel):
    """DocRAG 回答の削除結果。"""

    trace_id: str
