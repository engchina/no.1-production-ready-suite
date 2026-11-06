"""内容判定へ送る原文と、呼び出し側が保持する引用範囲の責務を分ける。"""

import json
from unittest.mock import patch

import pytest
from validation_unit_support import unit_audit_output

from rag_engine.evaluation.answer_validation import validate_answer_claims


@pytest.mark.parametrize("citation", [None, "source-a", "source-b"])
def test_citation_scope_stays_in_code_and_model_input_is_the_same(citation):
    """同じ原文選択でも、引用無し・正しい引用・別引用の最終結果は別になる。"""
    answer = "対象Aの上限は6です。"
    evidence = [
        {"id": "source-a", "text": answer},
        {"id": "source-b", "text": "対象Bの上限は3です。"},
    ]
    calls = []

    def parse(system, user, settings, schema, provider_id=None):
        payload = json.loads(user)
        if schema.__name__ != "FinalValidationUnitAudit":
            return schema(checks=[])
        calls.append((system, payload))
        assert "passage_citations" not in payload
        assert "passage_citations" not in system
        assert "出典が無い、出典の対応が未確認" not in system
        return unit_audit_output(
            schema,
            payload,
            {
                "claim_checks": [
                    {
                        "answer_passage_id": payload["answer_passages"][0]["id"],
                        "status": "supported",
                        "evidence_id": next(row["evidence_id"] for row in payload["evidence_items"] if row["id"] == "source-a"),
                        "evidence_quote": answer,
                        "applicability": "matched",
                        "reason": "原文Aが対象Aの上限を支持します。",
                    }
                ]
            },
        )

    citations = [{"start": 0, "end": len(answer), "chunk_ids": [citation]}] if citation is not None else None
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims(
            "対象Aの上限を教えてください。",
            answer,
            evidence,
            None,
            caller_citations=citations,
        )
    assert len(calls) == 1
    claim = result["claim_checks"][0]
    assert claim["audit_status"] == "supported"
    assert claim["status"] == ("supported" if citation == "source-a" else "unassessed")
    if citation != "source-a":
        assert claim["support_check"] == "uncited_evidence"
    assert "対象との一致が unknown" in calls[0][0]
