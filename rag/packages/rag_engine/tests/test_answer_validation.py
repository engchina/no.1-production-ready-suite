"""回答の最終の検証（#1246）。モデルはスタブ。標準回答を使わず主張だけを監査する。"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

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


# 多段の質問の評価の Run の形（#1364。cmp-hr-vs-attendance-retention）: 台帳の行（機密区分）と規程（保管期間）。
LEDGER = {"id": "doc-ledger:set:3", "source": "system-ledger.xlsx", "page_start": None, "page_end": None,
          "text": "システムID: SYS-103 / 正式名: 人事評価システム / 機密区分: 極秘"}
RETENTION = {"id": "doc-retention:set:1", "source": "data-retention.pdf", "page_start": 1, "page_end": 1,
             "text": "極秘のデータは 10 年間保管します。社外秘のデータは 7 年間保管します。"}
BRIDGE_ANSWER = "人事評価システムは「極秘」扱いなので、データは **10 年間** 保管されます。"


def _bridge_validation(evidence_id: str, *, evidence_quote: str = "") -> dict:
    def parse(system, inputs, settings, schema, provider_id=None):
        import json
        payload = json.loads(inputs)
        ids = {item["id"]: item["evidence_id"] for item in payload["evidence_items"]}
        assert "「,」で区切って evidence_id に書く" in system
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": payload["answer_passages"][0]["id"], "status": "supported",
             "evidence_id": evidence_id.format(ledger=ids[LEDGER["id"]], retention=ids[RETENTION["id"]]),
             "source_id": "", "evidence_quote": evidence_quote,
             "reason": "台帳で極秘、規程で極秘は 10 年と確かめられる。"},
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        return validate_answer_claims("人事評価システムのデータの保管期間は？", BRIDGE_ANSWER, [LEDGER, RETENTION],
                                      settings=None)


def test_claim_supported_by_two_evidence_ids_is_not_a_citation_error() -> None:
    """2 つの根拠を合わせて裏付ける結論の evidence_id に ID を並べても「未登録の原文ID」にしない (#1364)。"""
    for written in ("{ledger},{retention}", "{ledger}, {retention}", "{ledger}、{retention}",
                    "[{ledger} / {retention}]",
                    # chunk の id（MCP の evidence_id）を並べたもの・括弧で囲んだ 1 つの ID も同じ。
                    "doc-ledger:set:3, doc-retention:set:1", "【{ledger}】"):
        result = _bridge_validation(written)
        [claim] = result["claim_checks"]
        assert claim["status"] == "supported", written
        assert claim["source_id"] in {LEDGER["id"], RETENTION["id"]}
        assert result["counts"] == {"supported": 1}
    # 結び付けた片段の ID をすべて残す（最初の根拠を出典にする）。
    [claim] = _bridge_validation("{ledger}, {retention}")["claim_checks"]
    assert claim["source_id"] == LEDGER["id"] and claim["evidence_quote"] == LEDGER["text"]
    assert len(claim["evidence_id"].split(",")) == 2


def test_unknown_or_unquoted_ids_in_a_list_stay_citation_errors() -> None:
    """並べた ID の 1 つでも根拠に無い・引用がどの根拠にも無いときは、今までどおり引用エラー (#1364)。"""
    for written, quote in (("{ledger}, E0000000000000000000", ""), ("{ledger} {retention}", "根拠に無い文"),
                           ("E0000000000000000000", "")):
        [claim] = _bridge_validation(written, evidence_quote=quote)["claim_checks"]
        assert claim["status"] == "citation_error", written
        assert claim["reason"].startswith("未登録の原文ID。")


# 出典の行の事例（RAG と Agent の両方のテストが読む。#1370）。
CITATION_CASES = json.loads(
    (Path(__file__).resolve().parents[4] / "platform/contracts/answer-passages/citation-lines.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CITATION_CASES["citation_passages"], ids=lambda case: case["id"])
def test_citation_only_passages_are_not_claims(case: dict) -> None:
    """定位子・根拠の ID・文書名と場所だけの行は出典の行で、主張として監査しない (#1370)。"""
    from rag_engine.generation.operation_audit import is_citation_line, is_non_claim_passage

    assert is_citation_line(case["passage"])
    assert is_non_claim_passage(case["passage"])


@pytest.mark.parametrize("case", CITATION_CASES["claim_passages"], ids=lambda case: case["id"])
def test_passages_with_body_after_the_location_stay_claims(case: dict) -> None:
    """場所・ラベルの後に本文が続く行は主張のまま監査する（取りこぼさない。#1370）。"""
    from rag_engine.generation.operation_audit import is_non_claim_passage

    assert not is_non_claim_passage(case["passage"])


@pytest.mark.parametrize("case", CITATION_CASES["answers"], ids=lambda case: case["id"])
def test_citation_lines_of_the_evaluation_answers_are_not_audited(case: dict) -> None:
    """#1335 の再評価の回答の出典の行を監査に渡さず、unassessed にしない (#1370)。"""
    seen: list[list[str]] = []

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        seen.append([item["text"] for item in payload["answer_passages"]])
        evidence_id = payload["evidence_items"][0]["evidence_id"]
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": item["id"], "status": "supported", "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "根拠に記載"}
            for item in payload["answer_passages"]
        ]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("質問", case["answer"], EVIDENCE, settings=None)
    [audited] = seen
    assert not set(case["citation_passages"]) & set(audited)
    assert set(case["claim_passages"]) <= set(audited)
    assert set(result["counts"]) == {"supported"}


def test_citation_line_marked_not_a_claim_is_not_unassessed() -> None:
    """品質評価の主張の監査でも、モデルが not_a_claim にした出典の行は unassessed にしない (#1370)。"""
    from rag_engine.evaluation.answer_eval import _bind_claims

    passages = [{"id": "A1", "text": "*Locator*: `doc:1b5b/page:3/el:12`"},
                {"id": "A2", "text": "承認者は運行管理課長です。"}]
    output = ClaimAuditOutput.model_validate({"claim_checks": [
        {"answer_quote": item["text"], "answer_passage_id": item["id"], "status": "not_a_claim", "evidence_id": "",
         "source_id": "", "evidence_quote": "", "reason": "主張ではない"}
        for item in passages
    ]})
    bound = _bind_claims(output, {}, passages)
    assert [claim.status for claim in bound.claim_checks] == ["not_a_claim", "unassessed"]


# #1362 の評価の Run の形（#1391）。業務支援の da-trial-account-setup: 運用手順書の章ごとの chunk と、手順の回答。
PORTAL = "d71d90b414a74ad99ccfc7a9dc4b3cb5:2e474d0310926b739eeed94bfc31012518db916cc382524721434a8baa1d629d"
PORTAL_EVIDENCE = [
    {"id": f"{PORTAL}:2", "source": "portal-operations-manual.pdf", "page_start": 1, "page_end": 1,
     "text": "2. 検証用アカウントの登録\n1.管理画面の「利用者」を開き、「追加」を押します。\n2.利用者種別で「検証用」を選びます。"},
    {"id": f"{PORTAL}:3", "source": "portal-operations-manual.pdf", "page_start": 1, "page_end": 1,
     "text": "3. アクセス権限の付与\n個別の利用者に付与する場合: 利用者の詳細画面の「権限」タブで、"
             "付与する権限を選んで「付与」を押します。"},
    {"id": f"{PORTAL}:4", "source": "portal-operations-manual.pdf", "page_start": 1, "page_end": 1,
     "text": "4. 通知の設定\n1.権限を付与した後、利用者の詳細画面の「通知」タブを開きます。"},
]
PORTAL_ANSWER = "\n".join([
    "- 管理画面の「利用者」メニューを開き「追加」ボタンを押す。",
    "- 登録した検証用利用者の詳細画面の **「権限」タブ** を開く。",
    "- 権限付与が完了したら、同じ利用者の詳細画面の **「通知」タブ** を開く。",
    "以上の順序で実施すれば、検証用アカウントの登録から権限付与、さらに通知設定まで完了します。",
])


def _portal_validation(written: list[str]) -> dict:
    """段落ごとに、根拠の片段の ID（format の {0}〜{2}）から作った evidence_id を返すモデルのスタブ。"""
    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        ids = [item["evidence_id"] for item in payload["evidence_items"]]
        assert "省かず・短くせずにそのまま書く" in system
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": passage["id"], "status": "supported",
             "evidence_id": evidence_id.format(*ids), "source_id": "", "evidence_quote": "",
             "reason": "根拠文書に手順が記載されているため"}
            for passage, evidence_id in zip(payload["answer_passages"], written, strict=True)]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        return validate_answer_claims("検証用アカウントを登録し、権限を付けて、通知まで設定する手順は？", PORTAL_ANSWER,
                                      PORTAL_EVIDENCE, settings=None)


def test_span_ids_shortened_by_the_model_are_bound_to_their_evidence() -> None:
    """モデルが片段の ID の後ろを切って書いても（「E0d7d982」）、根拠の 1 つに決まれば「未登録の原文ID」にしない (#1391)。

    #1362 の評価の da-trial-account-setup を流し直すと、モデルは 21 文字の ID を 8〜10 文字に切って返し、
    手順の段落が引用エラーで外され、回答の本文が空になった。
    """
    result = _portal_validation(["{0:.8}", "{1:.10}", "{2:.12}", "{0:.8}, {1}, {2:.9}"])
    assert result["counts"] == {"supported": 4}
    claims = result["claim_checks"]
    assert [claim["source_id"] for claim in claims] == [f"{PORTAL}:2", f"{PORTAL}:3", f"{PORTAL}:4", f"{PORTAL}:2"]
    # 結び付けた片段の完全な ID を残す（監査の記録から根拠を引ける）。
    assert all(len(token) == 21 for claim in claims for token in claim["evidence_id"].split(","))
    assert len(claims[3]["evidence_id"].split(",")) == 3
    # 大文字で書いた ID・ラベルを付けた ID・「…」「...」で省いた ID も同じ。
    result = _portal_validation(["{0:.10}".upper(), "evidence_id: {1:.9}", "evidence_id {2:.8}…", "{0:.8}..."])
    assert result["counts"] == {"supported": 4}


def test_shortened_ids_that_do_not_point_to_one_evidence_stay_citation_errors() -> None:
    """根拠に無い ID・短すぎて根拠を決められない ID は、今までどおり引用エラー (#1391)。"""
    result = _portal_validation(["E0000000", "{0:.4}", "{1:.6}…", "{0:.8}, E0000000"])
    assert result["counts"] == {"citation_error": 4}
    assert all(claim["reason"].startswith("未登録の原文ID。") for claim in result["claim_checks"])


# 多段の br-document-portal-retention: 回答が根拠の ID を途中で省いて書き（「…fffa…:1」）、検証のモデルがそれを写した。
PORTAL_LEDGER = {"id": "87ae9f2fc8f944a8a78edfba9d8ac8b9:a3ff18c115fc4f2bb10aece14f0cfc09a975f6a90598cd4ccaf483042efd1814:5",
                 "source": "system-ledger.xlsx", "page_start": None, "page_end": None,
                 "text": "システムID: SYS-105 / 正式名: ドキュメントポータル / 担当部署: 総 / 重要度: B / 機密区分: 社内限り"}
RETENTION_SET = "af257c62c85d45cd9211a098fdca6fb4:16f11273eede4c4f508dcc4453985fffa816db12ed7c51b18f03305bee231ace"
RETENTION_RULES = [
    {"id": f"{RETENTION_SET}:1", "source": "data-retention-rules.pdf", "page_start": 1, "page_end": 1,
     "text": "第 1 章 機密区分と保管期間\n極秘のデータは 10 年間保管します。\n社内限りのデータは 3 年間保管します。"},
    {"id": f"{RETENTION_SET}:2", "source": "data-retention-rules.pdf", "page_start": 1, "page_end": 1,
     "text": "第 2 章 閲覧の記録\n極秘のデータを扱うシステムは、閲覧の記録（アクセスログ）を 3 年間保管します。"},
]
ABBREVIATED_CHUNK_ID = "af257c62c85d45cd9211a098fdca6fb4:16f11273eede4c4f508dcc4453985fffa…:1"
RETENTION_ANSWER = "\n".join([
    "ドキュメントポータル（SYS‑105）の機密区分は「社内限り」（システム台帳）であり、社内限りのデータは **3 年間** 保管します。",
    f"- データ保管規程: 「社内限りのデータは 3 年間保管します」【evidence_id {ABBREVIATED_CHUNK_ID}】",
    "したがって、ドキュメントポータルのデータの保管期間は **3 年** です。",
])


def _retention_validation(written: list[str], rechecks: list[dict] | None = None) -> dict:
    """段落ごとに written の evidence_id を返すモデルのスタブ。

    2 回目の呼び出し（ID を書かない段落の確かめ直し。#1412）は、確かめ直す段落に evidence_id を空で返し
    （出典が決まらない）、入力を rechecks に残す。
    """
    calls = []

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        calls.append(payload)
        answers = written if len(calls) == 1 else [""] * len(payload["answer_passages"])
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": passage["id"], "status": "supported", "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "台帳と規程で裏付けられます。"}
            for passage, evidence_id in zip(payload["answer_passages"], answers, strict=True)]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("ドキュメントポータルのデータの保管期間は何年ですか？", RETENTION_ANSWER,
                                        [PORTAL_LEDGER, *RETENTION_RULES], settings=None)
    if rechecks is not None:
        rechecks.extend(calls[1:])
    return result


def test_chunk_ids_abbreviated_in_the_answer_are_bound_when_copied_by_the_model() -> None:
    """回答に書かれた途中を省いた chunk の id をモデルが写しても、1 つの chunk に決まれば結び付ける (#1391)。"""
    both = f"{PORTAL_LEDGER['id']},{ABBREVIATED_CHUNK_ID}"
    result = _retention_validation([both, f"evidence_id {ABBREVIATED_CHUNK_ID}", both])
    assert result["counts"] == {"supported": 3}
    assert [claim["source_id"] for claim in result["claim_checks"]] == [
        PORTAL_LEDGER["id"], f"{RETENTION_SET}:1", PORTAL_LEDGER["id"]]
    # 実サービスで流し直すと、モデルは「…」を落として写した（「…fffa:1」）。部分ごとに照合して結び付ける。
    dropped = ABBREVIATED_CHUNK_ID.replace("…", "")
    result = _retention_validation([f"{PORTAL_LEDGER['id']},{dropped}", dropped, f"{PORTAL_LEDGER['id'][:20]}…:5"])
    assert result["counts"] == {"supported": 3}
    assert result["claim_checks"][1]["source_id"] == f"{RETENTION_SET}:1"
    assert result["claim_checks"][2]["source_id"] == PORTAL_LEDGER["id"]
    # 省いた所の後ろ（番号）が違えば根拠に無い chunk、番号まで省けば 2 つの chunk のどちらか決まらない。
    # 回答に書かれた ID でない語（「record_codes.values[1]」）も、今までどおり引用エラー。
    # ただし 2 つ目の段落は段落そのものが根拠の ID（「…fffa…:1」）を書いているので、その根拠に結び付ける（#1404）。
    # ID を書かない 1・3 つ目の段落は、2 つ目の段落が引いた根拠だけで確かめ直し、出典が決まらなければそのまま（#1412）。
    rechecks: list[dict] = []
    result = _retention_validation([ABBREVIATED_CHUNK_ID.replace(":1", ":9"), f"{RETENTION_SET[:40]}…",
                                    "record_codes.values[1]"], rechecks)
    assert [claim["status"] for claim in result["claim_checks"]] == ["citation_error", "supported", "citation_error"]
    assert result["claim_checks"][1]["source_id"] == f"{RETENTION_SET}:1"
    [recheck] = rechecks
    assert [passage["id"] for passage in recheck["answer_passages"]] == ["A1", "A3"]
    assert [item["id"] for item in recheck["evidence_items"]] == [f"{RETENTION_SET}:1"]
    assert result["rechecked_passage_ids"] == []
    # 「…」を落とした ID も、番号が違う・残した部分が短い・部分の数が違うものは結び付けない。
    result = _retention_validation([dropped.replace(":1", ":9"), f"{RETENTION_SET.split(':')[0]}:16f112:1",
                                    f"{RETENTION_SET}:extra:1"])
    assert [claim["status"] for claim in result["claim_checks"]] == ["citation_error", "supported", "citation_error"]


# #1362 の再評価の Run run_f45cb461c85946459d604dd08561d626（cmp-mes-vs-qms-deadline）の形（#1404。ID は合成した値）。
# 回答は根拠の chunk の id を完全な形で「【証拠 ID 文書:chunk_set:番号】」と書き、検証に渡した根拠にも同じ id がある。
MES = "5c61673c22be4d39b075223c6de78e14:3f574968" + "0" * 56 + ":2"
QMS = "49b9784f8b60426fb3195672b40bd6b7:ed020e40" + "1" * 56 + ":3"
DEADLINE_EVIDENCE = [
    {"id": MES, "source": "mes-operation-rules.pdf", "page_start": 2, "page_end": 2,
     "text": "第 3 章 変更の申請\n製造実行システムの変更は、実施日の 5 営業日前までに申請します。"},
    {"id": QMS, "source": "qms-operation-rules.pdf", "page_start": 3, "page_end": 3,
     "text": "第 4 章 変更の申請\n品質管理システムの変更は、実施日の 3 営業日前までに申請します。"},
    {"id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9:" + "2" * 64 + ":1", "source": "system-ledger.xlsx",
     "page_start": None, "page_end": None, "text": "システムID: SYS-101 / 正式名: 製造実行システム / 担当部署: 製造部"},
]
DEADLINE_ANSWER = "\n\n".join([
    f"製造実行システムの変更は、実施日の 5 営業日前までに申請します【証拠 ID {MES}】。",
    f"品質管理システムの変更は、実施日の 3 営業日前までに申請します【証拠 ID {QMS}】。",
    f"申請の期限は、製造実行システムが 5 営業日前、品質管理システムが 3 営業日前です【証拠 ID {MES}】【証拠 ID {QMS}】。",
    "したがって、申請の期限は製造実行システムの方が長い（5 営業日）です。",
])


def _deadline_validation(claims: list[tuple[str, str]], answer: str = DEADLINE_ANSWER,
                         recheck: list[tuple[str, str]] | None = None, calls: list[dict] | None = None) -> dict:
    """段落ごとに（status, evidence_id）を返すモデルのスタブ。理由はどれも「裏付けられる」。

    2 回目の呼び出し（ID を書かない段落の確かめ直し。#1412）には recheck を返す。evidence_id の
    「{0}」「{1}」は、確かめ直しに渡した根拠の片段の ID。recheck が無ければ 2 回目の呼び出しを認めない。
    """
    seen: list[dict] = [] if calls is None else calls

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        seen.append(payload)
        assert "ID の照合はシステムが行う" in system
        if len(seen) == 1:
            answers = claims
        else:
            assert recheck is not None, "確かめ直しを想定していない"
            ids = [item["evidence_id"] for item in payload["evidence_items"]]
            answers = [(status, evidence_id.format(*ids)) for status, evidence_id in recheck]
        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": passage["id"], "status": status, "evidence_id": evidence_id,
             "source_id": "", "evidence_quote": "", "reason": "主張は根拠で裏付けられる。"}
            for passage, (status, evidence_id) in zip(payload["answer_passages"], answers, strict=True)]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        return validate_answer_claims("製造実行システムと品質管理システムでは、変更の申請の期限はどちらが長いですか？",
                                      answer, DEADLINE_EVIDENCE, settings=None)


def test_full_chunk_ids_written_in_the_answer_are_not_citation_errors() -> None:
    """根拠の id と完全に一致する ID を引用した段落を「未登録の原文ID」にしない (#1404)。

    モデルは evidence_id に回答の「【証拠 ID …】」をラベルごと写したり、片段の ID を写し損ねたりする。
    ラベル・ID でない語は除いて照合し、それでも決まらなければ段落そのものが書いた ID で根拠に結び付ける。
    """
    result = _deadline_validation([
        ("supported", f"【証拠 ID {MES}】"),
        ("supported", "E9f9f9f9f9f9f9f9f9f9f"),  # 根拠に無い片段の ID: 段落の ID（QMS）で結び付ける。
        ("supported", f"【証拠 ID {MES}】、【証拠ID: {QMS}】"),
        ("supported", f"{MES}（第 3 章）, {QMS}"),
    ])
    assert result["counts"] == {"supported": 4}
    assert [claim["source_id"] for claim in result["claim_checks"]] == [MES, QMS, MES, MES]
    # 結び付けた片段の完全な ID を残す。
    assert all(token.startswith("E") and len(token) == 21
               for claim in result["claim_checks"][1:] for token in claim["evidence_id"].split(","))
    # evidence_id を空で返しても、段落が書いた ID（完全一致・途中を省いた形・部分ごとの前方一致）で結び付ける。
    mes_document, mes_set, _ = MES.split(":")
    answer = DEADLINE_ANSWER.replace(f"【証拠 ID {QMS}】。", f"【証拠 ID {QMS[:40]}…:3】。", 1).replace(
        f"【証拠 ID {MES}】【", f"【証拠 ID {mes_document}:{mes_set[:12]}:2】【", 1)
    result = _deadline_validation([("supported", ""), ("supported", ""), ("supported", ""),
                                   ("supported", f"【証拠 ID {MES}】")], answer=answer)
    assert result["counts"] == {"supported": 4}
    assert [claim["source_id"] for claim in result["claim_checks"]] == [MES, QMS, MES, MES]


def test_model_citation_errors_with_resolved_ids_are_not_kept() -> None:
    """モデルが citation_error を返しても、引用した ID がすべて根拠に決まれば出典の誤りにしない (#1404)。

    裏付けの判定はモデルから返っていないので、supported にはせず unassessed（確かめが終わっていない）にする。
    """
    first = [("citation_error", ""), ("citation_error", f"【証拠 ID {QMS}】"), ("supported", MES), ("citation_error", "")]
    # 4 つ目の段落は ID を書いておらず、モデルの evidence_id も空なので出典を照合できない。他の段落が引いた根拠
    # （MES・QMS）で確かめ直しても出典が決まらなければ、citation_error のまま（#1412）。
    result = _deadline_validation(first, recheck=[("citation_error", "")])
    statuses = [claim["status"] for claim in result["claim_checks"]]
    assert statuses == ["unassessed", "unassessed", "supported", "citation_error"]
    assert result["claim_checks"][0]["reason"].startswith("引用した ID は根拠に一致しますが")
    # 確かめ直しで候補の根拠に裏付けられれば supported（出典は候補の根拠）。
    calls: list[dict] = []
    result = _deadline_validation(first, recheck=[("supported", "{0},{1}")], calls=calls)
    assert [claim["status"] for claim in result["claim_checks"]] == ["unassessed", "unassessed", "supported", "supported"]
    assert result["claim_checks"][3]["source_id"] == MES and result["rechecked_passage_ids"] == ["A4"]
    assert [item["id"] for item in calls[1]["evidence_items"]] == [MES, QMS]


def test_unresolved_ids_in_the_answer_stay_citation_errors() -> None:
    """段落の ID が根拠に無い・1 つでも決まらないときは、今までどおり引用エラー (#1404)。"""
    unknown = "ffffffffffffffffffffffffffffffff:" + "3" * 64 + ":9"
    answer = "\n\n".join([
        f"製造実行システムの変更は、実施日の 5 営業日前までに申請します【証拠 ID {unknown}】。",
        f"品質管理システムの変更は、実施日の 3 営業日前までに申請します【証拠 ID {QMS}】【証拠 ID {unknown}】。",
        "したがって、申請の期限は製造実行システムの方が長い（5 営業日）です。",
        # 途中を省いた ID でも、番号が違えば根拠に無い chunk。
        f"両システムとも申請が要ります【証拠 ID {MES.split(':')[0][:10]}…:7】。",
    ])
    # ID を書かない 3 つ目の段落は、他の段落が引いた ID がどれも根拠に決まらないので確かめ直さない（#1412）。
    calls: list[dict] = []
    result = _deadline_validation([("supported", "E9f9f9f9f9f9f9f9f9f9f"), ("supported", "証拠"),
                                   ("supported", "第 3 章"), ("citation_error", "")], answer=answer, calls=calls)
    assert result["counts"] == {"citation_error": 4} and len(calls) == 1
    assert all(claim["reason"].startswith("未登録の原文ID。") for claim in result["claim_checks"][:3])


# #1362 の確認の Run run_3b7a1c0ff119477781a0042ea2a8dd23（holdout br-customer-portal-change-window）の形
# （#1412。ID と本文は合成した値）。結論とまとめの段落は根拠の ID を書かず、「根拠」の 2 段落が完全な ID を書く。
PLAN = "815d7759aaaa4887b1b070379e2958af:a657c65a" + "4" * 56
CHANGE = "2a5ec48abbbb4c81989a580a5676476a:58fa1e8c" + "5" * 56
WINDOW_LEDGER = "95e6cc20cccc4339a8e32763c996ce5c:28fb4739" + "6" * 56 + ":28"
WINDOW_EVIDENCE = [
    {"id": f"{PLAN}:0", "source": "maintenance-plan.pdf", "page_start": 1, "page_end": 1,
     "text": "定期保守計画 2026年度\n発行: 2026年4月1日 作成: 情シス"},
    {"id": f"{PLAN}:1", "source": "maintenance-plan.pdf", "page_start": 1, "page_end": 1,
     "text": "第 1 章 定期保守の時間帯\nCustomer Portal: 毎月第 1 日曜日の 0:00〜4:00"},
    {"id": WINDOW_LEDGER, "source": "system-ledger.xlsx", "page_start": None, "page_end": None,
     "text": "システムID: SYS-128 / 正式名: カスタマーポータル / 略称・別表記: Customer Portal / 重要度: A"},
    {"id": f"{CHANGE}:4", "source": "change-procedure.pdf", "page_start": 1, "page_end": 1,
     "text": "第 4 章 作業の時間帯\n重要度 A のシステムの変更は、定期保守の時間帯にだけ作業します。"},
    {"id": f"{CHANGE}:5", "source": "change-procedure.pdf", "page_start": 1, "page_end": 1,
     "text": "第 5 章 利用者への告知\nシステムの停止を伴う変更は、作業の 5 営業日前までに告知します。"},
]
WINDOW_ANSWER = (
    "**回答**  \nCustomer Portal（SYS‑128）の本番環境での変更作業は、**毎月第 1 日曜日の 0:00 〜 4:00 の定期保守枠**で"
    "実施できます。\n\n**根拠**  \n"
    f"1. 【定期保守計画 2026年度】「Customer Portal: 毎月第 1 日曜日の 0:00〜4:00」​【evidence_id: {PLAN}:1】  \n"
    f"2. 【システム変更手順書】重要度 A のシステムは「定期保守の時間帯にだけ作業します」​【evidence_id: {CHANGE}:4】\n\n"
    "以上の資料に基づき、Customer Portal の本番変更は上記の保守時間帯に行うことが規定されています。")
# 実サービスの最初の判定の形: 結論（A2）は supported で evidence_id が空、まとめ（A6）は citation_error。
WINDOW_FIRST = {"A2": ("supported", ""), "A4": ("supported", f"{PLAN}:1"), "A5": ("supported", f"{CHANGE}:4"),
                "A6": ("citation_error", "")}


def _window_validation(first: dict[str, tuple[str, str]], recheck: dict[str, tuple[str, str]] | Exception | None,
                       answer: str = WINDOW_ANSWER) -> tuple[dict, list[tuple[str, dict]]]:
    """段落の ID ごとに（status, evidence_id）を返すモデルのスタブ。

    evidence_id の「@chunk の id」は、その chunk の片段の ID（その呼び出しに渡した根拠の中のもの）。
    2 回目の呼び出し（確かめ直し）には recheck を返す（例外なら送出し、None なら確かめ直しを認めない）。
    """
    calls: list[tuple[str, dict]] = []

    def parse(system, inputs, settings, schema, provider_id=None):
        payload = json.loads(inputs)
        calls.append((system, payload))
        answers = first if len(calls) == 1 else recheck
        assert answers is not None, "確かめ直しを想定していない"
        if isinstance(answers, Exception):
            raise answers
        ids = {item["id"]: item["evidence_id"] for item in payload["evidence_items"]}

        def written(passage_id: str) -> str:
            text = answers[passage_id][1]
            for chunk_id, evidence_id in ids.items():
                text = text.replace("@" + chunk_id, evidence_id)
            return text

        return ClaimAuditOutput.model_validate({"claim_checks": [
            {"answer_quote": "段落", "answer_passage_id": passage["id"], "status": answers[passage["id"]][0],
             "evidence_id": written(passage["id"]), "source_id": "", "evidence_quote": "",
             "reason": "定期保守計画と変更手順書で根拠で裏付けられています。"}
            for passage in payload["answer_passages"]]})

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=parse):
        result = validate_answer_claims("Customer Portal の本番の変更は、いつ作業できますか？", answer,
                                        WINDOW_EVIDENCE, settings=None)
    return result, calls


def _cite(*chunk_ids: str) -> str:
    """スタブの evidence_id の書式（chunk の片段の ID を「,」で並べる）。"""
    return ",".join("@" + chunk_id for chunk_id in chunk_ids)


def test_unreferenced_conclusions_are_rechecked_with_evidence_cited_by_other_passages() -> None:
    """ID を書かない結論・まとめの段落の出典が決まらなければ、他の段落が引いた根拠で確かめ直す (#1412)。"""
    both = _cite(f"{PLAN}:1", f"{CHANGE}:4")
    result, calls = _window_validation(WINDOW_FIRST, {"A2": ("supported", both), "A6": ("supported", both)})
    assert result["counts"] == {"supported": 4}
    assert [claim["answer_passage_id"] for claim in result["claim_checks"]] == ["A2", "A4", "A5", "A6"]
    assert [claim["source_id"] for claim in result["claim_checks"]] == [
        f"{PLAN}:1", f"{PLAN}:1", f"{CHANGE}:4", f"{PLAN}:1"]
    assert result["rechecked_passage_ids"] == ["A2", "A6"]
    # 確かめ直しには ID を書かない 2 段落と、他の段落が引いた 2 つの根拠だけを渡す（質問・回答の全文は同じ）。
    (system, first), (_, recheck) = calls
    assert "段落に ID が無いことを citation_error にしない" in system
    assert "その evidence_id を evidence_id に書く" in system
    assert [passage["id"] for passage in first["answer_passages"]] == ["A2", "A4", "A5", "A6"]
    assert [passage["id"] for passage in recheck["answer_passages"]] == ["A2", "A6"]
    assert [item["id"] for item in recheck["evidence_items"]] == [f"{PLAN}:1", f"{CHANGE}:4"]
    assert recheck["answer_text"] == WINDOW_ANSWER and recheck["question"] == first["question"]


def test_recheck_keeps_the_first_result_unless_it_finds_support() -> None:
    """確かめ直しで出典が決まらない・否定の判定・呼び出しの失敗は、最初の判定のまま (#1412)。"""
    for recheck in ({"A2": ("supported", ""), "A6": ("citation_error", "")},
                    # 根拠を絞った判定なので、否定の判定は採らない。
                    {"A2": ("unsupported", ""), "A6": ("contradicted", _cite(f"{CHANGE}:4"))},
                    # 候補でない根拠（台帳の行）・根拠に無い片段の ID は、確かめ直しでは出典が決まらない。
                    {"A2": ("supported", WINDOW_LEDGER), "A6": ("supported", "E" + "0" * 20)},
                    TimeoutError("確かめ直しの呼び出しが失敗")):
        result, calls = _window_validation(WINDOW_FIRST, recheck)
        assert [claim["status"] for claim in result["claim_checks"]] == [
            "citation_error", "supported", "supported", "citation_error"], recheck
        assert result["claim_checks"][0]["reason"].startswith("未登録の原文ID。")
        assert result["rechecked_passage_ids"] == [] and len(calls) == 2
    # 片方だけ裏付けられれば、その段落だけ替える。
    result, _ = _window_validation(WINDOW_FIRST, {"A2": ("supported", _cite(f"{PLAN}:1")), "A6": ("unsupported", "")})
    assert [claim["status"] for claim in result["claim_checks"]] == [
        "supported", "supported", "supported", "citation_error"]
    assert result["rechecked_passage_ids"] == ["A2"]


def test_written_ids_missing_from_the_evidence_stay_citation_errors_and_are_not_rechecked() -> None:
    """段落が書いた ID が根拠に無い citation_error は確かめ直さず、その ID を候補にもしない (#1412)。"""
    unknown = "ffffffffffffffffffffffffffffffff:" + "7" * 64 + ":9"
    answer = WINDOW_ANSWER.replace(f"{PLAN}:1】", f"{unknown}】")
    first = {**WINDOW_FIRST, "A4": ("supported", unknown)}
    result, calls = _window_validation(first, {"A2": ("supported", _cite(f"{CHANGE}:4")),
                                               "A6": ("supported", _cite(f"{CHANGE}:4"))}, answer=answer)
    assert [claim["status"] for claim in result["claim_checks"]] == [
        "supported", "citation_error", "supported", "supported"]
    assert result["claim_checks"][1]["reason"].startswith("未登録の原文ID。")
    # 確かめ直しの段落に A4（ID を書いた段落）は入らず、候補は A5 が引いた根拠だけ。
    recheck = calls[1][1]
    assert [passage["id"] for passage in recheck["answer_passages"]] == ["A2", "A6"]
    assert [item["id"] for item in recheck["evidence_items"]] == [f"{CHANGE}:4"]
    # 他の段落が引いた根拠がどれも決まらなければ、確かめ直さない。
    answer = answer.replace(f"{CHANGE}:4】", f"{unknown}】")
    result, calls = _window_validation({**first, "A5": ("supported", unknown)}, None, answer=answer)
    assert result["counts"] == {"citation_error": 4} and len(calls) == 1
