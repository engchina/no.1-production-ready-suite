"""図・画像の説明を書く言語の基準（同じページの本文）が、Vision の呼び出しに渡ること（#4821）。

実設定・DB・モデルは使わない。文書と画像は合成。
"""

from __future__ import annotations

import json
import socket
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast

import pytest
from rag_engine.adapters.oci import _picture_output_contract
from rag_engine.models.layout import LayoutRecord
from rag_engine.parsing.vision_prompt_rules import PAGE_TEXT_SAMPLE_KEY
from rag_engine.resources.runtime import bind_prompt_overrides

from app.config import Settings
from app.rag.ingestion import IngestionPipeline
from app.schemas.extraction import StructuredExtraction

JAPANESE_BODY = "合成の文書 A の本文です。月ごとの件数の推移を次の図に示します。"
ENGLISH_BODY = "This is the body of synthetic document B. The next figure shows the monthly counts."


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*a: Any, **kw: Any) -> None:
        raise AssertionError("network_forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


class Reader:
    """Vision のスタブ。受け取った prompt を残し、決まった説明を返す。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_from_images(self, images: Any, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return json.dumps({"retrieval_text": "合成の説明", "visual_kind": "chart"})


def _record(
    record_id: str, category: str, text: str, seq_no: int, bbox: list[float]
) -> dict[str, Any]:
    return LayoutRecord(
        id=record_id,
        engine="docling",
        page=1,
        seq_no=seq_no,
        bbox=bbox,
        coord_system="image_top_left",
        page_width=100,
        page_height=100,
        category=category,
        text=text,
        raw_type="picture" if category == "Picture" else "",
    ).to_dict()


def _sent_metadata(prompt: str) -> dict[str, Any]:
    """prompt の「画像メタデータ:」の JSON（モデルが読む根拠データ）。"""
    start = prompt.index("画像メタデータ:\n") + len("画像メタデータ:\n")
    metadata, _end = json.JSONDecoder().raw_decode(prompt, start)
    return cast(dict[str, Any], metadata)


@pytest.mark.parametrize(
    ("body", "expected"),
    [(JAPANESE_BODY, JAPANESE_BODY), (ENGLISH_BODY, ENGLISH_BODY), (None, "")],
    ids=["japanese_document", "english_document", "image_only"],
)
@pytest.mark.asyncio
async def test_vision_call_receives_the_body_text_of_the_same_page(
    body: str | None, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    from app.clients import oci_enterprise_ai

    synthetic_config = SimpleNamespace(
        vision_model_id="synthetic",
        oci_enterprise_ai_endpoint="https://synthetic.invalid",
        oci_enterprise_ai_project_ocid="",
    )
    monkeypatch.setattr(
        oci_enterprise_ai,
        "config_from_settings",
        lambda *_a, **_k: SimpleNamespace(for_vision=lambda: synthetic_config),
    )
    reader = Reader()
    unused: Any = object()
    pipeline = IngestionPipeline(
        # model_construct は環境・保存済みの設定を読まない。値はすべて合成。
        settings=Settings.model_construct(
            rag_vision_enabled=True, rag_vision_image_purpose="record"
        ),
        vlm=cast(Any, reader),
        genai=unused,
        oracle=unused,
        object_storage=unused,
        document_understanding=unused,
        recipe_id="synthetic-recipe-a",
        recipe_revision=1,
    )
    records = [_record("picture-a", "Picture", "", 20, [10, 40, 80, 90])]
    if body is not None:
        records.insert(0, _record("text-a", "Text", body, 10, [10, 10, 90, 30]))
    extraction = StructuredExtraction(parser_artifacts={"layout_records": {"records": records}})
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PNG")

    # 保存済みの指示は無い（既定の指示で渡す）。
    with bind_prompt_overrides({}):
        await pipeline._attach_vision(
            "synthetic-trace",
            extraction,
            source_bytes=buffer.getvalue(),
            content_type="image/png",
            file_name="synthetic.png",
            parser_backend="docling",
            cancel_checker=None,
        )

    assert len(reader.prompts) == 1
    prompt = reader.prompts[0]
    assert _sent_metadata(prompt)[PAGE_TEXT_SAMPLE_KEY] == expected
    # 言語の決まり（基準の本文 → 画像の中の文字 → 既定の言語）は、どの文書でも同じ 1 つの文で渡る。
    language_rule = _picture_output_contract().split("### 説明の言語\n", 1)[1]
    assert prompt.count(language_rule) == 1
