"""検索の起点数・context の parent 数・生成の原文予算を環境変数で変えられる (#1054)。既定値は据え置き。"""
import unittest
from types import SimpleNamespace

from docrag.chunking.constants import DEFAULT_RETRIEVAL_TOP_K
from docrag.config import (DEFAULT_EVIDENCE_BUDGET_CHARS, DEFAULT_MAX_CONTEXT_RECORDS, DEFAULT_RETRIEVAL_TOP_K_SETTING,
                           get_settings)
from docrag.generation import answering
from docrag.generation.answer_records import MAX_CONTEXT_RECORDS
from docrag.retrieval.evidence_selection import EVIDENCE_BUDGET_CHARS


class RetrievalBudgetSettingsTest(unittest.TestCase):
    def test_defaults_match_the_code_constants(self):
        settings = get_settings(environ={}, dotenv_path=None)
        self.assertEqual((settings.retrieval_top_k, settings.max_context_records, settings.evidence_budget_chars),
                         (DEFAULT_RETRIEVAL_TOP_K, MAX_CONTEXT_RECORDS, EVIDENCE_BUDGET_CHARS))
        # config.py は循環 import を避けて値を写しているので、元の常量とのずれをここで止める
        self.assertEqual((DEFAULT_RETRIEVAL_TOP_K_SETTING, DEFAULT_MAX_CONTEXT_RECORDS, DEFAULT_EVIDENCE_BUDGET_CHARS),
                         (DEFAULT_RETRIEVAL_TOP_K, MAX_CONTEXT_RECORDS, EVIDENCE_BUDGET_CHARS))

    def test_environment_overrides_each_value(self):
        settings = get_settings(environ={"DOCRAG_RETRIEVAL_TOP_K": "30", "DOCRAG_MAX_CONTEXT_RECORDS": "16",
                                         "DOCRAG_EVIDENCE_BUDGET_CHARS": "72000"}, dotenv_path=None)
        self.assertEqual((settings.retrieval_top_k, settings.max_context_records, settings.evidence_budget_chars), (30, 16, 72000))
        self.assertEqual(get_settings(environ={"DOCRAG_MAX_CONTEXT_RECORDS": "0"}, dotenv_path=None).max_context_records, 1)

    def test_context_record_limit_falls_back_without_settings(self):
        self.assertEqual(answering._max_context_records(None), MAX_CONTEXT_RECORDS)
        self.assertEqual(answering._max_context_records(SimpleNamespace(max_context_records=16)), 16)

    def test_grounded_spans_pass_the_budget_to_evidence_selection(self):
        seen = {}

        def fake_spans(question, records, **kwargs):
            seen.update(kwargs)
            return []
        original = answering.evidence_spans
        answering.evidence_spans = fake_spans
        try:
            context = SimpleNamespace(records=[], evidence_tree=[], preferred_child_ids=())
            answering._grounded_spans("質問", context, [], None, budget=72000)
            self.assertEqual(seen.get("budget"), 72000)
            seen.clear()
            answering._grounded_spans("質問", context, [], None)
            self.assertNotIn("budget", seen)
        finally:
            answering.evidence_spans = original
