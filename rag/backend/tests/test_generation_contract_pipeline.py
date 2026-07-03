"""RagPipeline の回答契約修復と JSON Schema 経路。"""

from typing import Any

import pytest

from app.config import Settings
from app.rag.generation_contract import GenerationContractError
from app.rag.pipeline import RagPipeline
from app.schemas.search import RetrievedChunk


class SequencedLlm:
    def __init__(self, answers: list[str], *, json_schema: bool = False) -> None:
        self.answers = list(answers)
        self.json_schema = json_schema
        self.calls: list[dict[str, Any]] = []

    def supports_response_json_schema(self) -> bool:
        return self.json_schema

    async def generate(
        self,
        prompt: str,
        context: str,
        **kwargs: Any,
    ) -> str:
        self.calls.append({"prompt": prompt, "context": context, **kwargs})
        return self.answers.pop(0)


def _citation() -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-1",
        chunk_id="chunk-1",
        file_name="policy.pdf",
        text="申請期限は7月31日です。",
        score=1.0,
    )


@pytest.mark.anyio
async def test_contract_failure_repairs_once_with_same_context() -> None:
    llm = SequencedLlm(
        [
            "引用なしの回答です。",
            "申請期限は7月31日です。[policy.pdf#chunk-1]",
        ]
    )
    pipeline = RagPipeline(
        settings=Settings(
            rag_generation_profile="detailed_cited",
            rag_generation_service_enabled=False,
        ),
        llm=llm,  # type: ignore[arg-type]
    )

    result = await pipeline._generate_answer(
        "申請期限は？",
        "[policy.pdf#chunk-1]\n申請期限は7月31日です。",
        citations=[_citation()],
    )

    assert result.attempt_count == 2
    assert result.repair_count == 1
    assert "missing_paragraph_citation" in result.validation_codes
    assert len(llm.calls) == 2
    assert llm.calls[0]["prompt"] == llm.calls[1]["prompt"]
    assert llm.calls[0]["context"] == llm.calls[1]["context"]
    assert "【再生成】" in str(llm.calls[1]["system_prompt"])


@pytest.mark.anyio
async def test_contract_second_failure_raises_without_invalid_answer() -> None:
    llm = SequencedLlm(["引用なし。", "まだ引用なし。"])
    pipeline = RagPipeline(
        settings=Settings(
            rag_generation_profile="inline_cited",
            rag_generation_service_enabled=False,
        ),
        llm=llm,  # type: ignore[arg-type]
    )

    with pytest.raises(GenerationContractError) as captured:
        await pipeline._generate_answer(
            "申請期限は？",
            "[policy.pdf#chunk-1]\n申請期限は7月31日です。",
            citations=[_citation()],
        )

    assert captured.value.attempt_count == 2
    assert captured.value.codes == ("missing_inline_citation",)
    assert len(llm.calls) == 2


@pytest.mark.anyio
async def test_structured_json_uses_native_schema_when_supported() -> None:
    llm = SequencedLlm(
        [
            '{"answer":"申請期限は7月31日です。","evidence":["期限"],'
            '"sources":["policy.pdf#chunk-1"]}'
        ],
        json_schema=True,
    )
    pipeline = RagPipeline(
        settings=Settings(
            rag_generation_profile="structured_json",
            rag_generation_service_enabled=False,
        ),
        llm=llm,  # type: ignore[arg-type]
    )

    result = await pipeline._generate_answer(
        "申請期限は？",
        "[policy.pdf#chunk-1]\n申請期限は7月31日です。",
        citations=[_citation()],
    )

    assert result.attempt_count == 1
    schema = llm.calls[0]["response_schema"]
    assert isinstance(schema, dict)
    assert set(schema["required"]) == {"answer", "evidence", "sources"}
