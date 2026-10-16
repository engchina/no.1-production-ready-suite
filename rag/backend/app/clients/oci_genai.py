"""OCI Generative AI クライアント（埋め込み / リランク）。

埋め込み: Cohere Embed v4（1536 次元）。
リランク: Cohere Rerank v4 fast。
"""

import asyncio
import hashlib
import importlib
import json
import math
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from numbers import Real
from typing import Any, Literal, Protocol

from pr_backend_core.bounded_calls import Attempt, BoundedCallFailed

from app.clients.ai_calls import ingestion_ai_calls
from app.clients.oci_auth import load_oci_config_without_prompt
from app.config import Settings, get_settings

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[ぁ-んァ-ン一-龯々ー]+", re.IGNORECASE)
EmbeddingInputType = Literal["SEARCH_DOCUMENT", "SEARCH_QUERY", "CLASSIFICATION", "CLUSTERING"]
EMBEDDING_INPUT_TYPES = frozenset(
    {"SEARCH_DOCUMENT", "SEARCH_QUERY", "CLASSIFICATION", "CLUSTERING"}
)
# ponytail: Cohere Embed v4 128k tokens への保守的な文字数ガード。
# tokenizer は実際に必要になるまで入れない。
EMBEDDING_INPUT_MAX_CHARS = 100_000
# OCI Embed 4 は 1 request 内の全入力で 128k token。tokenizer 依存を増やさず保守的に抑える。
EMBEDDING_REQUEST_MAX_CHARS = 100_000
EMBEDDING_REQUEST_MAX_INPUTS = 96
type SdkCallRunner = Callable[[Callable[[], Any]], Awaitable[Any]]


class GenerativeAiInferenceClientProtocol(Protocol):
    """OCI Generative AI Inference client の最小インターフェース。"""

    def embed_text(self, embed_text_details: object) -> Any:
        """OCI Generative AI embed_text を呼び出す。"""

    def rerank_text(self, rerank_text_details: object) -> Any:
        """OCI Generative AI rerank_text を呼び出す。"""


class OciGenAiConfigError(ValueError):
    """利用者が OCI 認証設定を直せる Generative AI 設定エラー。"""

    safe_for_user = True


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
        self._embedding_cache: OrderedDict[str, list[float]] = OrderedDict()
        self._rerank_cache: OrderedDict[str, list[tuple[int, float]]] = OrderedDict()

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType = "SEARCH_DOCUMENT",
    ) -> list[list[float]]:
        """テキストを 1536 次元ベクトルに埋め込む（Cohere Embed v4）。"""
        if input_type not in EMBEDDING_INPUT_TYPES:
            raise ValueError(f"embedding input_type が不正です。input_type={input_type}")
        if not texts:
            return []
        _validate_embedding_input_lengths(texts)
        if self._embedding_cache_disabled():
            vectors = await self._embed_with_oci_batches(texts, input_type=input_type)
            _validate_embedding_batch(
                vectors,
                expected_count=len(texts),
                expected_dim=self._settings.oci_genai_embedding_dim,
            )
            return vectors

        vectors = await self._embed_with_cache(texts, input_type=input_type)
        _validate_embedding_batch(
            vectors,
            expected_count=len(texts),
            expected_dim=self._settings.oci_genai_embedding_dim,
        )
        return vectors

    async def _embed_with_cache(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[list[float]]:
        """cache hit を再利用し、miss だけをまとめて OCI embedding に出す。"""
        vectors: list[list[float] | None] = [None] * len(texts)
        miss_texts: list[str] = []
        miss_keys: list[str] = []
        miss_positions: dict[str, list[int]] = {}
        for position, text in enumerate(texts):
            key = _embedding_cache_key(
                text=text,
                input_type=input_type,
                model_id=self._settings.oci_genai_embedding_model,
                dimension=self._settings.oci_genai_embedding_dim,
            )
            cached = self._get_cached_embedding(key)
            if cached is not None:
                vectors[position] = cached
                continue
            positions = miss_positions.setdefault(key, [])
            positions.append(position)
            if len(positions) == 1:
                miss_keys.append(key)
                miss_texts.append(text)

        if miss_texts:
            missing_vectors = await self._embed_with_oci_batches(
                miss_texts,
                input_type=input_type,
            )
            _validate_embedding_batch(
                missing_vectors,
                expected_count=len(miss_texts),
                expected_dim=self._settings.oci_genai_embedding_dim,
            )
            for key, vector in zip(miss_keys, missing_vectors, strict=True):
                self._store_embedding(key, vector)
                for position in miss_positions[key]:
                    vectors[position] = list(vector)

        return [_require_cached_vector(vector, index) for index, vector in enumerate(vectors)]

    async def _embed_with_oci_batches(
        self,
        texts: list[str],
        *,
        input_type: EmbeddingInputType,
    ) -> list[list[float]]:
        """OCI embedding request を設定値で分割し、入力順に結合して返す。

        batch は上限つきの並行で送る(#2002。上限は ``rag_embedding_concurrency``)。429・5xx・
        接続・timeout の再試行と全体の待ちは共通の実行器が受け持つ(OCI SDK の再試行は切っている)。
        結果は入力の順に結合する。最後の再試行でも失敗した batch があれば、その失敗を出す。
        """
        batch_size = min(
            EMBEDDING_REQUEST_MAX_INPUTS,
            max(1, getattr(self._settings, "rag_embedding_batch_size", 96)),
        )
        batches = _embedding_request_batches(texts, max_count=batch_size)
        if not batches:
            return []

        async def embed_batch(batch: list[str], _: Attempt) -> list[list[float]]:
            batch_vectors = await self._embed_with_oci(batch, input_type=input_type)
            _validate_embedding_batch(
                batch_vectors,
                expected_count=len(batch),
                expected_dim=self._settings.oci_genai_embedding_dim,
            )
            return batch_vectors

        with ingestion_ai_calls(self._settings, "embedding", total=len(batches)) as caller:
            outcomes = await _unwrapped_item_failure(caller.map(batches, embed_batch))
        vectors: list[list[float]] = []
        for outcome in outcomes:
            if outcome.error is not None:
                raise outcome.error.__cause__ or outcome.error
            vectors.extend(outcome.value or [])
        return vectors

    async def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """Cohere Rerank v4 fast で再ランク付けし、(index, score) を返す。"""
        if top_n < 1:
            raise ValueError(f"rerank top_n は 1 以上である必要があります。actual={top_n}")
        if not documents:
            return []
        cache_key: str | None = None
        if not self._rerank_cache_disabled():
            cache_key = _rerank_cache_key(
                query=query,
                documents=documents,
                top_n=top_n,
                model_id=self._settings.oci_genai_rerank_model,
            )
            cached = self._get_cached_rerank(cache_key)
            if cached is not None:
                return cached

        async def rerank_once(_: Attempt) -> Sequence[tuple[object, object]]:
            return await self._rerank_with_oci(query, documents, top_n)

        # OCI SDK の再試行は切っているため、再試行は共通の実行器が受け持つ(#2002)。
        with ingestion_ai_calls(self._settings, "rerank", total=1) as caller:
            results = await _unwrapped_item_failure(caller.call(rerank_once, label="rerank"))
        validated = _validate_rerank_results(results, document_count=len(documents), top_n=top_n)
        if cache_key is not None:
            self._store_rerank(cache_key, validated)
        return validated

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
        )
        # 再試行は共通の上限つきの並行の実行器が受け持つ(#2002)。SDK の既定の再試行
        # (DEFAULT_RETRY_STRATEGY)を残すと、全体の待ち・並行の数の調整・予算を通らずに再試行する。
        # SDK の既定の circuit breaker も切る。429 が 10 回続くと先に開き、実行器の知らない
        # CircuitBreakerError で件・job が失敗する(#2211)。
        oci_retry = importlib.import_module("oci.retry")
        oci_breaker = importlib.import_module("oci.circuit_breaker")
        try:
            self._inference_client = genai.GenerativeAiInferenceClient(
                config,
                retry_strategy=oci_retry.NoneRetryStrategy(),
                circuit_breaker_strategy=oci_breaker.NoCircuitBreakerStrategy(),
            )
        except Exception as exc:
            if type(exc).__name__ == "InvalidConfig":
                raise OciGenAiConfigError(_oci_invalid_config_message(exc)) from exc
            raise
        return self._inference_client

    def _embedding_cache_disabled(self) -> bool:
        return not getattr(self._settings, "rag_embedding_cache_enabled", True) or (
            getattr(self._settings, "rag_embedding_cache_max_entries", 4096) <= 0
        )

    def _rerank_cache_disabled(self) -> bool:
        return not getattr(self._settings, "rag_rerank_cache_enabled", True) or (
            getattr(self._settings, "rag_rerank_cache_max_entries", 1024) <= 0
        )

    def _get_cached_embedding(self, key: str) -> list[float] | None:
        cached = self._embedding_cache.pop(key, None)
        if cached is None:
            return None
        self._embedding_cache[key] = cached
        return list(cached)

    def _store_embedding(self, key: str, vector: Sequence[float]) -> None:
        self._embedding_cache[key] = [float(value) for value in vector]
        self._trim_embedding_cache()

    def _trim_embedding_cache(self) -> None:
        max_entries = getattr(self._settings, "rag_embedding_cache_max_entries", 4096)
        while len(self._embedding_cache) > max_entries:
            self._embedding_cache.popitem(last=False)

    def _get_cached_rerank(self, key: str) -> list[tuple[int, float]] | None:
        cached = self._rerank_cache.pop(key, None)
        if cached is None:
            return None
        self._rerank_cache[key] = cached
        return list(cached)

    def _store_rerank(self, key: str, results: Sequence[tuple[int, float]]) -> None:
        self._rerank_cache[key] = [(int(index), float(score)) for index, score in results]
        self._trim_rerank_cache()

    def _trim_rerank_cache(self) -> None:
        max_entries = getattr(self._settings, "rag_rerank_cache_max_entries", 1024)
        while len(self._rerank_cache) > max_entries:
            self._rerank_cache.popitem(last=False)


async def _unwrapped_item_failure[T](awaitable: Awaitable[T]) -> T:
    """件の失敗は元の例外(SDK の例外・検証の失敗)で出す(呼び出し側の扱いを変えない)。

    job を止める失敗(``BoundedCallAborted``。権限・設定の誤りなど)は、直す所の案内の文のまま出す。
    """
    try:
        return await awaitable
    except BoundedCallFailed as exc:
        cause = exc.__cause__
        if isinstance(cause, Exception):
            raise cause from None
        raise


async def _run_sdk_call_in_thread(operation: Callable[[], Any]) -> Any:
    """同期 OCI SDK 呼び出しを event loop 外で実行する。"""
    return await asyncio.to_thread(operation)


def _embedding_cache_key(
    *,
    text: str,
    input_type: EmbeddingInputType,
    model_id: str,
    dimension: int,
) -> str:
    """embedding cache key を原文なしの安定 hash で作る。"""
    return _cache_key(
        {
            "kind": "embedding",
            "model_id": model_id,
            "dimension": dimension,
            "input_type": input_type,
            "text_sha256": _sha256_text(text),
        }
    )


def _rerank_cache_key(
    *,
    query: str,
    documents: list[str],
    top_n: int,
    model_id: str,
) -> str:
    """rerank cache key を query/document 原文なしの安定 hash で作る。"""
    return _cache_key(
        {
            "kind": "rerank",
            "model_id": model_id,
            "top_n": top_n,
            "query_sha256": _sha256_text(query),
            "document_sha256": [_sha256_text(document) for document in documents],
        }
    )


def _cache_key(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _oci_invalid_config_message(error: Exception) -> str:
    text = str(error).lower()
    if "fingerprint" in text:
        return (
            "OCI 認証設定の fingerprint が不正です。"
            "システム設定 > OCI 認証で API key fingerprint を"
            "16 進数のコロン区切り形式に修正してください。"
        )
    return "OCI 認証設定が不正です。システム設定 > OCI 認証で設定を確認してください。"


def _require_cached_vector(vector: list[float] | None, index: int) -> list[float]:
    if vector is None:
        raise ValueError(f"embedding[{index}] の cache 復元に失敗しました。")
    return list(vector)


def _validate_embedding_input_lengths(texts: Sequence[str]) -> None:
    for index, text in enumerate(texts):
        chars = len(text)
        if chars > EMBEDDING_INPUT_MAX_CHARS:
            raise ValueError(
                "embedding input が長すぎます。"
                f"index={index}, chars={chars}, max_chars={EMBEDDING_INPUT_MAX_CHARS}"
            )


def _embedding_request_batches(texts: Sequence[str], *, max_count: int) -> list[list[str]]:
    """入力順を保ったまま件数と保守的な総文字数の両方で request を分割する。"""
    batches: list[list[str]] = []
    batch: list[str] = []
    batch_chars = 0
    for text in texts:
        if batch and (
            len(batch) >= max_count or batch_chars + len(text) > EMBEDDING_REQUEST_MAX_CHARS
        ):
            batches.append(batch)
            batch = []
            batch_chars = 0
        batch.append(text)
        batch_chars += len(text)
    if batch:
        batches.append(batch)
    return batches


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
