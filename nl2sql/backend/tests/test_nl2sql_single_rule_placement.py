"""構造化出力の規則を、指示の本文か応答 schema の項目の説明の 1 か所にだけ置く契約（#5250）。

項目の説明は送る時に指示の最後の Step になる（``enterprise_ai_client._with_field_guide``）。
モデルに送る指示（説明の Step を足した後）で、同じ規則が 2 回入らないことを確かめる。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.nl2sql import db_admin_chat
from app.features.nl2sql.enterprise_ai_client import _with_field_guide
from app.features.nl2sql.ontology_build import _EXTRACTION_SYSTEM_PROMPT
from app.features.nl2sql.ontology_models import OntologyConceptExtraction
from app.features.nl2sql.structured_outputs import (
    DbAdminChatAnalysisOutput,
    DbAdminChatRepairOutput,
    DbAdminChatSqlOutput,
    response_format,
)


def _descriptions(node: Any) -> list[str]:
    """schema の中の説明をすべて集める。"""
    if isinstance(node, dict):
        found = [node["description"]] if isinstance(node.get("description"), str) else []
        return found + [text for value in node.values() for text in _descriptions(value)]
    if isinstance(node, list):
        return [text for value in node for text in _descriptions(value)]
    return []


# (呼び出し, 指示の本文, 応答の形式, 規則の語句, 置く層)
CASES = [
    (
        "DB 管理チャットの SQL",
        db_admin_chat.SYSTEM_PROMPT,
        response_format(DbAdminChatSqlOutput, name="DbAdminChatSql"),
        "日本語の短い説明",
        "description",
    ),
    (
        "DB 管理チャットの作り直し（文の番号）",
        db_admin_chat.REPAIR_SYSTEM_PROMPT,
        response_format(DbAdminChatRepairOutput, name="DbAdminChatRepair"),
        "直した文の番号",
        "description",
    ),
    (
        "DB 管理チャットの作り直し（直した 1 文）",
        db_admin_chat.REPAIR_SYSTEM_PROMPT,
        response_format(DbAdminChatRepairOutput, name="DbAdminChatRepair"),
        "直した 1 文",
        "description",
    ),
    (
        "DB 管理チャットの失敗の分析",
        db_admin_chat.ANALYSIS_SYSTEM_PROMPT,
        response_format(DbAdminChatAnalysisOutput),
        "空文字列",
        "description",
    ),
    (
        "オントロジーの構築（title_property を設定するとき）",
        _EXTRACTION_SYSTEM_PROMPT,
        response_format(OntologyConceptExtraction),
        "根拠のある表示用の property があれば設定し",
        "description",
    ),
    (
        "オントロジーの構築（title_property の未設定）",
        _EXTRACTION_SYSTEM_PROMPT,
        response_format(OntologyConceptExtraction),
        "未設定を資料不足・必須項目不足として報告しない",
        "description",
    ),
]


@pytest.mark.parametrize(
    ("system", "fmt", "rule", "layer"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_rule_is_sent_once_from_one_layer(
    system: str, fmt: dict[str, Any], rule: str, layer: str
) -> None:
    guided, _sent = _with_field_guide(system, fmt)
    in_descriptions = [text for text in _descriptions(fmt["schema"]) if rule in text]
    if layer == "description":
        assert rule not in system
        assert len(in_descriptions) == 1
    else:
        assert rule in system
        assert not in_descriptions
    assert guided.count(rule) == 1


def test_analysis_body_keeps_only_the_cross_field_handling_of_unfixable_statements() -> None:
    # 直せないときに conclusion へ書くこと（項目をまたぐ判断）は本文、corrected_sql の値は説明。
    assert "corrected_sql" not in db_admin_chat.ANALYSIS_SYSTEM_PROMPT
    assert "conclusion に理由と代わりの方法を書きます" in db_admin_chat.ANALYSIS_SYSTEM_PROMPT


def test_ontology_body_does_not_repeat_the_title_property_rule() -> None:
    assert "title_property は任意" not in _EXTRACTION_SYSTEM_PROMPT
    assert "title_property の未設定" not in _EXTRACTION_SYSTEM_PROMPT


def test_repair_body_does_not_redefine_the_statement_fields() -> None:
    assert "index" not in db_admin_chat.REPAIR_SYSTEM_PROMPT
    assert "直せない文は返さない" in db_admin_chat.REPAIR_SYSTEM_PROMPT
