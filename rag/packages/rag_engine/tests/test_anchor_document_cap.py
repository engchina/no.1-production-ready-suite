"""rerank の後、1 つの文書から採る child を文書ごとの上限までにし、超えた child を外す (#2167・#5165)。"""

import unittest
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.resources.tracing import bind_trace_observer
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from tests.record_fixtures import child, parent


def settings(limit):
    return replace(get_settings(environ={}, dotenv_path=None), max_anchors_per_document=limit)


class Step:
    def __init__(self):
        self.results = []
        self.lines = []

    def result(self, *details):
        self.results.extend(details)

    def add(self, *details):
        self.lines.extend(details)


class AnchorDocumentCapTest(unittest.TestCase):
    def ranked(self):
        # 文書 A の child が上位 4 件を占め、文書 B の child は 5 位。
        records = [child(f"a{i}", i, f"A の節 {i}", "A.pdf", f"pa{i}") for i in range(1, 5)]
        return [*records, child("b1", 5, "B の条", "B.json", "pb1"), child("a5", 6, "A の節 5", "A.pdf", "pa5")]

    def record(self, ranked, limit, *, top_k, max_records):
        """文書ごとの上限を掛け、実際の起点の選び方（``top_k``・親の枠）で診断を書く。"""
        step = Step()
        configured = settings(limit)
        capped = a.cap_anchors_per_document(ranked, configured)
        a.record_document_cap(
            step,
            ranked,
            capped,
            configured,
            top_k=top_k,
            max_records=max_records,
            select_anchors=lambda records: a._cut_anchors("合成の質問", records, ranked, configured, top_k=top_k, max_records=max_records),
        )
        return capped, step

    def test_drops_children_over_the_limit(self):
        capped, step = self.record(self.ranked(), 2, top_k=3, max_records=12)
        # 超えた child は後ろへ回さず外す（起点が足りなくても使い直さない）。
        self.assertEqual([r.id for r in capped], ["a1", "a2", "b1"])
        # 3 件を外し、上位 3 件のうち a3 が外れた。
        self.assertEqual(step.results, ["文書ごとの上限 2 件: 3 件を外しました（上位 3 件のうち 1 件）"])
        # 外した候補と、代わりに起点に入った候補を名前で示す（#2341）。本文は入れない。
        self.assertEqual(
            step.lines,
            [
                "外した候補:",
                "1. A.pdf › 管理-(1)登録（p.3）",
                "代わりに起点に入った候補:",
                "1. B.json › 管理-(1)登録（p.5）",
            ],
        )

    def test_candidate_cut_by_the_parent_limit_is_not_reported_as_raised(self):
        # 親の枠が 2 件なら、上位 3 件に繰り上がった b1 は起点に入らない（#5242）。「代わりに起点に入った候補」に
        # 出さず、起点に入らなかった候補として分けて書く。
        capped, step = self.record(self.ranked(), 2, top_k=3, max_records=2)
        self.assertEqual([r.id for r in capped], ["a1", "a2", "b1"])
        self.assertEqual(step.results, ["文書ごとの上限 2 件: 3 件を外しました（上位 3 件のうち 1 件）"])
        self.assertEqual(
            step.lines,
            [
                "外した候補:",
                "1. A.pdf › 管理-(1)登録（p.3）",
                "上位 3 件に繰り上がったが起点に入らなかった候補（親の枠 2 件・同じ親の起点の上限 2 件）:",
                "1. B.json › 管理-(1)登録（p.5）",
            ],
        )

    def test_answer_context_reports_only_candidates_that_became_anchors(self):
        # 回答の検索の工程の記録でも、親の枠（max_records）で起点に入らなかった候補を「代わりに起点に入った候補」に
        # 出さない。起点の選び方は変えない（#5242）。
        ranked = self.ranked()
        parents = [parent(f"pa{i}", i, f"A の節 {i}", "A.pdf") for i in range(1, 6)] + [parent("pb1", 5, "B の条", "B.json")]
        details = []

        @contextmanager
        def observe(operation, name):
            class Observation:
                def request(self, *args, **kwargs):
                    pass

                def response(self, *args, **kwargs):
                    pass

                def usage(self, *args, **kwargs):
                    pass

                def display(self, kind, params, structured=None):
                    if operation == "stage" and name == "文書ごとの上限" and structured:
                        details.append(structured)

            yield Observation()

        search = SimpleNamespace(child_chunks=ranked, all_chunks=[*ranked, *parents])
        with (
            mock.patch.object(a, "search_adb_hybrid_chunks", lambda **_: search),
            mock.patch.object(a, "_stored_chunk_answer_record", lambda record: record),
            mock.patch.object(a, "rerank_records", lambda question, records, *args, **kwargs: list(records)),
            bind_trace_observer(observe),
        ):
            result = a.build_adb_hybrid_answer_context(
                "合成の質問",
                "synthetic-run",
                ["docling"],
                settings(2),
                top_k=3,
                max_records=2,
                neighbor_child_count=0,
                retrieval_scope=a.RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
            )
        anchors = [item.record.id for p in result.evidence_tree for item in p.children if item.role == "retrieved_anchor"]
        self.assertEqual(anchors, ["a1", "a2"])
        self.assertEqual(len(details), 1)
        lines = details[0]["lines"]
        self.assertNotIn("代わりに起点に入った候補:", lines)
        self.assertIn("上位 3 件に繰り上がったが起点に入らなかった候補（親の枠 2 件・同じ親の起点の上限 2 件）:", lines)

    def test_zero_keeps_every_child_in_order(self):
        ranked = self.ranked()
        self.assertEqual(a.cap_anchors_per_document(ranked, settings(0)), ranked)

    def test_limit_counts_each_document_separately(self):
        capped = a.cap_anchors_per_document(self.ranked(), settings(1))
        self.assertEqual([r.id for r in capped], ["a1", "b1"])

    def test_engine_default_is_five_and_env_selects_the_limit(self):
        self.assertEqual(get_settings(environ={}, dotenv_path=None).max_anchors_per_document, 5)
        environ = {"RAG_ENGINE_MAX_ANCHORS_PER_DOCUMENT": "0"}
        self.assertEqual(get_settings(environ=environ, dotenv_path=None).max_anchors_per_document, 0)

    def test_attachments_keep_their_position_and_are_not_counted(self):
        # 会話の添付は位置を保ち、文書ごとの上限に数えない。検索拡張の根拠は候補に入らない（#5162）。
        ranked = self.ranked()
        attached = child("a-att", 7, "A の添付", "A.pdf", "pa-att")
        attached.metadata[ATTACHMENT_EVIDENCE_KEY] = True
        ranked.insert(2, attached)
        capped = a.cap_anchors_per_document(ranked, settings(2))
        self.assertEqual([r.id for r in capped], ["a1", "a2", "a-att", "b1"])


if __name__ == "__main__":
    unittest.main()
