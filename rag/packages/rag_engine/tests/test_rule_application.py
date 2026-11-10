"""選択済みの業務ガイドの指示・上限・本文の版の保存。登録ルールの設定は解釈しない。"""

import json
from dataclasses import replace

from rag_engine.knowledge.runtime_knowledge import build_runtime_knowledge_context


def rule(identity, content="資料を確認する。", **fields):
    return {"id": identity, "title": "設定", "triggers": ["設定"], "content": content, "tags": ["pinned"], **fields}


def context(tmp_path, *rules, limit=6):
    (tmp_path / "runtime_knowledge.json").write_text(json.dumps({"rules": list(rules)}, ensure_ascii=False))
    return build_runtime_knowledge_context("設定の手順", tmp_path, rule_limit=limit)


def generation_ids(ctx):
    return [r.rule_id for r in ctx.rules_for_stage("generation")]


def test_only_generation_uses_active_guides_and_empty_instructions_are_excluded(tmp_path):
    ctx = context(tmp_path, rule("guide-a"), rule("empty", ""), rule("off", status="deprecated"))
    assert generation_ids(ctx) == ["guide-a"]
    assert ctx.rules_for_stage("confirmation") == ()
    assert "id=guide-a" in ctx.prompt_context()
    assert not any(d["id"] == "off" for d in ctx.rule_applications)
    assert any(d["id"] == "empty" and d["reason"] == "no_generation_instruction" for d in ctx.rule_applications)


def test_removed_registration_settings_do_not_reorder_or_hold_guides(tmp_path):
    ctx = context(tmp_path, rule("a", "説明 A", exclusive_group="old"), rule("b", "説明 B", priority=4, exclusive_group="old"), limit=1)
    assert generation_ids(ctx) == ["a"]
    assert any(d["id"] == "b" and d["reason"] == "application_budget" for d in ctx.rule_applications)
    assert ctx.rules_for_stage("confirmation") == ()


def test_compatible_instructions_combine_and_exact_duplicates_are_recorded(tmp_path):
    ctx = context(
        tmp_path,
        rule("a", "全手順を説明する。"),
        rule("b", "簡潔に書く。", instruction_kind="presentation"),
        rule("c", "簡潔に書く。", instruction_kind="presentation"),
    )
    assert generation_ids(ctx) == ["a", "b"]
    assert any(d["id"] == "c" and d["reason"] == "duplicate_instruction" for d in ctx.rule_applications)


def test_snapshots_use_the_executed_version_and_remain_stable_after_updates(tmp_path):
    first = context(tmp_path, rule("a", "旧指示"))
    snapshot = first.to_payload()
    original = snapshot["matched_rules"][0]["version"]
    second = context(tmp_path, rule("a", "新指示"))
    assert second.matched_rules[0].version != original
    assert snapshot["matched_rules"][0]["content"] == "旧指示"
    assert snapshot["rule_applications"][0]["version"] == original
    resolved = replace(first, matched_rules=(replace(first.matched_rules[0], content="条件解決後の指示"),))
    executed = resolved.to_payload()
    assert executed["matched_rules"][0]["version"] != original
    assert all(d["version"] == executed["matched_rules"][0]["version"] for d in executed["rule_applications"])
