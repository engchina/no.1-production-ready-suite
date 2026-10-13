"""オントロジーの Markdown（`render_concepts` の形）を決定論で定義に戻す（#1729）。

公開前の確認は、Markdown を LLM で解析し直すと、同じ表を指す概念を「未解決」にしたり、行の対応を
落としたりして、毎回結果が変わる（公開が止まる）。下書きは `render_concepts` の決まった形
（`#### 種類` → `##### 表示名 (`API 名`)` → `- 項目: 値`）なので、その形の行はここで読む。
読めない行（自由に書き足した文・複雑な項目）が 1 行でもあれば `None` を返し、呼出元は LLM の解析に
任せる。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from .ontology_definitions import BusinessDefinition, DefinitionEvidence
from .ontology_terms import CONCEPT_LABELS, FIELD_LABELS, VALUE_LABELS, plain_label
from .ontology_unified_model import DEFINITIONS

CONCEPT_SECTION = "オントロジーの概念定義"
# #1729 より前の下書きの項目名（今の下書きを読み直すため。新しく書く Markdown には使わない）。
_PREVIOUS_FIELD_LABELS = {
    "説明": "description_ja",
    "業務上の粒度": "grain_ja",
    "表示名の項目": "title_property",
    "加算特性": "additivity",
    "別名": "aliases",
    "対応する表・列": "mappings",
    "識別名": "api_name",
    "名称": "name_ja",
    "選択可能な値": "allowed_values",
    "逆方向の関係名": "inverse_name_ja",
    "中間オブジェクト": "intermediary_object",
    "主識別子": "primary_key",
    "対象オブジェクト": "object_type",
    "データ型": "data_type",
    "関係の起点": "source",
    "関係の終点": "target",
    "結合条件": "join_expression_sql",
    "定義 SQL": "expression_sql",
    "絞り込み条件": "filter_sql",
    "集計粒度": "grain",
    "重複排除キー": "distinct_keys",
    "パラメーター": "parameters",
    "戻り値": "return_type",
    "業務条件": "predicate_sql",
    "対象プロパティ": "property",
    "必須の関係": "required_links",
    "必須の操作": "required_actions",
    "時間プロパティ": "time_property",
    "発生日時プロパティ": "timestamp_property",
}
_FIELDS: dict[str, str] = {
    **_PREVIOUS_FIELD_LABELS,
    **{plain_label(field): field for field in FIELD_LABELS},
    **{label: field for field, label in FIELD_LABELS.items()},
}
_KINDS = {label: kind for kind, label in CONCEPT_LABELS.items()}
_VALUES = {label: value for value, label in VALUE_LABELS.items()}
# 文字列・文字列の一覧・真偽値・対応する表・列だけを読む（それ以外の複雑な項目は LLM に任せる）。
_COMPLEX_FIELDS = frozenset(
    {"implements", "values", "parameters", "assignments", "evidence", "property_mapping"}
)
_HEADING = re.compile(r"^##### (?P<name>.+?) \(`(?P<api>[^`]+)`\)\s*$")
_ITEM = re.compile(r"^- (?P<label>[^:：]+?)[:：] ?(?P<value>.*)$")
_MAPPING = re.compile(r"^(?P<path>[^（]+?)(?:（式: (?P<expression>.+)）)?$")


def _mapping(text: str) -> dict[str, str] | None:
    match = _MAPPING.fullmatch(text.strip())
    if match is None:
        return None
    parts = [p for p in match.group("path").strip().split(".") if p]
    if len(parts) not in (2, 3) or any(" " in p for p in parts):
        return None
    return {
        "owner": parts[0],
        "object_name": parts[1],
        "column_name": parts[2] if len(parts) == 3 else "",
        "expression_sql": match.group("expression") or "",
    }


def _value(field: str, text: str, defaults: dict[str, Any]) -> Any:
    """`- 項目: 値` の値を型に合わせて戻す。読めなければ `ValueError`。"""

    default = defaults.get(field)
    if field in _COMPLEX_FIELDS:
        raise ValueError(field)
    if field == "mappings":
        mappings = [_mapping(part) for part in text.split(" / ")]
        if any(m is None for m in mappings):
            raise ValueError(field)
        return mappings
    if isinstance(default, bool):
        if text not in {"はい", "いいえ"}:
            raise ValueError(field)
        return text == "はい"
    if isinstance(default, list):
        return [part.strip() for part in text.split(" / ") if part.strip()]
    return _VALUES.get(text, text) if field in {"data_type", "cardinality"} else text


def parse_rendered_markdown(markdown: str) -> dict[str, Any] | None:
    """`render_concepts` の形の Markdown を、`MarkdownExtraction` と同じ形の dict にする。

    1 行でも読めなければ `None`（呼出元が LLM で解析する）。
    """

    definitions: list[BusinessDefinition] = []
    lines: list[dict[str, Any]] = []
    section, kind = "", ""
    current: dict[str, Any] | None = None

    def flush(end: int) -> bool:
        nonlocal current
        if current is None:
            return True
        start = current.pop("_start")
        current["evidence"] = [
            DefinitionEvidence(
                source_id="markdown",
                locator=f"line:{start}",
                excerpt_ja=current.pop("_heading"),
                source_kind="manual",
            ).model_dump(mode="json")
        ]
        try:
            definition = DEFINITIONS.validate_python([current])[0]
        except ValidationError:
            return False
        definitions.append(definition)
        lines.append(
            dict(
                start_line=start,
                end_line=end,
                disposition="definition",
                definition_api_names=[definition.api_name],
            )
        )
        current = None
        return True

    rows = markdown.splitlines()
    last = 0
    for number, raw in enumerate(rows, 1):
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            if not flush(last):
                return None
            section, kind = line[3:].strip(), ""
            lines.append(
                dict(
                    start_line=number,
                    end_line=number,
                    disposition="context",
                    reason_ja="節の見出し",
                )
            )
        elif section.startswith("Q/A ") and section != CONCEPT_SECTION:
            # Q/A 由来の規則・構造パターンなど、概念の定義の外の節は SQL の生成の文脈として残す。
            lines.append(
                dict(
                    start_line=number,
                    end_line=number,
                    disposition="context",
                    reason_ja="概念の定義の外の節（SQL の生成の文脈）",
                )
            )
        elif section != CONCEPT_SECTION:
            return None  # 概念の節の外の自由な文は LLM で解析する
        elif line.startswith("#### "):
            if not flush(last):
                return None
            kind = _KINDS.get(line[5:].strip(), "")
            if not kind:
                return None
            lines.append(
                dict(
                    start_line=number,
                    end_line=number,
                    disposition="context",
                    reason_ja="種類の見出し",
                )
            )
        elif match := _HEADING.match(line):
            if not flush(last) or not kind:
                return None
            current = {
                "kind": kind,
                "name_ja": match.group("name").strip(),
                "api_name": match.group("api").strip(),
                "origin": "manual",
                "_start": number,
                "_heading": line,
            }
        elif (match := _ITEM.match(line)) and current is not None:
            field = _FIELDS.get(match.group("label").strip())
            if field is None or field in {"kind", "api_name"}:
                return None
            defaults = _defaults(kind)
            if field not in defaults:
                return None
            try:
                current[field] = _value(field, match.group("value").strip(), defaults)
            except ValueError:
                return None
        else:
            return None
        last = number
    if not flush(last) or not definitions:
        return None
    return {"definitions": definitions, "coverage": [], "lines": lines, "issues_ja": []}


_DEFAULTS: dict[str, dict[str, Any]] = {}


def _defaults(kind: str) -> dict[str, Any]:
    """種類のモデルの項目と既定値（項目が種類に無ければ読めない行とする）。"""

    if kind not in _DEFAULTS:
        from .ontology_definitions import (
            ActionTypeDefinition,
            BusinessEventDefinition,
            BusinessRuleDefinitionV2,
            EnumerationDefinition,
            FunctionDefinition,
            InterfaceDefinition,
            LinkTypeDefinition,
            MetricDefinitionV2,
            ObjectSetDefinition,
            ObjectTypeDefinition,
            PropertyDefinition,
        )

        models: dict[str, Any] = {
            "object_type": ObjectTypeDefinition,
            "property": PropertyDefinition,
            "shared_property": PropertyDefinition,
            "value_type": PropertyDefinition,
            "link_type": LinkTypeDefinition,
            "function": FunctionDefinition,
            "action_type": ActionTypeDefinition,
            "interface": InterfaceDefinition,
            "metric": MetricDefinitionV2,
            "business_rule": BusinessRuleDefinitionV2,
            "enumeration": EnumerationDefinition,
            "business_event": BusinessEventDefinition,
            "object_set": ObjectSetDefinition,
        }
        _DEFAULTS[kind] = {
            name: info.get_default(call_default_factory=True)
            for name, info in models[kind].model_fields.items()
            if not info.is_required() or name in {"source", "target", "object_type", "property"}
        }
        for name, info in models[kind].model_fields.items():
            if info.is_required() and name not in _DEFAULTS[kind]:
                _DEFAULTS[kind][name] = ""
    return _DEFAULTS[kind]
