"""粗→細の検索: rerank 分数を文書ごとに集約して起点の文書を選び、後回しの文書は不足時に戻す (#1028)。"""
import unittest
from dataclasses import replace
from unittest.mock import patch

from docrag.config import get_settings
from docrag.generation import answering as a
from docrag.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence, context_bundle_from_parent_evidence


def settings(enabled=True, ratio=0.5, min_hits=2):
    return replace(get_settings(environ={}, dotenv_path=None), document_selection_enabled=enabled,
                   document_selection_score_ratio=ratio, document_selection_min_hits=min_hits)


def child(name, seq, text, source, parent, score=None, run="run"):
    metadata = {"section_path": ["管理-(1)登録"]}
    if score is not None:
        metadata["rerank"] = {"candidate_index": seq, "rank": seq, "relevance_score": score, "min_relevance_score": 0.0, "filtered": False}
    return a.AnswerRecord(id=name, engine="docling", engine_label="Docling", page=seq, seq_no=seq, category="Chunk", text=text,
                          source=source, source_run_id="source", chunk_id=name, chunk_uid=f"{run}:{name}", chunk_level="child",
                          chunk_seq=seq, parent_chunk_uid=f"{run}:{parent}", metadata=metadata)


def parent(name, seq, text, source, run="run"):
    return a.AnswerRecord(id=name, engine="docling", engine_label="Docling", page=seq, seq_no=seq, category="Chunk", text=text,
                          source=source, source_run_id="source", chunk_id=name, chunk_uid=f"{run}:{name}", chunk_level="parent",
                          chunk_seq=seq, parent_chunk_uid="", metadata={"section_path": ["管理-(1)登録"]})


class SelectDocumentsTests(unittest.TestCase):
    def setUp(self):
        # 文書 A: 3 件で高分、文書 B: 2 件で中分、文書 C: 1 件で低分。
        self.children = [child("a1", 1, "A の説明 1", "a.pdf", "pa", 0.9), child("b1", 2, "B の説明 1", "b.pdf", "pb", 0.6),
                         child("a2", 3, "A の説明 2", "a.pdf", "pa", 0.8), child("c1", 4, "C の説明", "c.pdf", "pc", 0.2),
                         child("a3", 5, "A の説明 3", "a.pdf", "pa", 0.7), child("b2", 6, "B の説明 2", "b.pdf", "pb", 0.1)]

    def test_low_scoring_single_hit_document_is_deferred_and_order_is_kept(self):
        kept, deferred, trace = a.select_documents(self.children, settings())
        self.assertEqual([r.id for r in kept], ["a1", "b1", "a2", "a3", "b2"])
        self.assertEqual([r.id for r in deferred], ["c1"])
        self.assertEqual([e["source"] for e in trace["selected"]], ["a.pdf", "b.pdf"])
        self.assertEqual(trace["deferred"], [{"source": "c.pdf", "score": 0.2, "hits": 1}])
        self.assertFalse(trace["restored"])

    def test_documents_with_close_scores_or_enough_hits_are_all_kept(self):
        kept, deferred, _ = a.select_documents(self.children, settings(ratio=0.05))  # 分数落差の閾値を満たさない
        self.assertEqual(deferred, [])
        kept, deferred, _ = a.select_documents(self.children, settings(min_hits=1))  # 命中数の条件を満たさない
        self.assertEqual(deferred, [])

    def test_disabled_selection_keeps_everything_and_records_only_the_flag(self):
        kept, deferred, trace = a.select_documents(self.children, settings(enabled=False))
        self.assertEqual(len(kept), 6)
        self.assertEqual((deferred, trace), ([], {"enabled": False, "selected": [], "deferred": [], "restored": False}))

    def test_without_rerank_scores_nothing_is_deferred(self):
        # 順位の逆数は差が小さく閾値に掛からない。分数が無ければ落差を判断できないので後回しにしない (#1057)
        plain = [replace(c, metadata={"section_path": ["管理-(1)登録"]}) for c in self.children]
        kept, deferred, trace = a.select_documents(plain, settings(ratio=0.5))
        self.assertEqual(deferred, [])
        self.assertEqual(trace["selected"][0]["source"], "a.pdf")

    def test_single_top_scoring_hit_is_not_deferred_by_documents_with_many_hits(self):
        # 文書 A は中程度の分数を 3 件（合計 2.25）、文書 D は 1 件だが最高分 0.8。合計で比べると D が後回しになる (#1057)
        children = [child("d1", 1, "D の説明", "d.pdf", "pd", 0.8), child("a1", 2, "A の説明 1", "a.pdf", "pa", 0.75),
                    child("a2", 3, "A の説明 2", "a.pdf", "pa", 0.75), child("a3", 4, "A の説明 3", "a.pdf", "pa", 0.75),
                    child("c1", 5, "C の説明", "c.pdf", "pc", 0.2)]
        kept, deferred, trace = a.select_documents(children, settings())
        self.assertEqual([r.id for r in deferred], ["c1"])
        self.assertEqual([e["source"] for e in trace["selected"]], ["d.pdf", "a.pdf"])
        self.assertEqual(trace["selected"][0], {"source": "d.pdf", "score": 0.8, "hits": 1})


class DefaultSettingTests(unittest.TestCase):
    def test_document_selection_is_on_by_default_and_can_be_disabled(self):
        self.assertTrue(get_settings(environ={}, dotenv_path=None).document_selection_enabled)  # #1040
        self.assertFalse(get_settings(environ={"DOCRAG_DOCUMENT_SELECTION": "false"}, dotenv_path=None).document_selection_enabled)
        self.assertEqual(get_settings(environ={}, dotenv_path=None).document_selection_score_ratio, 0.5)


class DeferredRestoreTests(unittest.TestCase):
    def setUp(self):
        self.pa = parent("pa", 1, "A の説明 1 A の説明 2", "a.pdf")
        self.pc = parent("pc", 4, "C の説明", "c.pdf")
        self.a1 = child("a1", 1, "A の説明 1", "a.pdf", "pa", 0.9)
        self.c1 = child("c1", 4, "C の説明", "c.pdf", "pc", 0.2)
        self.pool = (self.a1, self.c1, self.pa, self.pc)
        bundle = context_bundle_from_parent_evidence((ContextParentEvidence(
            record=self.pa, role="synthesis_parent", reason="retrieved",
            children=(ContextChildEvidence(record=self.a1, role="retrieved_anchor", reason="hit", retrieval_rank=1),)),), max_chars=10000)
        self.selected = a.AnswerContext(list(bundle.records), bundle.text, evidence=bundle.evidence, evidence_tree=bundle.evidence_tree,
                                        expansion_records=self.pool, ranked_candidates=(self.a1, self.c1), deferred_records=(self.c1,),
                                        document_selection={"enabled": True, "selected": [{"source": "a.pdf"}], "deferred": [{"source": "c.pdf"}], "restored": False})

    def test_crag_restores_deferred_documents_before_rewriting_and_records_it(self):
        attempt = lambda number, sufficient, **kw: a.CragRetrievalAttempt(number, "q", ("q",), sufficient, **kw)
        grades = [attempt(1, False, rewritten_query="登録 別の検索文"), attempt(2, True)]
        with patch.object(a, "build_adb_hybrid_answer_context", return_value=self.selected), \
             patch.object(a, "_grade_crag_retrieval", side_effect=grades):
            result, rewrites, attempts = a.build_crag_answer_context("登録条件は", "source", ["docling"], settings())
        self.assertEqual(rewrites, ())  # 改写検索の前に後回しの文書を戻す
        self.assertEqual(attempts[0].stop_reason, "deferred_documents_restored")
        self.assertTrue(attempts[1].document_selection["restored"])
        self.assertIn("pc", [r.id for r in result.records])  # 戻した文書 C の親が根拠に入る
        self.assertEqual(result.deferred_records, ())

    def test_generation_side_remedy_adds_the_deferred_document_parents_first(self):
        expanded, trace = a._expand_context_from_pool("登録条件は", self.selected)
        self.assertEqual(trace["mode"], "deferred_documents")
        self.assertEqual(trace["added_chunk_ids"], ["run:pc"])
        self.assertIn("pc", [r.id for r in expanded.records])
        self.assertEqual(expanded.deferred_records, ())
        self.assertTrue(expanded.document_selection["restored"])

    def test_generation_side_remedy_caps_added_parents_so_existing_evidence_is_kept(self):
        # 可視 1 件に対し後回しの文書が 3 件あっても、併合が既存の根拠を落とさない件数（可視の数）だけ足す。
        extra = [(child(f"d{i}", 10 + i, f"D{i} の説明", f"d{i}.pdf", f"pd{i}", 0.1), parent(f"pd{i}", 10 + i, f"D{i} の説明", f"d{i}.pdf")) for i in range(3)]
        context = replace(self.selected, expansion_records=self.pool + tuple(r for pair in extra for r in pair),
                          deferred_records=(self.c1, *(c for c, _ in extra)))
        expanded, trace = a._expand_context_from_pool("登録条件は", context)
        self.assertIsNotNone(expanded, trace)
        self.assertEqual(trace["added_chunk_ids"], ["run:pc"])
        self.assertIn("pa", [r.id for r in expanded.records])

    def test_pre_generation_same_unit_fill_does_not_consume_deferred_documents(self):
        # 生成前の同単元の補充は後回しの文書を使わない（答えていない要求が出てからの回退に取っておく）。
        filled, trace = a._fill_same_unit("登録条件は", self.selected)
        self.assertFalse(trace["applied"], trace)
        self.assertEqual([r.id for r in filled.deferred_records], ["c1"])

    def test_refine_keeps_the_selection_state(self):
        refined = a._refine_crag_context(self.selected, ("run:a1",), max_chars=10000)
        self.assertEqual([r.id for r in refined.deferred_records], ["c1"])
        self.assertEqual(len(refined.ranked_candidates), 2)
        self.assertTrue(refined.document_selection["enabled"])


if __name__ == "__main__":
    unittest.main()
