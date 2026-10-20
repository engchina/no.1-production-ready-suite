"""最終の検証の supported を、対応・対象・原文で確かめる（#2659・#2657・#2653）。

モデルは録画した応答のスタブ（監査が語・話題の一致で supported と返した形）。根拠・回答は合成。
"""

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from rag_engine.evaluation.answer_validation import (
    QUESTION_ID,
    SUPPORT_QUOTE_UNMATCHED,
    SUPPORT_TARGET_MISMATCH,
    SUPPORT_VALUE_UNBOUND,
    VALIDATION_SYSTEM_PROMPT,
    ClaimAuditOutput,
    validate_answer_claims,
)
from rag_engine.generation.answer_policy import RELATION_SUPPORT_POLICY

TABLE = "<table><tr><th>区分</th><th>選択肢の値</th></tr><tr><td>区分Ⅰ</td><td>01</td></tr><tr><td>区分Ⅱ</td><td>02</td></tr></table>"
# 出力できる帳票の名前と集計の内容の一覧（列の定義ではない）と、明細のファイルの列の定義。
REPORT_LIST = "出力帳票\n帳票名：集計表 A（区分ごとの件数を集計）\n帳票名：集計表 B（月ごとの件数を集計）"
FILE_COLUMNS = "明細ファイルの列\n列 1：対象の番号\n列 2：登録日"
# 同じ文書の、処理 A の取り消しと処理 B のエラーの修正の手順。
PROCESS_A = "処理 A の出力を取り消すには、処理 A の一覧で対象を選び、取消ボタンを押します。"
PROCESS_B = "処理 B でエラー E10 が出た場合は、訂正画面で設定値を 5 に変更します。"


def _audit(verdicts: list[dict[str, Any]]) -> Callable[..., ClaimAuditOutput]:
    """段落の順に、監査の判定（status・applicability・evidence_quote と、根拠の id）を返すスタブ。"""

    def parse(system: str, inputs: str, settings: Any, schema: Any, provider_id: str | None = None) -> ClaimAuditOutput:
        payload = json.loads(inputs)
        evidence = {item["id"]: item["evidence_id"] for item in payload["evidence_items"] if item["id"] != QUESTION_ID}
        claims = []
        for passage, verdict in zip(payload["answer_passages"], verdicts, strict=True):
            claims.append(
                {
                    "answer_quote": "段落",
                    "answer_passage_id": passage["id"],
                    "evidence_id": evidence[verdict["chunk"]],
                    "evidence_quote": verdict.get("quote", ""),
                    "applicability": verdict.get("applicability", "matched"),
                    "status": verdict.get("status", "supported"),
                    "source_id": "",
                    "reason": "資料に記載されている",
                }
            )
        return ClaimAuditOutput.model_validate({"claim_checks": claims})

    return parse


def _validate(answer: str, evidence: dict[str, str], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    items = [{"id": chunk, "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": text} for chunk, text in evidence.items()]
    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=_audit(verdicts)):
        return validate_answer_claims("質問", answer, items, settings=None)


def _by_quote(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {claim["answer_quote"]: claim for claim in result["claim_checks"]}


def test_value_from_another_row_is_not_supported_and_the_audit_reason_is_not_adopted() -> None:
    """表の区分と値の対応を取り違えた段落は、監査が「記載あり」と返しても supported にしない（#2659）。"""
    answer = "区分Ⅰでは選択肢の値 01 を選びます。\n\n区分Ⅱでは選択肢の値 01 を選びます。"
    result = _validate(answer, {"c1": TABLE}, [{"chunk": "c1"}, {"chunk": "c1"}])
    claims = _by_quote(result)
    assert claims["区分Ⅰでは選択肢の値 01 を選びます。"]["status"] == "supported"  # 正しい対応は残す
    wrong = claims["区分Ⅱでは選択肢の値 01 を選びます。"]
    assert (wrong["status"], wrong["audit_status"], wrong["support_check"]) == ("unsupported", "supported", SUPPORT_VALUE_UNBOUND)
    # 理由は根拠の対応を先に書き、監査の「記載あり」は採らなかった判定の理由として残す。
    assert wrong["reason"].startswith("根拠の表では値『01』は『区分Ⅱ』と別の行の値です。")
    assert "採らなかった監査の判定 supported の理由: 資料に記載されている" in wrong["reason"]
    assert result["counts"] == {"supported": 1, "unsupported": 1}


def test_quote_that_is_not_in_the_evidence_is_not_adopted() -> None:
    """監査が写した原文が根拠に無ければ（取り違えた対応を原文として書いた）、supported にしない（#2659）。"""
    answer = "区分Ⅱでは選択肢の値 02 を選びます。\n\n区分Ⅱの値は画面の一覧から選びます。"
    result = _validate(
        answer,
        {"c1": TABLE, "c2": "区分の値は画面の一覧から選びます。"},
        [{"chunk": "c1", "quote": "区分Ⅱ 02"}, {"chunk": "c2", "quote": "区分Ⅱの値は 01 です"}],
    )
    claims = _by_quote(result)
    assert claims["区分Ⅱでは選択肢の値 02 を選びます。"]["status"] == "supported"  # 表の原文を写した裏付けは残す
    assert claims["区分Ⅱでは選択肢の値 02 を選びます。"]["support_quote"] == "区分Ⅱ 02"
    fabricated = claims["区分Ⅱの値は画面の一覧から選びます。"]
    assert (fabricated["status"], fabricated["reference_check"], fabricated["support_check"]) == (
        "citation_error",
        "quote_unmatched",
        SUPPORT_QUOTE_UNMATCHED,
    )


def test_report_list_is_not_adopted_as_file_columns_when_the_audit_finds_another_target() -> None:
    """帳票の一覧で明細のファイルの列を述べた段落は、監査が別の対象とすれば supported にしない（#2657）。"""
    answer = "出力する明細ファイルには、区分ごとの件数の列が含まれます。\n\n明細ファイルの列 1 は対象の番号です。"
    result = _validate(
        answer,
        {"c1": REPORT_LIST, "c2": FILE_COLUMNS},
        [
            {"chunk": "c1", "quote": "集計表 A（区分ごとの件数を集計）", "applicability": "not_applicable"},
            {"chunk": "c2", "quote": "列 1：対象の番号"},
        ],
    )
    claims = _by_quote(result)
    columns = claims["出力する明細ファイルには、区分ごとの件数の列が含まれます。"]
    assert (columns["status"], columns["support_check"]) == ("unsupported", SUPPORT_TARGET_MISMATCH)
    assert claims["明細ファイルの列 1 は対象の番号です。"]["status"] == "supported"  # 本物の列の定義の案内は残す


def test_steps_of_another_process_are_not_supported_and_unknown_targets_are_left_to_the_verdict() -> None:
    """別の処理のエラーの修正の手順は、監査が supported と返しても採らない（#2653）。

    対象が同じか決められない（unknown）は採否に使わない（実の LLM で、対象が同じ正しい段落にも付いた）。
    確かめられない段落は監査の判定（unsupported）で外す。
    """
    answer = (
        "処理 A の一覧で対象を選び、取消ボタンを押します。\n\n"
        "エラー E10 が出た場合は、訂正画面で設定値を 5 に変更します。\n\n"
        "取消の後は、処理 A をもう一度実行します。"
    )
    result = _validate(
        answer,
        {"c1": PROCESS_A, "c2": PROCESS_B},
        [
            {"chunk": "c1", "quote": PROCESS_A},
            {"chunk": "c2", "quote": PROCESS_B, "applicability": "not_applicable"},
            {"chunk": "c1", "applicability": "unknown"},
        ],
    )
    statuses = [(claim["status"], claim["support_check"]) for claim in result["claim_checks"]]
    assert statuses == [
        ("supported", ""),  # 質問の処理の手順は残す（部分回答）
        ("unsupported", SUPPORT_TARGET_MISMATCH),
        ("supported", ""),
    ]
    assert [claim["applicability"] for claim in result["claim_checks"]] == ["matched", "not_applicable", "unknown"]


def test_negative_verdicts_and_reused_claims_are_kept() -> None:
    """否定の判定は変えない。システムが作った判定（applicability が無い）は、対象の一致では外さない。"""
    answer = "区分Ⅱでは選択肢の値 01 を選びます。"
    result = _validate(answer, {"c1": TABLE}, [{"chunk": "c1", "status": "contradicted", "applicability": "not_applicable"}])
    claim = result["claim_checks"][0]
    assert (claim["status"], claim["support_check"]) == ("contradicted", "")
    # 監査の判定を再利用した段落（#1527）も、表の行の対応に反すれば supported にしない。
    items = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": TABLE}]
    with patch("rag_engine.evaluation.answer_eval.parse_text_response") as never:
        reused = validate_answer_claims(
            "質問",
            "- 区分Ⅱでは選択肢の値 01 を選びます。\n- 区分Ⅱでは選択肢の値 02 を選びます。",
            items,
            settings=None,
            audited={"区分Ⅱでは選択肢の値 01 を選びます。": ["c1"], "区分Ⅱでは選択肢の値 02 を選びます。": ["c1"]},
        )
        never.assert_not_called()
    assert [(c["status"], c["support_check"]) for c in reused["claim_checks"]] == [
        ("unsupported", SUPPORT_VALUE_UNBOUND),
        ("supported", ""),
    ]


def test_audit_contract_asks_for_the_evidence_and_the_target_before_the_verdict() -> None:
    """判定（status）の前に、根拠の記載と対象の一致を書かせる（strict schema の生成順）。"""
    claim = ClaimAuditOutput.model_json_schema()["$defs"]["ValidationClaimCheck"]
    order = list(claim["properties"])
    assert order.index("evidence_quote") < order.index("claim_target") < order.index("evidence_target")
    assert order.index("evidence_target") < order.index("applicability") < order.index("status")
    assert claim["properties"]["applicability"]["enum"] == ["matched", "not_applicable", "unknown"]  # null はモデルに渡さない
    assert not {"audit_status", "reference_check", "classification_check", "support_quote", "support_check"} & set(order)
    assert RELATION_SUPPORT_POLICY in VALIDATION_SYSTEM_PROMPT
    assert "evidence_quote は空文字でよい" not in VALIDATION_SYSTEM_PROMPT
