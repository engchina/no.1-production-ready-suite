"""CSV→構造化 JSON 変換の決定論検証。"""

from __future__ import annotations

import json

from app.converters import convert


def _records(source: bytes, profile: str = "csv_to_json") -> dict:
    outcome = convert(source, "text/csv", profile, None)
    assert outcome.converted is True
    assert outcome.derived_bytes is not None
    return json.loads(outcome.derived_bytes.decode("utf-8"))


def test_csv_to_json_records_with_header() -> None:
    payload = _records(b"name,age\nAlice,30\nBob,25\n")
    assert payload["columns"] == ["name", "age"]
    assert payload["row_count"] == 2
    assert payload["rows"][0] == {"name": "Alice", "age": "30"}
    assert payload["rows"][1] == {"name": "Bob", "age": "25"}


def test_csv_to_json_is_deterministic() -> None:
    source = b"a,b\n1,2\n3,4\n"
    first = convert(source, "text/csv", "csv_to_json", None).derived_bytes
    second = convert(source, "text/csv", "csv_to_json", None).derived_bytes
    assert first == second


def test_csv_to_json_handles_short_rows_and_duplicate_headers() -> None:
    payload = _records(b"id,id,note\n1,2\n")
    # 重複ヘッダは決定論で一意化、欠損セルは空文字で補完。
    assert payload["columns"] == ["id", "id_1", "note"]
    assert payload["rows"][0] == {"id": "1", "id_1": "2", "note": ""}


def test_csv_to_json_detects_semicolon_delimiter() -> None:
    payload = _records(b"x;y\n10;20\n")
    assert payload["columns"] == ["x", "y"]
    assert payload["rows"][0] == {"x": "10", "y": "20"}


def test_non_csv_profile_passes_through() -> None:
    outcome = convert(b"a,b\n1,2\n", "text/csv", "office_to_pdf", None)
    assert outcome.converted is False


def test_empty_input_passes_through() -> None:
    outcome = convert(b"", "text/csv", "csv_to_json", None)
    assert outcome.converted is False
    assert "csv_empty" in outcome.warnings
