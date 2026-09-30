"""schema 駆動の構造化 field/entity 抽出(PoweRAG の LangExtract 抽出 由来)。

PoweRAG は LangExtract で entity/field/関係を抽出する。本モジュールは外部 LangExtract を
導入せず、確定スタック内で再実装する:
- **field schema store**: 抽出対象 field(name/description/value_type)を JSON 永続で定義
  (`extraction-fields.json`、env `RAG_FIELD_SCHEMA_FILE` で上書き。config.py/.env 非依存)。
  これは全体の既定で、ナレッジベースごとの定義(`rag_knowledge_bases.extraction_fields`)が
  あればそちらを使う(#548。`resolve_field_definitions`)。
- **抽出**: OCI Enterprise AI の structured output を注入された抽出器で呼び、`ExtractionField`
  へ Pydantic 検証して保存。検索可能な合成 element も付けて既存 chunking 経路へ流す。

抽出器は注入可能でテストは決定論。外部 LLM provider は導入しない。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Awaitable, Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import BACKEND_ROOT
from app.schemas.extraction import DocumentElement, ExtractionField, StructuredExtraction

FIELD_SCHEMA_FILE_ENV = "RAG_FIELD_SCHEMA_FILE"
DEFAULT_FIELD_SCHEMA_FILE = "extraction-fields.json"
MAX_FIELD_DEFINITIONS = 50

FieldValueType = Literal["string", "number", "date", "bool"]

# 抽出器: 文書 text と field 定義から ExtractionField のリストを返す。
FieldExtractor = Callable[[str, "list[FieldDefinition]"], Awaitable[list[ExtractionField]]]


class FieldDefinition(BaseModel):
    """抽出したい 1 つの field の宣言。"""

    name: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)
    value_type: FieldValueType = "string"

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("field name は空にできません。")
        return cleaned


class FieldSchemaStore(BaseModel):
    """field schema 定義ファイルの schema。"""

    version: Literal[1] = 1
    fields: list[FieldDefinition] = Field(default_factory=list, max_length=MAX_FIELD_DEFINITIONS)


def _field_schema_path() -> Path:
    raw = os.environ.get(FIELD_SCHEMA_FILE_ENV, "").strip() or DEFAULT_FIELD_SCHEMA_FILE
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def load_field_schema() -> FieldSchemaStore:
    """field schema 定義を読む。無ければ空、壊れていても安全に空 store。"""
    path = _field_schema_path()
    try:
        data = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return FieldSchemaStore()
    try:
        return FieldSchemaStore.model_validate_json(data)
    except ValueError:
        return FieldSchemaStore()


def validate_field_schema(fields: list[FieldDefinition]) -> FieldSchemaStore:
    """項目の定義を検証する(name の重複は大文字小文字を区別せず不可、上限は 50 項目)。"""
    names = [field.name.casefold() for field in fields]
    if len(names) != len(set(names)):
        raise ValueError("field name は重複できません。")
    if len(fields) > MAX_FIELD_DEFINITIONS:
        raise ValueError(f"項目は {MAX_FIELD_DEFINITIONS} 件までです。")
    return FieldSchemaStore(fields=fields)


def save_field_schema(fields: list[FieldDefinition]) -> FieldSchemaStore:
    """field schema 定義(全体の既定)を保存する(name 重複は不可)。"""
    store = validate_field_schema(fields)
    path = _field_schema_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    return store


def field_schema_from_json(value: object) -> list[FieldDefinition] | None:
    """KB に保存した項目の定義(`FieldSchemaStore` の JSON)を読む(#548)。

    NULL は「KB の定義なし = 全体の既定に従う」で None。壊れた値も None にして既定に従う
    (取込を止めない)。空の list は「この KB では項目を抽出しない」で、既定には戻さない。
    """
    if value is None:
        return None
    try:
        if isinstance(value, str | bytes):
            return FieldSchemaStore.model_validate_json(value).fields
        return FieldSchemaStore.model_validate(value).fields
    except ValueError:
        return None


def resolve_field_definitions(
    knowledge_base_fields: Sequence[list[FieldDefinition] | None],
    default_fields: list[FieldDefinition],
) -> list[FieldDefinition]:
    """文書が属する KB の定義から、抽出する項目を決める(#548)。

    - KB に定義が無い(None)ときは全体の既定を使う。所属 KB が無いときも全体の既定。
    - 複数の KB に属する文書は、各 KB の定義(無ければ既定)の和集合にする。同じ項目名
      (大文字小文字を区別しない)は先の KB の定義を使う。KB の順は呼び出し側が決める
      (作成の古い順)。
    """
    if not knowledge_base_fields:
        return list(default_fields)
    merged: list[FieldDefinition] = []
    seen: set[str] = set()
    for fields in knowledge_base_fields:
        for field in default_fields if fields is None else fields:
            key = field.name.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(field)
    return merged


_DATE_PATTERN = re.compile(r"^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
_TRUE_VALUES = {"true", "yes", "y", "1", "はい", "有", "あり", "真"}
_FALSE_VALUES = {"false", "no", "n", "0", "いいえ", "無", "なし", "偽"}


def normalize_field_value(value_type: FieldValueType, raw: str) -> str:
    """抽出値を型ごとの比較できる形へ寄せる(#549。検索の絞り込みで型どおりに比べるため)。

    number は桁区切り・空白を除いた数字、date は YYYY-MM-DD、bool は true / false にする。
    寄せられない値はそのまま返す(検索の文としては使えるが、範囲の絞り込みでは一致しない)。
    """
    text = unicodedata.normalize("NFKC", raw).strip()
    if value_type == "number":
        candidate = re.sub(r"[,\s]", "", text)
        try:
            number = Decimal(candidate)
        except InvalidOperation:
            return raw
        return format(number, "f") if number.is_finite() else raw
    if value_type == "date":
        match = _DATE_PATTERN.match(text)
        if match is None:
            return raw
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return raw
    if value_type == "bool":
        folded = text.casefold()
        if folded in _TRUE_VALUES:
            return "true"
        if folded in _FALSE_VALUES:
            return "false"
    return raw


def _extract_json_array(raw: str) -> str:
    """LLM 出力から JSON 配列部分だけを取り出す(code fence・前後説明文に強い)。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: -len("```")]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return "[]"
    return text[start : end + 1]


def _safe_confidence(value: object) -> float | None:
    """confidence を 0..1 の float へ寄せる(範囲外/非数は None)。"""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= parsed <= 1.0:
        return parsed
    return None


def parse_extraction_fields(raw: str, field_defs: list[FieldDefinition]) -> list[ExtractionField]:
    """LLM の JSON 出力を、許可された field 定義に照らして ExtractionField へ正規化する。

    定義外 name・重複・空 value は捨て、value_type は定義側を正とする(LLM の自己申告を信用しない)。
    """
    allowed = {definition.name.casefold(): definition for definition in field_defs}
    try:
        data = json.loads(_extract_json_array(raw))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    fields: list[ExtractionField] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        key = name.casefold()
        if key not in allowed or key in seen:
            continue
        value = item.get("value")
        if value is None or str(value).strip() == "":
            continue
        definition = allowed[key]
        try:
            field = ExtractionField(
                name=definition.name,
                value=normalize_field_value(definition.value_type, str(value)),
                value_type=definition.value_type,
                confidence=_safe_confidence(item.get("confidence")),
            )
        except ValueError:
            continue
        seen.add(key)
        fields.append(field)
    return fields


def field_definitions_prompt(field_defs: list[FieldDefinition]) -> str:
    """抽出器へ渡す field 仕様の JSON 文字列(OCI Enterprise AI structured output 用)。"""
    return json.dumps(
        [
            {"name": field.name, "description": field.description, "value_type": field.value_type}
            for field in field_defs
        ],
        ensure_ascii=False,
    )


def _field_element(field: ExtractionField, order: int) -> DocumentElement:
    """抽出 field を検索可能な element として表現する(content_kind=field)。"""
    metadata: dict[str, object] = {
        "field_name": field.name,
        "field_value": field.value,
        "field_value_type": field.value_type,
        "extracted_field": True,
    }
    if field.source_element_id:
        metadata["field_source_element_id"] = field.source_element_id
    return DocumentElement(
        kind="text",
        text=f"{field.name}: {field.value}",
        order=order,
        element_id=f"field-{order}-{field.name}"[:128],
        content_kind="field",
        page_number=field.page_number,
        bbox=list(field.bbox) if field.bbox else None,
        metadata=metadata,
    )


async def extract_fields_from_extraction(
    extraction: StructuredExtraction,
    field_defs: list[FieldDefinition],
    extract: FieldExtractor,
) -> StructuredExtraction:
    """field schema があれば注入された抽出器で named field を抽出して付与する。

    抽出結果は `StructuredExtraction.fields` へ保存し、検索可能な合成 element
    (content_kind=field)も追加する。抽出器の失敗・空は best-effort で据え置く。
    """
    text = extraction.raw_text.strip()
    if not field_defs or not text:
        return extraction
    try:
        fields = await extract(text, field_defs)
    except Exception:
        return extraction
    fields = [field for field in fields if field.name and field.value]
    if not fields:
        return extraction

    next_order = max((element.order for element in extraction.elements), default=0) + 1
    new_elements = [_field_element(field, next_order + index) for index, field in enumerate(fields)]
    return extraction.model_copy(
        update={
            "fields": [*extraction.fields, *fields],
            "elements": [*extraction.elements, *new_elements],
        }
    )
