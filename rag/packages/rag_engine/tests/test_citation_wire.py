"""整数行範囲の生成契約と、文字列範囲の履歴を分離する合成回帰。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from rag_engine.generation.citation_wire import bind_wire_range, canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedCitation, GroundedDraft, GroundedItem


def payload(citations=None):
    return {
        "items": [
            {
                "kind": "operation",
                "text": "対象を選択して実行します。",
                "citations": citations if citations is not None else [{"evidence_id": "E1", "line_start": 2, "line_end": 3}],
            }
        ],
        "summary": "対象を選択して実行します。",
        "confidence": "low",
    }


def test_wire_has_only_required_nullable_integer_locators():
    wire = generation_draft_wire_model()
    citation = wire.model_json_schema()["$defs"]["GroundedCitationWire"]
    assert set(citation["properties"]) == set(citation["required"]) == {"evidence_id", "line_start", "line_end"}
    assert citation["additionalProperties"] is False
    for key in ("line_start", "line_end"):
        assert citation["properties"][key]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    assert wire is generation_draft_wire_model()


@pytest.mark.parametrize("value", ["1", "p.1", "対象を選択", True, 1.5])
def test_wire_does_not_coerce_invalid_types(value):
    raw = payload()
    raw["items"][0]["citations"][0]["line_start"] = value
    with pytest.raises(ValidationError):
        generation_draft_wire_model().model_validate(raw)


@pytest.mark.parametrize("key", ["line_start", "line_end"])
def test_missing_key_is_not_implicitly_null(key):
    raw = payload()
    del raw["items"][0]["citations"][0][key]
    with pytest.raises(ValidationError):
        generation_draft_wire_model().model_validate(raw)


@pytest.mark.parametrize("key", ["lines", "quote"])
def test_wire_does_not_allow_free_locator_or_quote(key):
    raw = payload()
    raw["items"][0]["citations"][0][key] = "1"
    with pytest.raises(ValidationError):
        generation_draft_wire_model().model_validate(raw)


def test_factory_preserves_other_fields_and_constraints():
    wire = generation_draft_wire_model()
    for key, field in GroundedDraft.model_fields.items():
        if key != "items":
            assert wire.model_fields[key].asdict() == field.asdict()
    item_model = wire.model_fields["items"].annotation.__args__[0]
    for key, field in GroundedItem.model_fields.items():
        if key != "citations":
            assert item_model.model_fields[key].asdict() == field.asdict()
    raw = payload()
    raw["items"][0]["text"] = ""
    with pytest.raises(ValidationError):
        wire.model_validate(raw)


def test_conversion_preserves_non_locator_fields_and_marks_private_range():
    raw = payload()
    raw["items"][0].update(request_id="Q1", applies="conditional", condition="対象が選択済み")
    original = deepcopy(raw)
    draft = canonical_generation_draft(generation_draft_wire_model().model_validate(raw))
    assert type(draft) is GroundedDraft and raw == original
    citation = draft.items[0].citations[0]
    assert (citation.lines, citation._wire_range, citation.quote) == ("2-3", (2, 3), "")
    assert draft.items[0].request_id == "Q1" and draft.items[0].condition == "対象が選択済み"
    assert draft.items[0].applies == "conditional" and draft.summary == raw["summary"]
    assert "_wire_range" not in draft.model_dump_json()
    assert "_wire_range" not in str(GroundedCitation.model_json_schema())


@pytest.mark.parametrize("lines", ["2-3", "対象を選択", "p.2", ""])
def test_legacy_draft_is_not_rewritten(lines):
    draft = GroundedDraft(
        items=[GroundedItem(kind="operation", text="対象を選択します。", citations=[GroundedCitation(evidence_id="E1", lines=lines)])],
        summary="",
        confidence="low",
    )
    assert canonical_generation_draft(draft) is draft
    assert draft.items[0].citations[0].lines == lines
    assert draft.items[0].citations[0]._wire_range is None


@pytest.mark.parametrize(
    "start,end,count,numbered,lines,reason,whole",
    [
        (2, 3, 4, True, "2-3", None, False),
        (1, 1, 1, False, "1", None, False),
        (None, None, 1, False, "", None, True),
        (None, None, 4, True, "", "citation_range_missing", False),
        (None, 2, 4, True, "", "citation_range_invalid", False),
        (2, None, 4, True, "", "citation_range_invalid", False),
        (3, 2, 4, True, "3-2", "citation_range_invalid", False),
        (0, 1, 4, True, "0-1", "citation_range_out_of_bounds", False),
        (-2, -1, 4, True, "-2--1", "citation_range_out_of_bounds", False),
        (1, 5, 4, True, "1-5", "citation_range_out_of_bounds", False),
        (1, 1, 0, False, "", "citation_extraction_failed", False),
    ],
)
def test_binding_keeps_each_failure_distinct_without_clamping(start, end, count, numbered, lines, reason, whole):
    result = bind_wire_range(start, end, unit_count=count, numbered=numbered)
    assert (result.lines, result.reason_code, result.whole_text) == (lines, reason, whole)


def test_bad_ranges_do_not_discard_other_citations_or_body():
    raw = payload(
        [
            {"evidence_id": "E1", "line_start": 0, "line_end": 999},
            {"evidence_id": "E2", "line_start": 1, "line_end": 1},
            {"evidence_id": "E3", "line_start": None, "line_end": 2},
        ]
    )
    draft = canonical_generation_draft(generation_draft_wire_model().model_validate(raw))
    item = draft.items[0]
    assert item.text == raw["items"][0]["text"] and len(item.citations) == 3
    results = [bind_wire_range(*citation._wire_range, unit_count=3, numbered=True) for citation in item.citations]
    assert [result.reason_code for result in results] == ["citation_range_out_of_bounds", None, "citation_range_invalid"]
    assert item.citations[1].evidence_id == "E2" and item.citations[1].lines == "1"


def test_oversized_integer_keeps_original_range_without_discarding_valid_citation():
    huge = 10**30
    raw = payload([{"evidence_id": "E1", "line_start": 1, "line_end": huge}, {"evidence_id": "E2", "line_start": 1, "line_end": 1}])
    draft = canonical_generation_draft(generation_draft_wire_model().model_validate(raw))
    assert len(draft.items[0].citations) == 2
    bad, good = draft.items[0].citations
    assert bad._wire_range == (1, huge) and good.lines == "1"
    assert bind_wire_range(*bad._wire_range, unit_count=2, numbered=True).reason_code == "citation_range_out_of_bounds"
    assert draft.items[0].text == raw["items"][0]["text"]


def test_actual_sdk_request_serializes_the_integer_contract_and_parses_locally():
    import json

    import httpx
    from openai import OpenAI

    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "response-synthetic",
                "object": "response",
                "created_at": 0,
                "model": "synthetic-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "message-synthetic",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(payload()), "annotations": []}],
                    }
                ],
            },
        )

    with OpenAI(
        api_key="synthetic-key",
        base_url="https://synthetic.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(reply)),
    ) as client:
        response = client.responses.parse(model="synthetic-model", input="合成の資料", text_format=generation_draft_wire_model())
    assert len(requests) == 1
    format_ = requests[0]["text"]["format"]
    assert format_["strict"] is True and format_["type"] == "json_schema"
    citation = format_["schema"]["$defs"]["GroundedCitationWire"]
    assert set(citation["properties"]) == set(citation["required"]) == {"evidence_id", "line_start", "line_end"}
    assert citation["properties"]["line_start"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    draft = canonical_generation_draft(response.output_parsed)
    assert draft.items[0].citations[0]._wire_range == (2, 3)
