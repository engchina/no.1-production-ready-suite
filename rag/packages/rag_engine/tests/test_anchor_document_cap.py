"""rerank の後、1 つの文書から採る child を文書ごとの上限までにし、超えた child を外す (#2167・#5165)。"""

import unittest
from dataclasses import replace

from rag_engine.config import get_settings
from rag_engine.generation import answering as a
from rag_engine.retrieval.attachment_evidence import ATTACHMENT_EVIDENCE_KEY
from tests.record_fixtures import child


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

    def test_drops_children_over_the_limit(self):
        step = Step()
        capped = a.cap_anchors_per_document(self.ranked(), settings(2), top_k=3, step=step)
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

    def test_zero_keeps_every_child_in_order(self):
        ranked = self.ranked()
        self.assertEqual(a.cap_anchors_per_document(ranked, settings(0), top_k=3), ranked)

    def test_limit_counts_each_document_separately(self):
        capped = a.cap_anchors_per_document(self.ranked(), settings(1), top_k=10)
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
        capped = a.cap_anchors_per_document(ranked, settings(2), top_k=3)
        self.assertEqual([r.id for r in capped], ["a1", "a2", "a-att", "b1"])


if __name__ == "__main__":
    unittest.main()
