"""最終の検証の supported を、対応・対象・原文で確かめる（#2659・#2657・#2653）。

モデルは録画した応答のスタブ（監査が語・話題の一致で supported と返した形）。根拠・回答は合成。
"""

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from rag_engine.evaluation.answer_validation import (
    QUESTION_ID,
    SUPPORT_QUOTE_UNMATCHED,
    SUPPORT_TARGET_MISMATCH,
    SUPPORT_VALUE_UNBOUND,
    VALIDATION_SYSTEM_PROMPT,
    ClaimAuditOutput,
    _bind_validation,
    _enforce_support,
    validate_answer_claims,
)
from rag_engine.evaluation.final_validation_units import final_unit_schema, prepare_unit_request
from rag_engine.generation.answer_policy import RELATION_SUPPORT_POLICY

TABLE = "<table><tr><th>区分</th><th>選択肢の値</th></tr><tr><td>区分Ⅰ</td><td>01</td></tr><tr><td>区分Ⅱ</td><td>02</td></tr></table>"
# 出力できる帳票の名前と集計の内容の一覧（列の定義ではない）と、明細のファイルの列の定義。
REPORT_LIST = "出力帳票\n帳票名：集計表 A（区分ごとの件数を集計）\n帳票名：集計表 B（月ごとの件数を集計）"
FILE_COLUMNS = "明細ファイルの列\n列 1：対象の番号\n列 2：登録日"
# 同じ文書の、処理 A の取り消しと処理 B のエラーの修正の手順。
PROCESS_A = "処理 A の出力を取り消すには、処理 A の一覧で対象を選び、取消ボタンを押します。"
PROCESS_B = "処理 B でエラー E10 が出た場合は、訂正画面で設定値を 5 に変更します。"


def _audit(verdicts: list[dict[str, Any]]) -> Callable[..., ClaimAuditOutput]:
    """固定fixtureが選んだ出典の、実送達unit IDを返す（意味判定の補修はしない）。"""

    def parse(system: str, inputs: str, settings: Any, schema: Any, provider_id: str | None = None) -> ClaimAuditOutput:
        payload = json.loads(inputs)
        evidence = {item["id"]: item for item in payload["evidence_items"] if item["id"] != QUESTION_ID}
        claims = []
        for passage, verdict in zip(payload["answer_passages"], verdicts, strict=True):
            selected = verdict.get("unit_ids", [unit["unit_id"] for unit in evidence[verdict["chunk"]]["units"]])
            if verdict.get("repeat_first_unit"):
                selected = [selected[0], selected[0]]
            claims.append(
                {
                    "answer_quote": "段落",
                    "answer_passage_id": passage["id"],
                    "evidence_unit_ids": selected,
                    "applicability": verdict.get("applicability", "matched"),
                    "status": verdict.get("status", "supported"),
                    "reason": verdict.get("reason", "資料に記載されている"),
                }
            )
        return schema.model_validate({"claim_checks": claims})

    return parse


def _validate(answer: str, evidence: dict[str, str], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    items = [{"id": chunk, "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": text} for chunk, text in evidence.items()]
    answer = "\n\n".join(part + "\n根拠：manual.pdf p.1" for part in answer.split("\n\n"))
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
    # 表が否定した対応は根拠と矛盾（#2998）。理由は表の正しい対応を先に書き、監査の「記載あり」は採らなかった判定の理由として残す。
    assert (wrong["status"], wrong["audit_status"], wrong["support_check"]) == ("contradicted", "supported", SUPPORT_VALUE_UNBOUND)
    assert wrong["reason"].startswith("根拠の表では『区分Ⅱ』の値は『02』（『01』は別の行の値）です。")
    assert "採らなかった監査の判定 supported の理由: 資料に記載されている" in wrong["reason"]
    assert result["counts"] == {"supported": 1, "contradicted": 1}


def test_values_listed_from_several_rows_stay_supported() -> None:
    """複数の行の値の列挙・範囲を述べた段落は、表の対応と違うとしない（#2734）。"""
    table = (
        "<table><tr><td>コード</td><td>名称</td><td>単価</td></tr>"
        "<tr><td>A101</td><td>項目甲</td><td>254</td></tr>"
        "<tr><td>A102</td><td>項目乙</td><td>253</td></tr>"
        "<tr><td>A103</td><td>項目丙</td><td>300</td></tr></table>"
    )
    listed, ranged, wrong_text = (
        "単価の列に 254・253・300 などが表示されている。",
        "対象はコード A101〜A103 で、単価は 254 から 300 です。",
        "項目甲の単価は 253 です。",
    )
    answer = "\n\n".join([listed, ranged, wrong_text])
    result = _validate(answer, {"c1": table}, [{"chunk": "c1"}, {"chunk": "c1"}, {"chunk": "c1"}])
    claims = _by_quote(result)
    assert claims[listed]["status"] == "supported"
    assert claims[ranged]["status"] == "supported"
    wrong = claims[wrong_text]
    assert (wrong["status"], wrong["support_check"]) == ("contradicted", SUPPORT_VALUE_UNBOUND)


@pytest.mark.parametrize("invalid_mode", ["unsent", "duplicate"])
def test_unconfirmed_unit_ids_are_not_adopted(invalid_mode: str) -> None:
    """未送達・重複unit参照は本文や出典の推定でsupportedへ回復しない。"""
    answer = "区分Ⅱでは選択肢の値 02 を選びます。\n\n区分Ⅱの値は画面の一覧から選びます。"
    result = _validate(
        answer,
        {"c1": TABLE, "c2": "区分の値は画面の一覧から選びます。"},
        [{"chunk": "c1"}, {"chunk": "c2", **({"unit_ids": ["U-not-sent"]} if invalid_mode == "unsent" else {"repeat_first_unit": True})}],
    )
    claims = _by_quote(result)
    correct = claims["区分Ⅱでは選択肢の値 02 を選びます。"]
    assert correct["status"] == "supported"
    assert correct["support_quote"] == ""
    fabricated = claims["区分Ⅱの値は画面の一覧から選びます。"]
    assert (fabricated["status"], fabricated["reference_check"]) == ("citation_error", "audit_id_invalid")
    assert fabricated["support_quote"] == ""
    assert result["_selected_evidence"][1] == []


def test_legacy_quote_not_in_the_evidence_remains_unadopted() -> None:
    """旧保存形式のquote照合は新wire適合とは分け、従来の拒否契約を残す。"""
    answer = "区分Ⅱの値は画面の一覧から選びます。"
    source = {"id": "c1", "evidence_id": "E1", "text": "区分の値は画面の一覧から選びます。"}
    catalog = {"E1": source}
    passages = [{"id": "p1", "text": answer}]
    output = ClaimAuditOutput.model_validate(
        {
            "claim_checks": [
                {
                    "answer_quote": answer,
                    "answer_passage_id": "p1",
                    "evidence_id": "E1",
                    "evidence_quote": "区分Ⅱの値は 01 です",
                    "source_id": "",
                    "applicability": "matched",
                    "status": "supported",
                    "reason": "合成の旧監査",
                }
            ]
        }
    )
    bound = _bind_validation(output, catalog, passages)
    claims = _enforce_support(bound.claim_checks, passages, catalog, {"c1": source["text"]}, [source["text"]])
    assert (claims[0].status, claims[0].reference_check, claims[0].support_check) == (
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


def test_steps_of_another_process_are_not_supported_and_unknown_targets_remain_unverified() -> None:
    """別の処理のエラーの修正の手順は、監査が supported と返しても採らない（#2653）。

    対象が同じか決められない（unknown）は unassessed とし、モデルの supported だけで昇格しない。
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
        ("unassessed", "target_unverified"),
    ]
    assert [claim["applicability"] for claim in result["claim_checks"]] == ["matched", "not_applicable", "unknown"]


def test_negative_verdicts_and_reused_claims_are_kept() -> None:
    """否定の判定は変えない。システムが作った判定（applicability が無い）は、対象の一致では外さない。"""
    answer = "区分Ⅱでは選択肢の値 01 を選びます。"
    result = _validate(answer, {"c1": TABLE}, [{"chunk": "c1", "status": "contradicted", "applicability": "not_applicable"}])
    claim = result["claim_checks"][0]
    assert (claim["status"], claim["support_check"]) == ("contradicted", "")
    # 監査の判定を再利用した段落（#1527）も、表の行の対応に反すれば根拠と矛盾にする（#2998）。
    items = [{"id": "c1", "source": "manual.pdf", "page_start": 1, "page_end": 1, "text": TABLE}]
    schemas: list = []

    def no_audit(system, inputs, settings, schema, provider_id=None):
        # 監査は呼ばない。表の値に直す呼び出し（#3005）だけが来るので、失敗させて矛盾のままにする。
        schemas.append(schema.__name__)
        raise RuntimeError("synthetic failure")

    with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=no_audit):
        reused = validate_answer_claims(
            "質問",
            "- 区分Ⅱでは選択肢の値 01 を選びます。\n- 区分Ⅱでは選択肢の値 02 を選びます。\n根拠：manual.pdf p.1",
            items,
            settings=None,
            audited={"区分Ⅱでは選択肢の値 01 を選びます。": ["c1"], "区分Ⅱでは選択肢の値 02 を選びます。": ["c1"]},
        )
    assert schemas == ["TableRevision"]
    assert [(c["status"], c["support_check"]) for c in reused["claim_checks"]] == [
        ("contradicted", SUPPORT_VALUE_UNBOUND),
        ("supported", ""),
    ]


def test_audit_contract_asks_for_the_evidence_and_the_target_before_the_verdict() -> None:
    """判定（status）の前に、根拠の記載と対象の一致を書かせる（strict schema の生成順）。"""
    request = prepare_unit_request({"evidence_items": [{"id": "c1", "evidence_id": "E1", "text": "合成原文"}]})
    from rag_engine.evaluation.answer_validation import ValidationClaimCheck

    wire = final_unit_schema(ValidationClaimCheck, request)
    claim = wire.model_json_schema()["$defs"]["FinalValidationUnitClaim"]
    order = list(claim["properties"])
    assert order.index("evidence_unit_ids") < order.index("claim_target") < order.index("evidence_target")
    assert order.index("evidence_target") < order.index("applicability") < order.index("status")
    assert claim["properties"]["applicability"]["enum"] == ["matched", "not_applicable", "unknown"]
    assert not {
        "evidence_id",
        "evidence_quote",
        "source_id",
        "audit_status",
        "reference_check",
        "classification_check",
        "support_quote",
        "support_check",
        "unit_selection",
    } & set(order)
    assert RELATION_SUPPORT_POLICY in VALIDATION_SYSTEM_PROMPT
    assert "evidence_unit_ids" in VALIDATION_SYSTEM_PROMPT


def test_value_from_another_row_record_is_not_supported() -> None:
    """表計算・CSV の 1 行の記録の根拠をまたいで値を結び付けた段落も、同じ表に組み直して見つける（#2594）。"""
    cells = [["A", ["区分"]], ["B", ["上限値"]]]
    rows = {"r2": (2, "区分: 区分 A / 上限値: 120"), "r3": (3, "区分: 区分 B / 上限値: 300")}

    def items(with_record: bool) -> list[dict[str, Any]]:
        return [
            {
                "id": chunk,
                "source": "table.csv",
                "text": text,
                **({"table_record": {"group": "doc|set|csv", "row": row, "cells": cells}} if with_record else {}),
            }
            for chunk, (row, text) in rows.items()
        ]

    answer = "区分 A の上限値は 120 です。\n根拠：table.csv\n\n区分 A の上限値は 300 です。\n根拠：table.csv"
    verdicts = [{"chunk": "r2"}, {"chunk": "r3"}]
    for with_record, expected in ((False, "supported"), (True, "contradicted")):
        with patch("rag_engine.evaluation.answer_eval.parse_text_response", side_effect=_audit(verdicts)):
            result = validate_answer_claims("質問", answer, items(with_record), settings=None)
        claims = _by_quote(result)
        assert claims["区分 A の上限値は 120 です。"]["status"] == "supported"
        wrong = claims["区分 A の上限値は 300 です。"]
        assert wrong["status"] == expected
    assert wrong["support_check"] == SUPPORT_VALUE_UNBOUND
    assert wrong["reason"].startswith("根拠の表では『区分 A』の値は『120』（『300』は別の行の値）です。")


def test_word_values_and_audit_reasons_stating_another_rows_mapping_are_not_supported() -> None:
    """日本語の値の対応の取り違えと、根拠と逆の対応を「記載あり」と述べた監査の理由を supported にしない（#2659）。"""
    table = (
        "<table><tr><th>区分</th><th>処理の周期</th></tr>"
        "<tr><td>区分A</td><td>月次処理</td></tr><tr><td>区分B</td><td>日次処理</td></tr></table>"
    )
    swapped, correct = "区分Bは月次処理です。", "区分Aは月次処理です。"
    result = _validate(
        "\n\n".join([swapped, correct]),
        {"c1": table},
        [
            {"chunk": "c1"},
            # 段落は正しいが、監査の理由が逆の対応を根拠にある対応として述べた。
            {"chunk": "c1", "reason": "根拠の表に、区分Bは月次処理と記載されている。"},
        ],
    )
    claims = _by_quote(result)
    assert (claims[swapped]["status"], claims[swapped]["support_check"]) == ("contradicted", SUPPORT_VALUE_UNBOUND)
    assert claims[swapped]["reason"].startswith("根拠の表では『区分B』の値は『日次処理』（『月次処理』は別の行の値）です。")
    misread = claims[correct]
    assert (misread["status"], misread["support_check"]) == ("unsupported", SUPPORT_VALUE_UNBOUND)
    assert misread["reason"].startswith("監査の理由が、根拠の表と違う対応")
    # 理由が根拠どおりの対応なら supported のまま。
    kept = _validate(correct, {"c1": table}, [{"chunk": "c1", "reason": "根拠の表に、区分Aは月次処理と記載されている。"}])
    assert kept["counts"] == {"supported": 1}
