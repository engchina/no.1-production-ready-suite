"""回答修正の判断・計画・採否を、生成アルゴリズムと分けて記録する。"""

from dataclasses import replace
from unittest.mock import patch

from test_answer_round_latency import MaxCorrectionRoundsTest
from test_grounded_answer import QUESTION, FakeModel, audit
from test_grounded_round_budget import _Clock, _context, _draft_with_dropped

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation import answering, execution_record, grounded
from rag_engine.resources import deadline


def run_recorded(model, ctx, *, corrections=2, clock=None):
    steps = []
    token = execution_record._structured_steps.set(steps)

    def unused(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    settings = replace(get_settings(environ={}, dotenv_path=None), answer_max_correction_rounds=corrections, answer_audit_mode="detailed")
    try:
        with bind_dependencies(AnswerDependencies(unused, unused, model, unused, unused)):
            if clock is None:
                result = answering.synthesize_grounded_answer(QUESTION, ctx, settings, image_prompt_mode="text_only")
            else:
                with patch.object(deadline, "remaining_seconds", clock.remaining), patch.object(grounded, "monotonic", lambda: clock.now):
                    result = answering.synthesize_grounded_answer(QUESTION, ctx, settings, image_prompt_mode="text_only")
        return result.response, steps
    finally:
        execution_record._structured_steps.reset(token)


def fields(step):
    return {row["label"]: row["value"] for row in step.get("details", {}).get("fields", [])}


def fixture():
    ctx, model = MaxCorrectionRoundsTest()._miscopied()
    return ctx, model.drafts[0], model.drafts[1], model.audits[0]


def test_no_correction_is_completed_and_has_no_plan():
    ctx, _, fixed, review = fixture()
    result, steps = run_recorded(FakeModel([fixed], [review]), ctx)
    checks = [step for step in steps if step["name"] == "回答の修正が必要か確認する"]
    assert fields(checks[0])["結果"] == "修正は不要です"
    assert checks[0]["status"] != "skipped"
    assert not any(step["name"] == "是正" for step in steps)
    assert fields(steps[-1])["最終採用"] == "1 回目"


def test_correction_is_adopted_separately_from_call_success():
    ctx, wrong, fixed, review = fixture()
    result, steps = run_recorded(FakeModel([wrong, fixed], [review]), ctx)
    assert [row["accepted"] for row in result.generation_trace["rounds"]] == [True, True]
    assert any(step["name"] == "是正" for step in steps)
    assert fields(steps[-1])["最終採用"] == "2 回目"
    assert all("採否" in fields(step) for step in steps if step["name"].startswith("回答文の生成と根拠確認"))


def test_not_adopted_round_keeps_first_answer():
    ctx, wrong, _, review = fixture()
    result, steps = run_recorded(FakeModel([wrong, wrong], [review]), ctx)
    assert [row["accepted"] for row in result.generation_trace["rounds"]] == [True, False]
    assert fields(steps[-1])["最終採用"] == "1 回目"
    assert "改善が確認できないため" in fields(steps[-1])["結果"]
    second = next(step for step in steps if step["name"] == "回答文の生成と根拠確認（2回目）")
    assert second["status"] != "skipped"
    assert fields(second)["採否"].startswith("不採用")


def test_correction_count_limit_is_skipped_with_real_reason():
    ctx, wrong, _, review = fixture()
    result, steps = run_recorded(FakeModel([wrong], [review]), ctx, corrections=0)
    skipped = next(step for step in steps if step["name"] == "回答の修正")
    assert skipped["status"] == "skipped"
    assert "回数の上限" in fields(skipped)["結果"]
    assert fields(steps[-1])["最終採用"] == "1 回目"


def test_correction_time_limit_is_skipped_without_changing_budget():
    ctx, eid = _context()
    clock = _Clock(600.0)

    def slow_draft(prompt):
        clock.now += 250.0
        return _draft_with_dropped(eid)

    def slow_audit(prompt):
        clock.now += 100.0
        return audit((0, "supported", "matched", ""))

    result, steps = run_recorded(FakeModel([slow_draft], [slow_audit]), ctx, clock=clock)
    assert len(result.generation_trace["rounds"]) == 1
    skipped = next(step for step in steps if step["name"] == "回答の修正")
    assert skipped["status"] == "skipped"
    assert "時間の残り" in fields(skipped)["結果"]
    assert fields(steps[-1])["最終採用"] == "1 回目"
