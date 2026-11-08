"""用語の検索拡張と回答補助を個別に制御する。"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_answer_generation import _answer_output, _chunk_run_stub, _hybrid_result, _stored_child

from rag_engine.config import get_settings
from rag_engine.generation.answering import SIMPLE_RETRIEVAL_LABEL, STANDARD_ANSWER_FLOW_LABEL, answer_question_result
from rag_engine.knowledge.runtime_knowledge import build_runtime_knowledge_context
from rag_engine.retrieval.query_channels import expression_channels
from rag_engine.retrieval.weighted_rrf import query_key


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
    assert bool(context.retrieval_terms) is search
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
    assert not disabled.retrieval_terms


class TermExpansionRetrievalTests(unittest.TestCase):
    def setUp(self):
        from grounded_stub import stub_generation

        stub_generation(self)

    def test_term_match_adds_one_hybrid_query_with_priority_terms(self):
        """用語が一致したら「質問＋用語・同義語」の 1 本だけを足し、足した語を全文検索の語で優先する。"""
        question = "用語A の設定は？"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            path.write_text(
                json.dumps(
                    {"schema_version": 1, "terms": [{"term": "用語A", "aliases": ["別名B"], "description": "合成の説明"}], "rules": []},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            settings = replace(get_settings(), runtime_terms_search_enabled=True, runtime_knowledge_path=path, output_dir=Path(directory))
            with (
                patch("rag_engine.generation.answering.load_latest_or_source_chunk_run", return_value=_chunk_run_stub()),
                patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
                patch(
                    "rag_engine.generation.answering.search_adb_hybrid_chunks",
                    return_value=_hybrid_result(_stored_child("chunk-a", 1, "合成の根拠。")),
                ) as search,
                patch("rag_engine.generation.answering.parse_text_response", return_value=_answer_output(answer="合成の回答。")),
            ):
                answer_question_result(
                    question,
                    "abcdef",
                    ["docling"],
                    settings,
                    query_strategy=SIMPLE_RETRIEVAL_LABEL,
                    answer_flow=STANDARD_ANSWER_FLOW_LABEL,
                )
        kwargs = search.call_args.kwargs
        call_settings = next(value for value in (*search.call_args.args, *kwargs.values()) if hasattr(value, "retrieval_query_sources"))
        queries = kwargs["retrieval_queries"]
        normalized, expanded = queries
        self.assertTrue(expanded.endswith("用語A 別名B"))
        self.assertEqual(call_settings.retrieval_query_sources[expanded], ("原質問 + 用語・同義語",))
        self.assertEqual(expression_channels(expanded, normalized, call_settings.retrieval_query_sources), ("hybrid",))
        # 検索文の字は検索の直前に整えられるので、表記の差は検索文の鍵（query_key）でそろえて引く。
        keywords = call_settings.retrieval_query_keywords
        self.assertEqual({query_key(query): terms for query, terms in keywords.items()}, {query_key(expanded): ("用語A", "別名B")})
