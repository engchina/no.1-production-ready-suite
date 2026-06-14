"""OCI Generative AI クライアント（埋め込み / リランク）。

埋め込み: Cohere Embed v4（1536 次元）。
リランク: Cohere Rerank v4 fast。
"""

import asyncio
import importlib
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from numbers import Real
from typing import Any, Literal, Protocol

from app.clients.oci_auth import load_oci_config_without_prompt
from app.config import Settings, get_settings

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龯々ー]+", re.IGNORECASE)
EmbeddingInputType = Literal["SEARCH_DOCUMENT", "SEARCH_QUERY", "CLASSIFICATION", "CLUSTERING"]
EMBEDDING_INPUT_TYPES = frozenset(
    {"SEARCH_DOCUMENT", "SEARCH_QUERY", "CLASSIFICATION", "CLUSTERING"}
)
type SdkCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


class GenerativeAiInferenceClientProtocol(Protocol):
    """OCI Generative AI Inference client の最小インターフェース。"""

    def embed_text(self, embed_text_details: object) -> Any:
        """OCI Generative AI embed_text を呼び出す。"""

    def rerank_text(self, rerank_text_details: object) -> Any:
        """OCI Generative AI rerank_text を呼び出す。"""


class OciGenAiClient:
    """OCI Generative AI による埋め込み / リランククライアント。"""

    def __init__(
        self,
        settings: Settings | None = None,
        inference_client: GenerativeAiInferenceClientProtocol | None = None,
        sdk_call_runner: SdkCallRunner | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._inference_client = inference_client
        self._sdk_call_runner = sdk_call_runner or _run_sdk_call_in_thread

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        """テキストを 1536 次元ベクトルに埋め込む（Cohere Embed v4）。"""
        if input_type not in EMBEDDING_INPUT_TYPES:
            raise ValueError(f"embedding input_type が不正です。input_type={input_type}")
        vectors = await self._embed_with_oci(texts, input_type=input_type)
        _validate_embedding_batch(
            vectors,
            expected_count=len(texts),
            expected_dim=self._settings.oci_genai_embedding_dim,
        )
        return vectors

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """Cohere Rerank v4 fast で再ランク付けし、(index, score) を返す。"""
        if top_n < 1:
            raise ValueError(f"rerank top_n は 1 以上である必要があります。actual={top_n}")
        if not documents:
            return []
        results = await self._rerank_with_oci(query, documents, top_n)
        return _validate_rerank_results(results, document_count=len(documents), top_n=top_n)

    async def _embed_with_oci(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[list[float]]:
        """OCI Generative AI Embedding 呼び出し。

        本番接続の実装ポイント。LLM/VLM には使わず、embedding 専用に限定する。
        """
        models = importlib.import_module("oci.generative_ai_inference.models")
        details = models.EmbedTextDetails(
            inputs=texts,
            serving_mode=models.OnDemandServingMode(
                model_id=self._settings.oci_genai_embedding_model
            ),
            compartment_id=self._settings.oci_compartment_id,
            input_type=input_type,
            output_dimensions=self._settings.oci_genai_embedding_dim,
        )
        response = await self._sdk_call_runner(lambda: self._client().embed_text(details))
        embeddings = getattr(response.data, "embeddings", None)
        if not isinstance(embeddings, list):
            raise ValueError("OCI embedding response に embeddings がありません。")
        return embeddings

    async def _rerank_with_oci(
        self, query: str, documents: list[str], top_n: int
    ) -> Sequence[tuple[object, object]]:
        """OCI Generative AI Rerank 呼び出し。"""
        models = importlib.import_module("oci.generative_ai_inference.models")
        details = models.RerankTextDetails(
            input=query,
            documents=documents,
            serving_mode=models.OnDemandServingMode(model_id=self._settings.oci_genai_rerank_model),
            compartment_id=self._settings.oci_compartment_id,
            top_n=top_n,
        )
        response = await self._sdk_call_runner(lambda: self._client().rerank_text(details))
        document_ranks = getattr(response.data, "document_ranks", None)
        if not isinstance(document_ranks, list):
            raise ValueError("OCI rerank response に document_ranks がありません。")
        return [
            (getattr(rank, "index", None), getattr(rank, "relevance_score", None))
            for rank in document_ranks
        ]

    def _client(self) -> GenerativeAiInferenceClientProtocol:
        """OCI Generative AI Inference client を遅延初期化する。"""
        if self._inference_client is not None:
            return self._inference_client

        oci_config = importlib.import_module("oci.config")
        genai = importlib.import_module("oci.generative_ai_inference")
        config = load_oci_config_without_prompt(
            oci_config,
            self._settings.oci_config_file,
            self._settings.oci_config_profile,
            region=self._settings.oci_region.strip() or None,
        )
        self._inference_client = genai.GenerativeAiInferenceClient(config)
        return self._inference_client


async def _run_sdk_call_in_thread(operation: Callable[[], Any]) -> Any:
    """同期 OCI SDK 呼び出しを event loop 外で実行する。"""
    return await asyncio.to_thread(operation)


def _validate_embedding_batch(
    vectors: list[list[float]],
    expected_count: int,
    expected_dim: int,
) -> None:
    """Embedding adapter の返却件数と次元数を検証する。"""
    if len(vectors) != expected_count:
        raise ValueError(
            f"embedding の件数が一致しません。expected={expected_count}, actual={len(vectors)}"
        )
    for index, vector in enumerate(vectors):
        if len(vector) != expected_dim:
            raise ValueError(
                f"embedding[{index}] の次元数が不正です。"
                f"expected={expected_dim}, actual={len(vector)}"
            )


def _validate_rerank_results(
    results: Sequence[tuple[object, object]],
    *,
    document_count: int,
    top_n: int,
) -> list[tuple[int, float]]:
    """Rerank adapter の index/score 契約を検証し、score 降順に正規化する。"""
    expected_max = min(document_count, top_n)
    if len(results) > expected_max:
        message = f"rerank の返却件数が不正です。expected_max={expected_max}, actual={len(results)}"
        raise ValueError(message)

    seen_indexes: set[int] = set()
    validated: list[tuple[int, float]] = []
    for position, (index, score) in enumerate(results):
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"rerank index の型が不正です。position={position}, index={index!r}")
        if index in seen_indexes:
            raise ValueError(f"rerank index が重複しています。index={index}")
        if index < 0 or index >= document_count:
            raise ValueError(
                f"rerank index が範囲外です。index={index}, document_count={document_count}"
            )
        if not isinstance(score, Real) or isinstance(score, bool):
            message = f"rerank score が不正です。index={index}, score={score!r}"
            raise ValueError(message)
        normalized_score = float(score)
        if not math.isfinite(normalized_score):
            message = f"rerank score が不正です。index={index}, score={score!r}"
            raise ValueError(message)
        seen_indexes.add(index)
        validated.append((index, normalized_score))

    return sorted(validated, key=lambda item: item[1], reverse=True)


def _tokens(text: str) -> list[str]:
    """日本語・英数字の簡易トークン化。"""
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def _lexical_relevance(
    query_tokens: list[str], document_tokens: list[str], query: str, document: str
) -> float:
    """ローカル rerank 用の語彙一致スコア。"""
    if not query_tokens or not document_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    doc_counts = Counter(document_tokens)
    overlap = sum(min(query_counts[token], doc_counts[token]) for token in query_counts)
    recall = overlap / max(1, sum(query_counts.values()))
    precision = overlap / max(1, sum(doc_counts.values()))
    phrase_boost = 0.25 if query.lower() in document.lower() else 0.0
    return round((0.7 * recall) + (0.3 * precision) + phrase_boost, 6)
