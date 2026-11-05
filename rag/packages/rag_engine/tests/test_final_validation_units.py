"""最終検証は実送達単位だけを選び、原文をコードで復元する。"""

import json
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_validation import validate_answer_claims

TEXT = "対象を選びます。\n確認を押します。"
EVIDENCE = [{"id": "synthetic-chunk-a", "source": "合成資料A", "text": TEXT}]


def _validate(*, status="supported", invalid=False, duplicate=False, cited=True, answer="確認を押します。"):
    calls = []

    def parse(system, user, settings, schema, provider_id=None):
        data = json.loads(user)
        calls.append({"system": system, "inputs": data, "schema": schema.model_json_schema()})
        properties = [v.get("properties", {}) for v in schema.model_json_schema().get("$defs", {}).values()]
        claim_properties = next(p for p in properties if "status" in p)
        assert "evidence_quote" not in claim_properties
        assert "evidence_id" not in claim_properties
        assert "source_id" not in claim_properties
        units = next(e["units"] for e in data["evidence_items"] if e["id"] == EVIDENCE[0]["id"])
        chosen = [units[-1]["unit_id"]]
        if invalid:
            chosen = ["U-not-delivered"]
        if duplicate:
            chosen *= 2
        return schema.model_validate(
            {
                "claim_checks": [
                    {
                        "answer_quote": "",
                        "answer_passage_id": p["id"],
                        "evidence_unit_ids": chosen,
                        "claim_target": "合成操作",
                        "evidence_target": "合成操作",
                        "applicability": "matched",
                        "output_fields": None,
                        "status": status,
                        "reason": "選択した原文を用いる合成判定です。",
                    }
                    for p in data["answer_passages"]
                ]
            }
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "操作を教えてください。",
            answer,
            EVIDENCE,
            settings=None,
            caller_citations=[{"start": 0, "end": len(answer), "chunk_ids": [EVIDENCE[0]["id"]]}] if cited else [],
        )
    return result, calls


def test_final_schema_selects_units_without_quote_or_source_copy():
    result, calls = _validate()
    assert len(calls) == 1
    claim = result["claim_checks"][0]
    assert claim["status"] == "supported" and claim["support_quote"] == ""
    selected = result["_selected_evidence"][0]
    assert [r["text"] for r in selected] == ["確認を押します。"]
    assert selected[0]["fragment_start"] == 0 and selected[0]["start"] == TEXT.index("確認")


@pytest.mark.parametrize("status", ["supported", "contradicted"])
def test_invalid_unit_never_binds_source_and_retains_raw_negative(status):
    result, calls = _validate(status=status, invalid=True)
    claim = result["claim_checks"][0]
    assert claim["status"] == ("citation_error" if status == "supported" else "contradicted")
    assert claim["source_id"] == "" and claim["evidence_quote"] == ""
    assert claim["audit_status"] == status and claim["reference_check"] == "audit_id_invalid"
    assert result["_selected_evidence"].get(0) == []
    assert len(calls) <= 2


def test_duplicate_selection_is_not_silently_deduplicated():
    result, _ = _validate(duplicate=True)
    assert result["claim_checks"][0]["status"] == "citation_error"
    assert result["_selected_evidence"].get(0) == []


def test_valid_units_do_not_bypass_real_document_citation_scope():
    result, _ = _validate(cited=False)
    assert result["claim_checks"][0]["status"] == "unassessed"
    assert result["claim_checks"][0]["support_check"] == "uncited_evidence"


@pytest.mark.parametrize("status", ["supported", "contradicted"])
def test_sdk_mock_transport_preserves_raw_negative_then_exact_binding_rejects_invalid_enum(status):
    from types import SimpleNamespace

    import httpx
    from openai import OpenAI

    from rag_engine.adapters.oci import _parsed_response, _response_wire_request

    calls = []

    def http(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "U-not-delivered" not in json.dumps(payload["text"]["format"]["schema"])
        data = json.loads(payload["input"][1]["content"])
        row = {
            "answer_quote": "",
            "answer_passage_id": data["answer_passages"][0]["id"],
            "evidence_unit_ids": ["U-not-delivered"],
            "claim_target": "合成操作",
            "evidence_target": "合成操作",
            "applicability": "matched",
            "output_fields": None,
            "status": status,
            "reason": "合成の固定判定です。",
        }
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "object": "response",
                "created_at": 0,
                "model": "synthetic-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "synthetic-message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps({"claim_checks": [row]}), "annotations": []}],
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(http)) as http_client:
        client = OpenAI(api_key="synthetic-only", base_url="https://synthetic.invalid/v1", http_client=http_client, max_retries=0)

        def parse(system, user, settings, schema, provider_id=None):
            wire = _response_wire_request(
                {
                    "model": "synthetic-model",
                    "input": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "text_format": schema,
                }
            )
            response = client.responses.parse(**wire)
            return _parsed_response(response, schema, SimpleNamespace(label="合成モデル"))

        with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
            result = validate_answer_claims(
                "操作を教えてください。",
                "確認を押します。",
                EVIDENCE,
                None,
                caller_citations=[{"start": 0, "end": 9, "chunk_ids": [EVIDENCE[0]["id"]]}],
            )
    claim = result["claim_checks"][0]
    assert claim["status"] == ("citation_error" if status == "supported" else "contradicted")
    assert claim["audit_status"] == status and claim["reference_check"] == "audit_id_invalid"
    assert not claim["source_id"] and not claim["evidence_quote"] and not result["_selected_evidence"][0]
    assert len(calls) == (2 if status == "supported" else 1)


def test_final_unit_ids_bind_source_version_and_reject_previous_subset():
    from rag_engine.evaluation.final_validation_units import prepare_unit_request

    fragment = {
        "id": "synthetic-chunk",
        "evidence_id": "synthetic-fragment",
        "source": "合成資料",
        "text": TEXT,
        "fragment_start": 600,
        "fragment_end": 600 + len(TEXT),
        "source_run_id": "synthetic-run-v1",
    }
    old = prepare_unit_request({"evidence_items": [fragment]})
    new = prepare_unit_request({"evidence_items": [{**fragment, "source_run_id": "synthetic-run-v2"}]})
    assert set(old.units).isdisjoint(new.units)
    with pytest.raises(ValueError):
        new.bind(list(old.units))
    selected = old.bind([next(reversed(old.units))])
    assert selected[0]["text"] == "確認を押します。" and selected[0]["fragment_start"] == 600


def test_unit_internal_metadata_and_scope_never_enter_wire():
    from rag_engine.evaluation.final_validation_units import prepare_unit_request

    prepared = prepare_unit_request(
        {
            "evidence_items": [
                {
                    "id": "chunk-a",
                    "evidence_id": "E-a",
                    "text": TEXT,
                    "document_id": "private-document",
                    "chunk_set_id": "private-set",
                    "internal_asset_path": "private-path",
                }
            ]
        }
    )
    wire = json.dumps(prepared.inputs)
    assert all(value not in wire for value in ("private-document", "private-set", "private-path"))
    assert prepared.bind([next(iter(prepared.units))])[0]["scope"]["document_id"] == "private-document"


@pytest.mark.parametrize("reverse", [False, True])
def test_question_and_document_selection_order_preserves_document_source_priority(reverse):
    def parse(system, user, settings, schema, provider_id=None):
        data = json.loads(user)
        if schema.__name__ != "FinalValidationUnitAudit":
            return schema(checks=[])
        question = next(item for item in data["evidence_items"] if item["id"] == "question:1")
        document = next(item for item in data["evidence_items"] if item["id"] == "chunk-a")
        selected = [question["units"][0]["unit_id"], document["units"][0]["unit_id"]]
        if reverse:
            selected.reverse()
        return schema.model_validate(
            {
                "claim_checks": [
                    {
                        "answer_quote": "",
                        "answer_passage_id": data["answer_passages"][0]["id"],
                        "evidence_unit_ids": selected,
                        "claim_target": "合成項目",
                        "evidence_target": "合成項目",
                        "applicability": "matched",
                        "output_fields": None,
                        "status": "supported",
                        "reason": "既知数量と単価を用いる固定判定です。",
                    }
                ]
            }
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "件数は4です。合計は。",
            "合計は84です。",
            [{"id": "chunk-a", "text": "1件あたり21です。\n追加料金はありません。"}],
            None,
            caller_citations=[{"start": 0, "end": 9, "chunk_ids": ["chunk-a"]}],
        )
    claim = result["claim_checks"][0]
    assert claim["status"] == "supported" and claim["source_id"] == "chunk-a"
    assert claim["evidence_quote"] == "1件あたり21です。"
    selected = result["_selected_evidence"][0]
    assert [r["source_id"] for r in selected] == (["chunk-a", "question:1"] if reverse else ["question:1", "chunk-a"])
    assert "unit_selection" not in claim and "unit_selection_invalid" not in claim


def test_output_definition_in_unselected_unit_cannot_fill_support():
    def parse(system, user, settings, schema, provider_id=None):
        data = json.loads(user)
        document = next(item for item in data["evidence_items"] if item["id"] == "chunk-a")
        assert len(document["units"]) == 2
        return schema.model_validate(
            {
                "claim_checks": [
                    {
                        "answer_quote": "",
                        "answer_passage_id": data["answer_passages"][0]["id"],
                        "evidence_unit_ids": [document["units"][0]["unit_id"]],
                        "claim_target": "合成出力",
                        "evidence_target": "合成出力",
                        "applicability": "matched",
                        "output_fields": {
                            "asserts_output_fields": True,
                            "definition_quote": "列Aは対象の番号です。",
                            "output_artifact_matches": True,
                        },
                        "status": "supported",
                        "reason": "未選択の定義を使う誤った合成判定です。",
                    }
                ]
            }
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "出力の列は。",
            "出力ファイルに列Aが含まれます。",
            [{"id": "chunk-a", "text": "出力ファイルの一覧を開きます。\n列Aは対象の番号です。"}],
            None,
            caller_citations=[{"start": 0, "end": 18, "chunk_ids": ["chunk-a"]}],
        )
    assert result["claim_checks"][0]["status"] == "unsupported"
    assert result["claim_checks"][0]["support_check"] == "output_field_basis_unconfirmed"


def test_saved_legacy_prompt_override_is_not_silently_rewritten():
    from rag_engine.resources.runtime import bind_prompt_overrides

    legacy = "## Step 1: 根拠を読む\nevidence_id と evidence_quote を返してください。"
    with bind_prompt_overrides({"answer_validation.system": legacy}):
        result, calls = _validate()
    assert calls[0]["system"] == legacy
    assert result["claim_checks"][0]["status"] == "supported"
    assert "evidence_unit_ids" in json.dumps(calls[0]["schema"])
    assert "evidence_quote" not in json.dumps(calls[0]["schema"])
    # スタブの成功は旧指示の意味的な適合を証明しない。保存資源の更新は別の受入条件。
