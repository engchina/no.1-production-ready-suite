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


EVIDENCE_REF = "【証拠 ID: 03ac895f1d744a2b9eab4dd569eb68fc:b245283c90ea80e5d8aadd3283e6027f148113c0aa42c59bbf12923cc59ae01c:1】"


def test_location_lines_evidence_ids_and_table_structure_are_not_audited() -> None:
    """出典の位置のラベル・根拠の ID・表の区切りと見出しは監査に渡さない (#1317)。

    #1305 の実環境の評価で、これらがモデルに unassessed と返され、Agent の最終の検証が外して
    回答の対応を conditional / insufficient_evidence に誤らせていた（da-trial-account-expiry / -setup）。
    表の本文の行は主張のまま監査する。
    """
    answer = "\n".join([
        "検証用アカウントの有効期限は **最長 30日** です。",
        "- セクション: 「サンプル業務ポータル 運用手順書 第3版」 → **2. 検証用アカウントの登録**",
        "- ページ: 1",
        "| 手順 | 操作内容 | 根拠 |",
        "|------|----------|------|",
        f"| 1. 前提確認 | 本操作は「ポータル管理者」ロールを持つ利用者のみが実施可能です。 | 「1. 前提」 （ページ1）{EVIDENCE_REF} |",
        "---",
    ])
    seen: list[list[str]] = []

    def parse(system, inputs, settings, schema, provider_id=None):
        import json
        payload = json.loads(inputs)
        seen.append([item["text"] for item in payload["answer_passages"]])
        evidence_id = payload["evidence_items"][0]["evidence_id"]
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": item["id"], "status": "supported", "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "根拠に記載"}
            for item in payload["answer_passages"]
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("有効期限は？", answer, EVIDENCE, settings=None)
    assert seen == [[
        "検証用アカウントの有効期限は **最長 30日** です。",
        "| 1. 前提確認 | 本操作は「ポータル管理者」ロールを持つ利用者のみが実施可能です。",
    ]]
    assert result["counts"] == {"supported": 2}


def test_new_non_claim_rules_keep_claims() -> None:
    from rag_engine.generation.operation_audit import is_non_claim_passage, table_header_lines

    assert is_non_claim_passage("- **根拠**：regional-report-guide-v2.pdf、2. 出力の手順（ページ1）")
    assert is_non_claim_passage("（*portal-operations-manual.pdf*、セクション「3. アクセス権限の付与」）【証拠1】")
    assert is_non_claim_passage("- 「サンプル業務ポータル 運用手順書 第3版」 6. アカウントの削除")
    assert is_non_claim_passage("| :--- | ---: |")
    assert is_non_claim_passage("**")
    assert is_non_claim_passage("権限の付与先は、個別の利用者ですか、グループですか？【clarification: target】")
    # 表の見出しは区切りの行の直前の行だけ。
    assert table_header_lines("| 手順 | 内容 |\n| 1 | 開く |") == set()
    assert table_header_lines("| 手順 | 内容 |\n|---|---|\n| 1 | 開く |") == {"| 手順 | 内容 |"}
    # 主張・操作を含む段落は監査する。
    assert not is_non_claim_passage("| 締め日 | 毎月 10 日 |")
    assert not is_non_claim_passage("- ページ: 管理画面で「削除」を押してください")
    assert not is_non_claim_passage("- 抜粋: 「…有効期限は最長 30 日です。」")
    assert not is_non_claim_passage("- 「有効期限は最長 30 日です。」")
    assert not is_non_claim_passage("- 「検証用」")
    assert not is_non_claim_passage(f"| 2. 登録 | 1. 「利用者」を開く 2. 「追加」を押す | {EVIDENCE_REF} |")
