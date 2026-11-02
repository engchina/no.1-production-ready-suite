"""未校正の監査の矛盾の判定で、複合した説明の本文と要約を外さない。"""

from dataclasses import replace

from test_grounded_answer import FakeModel, audit, context, draft, ids, item, observed_audit, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answering import synthesize_grounded_answer

OPEN = "対象画面を開きます。"
BAD = "内容を保存します。"
SOURCE = OPEN + "内容は保存しません。"
COMPOUND = OPEN + BAD


def test_uncalibrated_partial_contradiction_keeps_original_body_and_summary():
    question = "対象画面を開く操作と保存の可否は？"
    ctx = context(record(0, SOURCE, "設定", source="操作説明.pdf"))
    eid = ids(ctx, question)[SOURCE]
    verdict = audit((0, "contradicted", "matched", ""), requests=[("Q1", "partial")], summary_supported=True)
    model = FakeModel([draft(item(eid, COMPOUND, SOURCE, request_id="Q1"), summary=COMPOUND)], [verdict])

    def unused(*args, **kwargs):
        raise AssertionError("外部I/Oは不要")

    with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
        response = synthesize_grounded_answer(
            question,
            ctx,
            replace(get_settings(environ={}, dotenv_path=None), answer_max_correction_rounds=0),
            image_prompt_mode="text_only",
        ).response
    assert COMPOUND in response.answer_text
    assert response.answer_text.startswith(COMPOUND)
    assert len([name for name, _prompt in model.prompts if name in {"GroundedAudit", "GroundedFactAudit"}]) == 1
    trace = response.generation_trace["rounds"][0]
    assert trace["draft"]["items"][0]["text"] == COMPOUND
    assert trace["items"][0]["review_assessment"]["effective_support"] == "unknown"
    raw = observed_audit(response)["reviews"][0]
    assert raw["support"] == "contradicted"
    assert not response.envelope["removed_passages"]
