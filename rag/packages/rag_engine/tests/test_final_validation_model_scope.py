"""引用集合はコードで制限し、監査の全入口へ意味判定の資料だけを渡す。"""

import json
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_validation import ClaimAuditOutput, validate_answer_claims

TEXT = "項目Aの値は42です。"
SECOND = "項目Bの値は24です。"
CHUNK = "synthetic-document-a:synthetic-version-a:0"
OTHER = "synthetic-document-b:synthetic-version-b:0"
# 5群を製品変更前に固定。最後の群は既存の4種の再確認経路を実行する。
CASES = (
    ("cited", "supported", "matched", "cited", "supported", 1),
    ("scope-missing", "supported", "matched", "missing", "unassessed", 1),
    ("scope-other", "supported", "matched", "other", "unassessed", 1),
    ("data-result", "supported", "matched", "missing", "supported", 1),
    ("unsupported", "unsupported", "matched", "cited", "unsupported", 1),
    ("contradicted", "contradicted", "matched", "cited", "contradicted", 1),
    ("unknown", "supported", "unknown", "cited", "unassessed", 1),
    ("not-applicable", "supported", "not_applicable", "cited", "unsupported", 1),
    ("classification-recheck", "not_a_claim", "matched", "cited", "supported", 2),
    ("reference-recheck", "supported", "matched", "cited", "supported", 2),
    ("figure-recheck", "supported", "matched", "cited", "figure_only", 2),
    ("unreferenced-recheck", "supported", "matched", "cited", "supported", 2),
)


def replay_case(case):
    name, initial, applicability, citation, _, _ = case
    answer = f"{TEXT}\n\n{SECOND}" if name == "unreferenced-recheck" else TEXT
    evidence = [
        {"id": CHUNK, "source": "合成資料A", "text": answer},
        {"id": OTHER, "source": "合成資料B", "text": "項目Cの値は16です。"},
    ]
    if name == "figure-recheck":
        evidence[0]["evidence_type"] = "figure_description"
    calls = []

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        calls.append({"system": system, "inputs": payload, "schema": schema.model_json_schema()})
        chosen = next(row for row in payload["evidence_items"] if row["id"] == ("data:1" if name == "data-result" else CHUNK))
        checks = []
        for passage in payload["answer_passages"]:
            status = initial if len(calls) == 1 else "figure_only" if name == "figure-recheck" else "supported"
            eid = chosen["evidence_id"]
            quote = TEXT if name == "data-result" else chosen["text"]
            if name == "reference-recheck" and len(calls) == 1:
                quote = "項目A=42"
            if name == "unreferenced-recheck" and len(calls) == 1 and passage["text"] == SECOND:
                status, eid, quote = "citation_error", "", ""
            checks.append(
                {
                    "answer_quote": passage["text"],
                    "answer_passage_id": passage["id"],
                    "status": status,
                    "evidence_id": eid,
                    "evidence_quote": quote,
                    "claim_target": "項目A",
                    "evidence_target": "項目A",
                    "applicability": applicability,
                    "source_id": "",
                    "reason": "合成の固定応答。",
                }
            )
        return ClaimAuditOutput.model_validate({"claim_checks": checks})

    citations = [] if citation == "missing" else [{"start": 0, "end": len(answer), "chunk_ids": [OTHER if citation == "other" else CHUNK]}]
    data = [{"source": "合成照会", "question": "項目Aの値", "sql": "SELECT 42 AS value FROM dual", "result": TEXT}]
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "項目Aの値は何ですか。", answer, evidence, None, caller_citations=citations, data_results=data if name == "data-result" else ()
        )
    return result, calls


@pytest.mark.parametrize("case", CASES, ids=[row[0] for row in CASES])
def test_final_status_and_existing_rechecks_are_preserved(case):
    result, calls = replay_case(case)
    assert result["status"] == "completed"
    assert {claim["status"] for claim in result["claim_checks"]} == {case[4]}
    assert len(calls) == case[5]
    if case[0].startswith("scope-"):
        assert {claim["support_check"] for claim in result["claim_checks"]} == {"uncited_evidence"}
    if case[0] == "reference-recheck":
        assert result["reference_reaudit"]["adopted_passage_ids"] == ["A1"]
    if case[0] == "unreferenced-recheck":
        assert result["rechecked_passage_ids"] == ["A2"]


@pytest.mark.parametrize("case", CASES, ids=[row[0] for row in CASES])
def test_all_model_calls_leave_citation_scope_to_code(case):
    _, calls = replay_case(case)
    for call in calls:
        assert "passage_citations" not in call["inputs"]
        assert "passage_citations" not in call["system"]
        # 原文コピーと対象照合は、この小段では既存契約のまま。
        fields = call["schema"]["$defs"]["ValidationClaimCheck"]["properties"]
        assert {"evidence_quote", "applicability", "evidence_target"} <= fields.keys()
