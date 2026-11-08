"""複数の要求を要求単位に分けることを検証する。"""

import pytest

from rag_engine.retrieval.task_contract import request_units


@pytest.mark.parametrize(
    "question",
    [
        "在庫設定の名称を変更する方法と、過去の記録への影響を教えてください。",
        "配送先の変更手順と確定済み伝票への影響は。",
        "支払変更の方法と再発行の要否を教えて。",
    ],
)
def test_each_explicit_requirement_gets_a_separate_audit_unit(question):
    units = request_units(question)
    facets = [u for u in units if u.get("kind") == "explicit_facet"]
    assert len(facets) == 2
    assert {u["id"] for u in facets} == {"Q1.F1", "Q1.F2"}
    assert all(question.rstrip("。") in u["text"] for u in facets)


def test_single_or_withdrawn_requirement_does_not_get_extra_facets():
    assert len(request_units("変更方法を教えてください。")) == 1
    assert len(request_units("変更方法を教えて。影響の説明は不要です。")) == 2
