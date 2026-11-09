"""同次監査の動的な要求ID対応を検証し、本文支持と材料不足から分離する。"""

import json

import pytest
from pydantic import ValidationError
from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record

from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans
from rag_engine.models.llm import GroundedAudit

QUESTION = "対象Aの画面を開く手順は？"
TEXT = "対象Aの画面を開きます。"


def run_review(requests):
    ctx = context(record(0, TEXT, "対象A"))
    reference = ids(ctx, QUESTION)[TEXT]
    seen = []

    def review(prompt):
        seen.append(json.loads(prompt)["task_contract"]["request_units"])
        return audit((0, "supported", "matched", ""), requests=requests)

    model = FakeModel([draft(item(reference, TEXT, TEXT, request_id="Q1"), summary="")], [review])
    current = grounded.run_round(
        QUESTION,
        _grounded_spans(QUESTION, ctx, (), None),
        None,
        prompt="合成入力",
        image_paths=(),
        provider_id=None,
        parse_text=model,
        parse_images=None,
    )
    assert len(model.prompts) == 2 and current.audit_calls == 1
    assert {row["id"] for row in seen[0]} == {"Q1"}
    entry = next(entry for entry in current.checked if entry.item.text == TEXT)
    assert entry.published and entry.confirmed and not entry.removed
    return current


def test_invalid_status_enum_is_a_schema_error_not_a_missing_result():
    payload = audit((0, "supported", "matched", ""), requests=[("Q1", "not-a-status")])
    with pytest.raises(ValidationError) as error:
        GroundedAudit.model_validate(payload)
    assert any(row["type"] == "literal_error" and row["loc"] == ("request_reviews", 0, "status") for row in error.value.errors())
