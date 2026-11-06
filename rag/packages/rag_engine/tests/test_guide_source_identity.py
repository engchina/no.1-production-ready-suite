"""監査で選択した出典だけを手順候補へ渡す。合成の固定監査応答、外部呼出しなし。"""

import json
from unittest.mock import patch

import pytest
from validation_unit_support import unit_audit_output

from rag_engine.evaluation import guide_step_validation as guide
from rag_engine.evaluation.answer_eval import _evidence_fragments
from rag_engine.evaluation.answer_validation import validate_answer_claims
from rag_engine.retrieval.guide_steps import GuidePlan

FIRST = "設定画面で項目Aの入力値を登録します。"
SECOND = "結果画面で確定ボタンを押して出力を確定します。"
ANSWER = "設定画面で項目Aの入力値を登録し、結果画面で確定ボタンを押して出力を確定します。"
A, B, C = "synthetic-a:version-a:0", "synthetic-b:version-b:0", "synthetic-c:version-c:0"


def evidence(order=(A, C, B)):
    return [
        {
            "id": key,
            "document_id": "doc-" + key[10],
            "page_start": 1,
            "page_end": 1,
            "source": "合成資料",
            "text": FIRST if key == A else SECOND,
        }
        for key in order
    ]


def plan():
    value = GuidePlan.from_payload(
        "",
        1,
        [
            {
                "id": key,
                "title": FIRST if source == A else SECOND,
                "references": [{"document_id": "doc-" + source[10], "page_ranges": [{"page_start": 1, "page_end": 1}]}],
            }
            for key, source in [("a", A), ("b", B), ("c", C)]
        ],
    )
    assert value is not None
    return value


def consume(claims, rows, *, selected_evidence=None):
    observed = []
    original = guide._items

    def capture(*args, **kwargs):
        checked, paragraphs = original(*args, **kwargs)
        observed.extend(entry.span["source_id"] for entry in checked)
        return checked, paragraphs

    with patch.object(guide, "_items", side_effect=capture):
        result = guide.validate_guide_steps(plan(), claims, rows, embed=None, selected_evidence=selected_evidence)
    assert all(step["status"] != "explained" for step in result["steps"])
    assert all(step["validation"]["body_coverage"]["execution"] == "not_run" for step in result["steps"])
    return observed, result


@pytest.mark.parametrize(
    "order,multi", [((A, C, B), True), ((A, B, C), True), ((C, B, A), False)], ids=["unselected-first", "selected-first", "single-positive"]
)
def test_actual_final_audit_keeps_selected_source_identity(order, multi):
    rows = evidence(order)
    answer = ANSWER if multi else SECOND
    selected = [A, B] if multi else [B]

    def parse(system, user, settings, schema, provider_id=None):
        if schema.__name__ != "FinalValidationUnitAudit":
            return schema(checks=[])
        data = json.loads(user)
        ids = {item["id"]: item["evidence_id"] for item in data["evidence_items"]}
        return unit_audit_output(
            schema,
            data,
            {
                "claim_checks": [
                    {
                        "answer_quote": answer,
                        "answer_passage_id": data["answer_passages"][0]["id"],
                        "evidence_id": ",".join(ids[key] for key in selected),
                        "evidence_quote": FIRST + "…" + SECOND if multi else SECOND,
                        "applicability": "matched",
                        "status": "supported",
                        "source_id": "",
                        "reason": "合成の固定支持応答。",
                    }
                ]
            },
        )

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "登録と確定の手順を確認してください。",
            answer,
            rows,
            None,
            caller_citations=[{"start": 0, "end": len(answer), "chunk_ids": selected}],
        )
    assert result["counts"] == {"supported": 1}
    sources, coverage = consume(result["claim_checks"], rows, selected_evidence=result["_selected_evidence"])
    assert sources == selected
    expected = {"a": bool(multi), "b": True, "c": False}
    assert {step["step_id"]: bool(step["answer_quote"]) for step in coverage["steps"]} == expected


def claim(eids, *, source=B, quote=SECOND):
    return {"status": "supported", "answer_quote": SECOND, "support_quote": quote, "source_id": source, "evidence_id": eids}


@pytest.mark.parametrize("mode", ["unknown", "mixed", "abbreviated", "no-identities"])
def test_unknown_identity_does_not_search_other_documents_by_text(mode):
    rows = evidence()
    eid = next(row["evidence_id"] for row in _evidence_fragments(rows) if row["id"] == B)
    selected = {"unknown": "Eunknown", "mixed": eid + ",Eunknown", "abbreviated": eid[:8], "no-identities": ""}[mode]
    assert consume([claim(selected, source="" if mode == "no-identities" else B)], rows)[0] == []


def test_missing_explicit_reused_source_is_not_replaced_by_equal_text():
    assert consume([claim("")], evidence((A, C)))[0] == []


def test_explicit_reused_source_without_fragment_id_is_preserved():
    assert consume([claim("", quote="")], evidence())[0] == [B]


def test_two_selected_equal_text_sources_remain_candidates_without_fulfillment():
    rows = evidence()
    ids = {row["id"]: row["evidence_id"] for row in _evidence_fragments(rows)}
    sources, result = consume([claim(ids[B] + "," + ids[C])], rows)
    assert sources == [B, C]
    assert {step["step_id"]: bool(step["answer_quote"]) for step in result["steps"]} == {"a": False, "b": True, "c": True}


@pytest.mark.parametrize("valid", [True, False], ids=["selected-later-fragment", "quote-outside-selected-fragment"])
def test_generated_fragment_id_keeps_its_original_range(valid):
    rows = evidence()
    for row in rows:
        if row["id"] == B:
            row["text"] = "補足情報。" * 410 + SECOND
    fragments = [row for row in _evidence_fragments(rows) if row["id"] == B]
    chosen = fragments[-1] if valid else fragments[0]
    assert chosen["fragment_start"] > 0 if valid else chosen["fragment_start"] == 0
    assert consume([claim(chosen["evidence_id"])], rows)[0] == ([B] if valid else [])


def test_exact_bound_chunk_id_is_preserved_without_reselecting_by_text():
    assert consume([claim(B)], evidence())[0] == [B]
