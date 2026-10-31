"""自然言語を明示的な検索範囲や完全な要求リストとして扱わない。"""

import pytest
from rag_engine.generation.grounded import _contract_view

from app.config import Settings
from app.rag.answer_engine import AnswerEngine, _SearchState
from app.schemas.search import SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle


@pytest.mark.parametrize(
    "question",
    [
        "A.pdf と B.pdf を比較してください。",
        "A.pdf は参照しないでください。",
        "A.pdf だけでなく他の資料も参照してください。",
        "A.pdf の 2 ページと B.pdf の 3 ページを比較してください。",
    ],
)
async def test_question_does_not_create_database_filters(question: str) -> None:
    oracle = FakeOracle()
    for chunk in oracle.chunks:
        chunk.file_name = "A.pdf"
    engine = AnswerEngine(Settings(), oracle=oracle, genai=FakeGenAi())  # type: ignore[arg-type]
    state = _SearchState()
    await engine._ensure_search_filters(
        SearchRequest(query=question, filters={"knowledge_base_id": "kb-a"}), state
    )
    assert state.filters == {"knowledge_base_id": "kb-a"}
    assert not oracle.probes


def test_question_plan_has_no_inference_fields() -> None:
    from rag_engine.retrieval.question_planning import plan_question

    question = "外部システムの A.csv の確認方法と、条件・例外を教えてください。"
    assert plan_question(question).to_payload() == {"original_question": question}


def test_whole_question_is_preserved_without_parent_child_requirements() -> None:
    question = "①項目Aと項目Bの意味を教えてください。②項目Cと項目Dの意味を教えてください。"
    contract = _contract_view(question)
    assert contract["current_request"] == question
    assert len(contract["request_units"]) == 1
    assert contract["request_units"][0]["text"] == question
