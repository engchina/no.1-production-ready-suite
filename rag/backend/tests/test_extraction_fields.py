"""schema 駆動 field 抽出(PoweRAG/LangExtract 由来)の単体テスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.main import app
from app.rag import extraction_field_adapter as fields_mod
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    extract_fields_from_extraction,
    parse_extraction_fields,
)
from app.schemas.extraction import DocumentElement, ExtractionField, StructuredExtraction
from tests.support import AsgiTestClient

client = AsgiTestClient(app)

_DEFS = [
    FieldDefinition(name="請求書番号", description="invoice no", value_type="string"),
    FieldDefinition(name="合計金額", description="total", value_type="number"),
]


@pytest.fixture(autouse=True)
def _isolated_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fields_mod.FIELD_SCHEMA_FILE_ENV, str(tmp_path / "extraction-fields.json"))


def test_parse_extraction_fields_handles_code_fence_and_unknown_names() -> None:
    raw = (
        "```json\n"
        '[{"name": "請求書番号", "value": "INV-1", "confidence": 0.9}, '
        '{"name": "未定義", "value": "x"}, '
        '{"name": "合計金額", "value": "1000"}]\n'
        "```"
    )
    parsed = parse_extraction_fields(raw, _DEFS)
    assert [(f.name, f.value, f.value_type) for f in parsed] == [
        ("請求書番号", "INV-1", "string"),
        ("合計金額", "1000", "number"),
    ]
    assert parsed[0].confidence == 0.9


def test_parse_drops_duplicates_and_empty_values() -> None:
    raw = (
        '[{"name":"請求書番号","value":"A"},'
        '{"name":"請求書番号","value":"B"},'
        '{"name":"合計金額","value":"  "}]'
    )
    parsed = parse_extraction_fields(raw, _DEFS)
    assert [(f.name, f.value) for f in parsed] == [("請求書番号", "A")]


def test_parse_returns_empty_on_garbage() -> None:
    assert parse_extraction_fields("not json at all", _DEFS) == []
    assert parse_extraction_fields('{"name":"x"}', _DEFS) == []


def test_parse_clamps_out_of_range_confidence() -> None:
    raw = '[{"name":"請求書番号","value":"A","confidence":5}]'
    assert parse_extraction_fields(raw, _DEFS)[0].confidence is None


@pytest.mark.anyio
async def test_extract_fields_appends_fields_and_searchable_elements() -> None:
    extraction = StructuredExtraction(
        raw_text="請求書 番号 INV-1 合計 1000円",
        elements=[DocumentElement(kind="text", text="本文", order=0, element_id="e0")],
    )

    async def _extract(text: str, defs: list[FieldDefinition]) -> list[ExtractionField]:
        return [ExtractionField(name="請求書番号", value="INV-1", value_type="string")]

    result = await extract_fields_from_extraction(extraction, _DEFS, _extract)
    assert [f.name for f in result.fields] == ["請求書番号"]
    field_elements = [e for e in result.elements if e.metadata.get("extracted_field")]
    assert len(field_elements) == 1
    assert field_elements[0].content_kind == "field"
    assert field_elements[0].metadata.get("field_name") == "請求書番号"
    assert "INV-1" in field_elements[0].text


@pytest.mark.anyio
async def test_extract_fields_noop_without_schema_or_text() -> None:
    extraction = StructuredExtraction(raw_text="本文あり")

    async def _extract(text: str, defs: list[FieldDefinition]) -> list[ExtractionField]:
        return [ExtractionField(name="x", value="y")]

    # field 定義が空なら抽出器を呼ばず no-op。
    assert await extract_fields_from_extraction(extraction, [], _extract) is extraction


def test_field_schema_store_save_load_round_trip() -> None:
    fields_mod.save_field_schema(_DEFS)
    loaded = fields_mod.load_field_schema()
    assert [f.name for f in loaded.fields] == ["請求書番号", "合計金額"]


def test_field_schema_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="重複"):
        fields_mod.save_field_schema([FieldDefinition(name="a"), FieldDefinition(name="A")])


def test_field_round_trips_through_document_payload_only_when_present() -> None:
    empty = StructuredExtraction(raw_text="本文")
    assert "fields" not in empty.to_document_payload()
    with_fields = empty.model_copy(
        update={"fields": [ExtractionField(name="請求書番号", value="INV-1")]}
    )
    payload = with_fields.to_document_payload()
    assert payload["fields"]
    restored = StructuredExtraction.model_validate(payload)
    assert restored.fields[0].value == "INV-1"


def test_extraction_fields_settings_api_get_and_patch() -> None:
    get_resp = client.get("/api/settings/extraction-fields")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["fields"] == []

    patch_resp = client.patch(
        "/api/settings/extraction-fields",
        json={"fields": [{"name": "請求書番号", "description": "invoice", "value_type": "string"}]},
    )
    assert patch_resp.status_code == 200
    assert [f["name"] for f in patch_resp.json()["data"]["fields"]] == ["請求書番号"]
    # 永続化を別 GET で確認。
    assert len(client.get("/api/settings/extraction-fields").json()["data"]["fields"]) == 1


def test_extraction_fields_settings_api_rejects_duplicate() -> None:
    resp = client.patch(
        "/api/settings/extraction-fields",
        json={"fields": [{"name": "a"}, {"name": "a"}]},
    )
    assert resp.status_code == 422
