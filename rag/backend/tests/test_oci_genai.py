"""OCI Generative AI adapter 境界のテスト。"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.clients.oci_genai import OciGenAiClient
from app.config import Settings


async def test_embed_validates_oci_embedding_count() -> None:
    """OCI embedding adapter の返却件数が入力件数と違う場合は拒否する。"""
    client = StubEmbeddingClient(vectors=[[1.0, 0.0, 0.0]])

    with pytest.raises(ValueError, match="embedding の件数が一致しません"):
        await client.embed(["a", "b"])


async def test_embed_validates_oci_embedding_dimension() -> None:
    """OCI embedding adapter の次元数が Oracle VECTOR 幅と違う場合は拒否する。"""
    client = StubEmbeddingClient(vectors=[[1.0, 0.0]])

    with pytest.raises(ValueError, match=r"embedding\[0\] の次元数が不正です"):
        await client.embed(["a"])


async def test_embed_accepts_expected_dimension() -> None:
    """期待次元の embedding はそのまま返す。"""
    client = StubEmbeddingClient(vectors=[[1.0, 0.0, 0.0]])

    assert await client.embed(["a"]) == [[1.0, 0.0, 0.0]]


async def test_oci_embed_uses_generative_ai_embedding_request() -> None:
    """OCI embedding は Generative AI embed_text API に Cohere Embed v4 設定を渡す。"""
    sdk = FakeGenAiInferenceClient(
        embed_response=SimpleNamespace(data=SimpleNamespace(embeddings=[[1.0, 0.0, 0.0]]))
    )
    client = OciGenAiClient(
        settings=_oci_settings(),
        inference_client=sdk,
        sdk_call_runner=_run_inline,
    )

    vectors = await client.embed(["検索 query"], input_type="SEARCH_QUERY")

    assert vectors == [[1.0, 0.0, 0.0]]
    assert sdk.embed_calls == 1
    request = sdk.last_embed_request
    assert request is not None
    assert request.inputs == ["検索 query"]
    assert request.compartment_id == "ocid1.compartment.oc1..example"
    assert request.serving_mode.model_id == "cohere.embed-v4.0"
    assert request.input_type == "SEARCH_QUERY"
    assert request.output_dimensions == 3


async def test_embed_rejects_invalid_input_type() -> None:
    """OCI/local 共通で embedding input_type の誤指定を fail fast する。"""
    client = OciGenAiClient(
        settings=_oci_settings(),
        inference_client=FakeGenAiInferenceClient(),
        sdk_call_runner=_run_inline,
    )

    with pytest.raises(ValueError, match="embedding input_type が不正です"):
        await client.embed(["a"], input_type="BAD_TYPE")  # type: ignore[arg-type]


async def test_rerank_accepts_and_sorts_valid_oci_results() -> None:
    """OCI rerank adapter の正しい結果は score 降順に正規化する。"""
    client = StubRerankClient(results=[(1, 0.25), (0, 0.75)])

    assert await client.rerank("社内規程", ["本文 A", "本文 B"], top_n=2) == [
        (0, 0.75),
        (1, 0.25),
    ]


async def test_oci_rerank_uses_generative_ai_rerank_request() -> None:
    """OCI rerank は Generative AI rerank_text API に Cohere Rerank v4 fast 設定を渡す。"""
    sdk = FakeGenAiInferenceClient(
        rerank_response=SimpleNamespace(
            data=SimpleNamespace(
                document_ranks=[
                    SimpleNamespace(index=1, relevance_score=0.25),
                    SimpleNamespace(index=0, relevance_score=0.75),
                ]
            )
        )
    )
    client = OciGenAiClient(
        settings=_oci_settings(),
        inference_client=sdk,
        sdk_call_runner=_run_inline,
    )

    results = await client.rerank("承認条件", ["本文 A", "本文 B"], top_n=2)

    assert results == [(0, 0.75), (1, 0.25)]
    assert sdk.rerank_calls == 1
    request = sdk.last_rerank_request
    assert request is not None
    assert request.input == "承認条件"
    assert request.documents == ["本文 A", "本文 B"]
    assert request.compartment_id == "ocid1.compartment.oc1..example"
    assert request.serving_mode.model_id == "cohere.rerank-v4.0-fast"
    assert request.top_n == 2


async def test_rerank_returns_empty_without_oci_call_when_documents_are_empty() -> None:
    """候補文書が空なら OCI rerank 呼び出しを行わない。"""
    client = StubRerankClient(results=[(0, 0.75)])

    assert await client.rerank("社内規程", [], top_n=1) == []
    assert client.calls == 0


async def test_rerank_rejects_invalid_top_n() -> None:
    """top_n は 1 以上に制限する。"""
    client = StubRerankClient(results=[])

    with pytest.raises(ValueError, match="rerank top_n は 1 以上"):
        await client.rerank("社内規程", ["本文"], top_n=0)


async def test_rerank_rejects_too_many_results() -> None:
    """OCI rerank adapter が top_n を超える件数を返した場合は拒否する。"""
    client = StubRerankClient(results=[(0, 0.75), (1, 0.25)])

    with pytest.raises(ValueError, match="rerank の返却件数が不正です"):
        await client.rerank("社内規程", ["本文 A", "本文 B"], top_n=1)


async def test_rerank_rejects_duplicate_index() -> None:
    """OCI rerank adapter が同じ候補 index を重複返却した場合は拒否する。"""
    client = StubRerankClient(results=[(0, 0.75), (0, 0.25)])

    with pytest.raises(ValueError, match="rerank index が重複しています"):
        await client.rerank("社内規程", ["本文 A", "本文 B"], top_n=2)


async def test_rerank_rejects_out_of_range_index() -> None:
    """OCI rerank adapter が候補数外の index を返した場合は拒否する。"""
    client = StubRerankClient(results=[(2, 0.75)])

    with pytest.raises(ValueError, match="rerank index が範囲外です"):
        await client.rerank("社内規程", ["本文 A", "本文 B"], top_n=2)


async def test_rerank_rejects_non_finite_score() -> None:
    """OCI rerank adapter が NaN/Infinity score を返した場合は拒否する。"""
    client = StubRerankClient(results=[(0, float("nan"))])

    with pytest.raises(ValueError, match="rerank score が不正です"):
        await client.rerank("社内規程", ["本文"], top_n=1)


class StubEmbeddingClient(OciGenAiClient):
    """OCI adapter の戻り値だけを差し替えるテスト用 client。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        settings = Settings.model_construct(
            ai_service_adapter="oci",
            oci_genai_embedding_dim=3,
        )
        super().__init__(settings=settings)
        self._vectors = vectors

    async def _embed_with_oci(
        self,
        texts: list[str],
        *,
        input_type: str,
    ) -> list[list[float]]:
        return self._vectors


class StubRerankClient(OciGenAiClient):
    """OCI rerank adapter の戻り値だけを差し替えるテスト用 client。"""

    def __init__(self, results: list[tuple[int, float]]) -> None:
        settings = Settings.model_construct(
            ai_service_adapter="oci",
            oci_genai_embedding_dim=3,
        )
        super().__init__(settings=settings)
        self._results = results
        self.calls = 0

    async def _rerank_with_oci(
        self, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        self.calls += 1
        return self._results


class FakeGenAiInferenceClient:
    """OCI SDK client の最小 fake。"""

    def __init__(
        self,
        embed_response: object | None = None,
        rerank_response: object | None = None,
    ) -> None:
        self._embed_response = embed_response or SimpleNamespace(
            data=SimpleNamespace(embeddings=[])
        )
        self._rerank_response = rerank_response or SimpleNamespace(
            data=SimpleNamespace(document_ranks=[])
        )
        self.last_embed_request: Any | None = None
        self.last_rerank_request: Any | None = None
        self.embed_calls = 0
        self.rerank_calls = 0

    def embed_text(self, embed_text_details: object) -> object:
        self.embed_calls += 1
        self.last_embed_request = embed_text_details
        return self._embed_response

    def rerank_text(self, rerank_text_details: object) -> object:
        self.rerank_calls += 1
        self.last_rerank_request = rerank_text_details
        return self._rerank_response


def _oci_settings() -> Settings:
    return Settings.model_construct(
        ai_service_adapter="oci",
        oci_region="ap-osaka-1",
        oci_compartment_id="ocid1.compartment.oc1..example",
        oci_genai_embedding_model="cohere.embed-v4.0",
        oci_genai_embedding_dim=3,
        oci_genai_rerank_model="cohere.rerank-v4.0-fast",
    )


async def _run_inline(operation: Callable[[], Any]) -> Any:
    """テストでは同期 fake を同一 thread で実行する。"""
    return operation()
