"""Responses API へ送る要求で、応答 schema の項目の説明が system の指示の Step だけにある（#5230）。"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from rag_engine.adapters.oci import _response_wire_request
from rag_engine.generation.citation_unit_ids import SentUnitCatalog
from rag_engine.generation.full_answer_wire import full_answer_model
from rag_engine.generation.grounded import generation_system_prompt


class _Probe(BaseModel):
    note: str = Field(description="この欄には必ず「ZEBRA-7」とだけ書く。")
    color: Literal["c1", "c2", "c3"] = Field(description="c1=赤、c2=緑、c3=青。")


def _request(text_format: type[BaseModel], system: str | None) -> dict:
    messages = [{"role": "user", "content": "合成の質問"}]
    if system is not None:
        messages.insert(0, {"role": "system", "content": system})
    return {"model": "vendor.text", "input": messages, "text_format": text_format, "max_output_tokens": 10}


def test_descriptions_are_sent_once_in_system_step_not_in_schema() -> None:
    wire = _request(_Probe, "## Step 1: 返す\n応答 schema どおりの JSON を返す。")
    sent = _response_wire_request(wire)

    system, user = sent["input"]
    assert system["role"] == "system" and user == {"role": "user", "content": "合成の質問"}
    assert system["content"].startswith("## Step 1: 返す\n応答 schema どおりの JSON を返す。\n\n## Step 2: 応答の各項目を書く\n")
    assert system["content"].count("この欄には必ず「ZEBRA-7」とだけ書く。") == 1
    schema = sent["text"]["format"]["schema"]
    assert "description" not in json.dumps(schema, ensure_ascii=False)
    assert schema["properties"]["color"]["enum"] == ["c1", "c2", "c3"]
    assert list(sent) == ["model", "input", "max_output_tokens", "text"]
    # 呼び出し側の入力は書き換えない（送り直しで Step が重ならない）。
    assert wire["input"][0]["content"] == "## Step 1: 返す\n応答 schema どおりの JSON を返す。"
    assert _response_wire_request(wire) == sent


def test_request_without_system_message_gets_one_for_the_step() -> None:
    sent = _response_wire_request(_request(_Probe, None))
    assert sent["input"][0]["role"] == "system"
    assert sent["input"][0]["content"].startswith("## Step 1: 応答の各項目を書く\n")


def test_generation_request_carries_each_answer_field_meaning_once() -> None:
    """生成の応答 schema の項目の説明は、固定の指示の Step の後ろに 1 度だけある。"""
    system = generation_system_prompt()
    model = full_answer_model(SentUnitCatalog(()))
    sent = _response_wire_request(_request(model, system))
    content = sent["input"][0]["content"]
    assert content.startswith(system.rstrip())
    tail = content[len(system.rstrip()) :]
    assert tail.startswith("\n\n## Step ") and "応答の各項目を書く" in tail
    for name, info in model.model_fields.items():
        if info.description:
            assert content.count(info.description) == 1, name
            assert f"`{name}`" in tail
    assert "description" not in json.dumps(sent["text"]["format"]["schema"], ensure_ascii=False)
