"""RAG の Enterprise AI の payload で、応答 schema の項目の説明が指示の Step だけにある（#5230）。

説明を Step にする処理そのもののテストは platform（``pr_backend_core.response_schema``）にある。
"""

from typing import Literal

from pydantic import BaseModel, Field

from rag_parser_core.oci_enterprise_ai import (
    OciEnterpriseAiConfig,
    _build_images_text_payload,
    _build_llm_payload,
)


class _Child(BaseModel):
    """型の説明（実装のメモ）。"""

    kind: Literal["k1", "k2"] = Field(description="k1=合成の種類 1、k2=合成の種類 2。")
    description: str = Field(description="子の項目の説明。")


class _Probe(BaseModel):
    """根の型の説明（実装のメモ）。"""

    note: str = Field(description="この欄には必ず「ZEBRA-7」とだけ書く。")
    color: Literal["c1", "c2", "c3"] = Field(description="c1=赤、c2=緑、c3=青。")
    plain: str
    first: list[_Child] = Field(description="最初の子の並び。")
    second: _Child | None = None


def _descriptions(node: object) -> list[str]:
    """schema の node に付いた ``description``（項目名の ``description`` は除く）。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                found.append(value)
            elif key in {"properties", "$defs"}:
                for child in value.values():
                    found.extend(_descriptions(child))
            else:
                found.extend(_descriptions(value))
    elif isinstance(node, list):
        for child in node:
            found.extend(_descriptions(child))
    return found


def test_text_payload_sends_descriptions_in_instructions_only() -> None:
    config = OciEnterpriseAiConfig(default_model_id="vendor.text")
    payload = _build_llm_payload(
        config,
        "",
        "合成の本文",
        system_prompt="## Step 1: 段の指示\n合成の指示。",
        response_schema=_Probe.model_json_schema(),
    )
    assert "## Step 2: 応答の各項目を書く" in payload["instructions"]
    assert "c1=赤、c2=緑、c3=青。" in payload["instructions"]
    assert _descriptions(payload["text"]["format"]["schema"]) == []


def test_schema_in_system_text_also_carries_no_descriptions() -> None:
    """schema を request に付けられないモデルでも、説明は Step の 1 か所だけ。"""
    config = OciEnterpriseAiConfig(default_model_id="google.gemini-synthetic")
    payload = _build_llm_payload(
        config,
        "",
        "合成の本文",
        system_prompt="合成の指示。",
        response_schema=_Probe.model_json_schema(),
    )
    assert "text" not in payload
    assert payload["instructions"].count("c1=赤、c2=緑、c3=青。") == 1


def test_image_payload_sends_descriptions_in_instructions_only() -> None:
    config = OciEnterpriseAiConfig(vision_model_id="vendor.vision")
    payload = _build_images_text_payload(
        config,
        [b"synthetic"],
        "合成の依頼",
        system_prompt="## Step 1: 画像を読む\n合成の指示。",
        response_schema=_Probe.model_json_schema(),
        response_schema_name="probe",
        mime_type="image/png",
    )
    assert "## Step 2: 応答の各項目を書く" in payload["instructions"]
    assert _descriptions(payload["text"]["format"]["schema"]) == []
