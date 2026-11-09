"""検索命中と独立したガイドの必須範囲を実行入力まで保持する。実I/Oは0。"""

from dataclasses import replace
from pathlib import Path

from test_guide_steps import context

from rag_engine.generation import answering, grounded
from rag_engine.knowledge.runtime_knowledge import RuntimeKnowledgeContext
from rag_engine.retrieval.guide_steps import GuidePlan, GuideStep

QUESTION = "設定の方法は？"


def plan() -> GuidePlan:
    return GuidePlan(
        guide_id="synthetic-guide",
        revision=3,
        steps=(
            GuideStep(step_id="open", title="画面を開く"),
            GuideStep(
                step_id="change",
                title="内容を変更する",
                depends_on=("open",),
                done_when="変更を確定する",
                evidence_requirements=("対象の選択",),
            ),
            GuideStep(step_id="conditional", title="条件付きの確認", required=False, depends_on=("change",)),
        ),
    )


def test_scope_keeps_all_steps_and_dependencies_without_claiming_support() -> None:
    rows = plan().task_scope
    assert [row["step_id"] for row in rows] == ["open", "change", "conditional"]
    assert [row["required"] for row in rows] == [True, True, False]
    assert rows[1]["depends_on"] == ["open"]
    assert rows[1]["done_when"] == "変更を確定する"
    assert rows[1]["evidence_requirements"] == ["対象の選択"]
    assert rows[0]["requirements"] == [{"id": "open:0", "text": "画面を開く", "kind": "completion"}]
    assert rows[1]["requirements"] == [
        {"id": "change:0", "text": "対象の選択", "kind": "evidence"},
        {"id": "change:1", "text": "変更を確定する", "kind": "completion"},
    ]
    empty = GuidePlan(guide_id="empty", revision=1, steps=(GuideStep(step_id="empty", title=""),))
    assert empty.task_scope[0]["requirements"] == []
    assert all(row["guide_id"] == "synthetic-guide" and row["revision"] == 3 for row in rows)
    assert not any("support" in row or "evidence_ids" in row for row in rows)


def test_scope_reaches_generation_without_any_material_association() -> None:
    runtime = RuntimeKnowledgeContext(
        original_question=QUESTION, expanded_question=QUESTION, path=Path("synthetic.json"), guide_task_scope=plan().task_scope
    )
    assert not runtime.has_matches
    prompt = answering._grounded_prompt(QUESTION, [], context(), (), None, runtime, template="{{question}}\n{{answer_context}}")
    assert '"guide_id": "synthetic-guide"' in prompt
    assert '"step_id": "conditional"' in prompt
    assert '"required": false' in prompt
    assert '"depends_on": ["open"]' in prompt


def test_condition_notices_reach_generation_after_task_scope() -> None:
    """確定した条件で選んだ分岐の補足は、生成の入力の guide_task_scope の直後に、ガイドの文のまま入る。"""
    note = "合成の補足: 作業は担当者 B へ依頼するよう明記する。"
    runtime = RuntimeKnowledgeContext(
        original_question=QUESTION,
        expanded_question=QUESTION,
        path=Path("synthetic.json"),
        guide_task_scope=plan().task_scope,
        guide_condition_notices=(note,),
    )
    template = "{{question}}\n{{answer_context}}"
    prompt = answering._grounded_prompt(QUESTION, [], context(), (), None, runtime, template=template)
    without = answering._grounded_prompt(QUESTION, [], context(), (), None, replace(runtime, guide_condition_notices=()), template=template)
    assert prompt.count(note) == 1
    assert prompt.index("guide_task_scope:\n") < prompt.index("guide_condition_notices:\n") < prompt.index(note)
    assert "guide_condition_notices" not in without
    # 補足の読み方は、業務ガイドの部品があるときに渡す 1 か所にだけある。
    assert grounded.GUIDE_RULES_PROMPT.count("guide_condition_notices") == 1
    assert "guide_condition_notices" not in grounded.GENERATE_SYSTEM_PROMPT
