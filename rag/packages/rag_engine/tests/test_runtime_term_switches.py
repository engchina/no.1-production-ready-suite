"""用語の検索拡張と回答補助を個別に制御する。"""

import json

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.answering import matched_term_query
from rag_engine.knowledge.runtime_knowledge import build_runtime_knowledge_context


@pytest.mark.parametrize("search,answer", [(False, False), (True, False), (False, True), (True, True)])
def test_independent_term_usage(tmp_path, search, answer):
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "terms": [{"term": "用語A", "aliases": ["別名B"], "description": "合成の説明"}], "rules": []},
            ensure_ascii=False,
        )
    )
    context = build_runtime_knowledge_context("用語A の設定は？", tmp_path, path, terms_search_enabled=search, terms_answer_enabled=answer)
    assert ("別名B" in context.expanded_question) is search
    assert bool(matched_term_query(context)) is search
    assert ("合成の説明" in context.prompt_context()) is answer
    assert ("別名B" in context.prompt_context()) is answer
    assert context.original_question == "用語A の設定は?"


def test_both_defaults_are_off():
    settings = get_settings(environ={}, dotenv_path=None)
    assert settings.runtime_terms_search_enabled is False
    assert settings.runtime_terms_answer_enabled is False
    assert settings.runtime_rules_confirmation_enabled is False
    assert settings.runtime_rules_answer_enabled is False


@pytest.mark.parametrize("confirmation,answer", [(False, False), (True, False), (False, True), (True, True)])
def test_registered_rule_uses_are_independent_and_never_expand_search(tmp_path, confirmation, answer):
    path = tmp_path / "runtime.json"
    rule = {
        "id": "rule-a",
        "title": "合成の設定ルール",
        "triggers": ["設定", "検索へ漏らさない別条件"],
        "content": "合成の回答指示",
        "purpose": "both",
        "clarification": {"question": "対象は？", "options": [{"id": "a", "label": "対象A"}]},
    }
    path.write_text(json.dumps({"schema_version": 1, "terms": [], "rules": [rule]}, ensure_ascii=False))
    ctx = build_runtime_knowledge_context("設定は？", tmp_path, path, rules_confirmation_enabled=confirmation, rules_answer_enabled=answer)
    assert bool(ctx.rules_for_stage("confirmation")) is confirmation
    assert bool(ctx.rules_for_stage("generation")) is answer
    assert ("合成の回答指示" in ctx.prompt_context()) is answer
    assert ctx.expanded_question == ctx.original_question
    if not (confirmation or answer):
        assert not ctx.matched_rules and not ctx.rule_applications


def test_disabled_registered_rules_do_not_call_matcher(tmp_path, monkeypatch):
    from rag_engine.knowledge import runtime_knowledge as rk

    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema_version": 1, "rules": [{"id": "a", "title": "設定", "triggers": ["設定"], "content": "指示"}]}))
    monkeypatch.setattr(rk, "rank_rules", lambda *args, **kwargs: pytest.fail("無効な登録ルールを照合した"))
    assert not build_runtime_knowledge_context("設定", tmp_path, path).matched_rules


def test_injected_context_cannot_bypass_disabled_term_policy(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"schema_version": 1, "terms": [{"term": "用語A", "aliases": ["別名B"], "description": "説明C"}]}))
    ctx = build_runtime_knowledge_context("用語A", tmp_path, path, terms_search_enabled=True, terms_answer_enabled=True)
    disabled = ctx.with_term_usage(search=False, answer=False)
    assert disabled.expanded_question == "用語A"
    assert disabled.prompt_context() == ""
    assert not matched_term_query(disabled)
