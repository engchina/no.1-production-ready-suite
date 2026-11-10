"""業務ガイドの選択済み指示だけが、順序・上限・重複の規則で回答へ渡る。"""

from rag_engine.knowledge.rule_application import select_rule_applications
from rag_engine.knowledge.rule_matching import select_guide_rules
from rag_engine.knowledge.runtime_knowledge import RuntimeRule


def guide(id, content="合成の指示", **kwargs):
    return RuntimeRule(id, "合成のガイド", content=content, tags=("pinned", "support_guide"), **kwargs)


def test_caller_selected_guides_preserve_order_and_exclude_inactive_and_unpinned():
    rules = (guide("b"), RuntimeRule("old", "合成の旧い登録ルール"), guide("inactive", status="deprecated"), guide("a"))
    result = select_guide_rules(rules, 1)
    assert [r.rule_id for r in result.selected] == ["b"]
    assert [(d["key"], d["reason"], d["rank"]) for d in result.decisions] == [("b", "caller_selected", 1), ("a", "limit", 2)]
    assert not result.decisions[0]["contributions"]


def test_generation_keeps_full_guide_text_deduplicates_and_applies_budget():
    content = "合成の長い指示" * 2000
    rules = (guide("a", content), guide("duplicate", content), guide("empty", ""), guide("b", "別の指示"))
    result = select_rule_applications(rules, 1)
    assert [r.rule_id for r in result.selected] == ["a"]
    assert result.selected[0].content == content
    assert [d["reason"] for d in result.decisions] == [
        "selected",
        "duplicate_instruction",
        "no_generation_instruction",
        "application_budget",
    ]
    assert {d["stage"] for d in result.decisions} == {"generation"}
