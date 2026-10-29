"""利用者が確定した前提だけを、正式入口から同次監査へ渡す。実モデル呼出0。"""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from rag_engine.config import LLM_PURPOSE_AUDIT, settings_for_llm_purpose
from rag_engine.generation.citation_wire import generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, AnswerScope, build_engine_settings
from app.rag.answer_validation import AnswerValidation
from app.schemas.search import SearchRequest
from tests.audit_fixtures import is_audit_schema, legacy_audit_result
from tests.test_answer_engine import FakeGenAi, FakeOracle, _fake_llm

LABEL = "操作担当の種別"
VALUE = "担当A"
QUESTION = "登録方法は?"


def guide_settings(source: str, state: str, *, mode: str = "standard") -> Settings:
    return Settings(
        rag_support_guide_enabled=True,
        rag_support_guide={
            "known_conditions": [{"label": LABEL, "value": VALUE, "source": source, "state": state}]
        },
        rag_answer_audit_mode=cast(Any, mode),
        rag_query_strategy="simple_retrieval",
        rag_section_expansion_enabled=False,
    )


@pytest.mark.parametrize(
    "source,state,expected",
    [
        ("user", "known", True),
        ("question", "known", True),
        ("assumption", "known", False),
        ("user", "unknown", False),
    ],
)
def test_settings_keep_confirmed_sources_and_preserve_them_for_model_purpose(
    source: str, state: str, expected: bool, tmp_path: Path
) -> None:
    settings = build_engine_settings(guide_settings(source, state), output_dir=tmp_path)
    assert settings.confirmed_premises == (((LABEL, VALUE),) if expected else ())
    assert (
        settings_for_llm_purpose(settings, LLM_PURPOSE_AUDIT).confirmed_premises
        == settings.confirmed_premises
    )


def test_scope_premises_are_explicit_and_do_not_parse_assumptions(tmp_path: Path) -> None:
    plain = build_engine_settings(
        Settings(), output_dir=tmp_path, scope=AnswerScope(context=f"仮定: {LABEL}={VALUE}")
    )
    assert plain.confirmed_premises == ()
    explicit = build_engine_settings(
        Settings(),
        output_dir=tmp_path,
        scope=AnswerScope(context="利用者が選んだ値", confirmed_premises=((LABEL, VALUE),)),
    )
    assert explicit.confirmed_premises == ((LABEL, VALUE),)


@pytest.mark.parametrize("mode", ["none", "standard"])
async def test_formal_answer_forwards_confirmed_premises_and_does_not_leak_to_next_request(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    calls: list[str] = []
    audit_inputs: list[dict[str, Any]] = []

    def parser(system: str, prompt: str, config: Any, schema: type, **options: Any) -> Any:
        calls.append(schema.__name__)
        if is_audit_schema(schema):
            data = json.loads(prompt)
            audit_inputs.append(data)
            assert data["question"] == QUESTION
            assert [unit["id"] for unit in data["task_contract"]["request_units"]] == ["Q1"]
            return legacy_audit_result(
                schema,
                {
                    "goal_alignment": "aligned",
                    "summary_supported": True,
                    "summary_basis": [row["text"] for row in data["items"]],
                    "reviews": [
                        {
                            "index": row["index"],
                            "support": "supported",
                            "applicability": "matched",
                            "reason": "合成の支持観察",
                        }
                        for row in data["items"]
                    ],
                    "request_reviews": [
                        {"request_id": "Q1", "status": "addressed", "reason": "合成の回答対応"}
                    ],
                },
            )
        result = _fake_llm(system, prompt, config, schema, **options)
        if schema in (GroundedDraft, generation_draft_wire_model()):
            # この回帰は履歴互換の草稿を返し、引用wire自体の検証とは分ける。
            data = result.model_dump()
            ids = list(dict.fromkeys(row["citations"][0]["evidence_id"] for row in data["items"]))
            data["request_coverage"] = [
                {
                    "request_id": "Q1",
                    "evidence_ids": ids,
                    "evidence_scope": [
                        {"evidence_id": identity, "target": "登録", "relation": "direct"}
                        for identity in ids
                    ],
                }
            ]
            for row in data["items"]:
                row["request_id"] = "Q1"
            return GroundedDraft.model_validate(data)
        return result

    monkeypatch.setattr("rag_engine.adapters.oci.parse_text_response", parser)
    validator = AsyncMock(return_value=AnswerValidation(status="completed", valid=True))
    monkeypatch.setattr("app.rag.answer_validation.validate_answer", validator)
    first = AnswerEngine(
        guide_settings("user", "known", mode=mode),
        oracle=cast(Any, FakeOracle()),
        genai=cast(Any, FakeGenAi()),
        scope=AnswerScope(
            context=f"利用者が回答済み: {LABEL}={VALUE}", confirmed_premises=((LABEL, VALUE),)
        ),
    )
    second = AnswerEngine(
        guide_settings("assumption", "unknown", mode=mode),
        oracle=cast(Any, FakeOracle()),
        genai=cast(Any, FakeGenAi()),
        scope=AnswerScope(context=f"仮定: {LABEL}={VALUE}"),
    )
    for engine in (first, second):
        outcome = await engine.run(SearchRequest(query=QUESTION))
        assert "登録ボタン" in outcome.answer
    generation_names = {GroundedDraft.__name__, generation_draft_wire_model().__name__}
    assert sum(name in generation_names for name in calls) == 2
    if mode == "none":
        assert not audit_inputs
        assert len(calls) == 2
        validator.assert_not_awaited()
    else:
        assert len(audit_inputs) == 2
        assert audit_inputs[0]["confirmed_premises"] == [{"question": LABEL, "answer": VALUE}]
        assert "confirmed_premises" not in audit_inputs[1]
        assert len(calls) == 4
        assert validator.await_count == 2
