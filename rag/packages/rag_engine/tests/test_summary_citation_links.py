"""摘要と最終本文の引用対応を、意味監査の実施とは別に検証する。"""

import pytest
from test_audit_modes import _run
from test_grounded_answer import FakeModel, context, draft, ids, item, record

from rag_engine.generation import grounded
from rag_engine.models.llm import GroundedDraft


def _case(indexes=None):
    text = "項目 A は記録の識別子です。"
    ctx = context(record(0, text, "項目の説明", source="synthetic-a.txt"))
    value = draft(item(ids(ctx)[text], text, text), summary="項目 A は記録を識別します。")
    if indexes is not None:
        value["summary_item_indexes"] = indexes
    else:
        value.pop("summary_item_indexes", None)
    return ctx, value


def test_collapsed_identical_items_reindex_summary_links():
    _, value = _case([1, 2])
    value["items"] *= 3
    model = GroundedDraft.model_validate(value)
    assert len(model.items) == 1
    assert model.summary_item_indexes == [0]

