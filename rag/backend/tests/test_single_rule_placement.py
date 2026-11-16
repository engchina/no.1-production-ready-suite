"""構造化出力の規則を、指示の本文か応答 schema の項目の説明の 1 か所にだけ置く契約（#5250）。

項目の説明は送る時に system の指示の最後の Step になる（``pr_backend_core.response_schema``）。
モデルに送る最初のターン（system に説明の Step を足した文と、利用者側の指示）で、同じ規則が
2 回入らないことを確かめる。
"""

from __future__ import annotations

import json

import pytest
from pr_backend_core.response_schema import with_schema_field_guide

from app.rag.document_summary import DOCUMENT_SUMMARY_RESPONSE_SCHEMA
from app.rag.ingestion_prompts import DOCUMENT_SUMMARY_PROMPT
from app.rag.support_guide_process_import import (
    GUIDE_DRAFT_PROMPT,
    GUIDE_DRAFT_RESPONSE_SCHEMA,
    GUIDE_DRAFT_SYSTEM_PROMPT,
)


def _guide_first_turn() -> str:
    system, _schema = with_schema_field_guide(
        GUIDE_DRAFT_SYSTEM_PROMPT, GUIDE_DRAFT_RESPONSE_SCHEMA
    )
    return system + "\n" + GUIDE_DRAFT_PROMPT


@pytest.mark.parametrize(
    "rule",
    [
        "s1・s2… の順の番号",
        "c1・c2… の順の番号",
        "先に済ませる手順の id",
        "資料を検索する語（画面名・機能名）",
        "補足のうち手順に係るもの",
        "資料の名前（回答の過程に書いたまま）",
        "全手順に共通の資料なら空",
        "利用者がしたいことを表す短い名前",
    ],
)
def test_guide_draft_field_rules_are_sent_once_from_the_schema(rule: str) -> None:
    """業務ガイドの下書きの項目の書き方は、項目の説明にだけ置く。"""
    assert rule in json.dumps(GUIDE_DRAFT_RESPONSE_SCHEMA, ensure_ascii=False)
    assert rule not in GUIDE_DRAFT_PROMPT
    assert _guide_first_turn().count(rule) == 1


def test_guide_draft_instruction_keeps_only_the_mapping_and_cross_field_decisions() -> None:
    # 入力の節を項目へ振り分けることと、項目をまたぐ判断（分岐・引き継ぎ）だけが本文に残る。
    for field in ("document_name", "depends_on", "step_ids", "expected_result", "note "):
        assert field not in GUIDE_DRAFT_PROMPT, field
    for decision in ("type=enum", "goto_step を分け", "handling=handoff", "match_terms と"):
        assert decision in GUIDE_DRAFT_PROMPT, decision


def test_document_summary_rules_follow_the_editable_instruction() -> None:
    """要約の summary の書き方は、利用者が編集できる指示にだけ置く。"""
    guided, _schema = with_schema_field_guide(
        DOCUMENT_SUMMARY_PROMPT, DOCUMENT_SUMMARY_RESPONSE_SCHEMA
    )
    assert "description" not in DOCUMENT_SUMMARY_RESPONSE_SCHEMA["properties"]["summary"]
    for rule in ("3〜5 文", "文書の本文と同じ言語"):
        assert rule in DOCUMENT_SUMMARY_PROMPT
        assert guided.count(rule) == 1
    # topics の「本文の語のまま」は、片方だけにすると守られる数が減ったので両方に置く（#5250）。
    assert "本文の語のまま" in DOCUMENT_SUMMARY_PROMPT
    assert (
        "本文の語のまま" in DOCUMENT_SUMMARY_RESPONSE_SCHEMA["properties"]["topics"]["description"]
    )
    assert guided.count("本文の語のまま") == 2
