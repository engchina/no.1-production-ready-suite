"""schema 駆動 field 抽出(PoweRAG/LangExtract 由来)の単体テスト。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from app.config import Settings
from app.main import app
from app.rag import extraction_field_adapter as fields_mod
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    FieldSchemaStore,
    extract_fields_from_extraction,
    field_schema_from_json,
    normalize_field_value,
    parse_extraction_fields,
    resolve_field_definitions,
    validate_field_schema,
)
from app.rag.ingestion import IngestionPipeline
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


def test_extraction_fields_settings_api_returns_saved_schema() -> None:
    get_resp = client.get("/api/settings/extraction-fields")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["uses_standard"] is True

    fields_mod.save_field_schema(
        [FieldDefinition(name="請求書番号", description="invoice", value_type="string")]
    )

    data = client.get("/api/settings/extraction-fields").json()["data"]
    assert [f["name"] for f in data["fields"]] == ["請求書番号"]
    assert data["uses_standard"] is False


# ---- 標準の項目（未保存の環境の全体の既定。#556）----

_STANDARD_NAMES = ["文書の種類", "文書タイトル", "発行日・作成日", "発行元・作成部署"]


def test_standard_fields_are_the_default_until_saved() -> None:
    """一度も保存していない(ファイルが無い)環境は、標準の 4 項目を全体の既定にする。"""
    assert fields_mod.load_saved_field_schema() is None
    loaded = fields_mod.load_field_schema().fields
    assert [field.name for field in loaded] == _STANDARD_NAMES
    assert [field.value_type for field in loaded] == ["string", "string", "date", "string"]
    # 説明はモデルへの指示になるため、すべて空でなく、保存の上限(500 字)に収まる。
    assert all(0 < len(field.description) <= 500 for field in loaded)
    # 標準の項目は保存の検証(重複・上限)も通る。
    assert validate_field_schema(loaded).fields == loaded
    # 呼び出し側が変更しても定数は変わらない。
    loaded[0].name = "変更"
    assert fields_mod.STANDARD_FIELD_DEFINITIONS[0].name == "文書の種類"


def test_saved_empty_schema_is_respected_not_replaced_by_standard() -> None:
    """意図して空を保存した環境は、標準の項目を使わない(項目を抽出しない)。"""
    fields_mod.save_field_schema([])
    saved = fields_mod.load_saved_field_schema()
    assert saved is not None
    assert saved.fields == []
    assert fields_mod.load_field_schema().fields == []
    data = client.get("/api/settings/extraction-fields").json()["data"]
    assert data["fields"] == []
    assert data["uses_standard"] is False


def test_broken_saved_schema_is_empty_not_standard(tmp_path: Path) -> None:
    """壊れたファイルは「保存済み」として安全に空にし、標準の項目へ黙って切り替えない。"""
    (tmp_path / "extraction-fields.json").write_text("{broken", encoding="utf-8")
    assert fields_mod.load_field_schema().fields == []


def test_reset_extraction_fields_api_returns_to_standard(tmp_path: Path) -> None:
    """「標準の項目に戻す」(DELETE)は保存した定義を消し、標準の項目を返す。"""
    schema_file = tmp_path / "extraction-fields.json"
    fields_mod.save_field_schema(_DEFS)
    assert schema_file.exists()

    resp = client.delete("/api/settings/extraction-fields")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["uses_standard"] is True
    assert [field["name"] for field in data["fields"]] == _STANDARD_NAMES
    assert not schema_file.exists()
    assert [f.name for f in fields_mod.load_field_schema().fields] == _STANDARD_NAMES
    # 保存していないときに呼んでも成功する(何度呼んでも同じ結果)。
    again = client.delete("/api/settings/extraction-fields")
    assert again.status_code == 200
    assert again.json()["data"] == data


def test_reset_extraction_fields_api_reports_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail() -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("app.api.routes.settings.reset_field_schema", _fail)
    resp = client.delete("/api/settings/extraction-fields")
    assert resp.status_code == 500
    assert "標準の項目に戻せませんでした" in resp.text


# ---- ナレッジベースごとの項目の定義（#548）----

_CONTRACT = [
    FieldDefinition(name="契約日", value_type="date"),
    FieldDefinition(name="金額", value_type="number"),
]
_MANUAL = [
    FieldDefinition(name="製品名", value_type="string"),
    FieldDefinition(name="金額", value_type="string"),
]


def test_resolve_uses_knowledge_base_definitions_over_default() -> None:
    """KB に定義があればその定義だけを使い、全体の既定は混ぜない。"""
    assert resolve_field_definitions([_CONTRACT], _DEFS) == _CONTRACT


def test_resolve_falls_back_to_default_without_definitions_or_knowledge_bases() -> None:
    """KB に定義が無い(None)とき、所属 KB が無いときは全体の既定を使う(既存の挙動)。"""
    assert resolve_field_definitions([None], _DEFS) == _DEFS
    assert resolve_field_definitions([], _DEFS) == _DEFS


def test_resolve_empty_knowledge_base_definition_disables_extraction() -> None:
    """KB の定義が空の list なら、その KB では抽出しない(既定には戻さない)。"""
    assert resolve_field_definitions([[]], _DEFS) == []


def test_resolve_unions_multiple_knowledge_bases_first_definition_wins() -> None:
    """複数の KB に属する文書は和集合。同じ項目名(大文字小文字を区別しない)は先の KB の定義。"""
    merged = resolve_field_definitions([_CONTRACT, _MANUAL, None], _DEFS)
    assert [(f.name, f.value_type) for f in merged] == [
        ("契約日", "date"),
        ("金額", "number"),
        ("製品名", "string"),
        ("請求書番号", "string"),
        ("合計金額", "number"),
    ]


def test_validate_field_schema_rejects_duplicates_and_over_limit() -> None:
    with pytest.raises(ValueError, match="重複"):
        validate_field_schema([FieldDefinition(name="契約日"), FieldDefinition(name=" 契約日 ")])
    too_many = [FieldDefinition(name=f"項目{index}") for index in range(51)]
    with pytest.raises(ValueError, match="50"):
        validate_field_schema(too_many)
    assert len(validate_field_schema(too_many[:50]).fields) == 50


def test_field_schema_from_json_distinguishes_unset_empty_and_broken() -> None:
    """NULL と壊れた値は None(既定に従う)、空の定義は [](抽出しない)。"""
    assert field_schema_from_json(None) is None
    assert field_schema_from_json("{not json") is None
    assert field_schema_from_json({"version": 1, "fields": []}) == []
    stored = FieldSchemaStore(fields=_CONTRACT)
    assert field_schema_from_json(stored.model_dump_json()) == _CONTRACT
    assert field_schema_from_json(stored.model_dump()) == _CONTRACT


@pytest.mark.parametrize(
    ("value_type", "raw", "expected"),
    [
        ("number", "1,200,000", "1200000"),
        ("number", "１２．５", "12.5"),
        ("number", "約100万円", "約100万円"),
        ("date", "2025年4月1日", "2025-04-01"),
        ("date", "2025/4/1", "2025-04-01"),
        ("date", "2025-02-30", "2025-02-30"),
        ("bool", "はい", "true"),
        ("bool", "False", "false"),
        ("string", " A-1 ", " A-1 "),
    ],
)
def test_normalize_field_value_makes_values_comparable(
    value_type: fields_mod.FieldValueType, raw: str, expected: str
) -> None:
    """型に寄せられる値は比較できる形へ、寄せられない値はそのまま(#549)。"""
    assert normalize_field_value(value_type, raw) == expected


def test_parse_extraction_fields_normalizes_typed_values() -> None:
    raw = '[{"name":"合計金額","value":"1,000"}]'
    assert parse_extraction_fields(raw, _DEFS)[0].value == "1000"


class _FieldSetOracle:
    """文書が属する KB の項目の定義だけを返す fake。"""

    def __init__(self, field_sets: list[list[FieldDefinition] | None]) -> None:
        self.field_sets = field_sets
        self.document_ids: list[str] = []

    async def list_document_extraction_field_sets(
        self, document_id: str
    ) -> list[list[FieldDefinition] | None]:
        self.document_ids.append(document_id)
        return self.field_sets


class _PromptRecorder:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, text: str) -> str:
        self.prompts.append(prompt)
        return '[{"name":"契約日","value":"2025年4月1日"},{"name":"請求書番号","value":"INV-1"}]'


def _pipeline(oracle: _FieldSetOracle, vlm: _PromptRecorder) -> IngestionPipeline:
    return IngestionPipeline(
        vlm=cast(Any, vlm),
        genai=cast(Any, object()),
        oracle=cast(Any, oracle),
        object_storage=cast(Any, object()),
        document_understanding=cast(Any, object()),
        speech=cast(Any, object()),
        settings=Settings().model_copy(update={"rag_field_extraction_enabled": True}),
    )


@pytest.mark.anyio
async def test_ingestion_extracts_with_knowledge_base_definitions() -> None:
    """取込は文書が属する KB の定義で抽出し、全体の既定の項目は渡さない。"""
    fields_mod.save_field_schema(_DEFS)
    oracle = _FieldSetOracle([_CONTRACT])
    vlm = _PromptRecorder()
    result = await _pipeline(oracle, vlm)._attach_extraction_fields(
        "trace", StructuredExtraction(raw_text="契約日は 2025年4月1日"), document_id="doc-1"
    )
    assert oracle.document_ids == ["doc-1"]
    assert "契約日" in vlm.prompts[0]
    assert "請求書番号" not in vlm.prompts[0]
    # 定義外の項目(全体の既定の請求書番号)は捨て、date は YYYY-MM-DD にそろえる。
    assert [(f.name, f.value, f.value_type) for f in result.fields] == [
        ("契約日", "2025-04-01", "date")
    ]


@pytest.mark.anyio
async def test_ingestion_extracts_with_default_when_knowledge_base_has_no_definitions() -> None:
    """KB に定義が無ければ全体の既定で抽出する(既存環境の挙動を変えない)。"""
    fields_mod.save_field_schema(_DEFS)
    vlm = _PromptRecorder()
    result = await _pipeline(_FieldSetOracle([None]), vlm)._attach_extraction_fields(
        "trace", StructuredExtraction(raw_text="請求書 INV-1"), document_id="doc-1"
    )
    assert "請求書番号" in vlm.prompts[0]
    assert [f.name for f in result.fields] == ["請求書番号"]


class _StandardResponder(_PromptRecorder):
    async def generate(self, prompt: str, text: str) -> str:
        self.prompts.append(prompt)
        return (
            '[{"name":"文書の種類","value":"規程"},'
            '{"name":"発行日・作成日","value":"2026年4月1日"}]'
        )


@pytest.mark.anyio
async def test_ingestion_uses_standard_fields_until_saved() -> None:
    """全体の既定を保存していない環境の取込は、標準の項目で抽出する(#556)。"""
    vlm = _StandardResponder()
    result = await _pipeline(_FieldSetOracle([None]), vlm)._attach_extraction_fields(
        "trace", StructuredExtraction(raw_text="就業規程 2026年4月1日 総務部"), document_id="doc-1"
    )
    assert all(name in vlm.prompts[0] for name in _STANDARD_NAMES)
    assert [(f.name, f.value, f.value_type) for f in result.fields] == [
        ("文書の種類", "規程", "string"),
        ("発行日・作成日", "2026-04-01", "date"),
    ]


@pytest.mark.anyio
async def test_ingestion_skips_extraction_when_empty_default_was_saved() -> None:
    """空の全体の既定を保存した環境では、標準の項目を使わず LLM を呼ばない(#556)。"""
    fields_mod.save_field_schema([])
    vlm = _StandardResponder()
    extraction = StructuredExtraction(raw_text="本文")
    result = await _pipeline(_FieldSetOracle([None]), vlm)._attach_extraction_fields(
        "trace", extraction, document_id="doc-1"
    )
    assert result is extraction
    assert vlm.prompts == []


@pytest.mark.anyio
async def test_ingestion_skips_extraction_when_no_definitions_apply() -> None:
    """KB の定義が空で既定も無ければ LLM を呼ばない。"""
    vlm = _PromptRecorder()
    extraction = StructuredExtraction(raw_text="本文")
    result = await _pipeline(_FieldSetOracle([[]]), vlm)._attach_extraction_fields(
        "trace", extraction, document_id="doc-1"
    )
    assert result is extraction
    assert vlm.prompts == []
