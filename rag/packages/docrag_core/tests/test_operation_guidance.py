"""操作案内の十分性と、未確認データ・資料を残す契約の回帰テスト。"""
from unittest.mock import patch

import pytest

from docrag.generation.answer_policy import OPERATION_GUIDANCE_POLICY
from docrag.generation.answering import (
    AnswerContext, _grade_crag_retrieval,
)
from docrag.knowledge.prompt_files import DEFAULT_VLM_ANSWER_PROMPT
from docrag.models.llm import CragRetrievalGradeOutput
from docrag.retrieval.task_contract import task_contract
from test_task_contract_audit import record


# 十分性は business_object と relevant な候補で決まる（OR 型、#983）。他の観点の不足や根拠の無い data_confirmation は
# 十分性を落とさず missing_aspects に残る。
@pytest.mark.parametrize("aspect,status,source,reason,duplicate,expected,expected_missing", [
    ("requested_result", "data_confirmation", "a", "現在の出力を閲覧し位置を照合", False, True, ()),
    ("applicability", "data_confirmation", "a", "設定値を閲覧し適用条件と照合", False, True, ()),
    ("requested_result", "data_confirmation", "unknown", "実値を照合", False, True, ("requested_result",)),
    ("requested_result", "data_confirmation", "a", " ", False, True, ("requested_result",)),
    ("requested_result", "data_confirmation", "a", "実値を照合", True, True, ("requested_result",)),
    ("procedure", "data_confirmation", "a", "操作自体は未確認", False, True, ("procedure",)),
    ("business_object", "data_confirmation", "a", "対象不明", False, False, ("business_object",)),
    ("applicability", "missing", "a", "適用版の文書が不足", False, True, ("applicability",)),
])
def test_crag_sufficiency_needs_business_object_and_reports_other_gaps(aspect, status, source, reason, duplicate, expected, expected_missing):
    question = "印字位置の確認方法"
    r = record("a", "帳票設定画面で位置設定を確認し、現在の出力と照合する。")
    checks = [dict(aspect=k, status="supported", source_ids=["a"], reason="操作の根拠")
              for k in task_contract(question)["required_aspects"]]
    target = next(c for c in checks if c["aspect"] == aspect)
    target.update(status=status, source_ids=[source], reason=reason)
    if duplicate:
        checks.append(dict(target))
    output = CragRetrievalGradeOutput(sufficient=True, confidence=.9, candidate_verdicts=[dict(chunk_uid="a", relevant=True, reason="関連")],
                                     reason="操作案内", rewritten_query="", aspect_checks=checks)
    with patch("docrag.generation.answering.parse_text_response", return_value=output) as parse:
        result = _grade_crag_retrieval(1, question, question, [question],
                                      AnswerContext(records=[r], text=r.text), object())
    assert result.sufficient is expected
    assert result.missing_aspects == expected_missing
    # 判定 prompt には共有方針の全文ではなく、十分性に関わる要約だけを載せる (#957)。
    assert OPERATION_GUIDANCE_POLICY not in parse.call_args.args[1]
    assert "実値が未提供でも、根拠にある手順・規則・確認方法を説明できれば十分とする" in parse.call_args.args[1]


def test_default_and_optional_evaluation_use_guidance_goal():
    from docrag.evaluation.answer_eval import SYSTEM_PROMPT

    from docrag.generation.grounded import GENERATE_SYSTEM_PROMPT

    # 生成 prompt は共有方針の全文を結合せず、items 向けに書いた方針を持つ (#960)。編集可能テンプレートには重複させない。
    assert OPERATION_GUIDANCE_POLICY not in GENERATE_SYSTEM_PROMPT
    assert "4.1 例示値と実データ" in GENERATE_SYSTEM_PROMPT
    assert OPERATION_GUIDANCE_POLICY not in DEFAULT_VLM_ANSWER_PROMPT
    assert OPERATION_GUIDANCE_POLICY in SYSTEM_PROMPT
    assert "未提供なら `confidence=low`" not in GENERATE_SYSTEM_PROMPT + DEFAULT_VLM_ANSWER_PROMPT
    assert "「関連資料を追加する」" not in GENERATE_SYSTEM_PROMPT + DEFAULT_VLM_ANSWER_PROMPT
