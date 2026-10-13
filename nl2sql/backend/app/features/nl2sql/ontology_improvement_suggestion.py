"""公開前の確認の「業務説明に追記する改善案」（#1729）。

残ったエラー・警告・補うと精度が上がる項目から、利用者が「業務説明（自然言語）」に追記して
「AI 構築を実行」をやり直すための、業務の言葉の日本語の文を作る。LLM は使わず、診断と物理の
schema（主キーの制約・列の型）と定義（対応する表・リンク型の結合のキー）だけから決定論で組み立てる
（速く、毎回同じ）。分からない値は推測で埋めず「（○○を確認して書いてください）」と空けておく。
既に業務説明に書かれている文は、画面が重ねて出さない（文ごとに返す）。
"""

from __future__ import annotations

import re
from typing import Any

from .object_identity import object_match_key
from .ontology_definition_validation import mapping_column_key, mapping_object_key
from .ontology_definitions import BusinessDefinition, ObjectTypeDefinition, PropertyDefinition
from .ontology_terms import VALUE_LABELS, plain_label
from .ontology_unified_model import base_data_type, schema_tables

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
# オブジェクト型の文で補える診断（対応する表・主キー・粒度・所属の誤り・関係の多重度）。
_OBJECT_CODES = frozenset(
    {
        "OBJECT_MAPPING_REQUIRED",
        "MAPPING_OUTSIDE_PROFILE",
        "OBJECT_EXCLUDED_WITHOUT_TABLE",
        "OBJECT_IDENTITY_REQUIRED",
        "OBJECT_GRAIN_REQUIRED",
        "PROPERTY_OWNER_MISMATCH",
    }
)
_TYPE_FIELDS = frozenset({"data_type", "physical_type"})


def _ask(what: str) -> str:
    return f"（{what}を確認して書いてください）"


def _japanese(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ一-鿿]", text))


def improvement_suggestions(
    definitions: list[BusinessDefinition],
    findings: list[dict[str, Any]],
    schema: dict[str, Any] | None,
    *,
    excluded: list[BusinessDefinition] | None = None,
) -> list[dict[str, str]]:
    """改善案の文の一覧（`severity` と `text`）。公開を止めるエラーを解く文を先に並べる。"""

    values = [*definitions, *(excluded or [])]
    by_key: dict[str, BusinessDefinition] = {}
    for d in values:
        for key in (d.id, d.api_name):
            if key:
                by_key.setdefault(key, d)
    by_name = {d.api_name: d for d in values}
    tables = schema_tables(schema)
    sentences: dict[str, str] = {}

    def add(severity: str, text: str) -> None:
        current = sentences.get(text)
        if current is None or _SEVERITY_ORDER[severity] < _SEVERITY_ORDER[current]:
            sentences[text] = severity

    def label(name: str) -> str:
        target = by_name.get(name)
        return target.name_ja if target is not None else name

    def object_sentence(obj: ObjectTypeDefinition) -> str:
        names = [obj.name_ja, *[a for a in obj.aliases if _japanese(a)][:2]]
        head = "・".join(dict.fromkeys(names)) + f"（{obj.api_name}）"
        keys = sorted({mapping_object_key(m) for m in obj.mappings if m.object_name})
        if len(keys) == 1 and keys[0] in tables:
            table = tables[keys[0]]
            where = f"{table['qualified']} の{'ビュー' if table['kind'] == 'view' else '表'}"
        elif keys:
            where = " / ".join(keys) + " の表・ビュー"
        else:
            where = "（対応する表・ビューの名前を確認して書いてください）の表"
            table = None
        primary = [
            column
            for name in obj.primary_key
            if isinstance(prop := by_name.get(name), PropertyDefinition)
            for column in [m.column_name for m in prop.mappings if m.column_name][:1]
        ]
        if not primary and len(keys) == 1 and keys[0] in tables:
            table = tables[keys[0]]
            primary = [table["columns"][c]["column"] for c in table["primary_key"]]
        grain = obj.grain_ja.strip().rstrip("。")
        if not grain:
            grain = f"1 行が 1 つの{obj.name_ja}" if primary else _ask("1 行が何を表すか")
        key_text = "・".join(primary) if primary else _ask("1 行を特定する列")

        def spaced(text: str) -> str:
            # 物理の名前の前だけ空白を置く（「（…確認して書いてください）」の前には置かない）。
            return text if text.startswith("（") else f" {text}"

        parts = [f"{head}は{spaced(where)}で、{grain}、主キーは{spaced(key_text)}"]
        for link in values:
            if link.kind != "link_type" or getattr(link, "source", "") != obj.api_name:
                continue
            join = getattr(link, "join_expression_sql", "")
            left = join.split("=", 1)[0].strip() if "=" in join and " AND " not in join else ""
            column = object_match_key(left).rsplit(".", 1)[-1] if left else ""
            cardinality = VALUE_LABELS.get(getattr(link, "cardinality", ""), "")
            how = (
                f"（{cardinality}）"
                if cardinality and getattr(link, "cardinality", "unknown") != "unknown"
                else _ask("多重度（1 対多など）")
            )
            target = label(getattr(link, "target", ""))
            via = f"{column} で" if column else _ask("結合のキーの列") + "で"
            parts.append(f"{via}{target}に属する{how}")
        return "、".join(parts) + "。"

    objects_by_severity: dict[str, str] = {}
    for finding in findings:
        severity = str(finding.get("severity", ""))
        if severity not in _SEVERITY_ORDER:
            continue
        code = str(finding.get("code", ""))
        target = by_key.get(str(finding.get("definition_id", "")))
        if code in _OBJECT_CODES or code == "CARDINALITY_REQUIRED":
            obj = target
            if code == "CARDINALITY_REQUIRED" and target is not None:
                obj = by_name.get(getattr(target, "source", ""))
            if isinstance(target, PropertyDefinition) and code == "PROPERTY_OWNER_MISMATCH":
                obj = by_name.get(target.object_type)
            if isinstance(obj, ObjectTypeDefinition):
                previous = objects_by_severity.get(obj.api_name)
                if previous is None or _SEVERITY_ORDER[severity] < _SEVERITY_ORDER[previous]:
                    objects_by_severity[obj.api_name] = severity
        elif code == "DEFINITION_CONFLICT":
            detail = finding.get("detail") or {}
            field = str(detail.get("field", ""))
            conflicted = by_name.get(str(detail.get("api_name", ""))) or target
            if conflicted is None or not field:
                continue
            owner = label(getattr(conflicted, "object_type", "") or "")
            subject = f"{owner}の{conflicted.name_ja}" if owner else conflicted.name_ja
            columns = [m for m in conflicted.mappings if m.column_name and not m.expression_sql]
            info = (
                tables.get(mapping_object_key(columns[0]), {})
                .get("columns", {})
                .get(mapping_column_key(columns[0]))
                if field in _TYPE_FIELDS and len(columns) == 1
                else None
            )
            if info and info.get("data_type"):
                physical = str(info["data_type"])
                base = VALUE_LABELS.get(base_data_type(physical), "")
                add(
                    severity,
                    f"{subject}は {columns[0].owner}.{columns[0].object_name}."
                    f"{columns[0].column_name} の列で、型は {physical}"
                    + (f"（{base}）" if base else "")
                    + "。",
                )
            elif field == "required":
                add(severity, f"{subject}は{_ask('必須（値が必ずある項目）かどうか')}。")
            else:
                choices = " と ".join(str(v) for v in detail.get("values", []) if str(v))
                what = f"{choices} のどちらか" if choices else plain_label(field)
                add(severity, f"{subject}の{plain_label(field)}は{_ask(what)}。")
        elif code == "METRIC_SEMANTICS_REQUIRED" and target is not None:
            field = str(finding.get("field", ""))
            add(
                severity,
                f"指標「{target.name_ja}」の{plain_label(field)}は{_ask(plain_label(field))}。",
            )
    for name, severity in objects_by_severity.items():
        obj = by_name[name]
        if isinstance(obj, ObjectTypeDefinition):
            add(severity, object_sentence(obj))
    return [
        {"severity": severity, "text": text}
        for text, severity in sorted(sentences.items(), key=lambda item: _SEVERITY_ORDER[item[1]])
    ]


_OBJECT_SENTENCE = re.compile(
    r"（(?P<api>[A-Za-z][A-Za-z0-9_.]*)）は (?P<table>\S+) の(?:表|ビュー)で、"
    r"(?P<grain>[^、。]+)、主キーは ?(?P<key>[^、。]+)"
)


def apply_business_text_facts(
    definitions: list[BusinessDefinition], business_text: str
) -> list[str]:
    """業務説明に追記された改善案の文（`object_sentence` の形）から、粒度と主キーを定義に入れる。

    AI が業務説明を読み落としても、追記した改善案が次の構築に必ず効くようにする（#1729）。
    「（…を確認して書いてください）」のまま残った所は使わない（推測で埋めない）。値の入った定義は
    変えない。入れた定義の API 名を返す。
    """

    by_name = {d.api_name: d for d in definitions}
    applied: list[str] = []
    for match in _OBJECT_SENTENCE.finditer(business_text):
        obj = by_name.get(match.group("api"))
        if not isinstance(obj, ObjectTypeDefinition):
            continue
        grain = match.group("grain").strip()
        if not obj.grain_ja and "確認して書いてください" not in grain:
            obj.grain_ja = grain
            applied.append(obj.api_name)
        key = match.group("key").strip()
        if not obj.primary_key and "確認して書いてください" not in key:
            columns = {object_match_key(c.strip()) for c in key.split("・") if c.strip()}
            props = [
                d.api_name
                for d in definitions
                if isinstance(d, PropertyDefinition)
                and d.kind == "property"
                and d.object_type == obj.api_name
                and len(d.mappings) == 1
                and d.mappings[0].column_name
                and mapping_column_key(d.mappings[0]) in columns
            ]
            if len(props) == len(columns):
                obj.primary_key = props
                applied.append(obj.api_name)
    return list(dict.fromkeys(applied))
