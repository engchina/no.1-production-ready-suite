"""回答の最終の検証（#1246）。モデルはスタブ。標準回答を使わず主張だけを監査する。"""
from unittest.mock import patch

from rag_engine.evaluation.answer_validation import ClaimAuditOutput, validate_answer_claims

EVIDENCE = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "page_end": 1,
             "text": "検証用アカウントの有効期限は最長 30 日です。"}]
ANSWER = "検証用アカウントの有効期限は最長 30 日です。\n\n延長は 90 日までできます。"


def _passages(inputs: str) -> list[str]:
    import json
    payload = json.loads(inputs)
    return [item["id"] for item in payload["answer_passages"]], payload["evidence_items"][0]["evidence_id"]


def test_claims_are_bound_to_passages_and_evidence() -> None:
    def parse(system, inputs, settings, schema, provider_id=None):
        ids, evidence_id = _passages(inputs)
        assert schema is ClaimAuditOutput
        assert "standard_answer" not in inputs  # 標準回答は使わない
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": ids[0], "status": "supported", "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "根拠に記載"},
            {"answer_quote": "段落", "answer_passage_id": ids[1], "status": "unsupported", "evidence_id": "",
             "source_id": "", "evidence_quote": "", "reason": "延長の記載が無い"},
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("有効期限は？", ANSWER, EVIDENCE, settings=None)
    assert result["status"] == "completed"
    assert result["counts"] == {"supported": 1, "unsupported": 1}
    supported = next(c for c in result["claim_checks"] if c["status"] == "supported")
    assert supported["source_id"] == "c1"
    assert supported["answer_quote"] == "検証用アカウントの有効期限は最長 30 日です。"
    assert result["evidence_ids"] == ["c1"] and result["evidence_truncated"] is False


def test_missing_passages_are_unassessed_and_empty_answers_are_not_audited() -> None:
    def parse(system, inputs, settings, schema, provider_id=None):
        ids, _ = _passages(inputs)
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": ids[0], "status": "data_confirmation", "evidence_id": "",
             "source_id": "", "evidence_quote": "", "reason": "実データの確認"},
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("q", ANSWER, EVIDENCE, settings=None)
    assert result["counts"] == {"data_confirmation": 1, "unassessed": 1}
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as never:
        assert validate_answer_claims("q", "", EVIDENCE, settings=None)["status"] == "no_claims"
        never.assert_not_called()
