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


def test_headings_citations_and_questions_are_not_audited() -> None:
    """見出し・出典の行・利用者への質問は監査に渡さず、claim_checks にも出さない (#1306)。

    以前はモデルが not_a_claim にしても、is_heading に当たらない出典・質問が unassessed になり、
    Agent の最終の検証が「確かめられていない点」に移していた。
    """
    answer = "\n".join([
        "## 試用アカウントの有効期限",
        "検証用アカウントの有効期限は最長 30 日です。",
        "【portal-operations-manual.pdf, section 2. アカウント管理, page 1】",
        "出典: manual.pdf p.1",
        "所属部署の部門長の承認は得ていますか？（はい／いいえ）",
        "延長は 90 日までできます。",
    ])
    seen: list[list[str]] = []

    def parse(system, inputs, settings, schema, provider_id=None):
        import json
        payload = json.loads(inputs)
        seen.append([item["text"] for item in payload["answer_passages"]])
        evidence_id = payload["evidence_items"][0]["evidence_id"]
        ids = [item["id"] for item in payload["answer_passages"]]
        assert "reason も日本語で書く" in system
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": ids[0], "status": "supported", "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "根拠に記載"},
            {"answer_quote": "段落", "answer_passage_id": ids[1], "status": "unsupported", "evidence_id": "",
             "source_id": "", "evidence_quote": "", "reason": "延長の記載が無い"},
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("有効期限は？", answer, EVIDENCE, settings=None)
    assert seen == [["検証用アカウントの有効期限は最長 30 日です。", "延長は 90 日までできます。"]]
    assert result["counts"] == {"supported": 1, "unsupported": 1}
    # 主張が無い回答（見出し・出典・質問だけ）はモデルを呼ばない。
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as never:
        only = "**確認事項**\n付与先の部署名を教えてください。\n【manual.pdf p.3】"
        assert validate_answer_claims("q", only, EVIDENCE, settings=None)["status"] == "no_claims"
        never.assert_not_called()


def test_non_claim_passage_rules_keep_claims() -> None:
    from rag_engine.generation.operation_audit import is_non_claim_passage

    assert is_non_claim_passage("（参照: security-policy.docx p.4）")
    assert is_non_claim_passage("[運用マニュアル](https://example.com/manual.pdf)")
    assert is_non_claim_passage("削除するのはどちらのアカウントですか？")
    # 主張・操作を含む文は監査する。
    assert not is_non_claim_passage("根拠: 契約書の第 5 条により、30 日前までに申し出る必要があります")
    assert not is_non_claim_passage("削除できますが、よろしいですか？")
    assert not is_non_claim_passage("【パスワードは 90 日ごとに変更し、変更の履歴は管理画面に残ります】")
    assert not is_non_claim_passage("管理画面で「削除」を押してください。")
