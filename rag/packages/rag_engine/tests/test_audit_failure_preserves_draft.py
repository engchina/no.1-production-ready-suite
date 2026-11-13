"""監査の実行失敗と材料不足を区別し、初回の有用な本文を保つ。"""

import pytest
from test_grounded_answer import FakeModel, context, draft, ids, item, record

from rag_engine.config import get_settings
from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans

QUESTION = "対象Aの画面を開く方法は？"
TEXT = "設定メニューから対象Aの画面を開きます。"


def run_failed_audit(monkeypatch, error, *, deadline=None):
    ctx = context(record(0, TEXT, "対象A"))
    evidence_id = ids(ctx, QUESTION)[TEXT]
    model = FakeModel([draft(item(evidence_id, TEXT, TEXT), summary=TEXT)], [])
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        if deadline is not None:
            deadline()
        raise error

    monkeypatch.setattr(grounded, "_parse_audit", fail)
    current = grounded.run_round(
        QUESTION,
        _grounded_spans(QUESTION, ctx, (), None),
        get_settings(environ={}),
        prompt="p",
        image_paths=[],
        provider_id=None,
        parse_text=model,
        parse_images=None,
    )
    return current, model, calls


def test_generation_failure_is_outside_audit_fallback(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("generation failed")

    with pytest.raises(RuntimeError, match="generation failed"):
        grounded.run_round(
            QUESTION, [], get_settings(environ={}), prompt="p", image_paths=[], provider_id=None, parse_text=fail, parse_images=None
        )
    assert calls == [True]
