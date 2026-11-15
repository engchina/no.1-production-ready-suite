"""業務 Agent の根拠の取得（MCP の rag_retrieve_evidence。complete_evidence=False）のテスト。

回答のモデルが画像を読めても、根拠の取得では図の画像を切り出さない（図は呼び出し元が
rag_read_source で読む。#5125）。検索は渡された質問 1 つで通常どおり行う。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import EnterpriseAiConfiguredModel, Settings
from app.rag.answer_engine import AnswerEngine
from app.rag.pipeline import RagPipeline
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest
from tests.test_answer_engine import FakeGenAi, FakeOracle


class QueryOracle(FakeOracle):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[str] = []

    async def hybrid_search(
        self,
        query: str,
        embedding: list[float],
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedChunk]:
        self.queries.append(query)
        return await super().hybrid_search(query, embedding, top_k, mode, filters)


def _vision_settings() -> Settings:
    """回答のモデルが画像を読める設定（回答の検索なら根拠の画像を切り出す。#3444）。"""
    return Settings(
        oci_compartment_id="ocid1.compartment.oc1..example",
        oci_enterprise_ai_models=[
            EnterpriseAiConfiguredModel(model_id="vlm", display_name="VLM", vision_enabled=True)
        ],
        oci_enterprise_ai_default_text_model="vlm",
    )


async def _fail_crop(*args: object, **kwargs: object) -> None:
    raise AssertionError("根拠の取得では図の画像を切り出さない")


@pytest.fixture
def no_image_crop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # 根拠の画像の切り出し（回答の検索だけが行う）。
    monkeypatch.setattr(AnswerEngine, "_materialize_images", _fail_crop)
    yield


async def test_evidence_retrieval_does_not_crop_images(no_image_crop: None) -> None:
    oracle = QueryOracle()
    genai = FakeGenAi()
    pipeline = RagPipeline(
        settings=_vision_settings(),
        oracle=oracle,  # type: ignore[arg-type]
        genai=genai,  # type: ignore[arg-type]
        complete_evidence=False,
    )

    response = await pipeline.run(
        SearchRequest(
            query="受注 登録方法",
            search_answer_profile_id="sap-1",
            generate_answer=False,
        )
    )

    assert response.answer == ""
    assert "受注 登録方法" in set(oracle.queries)
    roles = {citation.metadata.get("evidence_role") for citation in response.citations}
    assert "retrieved_anchor" in roles


async def test_answer_search_still_crops_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 対照: 回答の検索（RAG 検索の画面と同じ complete_evidence=True）は切り出しの工程を通る。
    called: list[int] = []

    async def record(self: AnswerEngine, chunks: object, state: object) -> None:
        called.append(1)

    monkeypatch.setattr(AnswerEngine, "_materialize_images", record)
    pipeline = RagPipeline(
        settings=_vision_settings(),
        oracle=QueryOracle(),  # type: ignore[arg-type]
        genai=FakeGenAi(),  # type: ignore[arg-type]
    )

    await pipeline.run(
        SearchRequest(
            query="受注 登録方法", search_answer_profile_id="sap-1", generate_answer=False
        )
    )

    assert called
