"""項目抽出の番号、重複、指示、schema を含む本番の要求で分割する。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pr_backend_core.generation_input import complete_input_measurement
from rag_parser_core.oci_enterprise_ai import OciEnterpriseAiConfig, measure_llm_request

from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.config import Settings
from app.rag import extraction_field_adapter as fields_module
from app.rag import ingestion as ingestion_module
from app.rag.extraction_field_adapter import (
    FieldDefinition,
    FieldExtractionFailed,
    UnitExtraction,
    extract_fields_from_extraction,
)
from app.rag.field_extraction_units import (
    FieldExtractionUnit,
    FieldSentenceTooLarge,
    plan_field_extraction_units,
    split_field_extraction_unit,
)
from app.rag.ingestion import IngestionPipeline, _field_extraction_failure_message
from app.schemas.extraction import DocumentElement, StructuredExtraction
from tests.test_field_extraction_units import _MemoryStorage, _NoKbOracle
from tests.test_xml_paragraph_boundaries import _sample

_SCHEMA = {
    "type": "object",
    "properties": {"code": {"type": "string", "description": "合成の項目"}},
}


def _config() -> OciEnterpriseAiConfig:
    return OciEnterpriseAiConfig(default_model_id="test-model", text_max_input_tokens=2_000)


def _fits(text: str) -> bool:
    return measure_llm_request(
        _config(),
        "",
        text,
        system_prompt="資料から読み取る。",
        response_schema=_SCHEMA,
        response_schema_name="field_extraction",
    ).fits


def test_plan_writes_numbers_after_packing_complete_wire_requests() -> None:
    paragraphs = [[f"原文{i} " + "あ" * 125 + "（説明。）最後。"] for i in range(25)]
    source = _sample(*paragraphs)
    nominal = plan_field_extraction_units(source, text_budget_tokens=6_000)
    assert len(nominal) == 1 and not _fits(nominal[0].text)
    result = plan_field_extraction_units(source, text_budget_tokens=6_000, request_fits=_fits)
    assert len(result) > 1
    assert all(
        u.index == i and u.total == len(result) and _fits(u.text) for i, u in enumerate(result)
    )
    assert [a.text for u in result for a in u.atoms] == [e.text for e in source.elements]


def test_long_paragraph_and_retry_use_only_xml_sentence_boundaries() -> None:
    sentences = [f"文{i} " + "甲" * 66 + "（説明。）最後。" for i in range(8)]
    source = _sample(sentences)
    parent = plan_field_extraction_units(source, text_budget_tokens=4000)[0]
    refined = split_field_extraction_unit(parent, text_budget_tokens=400, request_fits=_fits)
    assert len(refined) > 1 and all(_fits(u.text) for u in refined)
    assert "".join(a.text for u in refined for a in u.atoms) == source.elements[0].text
    for sentence in sentences:
        assert any(sentence in u.text for u in refined)


@pytest.mark.asyncio
async def test_initial_wire_overflow_stops_before_model_or_checkpoint() -> None:
    # 1 文字 = 1 token と数え、第 2 要素だけで上限 2000 の 3/4 を超える(#5813)。
    source = _sample(["合成の短文。"], ["漢" * 2_000 + "（説明。）"])
    seen = []

    async def send(unit: FieldExtractionUnit) -> UnitExtraction:
        seen.append(unit.index)
        return UnitExtraction(candidates=[])

    with pytest.raises(FieldExtractionFailed, match="input_too_large") as raised:
        await extract_fields_from_extraction(
            source,
            [FieldDefinition(name="見本")],
            send,
            text_budget_tokens=4000,
            request_fits=_fits,
        )
    assert seen == []
    assert "第 2 要素" in _field_extraction_failure_message(raised.value, checkpoints=False)


@pytest.mark.parametrize("nominal", [96000, 1000])
def test_plain_unsplittable_sentence_keeps_original_range(nominal: int) -> None:
    prefix = "見出し。\n\n"
    text = prefix + "x" * 96_001
    doc = StructuredExtraction(elements=[DocumentElement(text=text, page_number=7)])
    with pytest.raises(FieldSentenceTooLarge) as raised:
        plan_field_extraction_units(
            doc, text_budget_tokens=nominal, request_fits=lambda t: len(t.encode()) <= 72_000
        )
    assert (raised.value.element_number, raised.value.page_number) == (1, 7)
    assert text[raised.value.start : raised.value.end] == "x" * 96_001


def test_single_paragraph_can_exceed_initial_share_if_full_input_fits() -> None:
    text = "字" * 500 + "。"
    doc = StructuredExtraction(elements=[DocumentElement(text=text)])
    result = plan_field_extraction_units(
        doc, text_budget_tokens=256, request_fits=lambda t: len(t.encode()) <= 1800
    )
    assert [u.text for u in result] == [text]


def test_plain_long_paragraph_keeps_soft_wrap_brackets_and_source_offsets() -> None:
    sentence = "前半（中で。説明）\n折り返し" + "字" * 105 + "終。"
    paragraph = sentence * 4
    text = "一段落。\n\n" + paragraph + "\n\n次段落。"
    doc = StructuredExtraction(elements=[DocumentElement(text=text)])
    result = plan_field_extraction_units(
        doc, text_budget_tokens=3000, request_fits=lambda t: len(t.encode()) <= 800
    )
    assert len(result) > 1
    assert "".join(a.text for u in result for a in u.atoms) == text
    for unit in result:
        for atom in (*unit.atoms, *unit.overlap):
            assert atom.text == text[atom.source_start : atom.source_start + len(atom.text)]
            assert "中で。" not in atom.text or sentence in atom.text


async def test_ingestion_packs_the_actual_request_and_reuses_only_accepted_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap = 8000
    cfg = OciEnterpriseAiConfig(default_model_id="test-model", text_max_input_tokens=cap)
    monkeypatch.setattr(ingestion_module, "enterprise_ai_default_model_id", lambda *_: "test-model")
    monkeypatch.setattr(ingestion_module, "enterprise_ai_max_input_tokens", lambda *_: cap)
    monkeypatch.setattr("app.clients.oci_enterprise_ai.config_from_settings", lambda *_, **__: cfg)
    monkeypatch.setenv(fields_module.FIELD_SCHEMA_FILE_ENV, str(tmp_path / "schema.json"))
    fields_module.save_field_schema([FieldDefinition(name="合成の番号")])
    client = OciEnterpriseAiClient(settings=Settings())
    sent = []

    async def post(
        path: str, payload: Mapping[str, Any], *, vision: bool = False
    ) -> Mapping[str, Any]:
        assert not vision
        assert complete_input_measurement(payload, limit_tokens=cap).fits
        sent.append(payload)
        return {"output_text": '{"fields":[]}'}

    monkeypatch.setattr(client, "_post_enterprise_ai", post)
    pipe = IngestionPipeline(
        vlm=client,
        settings=Settings(
            rag_field_extraction_enabled=True, rag_field_extraction_input_token_percent=90
        ),
        oracle=_NoKbOracle(),  # type: ignore[arg-type]
        object_storage=_MemoryStorage(),  # type: ignore[arg-type]
    )
    doc = StructuredExtraction(
        elements=[DocumentElement(text=f"第{i}番" + "字" * 1_400 + "。") for i in range(6)]
    )
    first = await pipe._attach_extraction_fields("trace", doc, document_id="sample")
    count = len(sent)
    assert count > 1
    second = await pipe._attach_extraction_fields("trace", doc, document_id="sample")
    assert len(sent) == count
    key = fields_module.FIELD_EXTRACTION_OUTCOME_ARTIFACT_KEY
    assert isinstance(first.parser_artifacts[key], dict)
    outcome = second.parser_artifacts[key]
    assert isinstance(outcome, dict) and outcome["reused_unit_count"] == count
