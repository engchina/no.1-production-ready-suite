"""回答の段落の分け方と位置・引用の照合の規則（Agent と共有の契約の事例。モデルは呼ばない）。"""

import json
from pathlib import Path

import pytest

# 段落の分け方の事例（RAG と Agent の両方のテストが読む。#1455）。
SENTENCE_CASES = json.loads(
    (Path(__file__).resolve().parents[4] / "platform/contracts/answer-passages/sentence-splits.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", SENTENCE_CASES["cases"], ids=lambda case: case["id"])
def test_passages_are_not_split_inside_brackets(case: dict) -> None:
    """括弧の中の「。」で段落を分けず、太字の印・閉じ括弧だけの断片を作らない（#1455）。"""
    from rag_engine.generation.operation_audit import answer_passage_ranges

    line = case["line"]
    assert [line[start:end] for _id, start, end in answer_passage_ranges(line)] == case["passages"]


def test_unverified_section_titles_match_the_builtin_language_pack() -> None:
    """回答の末尾の節（回答に入れられなかった範囲・省いた処理）の見出しは、共有の語彙
    （platform/contracts/answer-passages/vocabulary.json の unverified_section_titles）と同じ（#3240。
    Agent は RAG の言語資源を読まず、契約の語彙でこの節を見分ける）。"""
    from rag_parser_core.language import builtin_pack

    vocabulary = json.loads((Path(__file__).resolve().parents[4] / "platform/contracts/answer-passages/vocabulary.json").read_text("utf-8"))
    pack = builtin_pack()
    titles = [pack.text("answer_labels", key) for key in ("gaps_section", "unverified_section", "deadline_section")]

    assert vocabulary["unverified_section_titles"] == titles


def test_passage_ranges_locate_each_passage_in_the_answer() -> None:
    """段落の位置（``answer_passage_ranges``）は、回答の中のその段落の原文を指す（#3240。MCP の
    ``rag_validate_answer`` の ``answer_range``）。複数の行・行頭の空白・空の行・600 文字を超える文を含む。"""
    from rag_engine.generation.operation_audit import answer_passage_ranges

    long_sentence = "あ" * 650 + "。"
    answer = "\n".join(
        [
            *(case["line"] for case in SENTENCE_CASES["cases"]),
            "",
            "   見出し",
            f"  {long_sentence}続きの文です。",
            "  「引用の文です。引用の続きです。」",
        ]
    )
    ranges = answer_passage_ranges(answer)

    assert [passage_id for passage_id, _start, _end in ranges] == [f"A{index}" for index in range(1, len(ranges) + 1)]
    assert [answer[start:end] for _id, start, end in ranges][-4:] == [
        "あ" * 600,
        "あ" * 50 + "。",
        "続きの文です。",
        "「引用の文です。引用の続きです。」",
    ]


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        ("値は **43** です。", "値は 42 です。"),
        ("対象の状態を確認します。", "別の記録を保存します。"),
        ("式は 23 です。", "式は 2*3 です。"),
        ("値は 23 です。", "値は 2**3 です。"),
    ],
)
def test_quote_format_normalization_does_not_accept_changed_content(quote: str, text: str) -> None:
    from rag_engine.generation.grounded import quote_in_text

    assert not quote_in_text(quote, text)
