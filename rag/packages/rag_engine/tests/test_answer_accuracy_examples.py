"""合成の操作説明書・CSV・応答で、修正前後を同じ入力で検証する。"""

import csv
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, observed_audit, record

from rag_engine.config import get_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.retrieval.context_builder import ContextParentEvidence
from rag_engine.retrieval.request_coverage import CoverageBudget, coverage_targets, request_rows, run_targeted_retrievals

EXAMPLES = Path(__file__).with_name("fixtures") / "answer_accuracy"
CASES = json.loads((EXAMPLES / "cases.json").read_text())


def test_context_is_required_before_coverage_can_skip_additional_retrieval():
    case = CASES["coverage"]

    def tokenize(text):
        return re.findall("対象選択|設定|入力|方法|開いて", text)

    targets = coverage_targets(case["question"], tokenize)
    rows = request_rows(targets)
    assert rows[0]["status"] == "unassessed"
    rows[0]["answer_status"] = "missing"
    calls = []

    def retrieve(text, queries):
        calls.extend(queries)
        assert any("対象選択" in query for query in queries)
        ctx = context(record(1, case["additional_evidence"], "対象選択の設定"))
        return replace(
            ctx, evidence_tree=(ContextParentEvidence(record=ctx.records[0], role="synthesis_parent", reason="targeted", children=()),)
        )

    results, stop = run_targeted_retrievals(
        rows,
        targets,
        budget=CoverageBudget(),
        retrieve=retrieve,
        tokenize=tokenize,
        searched_queries=("初回の検索文",),
    )
    assert calls and results[0].parents
    assert stop == ""
    after = request_rows(targets)
    assert after[0]["status"] == "unassessed"

