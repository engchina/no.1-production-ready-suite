"""根拠の表の対応と取り違えた段落を、表の正しい値に直す（研究と改訂。#3005）。根拠・文は合成、LLM はスタブ。"""

import json
from typing import Any

import pytest

from rag_engine.generation import grounded
from rag_engine.generation.table_revision import REVISION_SYSTEM_PROMPT, TableRevision, revise_table_passage
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem

TABLE = (
    "<table><tr><th>区分</th><th>選択肢の値</th></tr>"
    "<tr><td>区分Ⅰ</td><td>01</td></tr><tr><td>区分Ⅱ</td><td>02</td></tr><tr><td>区分Ⅲ</td><td>03</td></tr></table>"
)
REVISED = "区分Ⅱでは選択肢の値 02 を選びます。"
WRONG = "区分Ⅱでは選択肢の値 01 を選びます。"


def _reviser(text: str | Exception, seen: list | None = None):
    def parse(system: str, user: str, schema: Any) -> TableRevision:
        if seen is not None:
            seen.append((system, json.loads(user), schema))
        if isinstance(text, Exception):
            raise text
        return TableRevision(revised_text=text)

    return parse


def test_wrong_value_is_replaced_by_the_value_of_the_table() -> None:
    seen: list = []
    revised = revise_table_passage(WRONG, [TABLE], _reviser(REVISED, seen))
    assert revised is not None and revised.text == REVISED
    assert "『区分Ⅱ』の値は『02』" in revised.note
    [(system, inputs, schema)] = seen
    assert system == REVISION_SYSTEM_PROMPT and schema is TableRevision
    assert system.startswith("## Step 1:")
    assert "## Step 4:" in system
    # LLM には段落と表の正しい対応だけを渡す。
    assert inputs == {
        "passage": WRONG,
        "corrections": [{"wrong": "01", "correct": "02"}],
        "notes": ["『区分Ⅱ』の値は『02』（『01』は別の行の値）"],
    }


def test_value_of_another_column_is_replaced() -> None:
    table = "| 項目 | 改定前 | 改定後 |\n|---|---|---|\n| 申請の期限 | 30日以内 | 60日以内 |\n| 保存期間 | 3年 | 5年 |"
    revised = revise_table_passage("改定後の申請の期限は30日以内です。", [table], _reviser("改定後の申請の期限は60日以内です。"))
    assert revised is not None and revised.text == "改定後の申請の期限は60日以内です。"


@pytest.mark.parametrize(
    "output",
    [
        WRONG,  # 直していない
        "区分Ⅱでは選択肢の値 03 を選びます。",  # 別の行の値に変えた
        "区分Ⅱを選ぶときは、画面の右上のボタンから選択肢の一覧を開き、表の 2 行目の値である 02 を選択します。",  # 書き換えた
        "区分Ⅱの値は確認してください。",  # 正しい値を述べていない
        RuntimeError("synthetic failure"),
    ],
    ids=["unchanged", "other-row", "rewritten", "missing-value", "model-failure"],
)
def test_revisions_that_fail_the_check_are_not_used(output: Any) -> None:
    assert revise_table_passage(WRONG, [TABLE], _reviser(output)) is None


def test_passages_without_a_mismatch_are_not_sent() -> None:
    seen: list = []
    assert revise_table_passage(REVISED, [TABLE], _reviser(REVISED, seen)) is None
    assert seen == []


HEADER_WORD_TABLE = "| 行ID | 項目 | 上限額 |\n|---|---|---|\n| C1 | 項目甲 | 1,000円 |\n| C2 | 項目乙 | 1,200円 |"


def test_row_of_the_table_is_not_rewritten() -> None:
    """表の行そのままの段落は、見出しの語を含むセルがあっても LLM に送らず、値を書き換えない（#4691）。"""
    seen: list = []
    assert revise_table_passage("| C2 | 項目乙 | 1,200円 |", [HEADER_WORD_TABLE], _reviser("| C2 | 項目乙 | 項目乙 |", seen)) is None
    assert seen == []


def test_value_is_not_replaced_by_a_cell_of_another_form() -> None:
    """別の列のセルの形が値と違う（金額と名前）なら、値の書き換えの候補にしない（段落は今どおり矛盾として扱う）。"""
    seen: list = []
    passage = "項目乙という項目で 1,200円 です。"
    assert revise_table_passage(passage, [HEADER_WORD_TABLE], _reviser("項目乙という項目で 項目乙 です。", seen)) is None
    assert seen == []


@pytest.mark.parametrize(
    ("table", "passage"),
    [
        # 同じ値が 2 つの行にあり、別の列の値がそれぞれ違う（どの行の値に直すかが表の対応だけでは決まらない）。
        (
            "| 行ID | 改定前 | 改定後 |\n|---|---|---|\n| R1 | 1,200円 | 1,500円 |\n| R2 | 1,200円 | 1,800円 |",
            "R2 の改定後は 1,200円 です。",
        ),
        # 同じ値が 2 つの列にあり、その行の 2 つの列の値が違う。
        (
            "| 区分 | 上限 | 下限 |\n|---|---|---|\n| 区分A | 100円 | 50円 |\n| 区分B | 200円 | 100円 |\n| 区分C | 300円 | 250円 |",
            "区分Cは 100円 です。",
        ),
    ],
    ids=["same-value-in-two-rows", "same-value-in-two-columns"],
)
def test_value_is_not_rewritten_when_the_correct_cell_is_ambiguous(table: str, passage: str) -> None:
    """正しいセルが 1 つに決まらなければ、LLM に送らず値を書き換えない。"""
    seen: list = []
    assert revise_table_passage(passage, [table], _reviser("確かめられない値", seen)) is None
    assert seen == []


def test_real_mismatches_are_still_corrected_in_tables_with_header_words_in_cells() -> None:
    """見出しの語を含むセルの表でも、別の行・別の列の値は従来どおり表の値に直す。"""
    seen: list = []
    revised = revise_table_passage("| C2 | 項目乙 | 1,000円 |", [HEADER_WORD_TABLE], _reviser("| C2 | 項目乙 | 1,200円 |", seen))
    assert revised is not None and revised.text == "| C2 | 項目乙 | 1,200円 |"
    assert seen[0][1]["corrections"] == [{"wrong": "1,000円", "correct": "1,200円"}]

    columns = "| 行ID | 項目 | 改定前 | 改定後 |\n|---|---|---|---|\n| C1 | 項目甲 | 30日 | 60日 |\n| C2 | 項目乙 | 3年 | 5年 |"
    seen = []
    revised = revise_table_passage("項目乙の改定後は 3年 です。", [columns], _reviser("項目乙の改定後は 5年 です。", seen))
    assert revised is not None and revised.text == "項目乙の改定後は 5年 です。"
    assert seen[0][1]["corrections"] == [{"wrong": "3年", "correct": "5年"}]


def test_value_shared_by_columns_is_corrected_when_the_cell_is_unique() -> None:
    """同じ値のセルが複数の列にあっても、正しいセルが 1 つに決まれば直す。"""
    table = "| 行ID | 上限 | 下限 | 標準 |\n|---|---|---|---|\n| R1 | 100円 | 100円 | 80円 |\n| R2 | 200円 | 150円 | 150円 |"
    seen: list = []
    revised = revise_table_passage("R1 の標準は 100円 です。", [table], _reviser("R1 の標準は 80円 です。", seen))
    assert revised is not None and revised.text == "R1 の標準は 80円 です。"
    assert seen[0][1]["corrections"] == [{"wrong": "100円", "correct": "80円"}]


def _checked_entry(text: str, quote: str):
    """見出しの語を含むセルの表を根拠に、回答の生成の検査（`grounded.verify`）を通した要点。"""
    question = "C2 の上限額は？"
    spans = grounded.tag_spans(
        question, [{"evidence_id": "E1", "source_id": "c1", "source": "m.pdf", "page": 1, "text": HEADER_WORD_TABLE, "section_path": []}]
    )
    item = GroundedItem(kind="rule", text=text, citations=[GroundedCitation(evidence_id="E1", quote=quote)])
    entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[item]), spans)
    return entries, next(entry for entry in entries if entry.item.text == text)


def test_generated_item_copying_a_row_is_not_a_table_conflict() -> None:
    """回答の生成の検査でも、表の行そのままの要点は取り違えとして外さず、値を書き換えない（#4691）。"""
    row = "| C2 | 項目乙 | 1,200円 |"
    entries, entry = _checked_entry(row, row)
    assert entry.table_conflict == ""
    assert entry.published and not entry.removed
    seen: list = []
    assert grounded.revise_table_conflicts(entries, _reviser("| C2 | 項目乙 | 項目乙 |", seen)) == 0
    assert seen == [] and entry.item.text == row


def test_generated_item_with_a_value_of_another_row_is_still_a_table_conflict() -> None:
    """同じ表で別の行の値を書いた要点は、回答の生成の検査でも従来どおり外して表の値に直す。"""
    wrong = "| C2 | 項目乙 | 1,000円 |"
    entries, entry = _checked_entry(wrong, "| C2 | 項目乙 | 1,200円 |")
    assert entry.removed and entry.table_conflict
    assert grounded.revise_table_conflicts(entries, _reviser("| C2 | 項目乙 | 1,200円 |")) == 1
    assert entry.published and not entry.removed and entry.item.text == "| C2 | 項目乙 | 1,200円 |"


def test_generated_item_is_corrected_and_kept_with_a_mark() -> None:
    """回答の生成で、表の対応を取り違えた説明を正しい値に直して本文に残し、直した印と前後を出す。"""
    question = "区分Ⅱで選ぶ選択肢の値は？"
    spans = grounded.tag_spans(
        question, [{"evidence_id": "E1", "source_id": "c1", "source": "m.pdf", "page": 1, "text": TABLE, "section_path": []}]
    )
    item = GroundedItem(kind="rule", text=WRONG, citations=[GroundedCitation(evidence_id="E1", quote="区分Ⅱ 02")])
    entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[item]), spans)
    entry = next(entry for entry in entries if entry.item.text == WRONG)
    assert entry.removed and entry.table_conflict
    assert grounded.revise_table_conflicts(entries, _reviser(REVISED)) == 1
    assert entry.published and not entry.removed and entry.item.text == REVISED
    assert [mark["code"] for mark in entry.marks] == ["table_corrected"]
    assert entry.marks[0]["detail"].startswith(f"「{WRONG}」を根拠の表の値に直しました")
    # 直せなければ外したまま。
    entries, _ = grounded.verify(question, GroundedDraft(summary="", confidence="high", items=[item]), spans)
    assert grounded.revise_table_conflicts(entries, _reviser(RuntimeError("x"))) == 0
    assert next(entry for entry in entries if entry.item.text == WRONG).removed
