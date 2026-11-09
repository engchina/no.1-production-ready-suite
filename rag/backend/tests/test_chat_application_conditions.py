"""合成の異なる操作の期限を、後続回答の条件へ移さない公開門。"""

import re
from dataclasses import replace
from typing import Any, cast

import pytest
from pytest import MonkeyPatch
from rag_engine.config import get_settings as engine_settings
from rag_engine.dependencies import AnswerDependencies, bind_dependencies
from rag_engine.generation.answer_models import AnswerContext, AnswerRecord
from rag_engine.generation.answering import synthesize_grounded_answer
from rag_engine.generation.application_explanation import is_explanation_schema

from app.api.routes import chat as chat_route
from app.schemas.search import SearchResponse
from tests.test_chat_api import FakeChatOracle, _FakePipeline, _stub_stream, client

SOURCE_A = "操作Aは2営業日前の17時までなら0%、その期限後は50%です。"
SOURCE_B = "操作Bの申請期限は前営業日の17時までです。"


def test_followup_preserves_original_and_saves_the_conclusion(monkeypatch: MonkeyPatch) -> None:
    calls: list[type[Any]] = []

    def parse(system: str, prompt: str, settings: Any, schema: Any, **options: Any) -> Any:
        calls.append(schema)
        unit = re.findall(r"(?m)^\d+\| \[(U[0-9a-f]{24})\]", prompt)[0]
        return schema.model_validate(
            {
                "request_coverage": [
                    {
                        "request_id": "Q1",
                        "evidence_scope": [
                            {
                                "evidence_id": "E1",
                                "target": "操作A",
                                "relation": "direct",
                                "link": None,
                            }
                        ],
                    }
                ],
                "answer_parts": [
                    {
                        "request_id": "Q1",
                        "requirement": "操作Aの割合",
                        "answer": {
                            "kind": "answer",
                            "basis": {"unit_ids": [unit]},
                            "reasoning": "前営業日は期限後です。",
                            "result": "割合は50%です。",
                        },
                    }
                ],
                "summary_part_indexes": [0],
                "confidence": "high",
            }
        )

    def unused(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("検索・DB・外部モデルは決定論の合成入力で置き換える")

    class PeriodPipeline(_FakePipeline):
        async def run(self, request: Any, *args: Any, **kwargs: Any) -> SearchResponse:
            if request.query != "操作Bの期限を教えてください。":
                assert any(turn.content == SOURCE_B for turn in kwargs.get("history", []))
            return cast(SearchResponse, await super().run(request, *args, **kwargs))  # type: ignore[no-untyped-call]

        def _response(self, request: Any, trace_id: str | None) -> SearchResponse:
            response = super()._response(request, trace_id)
            if request.query == "操作Bの期限を教えてください。":
                response.answer = SOURCE_B
                return response
            records = [
                AnswerRecord(
                    id=f"synthetic-{i}",
                    engine="docling",
                    engine_label="Docling",
                    page=1,
                    seq_no=i + 1,
                    category="Chunk",
                    text=text,
                    source=f"synthetic-{i}.txt",
                    chunk_level="parent",
                    chunk_id=f"synthetic-{i}",
                    source_run_id="synthetic",
                )
                for i, text in enumerate((SOURCE_A, SOURCE_B))
            ]
            config = replace(
                engine_settings(environ={}, dotenv_path=None),
                answer_audit_mode="none",
                answer_max_correction_rounds=0,
            )
            with bind_dependencies(AnswerDependencies(unused, unused, parse, unused, unused)):
                generated = synthesize_grounded_answer(
                    request.query,
                    AnswerContext(records=records, text="\n".join((SOURCE_A, SOURCE_B))),
                    config,
                    image_prompt_mode="text_only",
                ).response
            response.answer = generated.answer_text
            response.citations[0].text = SOURCE_A
            response.diagnostics.answer = generated.generation_trace
            return response

    fake = FakeChatOracle()
    fake.search_answer_profiles = {"synthetic-profile": "合成プロファイル"}
    _stub_stream(monkeypatch, fake, ["synthetic-model"])
    monkeypatch.setattr(chat_route, "RagPipeline", PeriodPipeline)
    created = client.post(
        "/api/chat/conversations", json={"search_answer_profile_id": "synthetic-profile"}
    )
    assert created.status_code == 200
    conversation = created.json()["data"]["id"]
    endpoint = f"/api/chat/conversations/{conversation}/messages/stream"
    prior = client.post(endpoint, json={"content": "操作Bの期限を教えてください。"})
    assert prior.status_code == 200 and "event: all_done" in prior.text
    followup = client.post(
        endpoint, json={"content": "操作Aを前営業日に行った場合の割合を教えてください。"}
    )
    assert followup.status_code == 200 and "event: all_done" in followup.text
    saved = client.get(f"/api/chat/conversations/{conversation}").json()["data"]["messages"][-1]
    # 推導の説明の段（#4393）は公開する推導に 1 回。
    assert sum(is_explanation_schema(schema) for schema in calls) == 1
    assert SOURCE_A in saved["content"]
    assert saved["citations"][0]["text"] == SOURCE_A
    assert saved["content"].startswith("割合は50%です。")
