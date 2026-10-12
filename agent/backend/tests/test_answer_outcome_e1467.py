"""評価 e1467 の記録で取り違えた回答の対応（#1491）を、記録から作った fixture で確かめる。

fixture（`fixtures/answer_outcome_e1467.json`）は業務支援の評価（D）の Run の記録から、対応の
決定に使う最小の項目だけを残したもの（モデルの最終の回答・ツールの名前と対応・最終の検証の段落の
判定と指摘）。
モデルは呼ばない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.features.agent.answer_outcome import answer_outcome
from app.features.agent.answer_validation import publish_answer
from app.features.agent.runtime import RunStep, StepStatus
from app.features.agent.tools import ToolCall, ToolResult

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "answer_outcome_e1467.json").read_text(encoding="utf-8")
)


def _steps(case: dict[str, Any], *, rag_outcome: str | None = None) -> list[RunStep]:
    steps = []
    for item in case["steps"]:
        output = dict(item["output"])
        if rag_outcome is not None and item["name"].endswith("rag_search"):
            output["outcome"] = rag_outcome
        steps.append(
            RunStep(
                run_id="run-1",
                status=StepStatus.COMPLETED if item["success"] else StepStatus.FAILED,
                tool_call=ToolCall(name=item["name"], arguments={}),
                tool_result=ToolResult(
                    name=item["name"],
                    success=item["success"],
                    output=output if item["success"] else None,
                ),
            )
        )
    return steps


def _outcome(name: str, **kwargs: Any) -> dict[str, Any]:
    case = FIXTURE[name]
    validation = kwargs.pop("validation", case["validation"])
    return answer_outcome(case["answer"], steps=_steps(case, **kwargs), validation=validation)


def test_withheld_supplement_after_a_supported_conclusion_stays_answered() -> None:
    # da-trial-account-expiry: 結論（最長 30 日）は supported で、外したのは補足の「なお、…
    # 見当たりませんでした。」だけ。withheld_claims で conditional になっていた（期待は answered）。
    content = _outcome("da-trial-account-expiry")
    assert (content["value"], content["basis"]) == ("answered", "rag_search")
    assert content["signals"] == ["supplement_withheld"]


def test_withheld_conclusion_is_still_conditional() -> None:
    # 同じ回答で、結論の段落を外したなら答えの不足（withheld_claims）のまま。
    case = FIXTURE["da-trial-account-expiry"]
    validation = json.loads(json.dumps(case["validation"]))
    for claim in validation["result"]["claims"]:
        if claim["status"] == "supported" and "30" in claim["answer_quote"]:
            claim["status"] = "unsupported"
    content = _outcome("da-trial-account-expiry", validation=validation)
    assert content["value"] == "conditional"
    assert "withheld_claims" in content["signals"]


def test_guide_step_error_that_withholds_the_whole_answer_needs_a_human() -> None:
    # mt-delete-account-approved（2 回目の発言）: 業務ガイドの手順の検査の error
    # （step_wrong_branch）で本文をすべて外した。資料が無いのではないので needs_human。
    validation = FIXTURE["mt-delete-account-approved"]["validation"]
    assert validation["withheld"]["all"] is True
    content = _outcome("mt-delete-account-approved")
    assert (content["value"], content["basis"]) == ("needs_human", "validation_withheld_guide")
    assert "check_errors" in content["signals"]


def test_without_the_false_step_error_the_body_is_kept() -> None:
    # RAG の手順の照合が満たした前提の言及を数えなくなると（#1491）、残る error は要求の不足だけで、
    # 本文は外さず、内側の rag_search の対応に拠る。
    case = FIXTURE["mt-delete-account-approved"]
    result = json.loads(json.dumps(case["validation"]["result"]))
    result["findings"] = [item for item in result["findings"] if item["check"] != "guide_steps"]
    _text, withheld = publish_answer(case["answer"], result)
    assert withheld["all"] is False
    validation = {**case["validation"], "result": result, "withheld": withheld}
    # #1453 の後の RAG は、承認・ロールを利用者の条件にして conditional を返す。
    content = _outcome(
        "mt-delete-account-approved", validation=validation, rag_outcome="conditional"
    )
    assert (content["value"], content["basis"]) == ("conditional", "rag_search")


@pytest.mark.parametrize(
    ("rag_outcome", "expected"),
    [("insufficient_evidence", "insufficient_evidence"), ("needs_environment_data", "conditional")],
    ids=["rag-insufficient", "rag-environment-data"],
)
def test_reference_material_only_rag_search_concludes_absence(
    rag_outcome: str, expected: str
) -> None:
    # km-auto-disable: 内側の rag_search は要求がすべて missing で、確認は別の資料の行だけ。RAG が
    # insufficient_evidence を返すようになると（#1491）、答えなし（absence_conclusion）になる。
    content = _outcome("km-auto-disable", rag_outcome=rag_outcome)
    assert content["value"] == expected
    if expected == "insufficient_evidence":
        assert content["basis"] == "absence_conclusion"
