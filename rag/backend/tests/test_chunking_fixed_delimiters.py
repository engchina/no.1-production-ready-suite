"""固定分割符の優先度順の分割と、保存済みの値の読み方(#4733)。"""

import json
from pathlib import Path

import pytest
from dotenv import dotenv_values
from rag_parser_core.extraction import DocumentElement, StructuredExtraction
from rag_pipeline_core.chunking import (
    DEFAULT_FIXED_DELIMITERS,
    chunk_extraction_with_strategy,
    normalize_fixed_delimiters,
)

from app.api.routes.settings import _format_env_value


def _chunks(text: str, delimiters: list[str], chunk_size: int = 20) -> list[str]:
    extraction = StructuredExtraction(
        raw_text=text, elements=[DocumentElement(kind="text", text=text, page_number=1)]
    )
    chunks = chunk_extraction_with_strategy(
        extraction, strategy="fixed_delimiter", chunk_size=chunk_size, delimiter=delimiters
    )
    for chunk in chunks:
        assert text[chunk.start_offset : chunk.end_offset] == chunk.text
    return [chunk.text for chunk in chunks]


def test_lower_delimiters_only_split_oversized_pieces() -> None:
    text = "短い段落\n\n" + "あいうえおかきくけこ。" * 3
    assert _chunks(text, ["---", "\\n\\n", "。"], 25) == [
        "短い段落\n\nあいうえおかきくけこ。",
        "あいうえおかきくけこ。あいうえおかきくけこ。",
    ]


def test_first_delimiter_is_a_hard_boundary() -> None:
    assert _chunks("あ\n\nい", ["\\n\\n", "。"], 800) == ["あ", "い"]


def test_small_document_stays_whole_with_default_delimiters() -> None:
    text = "一文目。\n\n二文目。"
    assert _chunks(text, list(DEFAULT_FIXED_DELIMITERS), 800) == [text]


def test_unsplittable_piece_is_kept_over_the_limit() -> None:
    text = "あ" * 50
    assert _chunks(text, ["---", "。"], 20) == [text]


def test_single_custom_delimiter_keeps_previous_result() -> None:
    long_piece = "あ" * 50
    assert _chunks(f"{long_piece}---い", ["---"], 20) == [long_piece, "い"]


@pytest.mark.parametrize(
    ("delimiter", "expected"),
    [("。", ["一文目。", "二文目。"]), ("? ", ["Is it?", "Yes"]), ("---", ["前", "後"])],
    ids=["sentence-end", "english-sentence-end", "custom"],
)
def test_only_sentence_end_marks_stay_in_chunks(delimiter: str, expected: list[str]) -> None:
    text = {"。": "一文目。二文目。", "? ": "Is it? Yes", "---": "前---後"}[delimiter]
    assert _chunks(text, [delimiter], 800) == expected


def test_custom_delimiter_between_pieces_is_not_merged_back() -> None:
    text = ("あ" * 15) + "---" + ("い" * 3)
    assert _chunks(text, ["<<>>", "---"], 20 - 3) == ["あ" * 15, "い" * 3]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  ---  ", ["---"]),
        ("\\n\\n", ["\\n\\n"]),
        ('["a", ". ", "a"]', ["a", ". "]),
        ("[not-json", ["[not-json"]),
    ],
    ids=["legacy-string", "legacy-escape", "json-deduped", "bracket-string"],
)
def test_normalize_reads_saved_values(value: str, expected: list[str]) -> None:
    assert normalize_fixed_delimiters(value) == expected


@pytest.mark.parametrize("value", ["  ", [], ["   "], [1], ["a" * 257], None], ids=str)
def test_normalize_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="分割符"):
        normalize_fixed_delimiters(value)


def test_default_delimiters_round_trip_through_env_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    encoded = json.dumps(list(DEFAULT_FIXED_DELIMITERS), ensure_ascii=False)
    path.write_text(f"RAG_CHUNK_DELIMITER={_format_env_value(encoded)}\n", encoding="utf-8")
    assert normalize_fixed_delimiters(dotenv_values(path)["RAG_CHUNK_DELIMITER"]) == list(
        DEFAULT_FIXED_DELIMITERS
    )
