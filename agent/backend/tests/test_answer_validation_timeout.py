"""最終の検証が RAG の時間の上限で確かめ終わらなかったときの回答（#2700）の決定論テスト。

RAG は時間の上限までに監査し終わらなかった段落を unassessed（status=partial）で返し、
どの段落も終わらなければ status=timed_out を返す。Agent は確かめ終わった判定で回答を
組み立て、確かめ終わらなかった段落は外さない。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from mcp_support import DEFAULT_OUTPUTS, FakeProductMcp
from pytest import MonkeyPatch
from test_answer_validation import (  # noqa: F401 - fixture を使う
    ANSWER,
    _searched_run,
    _validation,
    mcp,
)

from app.features.agent.answer_validation import (
    TIMED_OUT_KEPT_INTRO,
    UNVERIFIED_HEADING,
    WITHHELD_INTRO,
    merge_results,
    publish_answer,
)
from app.features.agent.runtime import run_answer_text

FIRST = "契約の更新は 30 日前までに申し出ます。"
SECOND = "申し出は所定の様式で行います。"
THIRD = "更新の手数料はかかりません。"
PARTIAL_ANSWER = f"{FIRST}\n\n{SECOND}\n\n{THIRD}"


def _claim(quote: str, status: str) -> dict[str, Any]:
    claim: dict[str, Any] = {"answer_quote": quote, "status": status, "reason": "理由"}
    if status in {"supported", "contradicted"}:
        claim["chunk_id"] = "chunk-1"
    return claim


def _result(status: str, *claims: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = deepcopy(DEFAULT_OUTPUTS["rag_validate_answer"])
    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim["status"]] = counts.get(claim["status"], 0) + 1
    output.update({"valid": False, "status": status, "counts": counts, "claims": list(claims)})
    return output


def test_partial_result_keeps_the_unfinished_passages_and_withholds_refuted_ones() -> None:
    result = _result(
        "partial",
        _claim(FIRST, "supported"),
        _claim(SECOND, "unassessed"),
        _claim(THIRD, "contradicted"),
    )

    published, withheld = publish_answer(PARTIAL_ANSWER, result)

    body, section = published.split(f"\n\n**{UNVERIFIED_HEADING}**\n\n", 1)
    # 根拠と矛盾した段落は外し、時間切れで確かめ終わらなかった段落は本文に残す。
    assert body == f"{FIRST}\n\n{SECOND}"
    lines = section.splitlines()
    assert lines[0] == WITHHELD_INTRO
    assert TIMED_OUT_KEPT_INTRO in lines
    assert any(SECOND in line for line in lines[lines.index(TIMED_OUT_KEPT_INTRO) + 1 :])
    assert withheld == {"claims": 1, "findings": 0, "all": False, "unverified": 1}


def test_partial_result_with_only_unfinished_passages_keeps_the_whole_answer() -> None:
    result = _result("partial", _claim(FIRST, "supported"), _claim(SECOND, "unassessed"))
    answer = f"{FIRST}\n\n{SECOND}"

    published, withheld = publish_answer(answer, result)

    body, section = published.split(f"\n\n**{UNVERIFIED_HEADING}**\n\n", 1)
    assert body == answer
    assert section.splitlines()[0] == TIMED_OUT_KEPT_INTRO
    assert WITHHELD_INTRO not in section
    assert withheld == {"claims": 0, "findings": 0, "all": False, "unverified": 1}


def test_merge_results_keeps_partial_when_one_connection_timed_out() -> None:
    merged = merge_results(
        [
            _result("completed", _claim(FIRST, "supported")),
            _result("partial", _claim(FIRST, "unassessed")),
        ]
    )
    assert merged["status"] == "partial"
    assert merged["valid"] is False
    # 同じ段落は、どれかの接続で確かめ終わった判定を使う。
    assert [claim["status"] for claim in merged["claims"]] == ["supported"]


def test_timed_out_validation_keeps_the_answer_with_a_notice(
    monkeypatch: MonkeyPatch,
    mcp: FakeProductMcp,  # noqa: F811 - fixture
) -> None:
    mcp.outputs["rag_validate_answer"] = _result("timed_out")

    run, _ = _searched_run(monkeypatch)

    assert run_answer_text(run) == f"{ANSWER}\n\nこの回答は検証できませんでした。"
    content = _validation(run)
    assert (content["status"], content["reason"]) == ("unvalidated", "validation_timed_out")


def test_partial_validation_run_keeps_the_unfinished_answer(
    monkeypatch: MonkeyPatch,
    mcp: FakeProductMcp,  # noqa: F811 - fixture
) -> None:
    mcp.outputs["rag_validate_answer"] = _result("partial", _claim(ANSWER, "unassessed"))

    run, _ = _searched_run(monkeypatch)

    assert (run_answer_text(run) or "").startswith(f"{ANSWER}\n\n**{UNVERIFIED_HEADING}**")
    content = _validation(run)
    assert content["status"] == "completed"
    assert content["result"]["status"] == "partial"
    assert content["withheld"]["unverified"] == 1
