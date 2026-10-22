"""レイアウト・表記の違いと出力指示を資料の不足にしない。"""

import pytest

from rag_engine.generation.value_grounding import unsupported_document_value_claim
from rag_engine.retrieval.task_contract import task_contract


@pytest.mark.parametrize("value", ["1万4千", "1万4000", "14,000"])
def test_mixed_numbers_match_source(value):
    assert (
        unsupported_document_value_claim(f"上限は{value}円です。", [{"origin": "document_text", "text": "上限は一万四千円です。"}], "")
        == ""
    )


def test_wrong_mixed_number_still_warns():
    assert unsupported_document_value_claim("上限は1万5千円です。", [{"origin": "document_text", "text": "上限は一万四千円です。"}], "")


def test_layout_gap_matches_source():
    assert (
        unsupported_document_value_claim("上限は48000件です。", [{"origin": "document_text", "text": "上限は四万八 千件です。"}], "") == ""
    )


@pytest.mark.parametrize(
    "instruction",
    ["資料にない値は不明と明記してください。", "確認できなければ分からないと書いてください。", "答えは不明と表記してください。"],
)
def test_output_instruction_is_not_a_document_request(instruction):
    units = task_contract("上限と期限を教えてください。" + instruction)["request_units"]
    assert units[0]["kind"] == "request"
    assert units[-1]["kind"] == "context"
    assert instruction in units[-1]["text"]


def test_factual_question_about_writing_is_kept():
    assert task_contract("記載方法を教えてください。")["request_units"][0]["kind"] == "request"


def test_question_about_a_written_value_is_still_factual():
    assert task_contract("資料に不明と明記されていますか？")["request_units"][0]["kind"] == "request"
