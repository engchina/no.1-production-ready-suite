"""AI 構築の LLM に渡す「前の定義」と「前の確認で直すべき点」（#1734）。

再構築のたびに AI が同じ列に違う名前を付けると、共有プロパティ・プロパティが揺れて増える。
LLM の入力に前の定義の名前と物理の対応（表・列）を入れ、「既存の名前と対応を保ち、根拠がある
ものだけを足す・直す」と指示する。前回の公開前の確認のエラー・警告・補う項目も、平易な日本語の
「直すべき点」として渡す。

LLM の入力は手がかりにすぎない。決定論の併合と検証（`ontology_unified_model`・
`ontology_definition_validation`）を今どおり正とし、LLM が前の誤りを写しても物理の表・列の
検証と併合で落とす。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from .ontology_definitions import BusinessDefinition

PriorBasis = Literal["draft", "published", "none"]

# 前の確認の「直すべき点」の上限（同じ文は 1 つにまとめる）。
MAX_PRIOR_FINDINGS = 40

PRIOR_PROMPT = (
    "入力 JSON に previous_ontology（前の定義の要約）があれば、既存のオブジェクト型・プロパティ・"
    "リンク型・共有プロパティの api_name・表示名と、表・列の対応を保ってください。"
    "同じ表・列の概念に別の api_name や表示名を付け直さず、資料・スキーマ・Q/A に根拠がある"
    "ものだけを足す・直してください。previous_check_findings は前回の公開前の確認で見つかった"
    "直すべき点です。根拠があれば、その点を直した定義を出してください。"
)


def select_prior_definitions(
    draft_markdown: str,
    draft_definitions: Sequence[BusinessDefinition],
    published_definitions: Sequence[BusinessDefinition] | None,
) -> tuple[PriorBasis, list[BusinessDefinition]]:
    """前の定義の基準を選ぶ: 下書きがあれば下書き、無ければ公開版、どちらも無ければ無し。

    下書き（公開版＋利用者の編集を含む）は、Markdown を決定論で読んだ定義（`draft_definitions`）。
    下書きの Markdown があるのに定義が読めないとき（自由な文だけ）は、公開版に戻らず「下書き」の
    空の要約にする（利用者が消した定義を公開版から戻さない）。
    """

    if draft_markdown.strip():
        return "draft", list(draft_definitions)
    if published_definitions:
        return "published", list(published_definitions)
    return "none", []


def _columns(d: BusinessDefinition) -> list[str]:
    return [
        ".".join(p for p in (m.owner, m.object_name, m.column_name) if p)
        for m in d.mappings
        if m.object_name
    ]


def summarize_prior_definitions(
    basis: PriorBasis, definitions: Iterable[BusinessDefinition]
) -> dict[str, Any] | None:
    """オブジェクト型・プロパティ・リンク型・共有プロパティの名前・別名・物理の対応だけの要約。"""

    if basis == "none":
        return None
    values = list(definitions)
    summary: dict[str, Any] = {
        "basis": basis,
        "object_types": [
            {
                "api_name": d.api_name,
                "name_ja": d.name_ja,
                "aliases": list(d.aliases),
                "tables": _columns(d),
            }
            for d in values
            if d.kind == "object_type"
        ],
        "properties": [
            {
                "api_name": d.api_name,
                "name_ja": d.name_ja,
                "aliases": list(d.aliases),
                "object_type": getattr(d, "object_type", ""),
                "columns": _columns(d),
                "shared_property": getattr(d, "shared_property", ""),
            }
            for d in values
            if d.kind == "property"
        ],
        "link_types": [
            {
                "api_name": d.api_name,
                "name_ja": d.name_ja,
                "source": getattr(d, "source", ""),
                "target": getattr(d, "target", ""),
                "join_expression_sql": getattr(d, "join_expression_sql", ""),
            }
            for d in values
            if d.kind == "link_type"
        ],
        "shared_properties": [
            {"api_name": d.api_name, "name_ja": d.name_ja, "aliases": list(d.aliases)}
            for d in values
            if d.kind == "shared_property"
        ],
    }
    return summary


def shrink_prior_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """上限を超えるときに順に試す、小さくした要約（最後は空）。診断は別に残す。"""

    def without(*keys: str) -> dict[str, Any]:
        return {k: v for k, v in summary.items() if k not in keys}

    lean_properties = [
        {k: v for k, v in p.items() if k in {"api_name", "name_ja", "columns"}}
        for p in summary.get("properties", [])
    ]
    return [
        summary,
        {**summary, "properties": lean_properties},
        without("properties", "link_types"),
        without("properties", "link_types", "shared_properties"),
    ]


def plain_prior_findings(
    findings: Iterable[dict[str, Any]], names: dict[str, str] | None = None
) -> list[str]:
    """前の確認のエラー・警告・補う項目を「直すべき点」の平易な文にする（重複を除く）。

    内部の ID・診断コードは渡さず、対象は業務の名前で書く。
    """

    labels = {"error": "公開を止める問題", "warning": "警告", "info": "補うと精度が上がる項目"}
    result: list[str] = []
    for finding in findings:
        severity = str(finding.get("severity") or "")
        message = " ".join(str(finding.get("message_ja") or "").split())
        if severity not in labels or not message:
            continue
        target = (names or {}).get(str(finding.get("definition_id") or ""), "")
        text = f"{labels[severity]}: " + (f"『{target}』 " if target else "") + message
        if text not in result:
            result.append(text)
        if len(result) >= MAX_PRIOR_FINDINGS:
            break
    return result
