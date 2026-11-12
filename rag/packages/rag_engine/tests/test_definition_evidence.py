"""別の版の子の根拠が頁や本文を決めないことを確かめる。"""

import json
from dataclasses import replace
from pathlib import Path

from rag_engine.generation.answering import AnswerRecord
from rag_engine.retrieval.evidence_selection import evidence_spans

FIXTURE = json.loads((Path(__file__).parent / "fixtures/definition_short_labels.json").read_text())
PARENT = AnswerRecord(**FIXTURE["parent"])
CHILD = AnswerRecord(**FIXTURE["child"])


def test_child_from_another_version_cannot_supply_page_or_text():
    spans = evidence_spans(FIXTURE["question"], [PARENT], anchors=[replace(CHILD, source_run_id="other", page=99)])
    assert all(s["page"] != 99 for s in spans)
