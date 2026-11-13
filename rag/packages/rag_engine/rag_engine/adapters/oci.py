"""OCI/OpenAI 互換 LLM、rerank、embedding の呼び出しを抽象化する。"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
from typing import Any, cast

from pr_backend_core.bounded_calls import ErrorKind, classify_error, parse_retry_after
from pr_backend_core.llm_request_params_httpx import ParameterDroppingClient
from pr_backend_core.observability.call_stream import CallStreamCapture
from pr_backend_core.observability.model_purpose import (
    call_with_model_purpose,
    model_call_purpose,
    model_purpose_attributes,
)
from pr_backend_core.output_budget import online_output_budget, output_limit_message
from pydantic import BaseModel

from rag_engine.config import (
    DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS,
    DEFAULT_MODEL_OUTPUT_TOKENS,
    LlmProviderSettings,
    Settings,
    get_llm_provider,
    llm_output_token_limit,
    oci_inference_endpoint,
)
from rag_engine.generation.response_language import default_response_language
from rag_engine.knowledge.prompt_files import (
    IMAGE_RETRIEVAL_PROMPT_KEY,
    continue_prompt_steps,
    read_prompt,
    render_prompt_template,
)
from rag_engine.models.llm import (
    GroundedDraft,
    PictureDescriptionOutput,
    RerankTextRank,
    TextOutput,
)
from rag_engine.parsing.vision_prompt_rules import (
    MAX_CHART_SERIES_VALUES,
    MAX_TABLE_COLUMNS,
    PAGE_TEXT_SAMPLE_KEY,
    PICTURE_EXTRACTION_INSTRUCTION,
    TABLE_EXTRACTION_INSTRUCTION,
    VISION_LIST_FIELD_LIMITS,
    refine_image_retrieval_prompt,
    vision_context_instructions,
)
from rag_engine.resources.cache import application_cache
from rag_engine.resources.deadline import (
    DeadlineExceeded,
    check_deadline,
    current_deadline,
    expire_deadline,
    remaining_seconds,
)
from rag_engine.resources.external_calls import send_http
from rag_engine.resources.llm_retry_budget import LlmRetryBudget as _LlmRetryBudget
from rag_engine.resources.llm_retry_budget import retry_budget_context as _llm_retry_budget
from rag_engine.resources.llm_usage import (
    record_llm_effort_retry,
    record_llm_output_retry,
    record_llm_runaway_resend,
    record_llm_stall_resend,
    record_llm_usage,
    response_usage,
)
from rag_engine.resources.rerank_diagnostics import (
    RerankCallReceipt,
    bind_rerank_attempt,
    current_rerank_attempt,
    current_rerank_receipt,
    use_rerank_receipt,
)
from rag_engine.resources.runtime import resolve_prompt
from rag_engine.resources.structured_failures import structured_failure_details
from rag_engine.resources.structured_output_retry import notify_invalid_output, structured_output_prompt
from rag_engine.resources.tracing import observe_call

VISION_SYSTEM_PROMPT = "## Step 1: 画像説明を作成する\nあなたは問い合わせRAG用の画像説明作成担当です。\n"
API_SIGNING_KEY_FIELDS = ("user", "tenancy", "fingerprint", "key_file", "region")


EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT = "SEARCH_DOCUMENT"
EMBEDDING_INPUT_TYPE_SEARCH_QUERY = "SEARCH_QUERY"
EMBEDDING_INPUT_TYPE_IMAGE = "IMAGE"
SUPPORTED_EMBEDDING_OUTPUT_DIMENSIONS = {256, 512, 1024, 1536}
# tokenizer 未設定時の保守的な batch 分割目安。実 token 上限は OCI が NONE で検証する。
EMBEDDING_BATCH_MAX_UTF8_BYTES = 64000
# Cohere Rerank v4 の 1 document あたりの入力目安（UTF-8 byte。token 実測値ではない保守的な値）。
RERANK_V4_DOCUMENT_BUDGET_UTF8_BYTES = 32000
# 1 request で送れる document × 内部 chunk 数の上限。
RERANK_MAX_DOCUMENT_CHUNKS_PER_REQUEST = 10000


@dataclass(frozen=True)
class _Runtime:
    oci: Any
    models: Any
    client: Any
    compartment_id: str


def _load_api_signing_key_config(oci: Any, config_file: str, profile: str) -> dict[str, Any]:
    """CLI session token ではなく、長期利用する OCI API signing-key profile を読み込みます。"""
    sdk_config = dict(oci.config.from_file(str(Path(config_file).expanduser()), profile))
    missing = [field for field in API_SIGNING_KEY_FIELDS if not sdk_config.get(field)]
    if missing:
        raise RuntimeError("OCI API signing key profile に必要な設定がありません: " + ", ".join(missing))
    # 混在 profile に任意 key が含まれていても、この client は session-token auth ではなく
    # 標準の user/fingerprint/private-key signer を必ず使う。
    sdk_config.pop("security_token_file", None)
    return sdk_config


@application_cache(maxsize=16)
def _runtime(
    config_file: str,
    profile: str,
    compartment_id: str,
    endpoint: str,
    timeout_seconds: int,
) -> _Runtime:
    try:
        import oci
    except ImportError as exc:
        raise RuntimeError("OCI SDK が未インストールです。`oci>=2.185.0` をインストールしてください。") from exc
    if not compartment_id:
        raise RuntimeError("OCI_COMPARTMENT_ID が未設定です。")

    sdk_config = _load_api_signing_key_config(oci, config_file, profile)
    service_endpoint = endpoint or oci_inference_endpoint(sdk_config["region"])
    read_timeout = max(1, int(timeout_seconds))
    client = oci.generative_ai_inference.GenerativeAiInferenceClient(
        sdk_config,
        service_endpoint=service_endpoint,
        timeout=(min(10, read_timeout), read_timeout),
        # 再試行は呼び出しごとに切り(NoneRetryStrategy)、呼び出し側の実行器が受け持つ。SDK の既定の
        # circuit breaker も切る。429 が続くと先に開き、実行器の知らない例外で失敗する(#2211)。
        circuit_breaker_strategy=oci.circuit_breaker.NoCircuitBreakerStrategy(),
    )
    return _Runtime(
        oci=oci,
        models=oci.generative_ai_inference.models,
        client=client,
        compartment_id=compartment_id,
    )


# 生成・評価の判定・Vision 説明の全呼出で同じ入力から同じ出力を得やすくするための設定。
LLM_TEMPERATURE = 0.0


class _RecordingHttpClient(ParameterDroppingClient):
    """モデルの API の httpx client。評価の記録・再生の session があれば応答を記録・再生する（#2574）。

    session が無ければ素通しなので、本番の経路は変わらない。
    """

    def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        parent = super().send
        return send_http(lambda item: parent(item, *args, **kwargs), request)


@application_cache(maxsize=16)
def _openai_client(
    provider_id: str,
    base_url: str,
    api_key: str,
    project_id: str,
    timeout_seconds: int,
) -> Any:
    """OpenAI 互換 Responses 経路の client を生成します（provider ごとに再利用）。

    SDK の再試行は切ります。やり直しは 1 回の呼び出しの共通の枠（`_parse_with_retry`。#4736）だけで数えます。

    `project_id` は OCI の OpenAI 互換 API 専用の設定です。空のときは `OpenAI-Project`
    ヘッダを付けない（`project=None`）ことで、project の概念を持たない OpenAI 互換 API でも
    そのまま使えるようにします。
    """
    try:
        from openai import DEFAULT_CONNECTION_LIMITS, OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI Python SDK が未インストールです。`openai` をインストールしてください。") from exc
    timeout = max(1, int(timeout_seconds))
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        project=project_id or None,
        timeout=timeout,
        max_retries=0,
        # モデルが受け付けない引数（temperature など）の 400 は、外して 1 回だけ送り直し、以後この
        # モデルには送らない（#3539。platform の部品）。受け付けるモデルには temperature 0 を送り続ける。
        # timeout・接続の上限・redirect は SDK の既定の client と同じにする。
        http_client=_RecordingHttpClient(timeout=timeout, limits=DEFAULT_CONNECTION_LIMITS, follow_redirects=True),
    )


def _serving_mode(models: Any, model_id: str):
    if model_id.startswith("ocid1.generativeaiendpoint."):
        return models.DedicatedServingMode(endpoint_id=model_id)
    return models.OnDemandServingMode(model_id=model_id)


def _image_retrieval_template() -> str:
    """保存済みの画像検索 prompt へ共通規則を合成した、Vision 用テンプレートを読み込みます。"""
    return refine_image_retrieval_prompt(read_prompt(IMAGE_RETRIEVAL_PROMPT_KEY)).strip()


def _render_prompt(metadata: dict[str, Any], *, target_kind: str = "picture", template: str | None = None) -> str:
    """Vision へ送る prompt を組み立てます。

    template を省略すると呼び出しごとに保存済みファイルを読みます。複数の対象を処理する呼び出し元は
    `_image_retrieval_template()` を一度だけ読んで渡し、処理中の保存で新旧の prompt が混ざらないようにします。
    """
    if target_kind not in {"picture", "table"}:
        raise ValueError(f"Unknown Vision target kind: {target_kind}")
    if template is None:
        template = _image_retrieval_template()
    refined = refine_image_retrieval_prompt(template)
    prompt = render_prompt_template(
        refined,
        {
            "image_metadata": _json_dumps(metadata),
            "image": _image_prompt_note(metadata),
        },
    )
    # 保存済みテンプレートを書き換えず、古い JSON 例にも現在の API 出力契約を補います。
    instruction = (
        resolve_prompt("picture_description.table", TABLE_EXTRACTION_INSTRUCTION)
        if target_kind == "table"
        else resolve_prompt("picture_description.picture", PICTURE_EXTRACTION_INSTRUCTION)
    )
    # 周辺の画像・記録の読み方は、その入力があるときだけ対象の指示の後ろに足す。
    instruction = "\n\n".join([instruction, *vision_context_instructions(metadata)])
    # 保存したテンプレートの Step の数が既定と違っても、対象の指示と出力契約の番号を続けます（#3810）。
    # 番号は差し込み前のテンプレートから数え、metadata などの根拠データの見出しで決めません。
    instruction = continue_prompt_steps(refined, instruction)
    contract = continue_prompt_steps(refined + "\n\n" + instruction, _picture_output_contract())
    return prompt + "\n\n" + instruction + "\n\n" + contract


def picture_response_schema() -> dict[str, Any]:
    """画像の説明の応答の schema。項目・型・項目の意味（Field の説明）はここだけで渡します（#4782）。"""
    return _compact_schema(PictureDescriptionOutput.model_json_schema(), keep_field_descriptions=True)


def _picture_output_contract() -> str:
    """応答の schema では表せない、空の値の扱い・構造配列の件数の上限（保存の処理と同じ定数）・説明を書く言語。

    言語の決まりはここだけに書く（#4821）。基準の本文は metadata の key で渡し、無いときの既定は回答と同じ既定の言語。
    """
    return (
        "## Step 7: 結果を返す\n"
        "該当しない文字列は空文字、配列は空配列にしてください。\n"
        + "構造配列の上限: "
        + "、".join(f"{field}={limit}" for field, limit in VISION_LIST_FIELD_LIMITS.items())
        + f"。table_rows の各行は最大 {MAX_TABLE_COLUMNS} セル、chart_series の values は各系列最大 {MAX_CHART_SERIES_VALUES} 件。\n"
        "\n"
        "### 説明の言語\n"
        "画像から写す文字は原文の表記のまま残し、あなたが書く文（要約・説明・質問例・言い換え）は、"
        f"metadata.{PAGE_TEXT_SAMPLE_KEY}（対象と同じページの本文の抜粋）と同じ言語で書いてください。"
        f"{PAGE_TEXT_SAMPLE_KEY} が空なら画像の中の文字と同じ言語、画像に文字も無ければ{default_response_language()}で書いてください。"
    )


def _compact_schema(schema: dict[str, Any], *, keep_field_descriptions: bool = False, _field: bool = False) -> dict[str, Any]:
    """検証制約を保持し、prompt 用コピーから title 等の重複した注釈だけを除きます。

    ``keep_field_descriptions`` は項目の説明（出力の項目の意味の正本）を残すときに渡します。
    モデルの docstring に由来する型の説明は常に除きます。
    """
    compact: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"title", "default"} or (key == "description" and not (keep_field_descriptions and _field)):
            continue
        if key in {"properties", "$defs"}:
            # description という実際の field 名は注釈ではないため、辞書のキーを保持します。
            compact[key] = {
                name: _compact_schema(item, keep_field_descriptions=keep_field_descriptions, _field=key == "properties")
                for name, item in value.items()
            }
        elif isinstance(value, dict):
            compact[key] = _compact_schema(value, keep_field_descriptions=keep_field_descriptions)
        elif isinstance(value, list):
            compact[key] = [
                _compact_schema(item, keep_field_descriptions=keep_field_descriptions) if isinstance(item, dict) else item for item in value
            ]
        else:
            compact[key] = value
    return compact


def _image_prompt_note(metadata: dict[str, Any]) -> str:
    image_inputs = metadata.get("image_inputs")
    if not isinstance(image_inputs, list) or not image_inputs:
        return "画像はこのメッセージに添付されています。"

    lines = [
        "画像はこのメッセージに次の順序で添付されています。",
        "画像1が主対象、画像2以降が周辺文脈です。抽出と帰属は共通規則に従ってください。",
    ]
    for index, image_input in enumerate(image_inputs, start=1):
        if not isinstance(image_input, dict):
            continue
        role = str(image_input.get("role") or f"image_{index}")
        description = str(image_input.get("description") or "").strip()
        bbox = image_input.get("bbox")
        bbox_text = f" bbox={bbox}" if isinstance(bbox, list) and bbox else ""
        if description:
            lines.append(f"画像{index}: {role} - {description}{bbox_text}")
        else:
            lines.append(f"画像{index}: {role}{bbox_text}")
    return "\n".join(lines)


def parse_text_response[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    *,
    provider_id: str | None = None,
    purpose: str | None = None,
) -> StructuredOutputT:
    """text-only LLM 呼び出しを構造化 output model として解析します。"""
    provider = _validated_llm_provider(
        settings,
        provider_id or settings.default_answer_llm,
        require_vision=False,
    )
    with model_call_purpose(purpose):
        return _parse_text_with_retry(system_prompt, prompt, settings, text_format, provider)


def parse_image_response[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_path: Path,
    settings: Settings,
    text_format: type[StructuredOutputT],
    *,
    provider_id: str | None = None,
) -> StructuredOutputT:
    """単一画像付き LLM 呼び出しを構造化 output model として解析します。"""
    provider = _validated_llm_provider(
        settings,
        provider_id or settings.default_vision_llm,
        require_vision=True,
    )
    return _parse_image_with_retry(system_prompt, prompt, image_path, settings, text_format, provider)


def parse_multimodal_response[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[str | Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    *,
    provider_id: str | None = None,
    purpose: str | None = None,
) -> StructuredOutputT:
    """複数画像付き LLM 呼び出しを構造化 output model として解析します。"""
    paths = [Path(path) for path in image_paths if Path(path).is_file()]
    if not paths:
        return parse_text_response(
            system_prompt,
            prompt,
            settings,
            text_format,
            provider_id=provider_id,
            purpose=purpose,
        )
    provider = _validated_llm_provider(
        settings,
        provider_id or settings.default_answer_llm,
        require_vision=True,
    )
    with model_call_purpose(purpose):
        return _parse_multimodal_with_retry(system_prompt, prompt, paths, settings, text_format, provider)


def _parse_text_with_retry[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    label = "Responses text"
    return _parse_with_retry(
        lambda: _parse_text_once(system_prompt, structured_output_prompt(prompt), settings, text_format, provider),
        label,
        settings,
    )


def _parse_image_with_retry[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_path: Path,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    label = "Responses vision"
    return _parse_with_retry(
        lambda: _parse_image_once(system_prompt, prompt, image_path, settings, text_format, provider),
        label,
        settings,
    )


def _parse_multimodal_with_retry[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    label = "Responses vision"
    return _parse_with_retry(
        lambda: _parse_multimodal_once(system_prompt, prompt, image_paths, settings, text_format, provider),
        label,
        settings,
    )


# 1 回の LLM の呼び出しの既定のやり直し回数（設定で変更できる）。通信系・止まった stream・失敗の event・暴走・
# 不正な出力・U+FFFD・出力の上限での送り直しのすべてをこの枠で数える。
LLM_CALL_MAX_RETRIES = 1
# 待ちの間に取り消しを確かめる間隔の秒数。
LLM_RETRY_WAIT_TICK_SECONDS = 0.5
# 埋め込みの通信系の再試行（LLM の枠とは別）。
EMBEDDING_RETRIES = 5
EMBEDDING_RETRY_INITIAL_WAIT_SECONDS = 1.0
EMBEDDING_RETRY_MAX_WAIT_SECONDS = 16.0


def _wait_before_retry(exc: Exception, retry: int, *, initial_wait_seconds: float = 1.0, max_wait_seconds: float = 16.0) -> None:
    """指数 backoff と full jitter で待つ。Retry-After は下限とし、期限や取消を優先する。"""
    cap = min(max_wait_seconds, initial_wait_seconds * (2 ** max(0, retry - 1)))
    wait = random.random() * cap  # nosec B311 - 再試行の jitter で、暗号の用途ではない
    # stream の失敗では SDK の HTTP 情報が原因例外に残る場合もある。
    source: BaseException | None = exc
    for _ in range(4):
        if source is None:
            break
        headers = getattr(getattr(source, "response", None), "headers", None) or getattr(source, "headers", None)
        advised = parse_retry_after(headers)
        if advised is not None:
            wait += advised
            break
        source = source.__cause__ or source.__context__
    remaining = remaining_seconds()
    if remaining is not None and remaining <= wait:
        raise DeadlineExceeded("回答の期限までに LLM を再試行できません。") from exc
    check_deadline()
    left = wait
    while left > 0:
        tick = min(left, LLM_RETRY_WAIT_TICK_SECONDS)
        time.sleep(tick)
        check_deadline()
        left -= tick


def _parse_with_retry[StructuredOutputT: BaseModel](
    operation,
    label: str,
    settings: Settings,
) -> StructuredOutputT:
    """1 回の LLM の呼び出しを、やり直しの共通の枠の中で実行する（#4736）。

    枠は `_openai_parsed` の送り直しと共有するので、外側のやり直しで数が 0 に戻らない。試行はすべて
    `pr.call.retry_group` の同じ値を持つ（画面が同じ呼び出しの試行を束ねる）。
    """
    from rag_engine.resources.call_context import call_context, update_call_context

    retry_budget = _llm_retry_budget.get() or _LlmRetryBudget(max_retries=settings.llm_max_retries)
    token = _llm_retry_budget.set(retry_budget)
    try:
        with call_context({"pr.call.retry_group": retry_budget.group_id}):
            while True:
                # 回答の期限を過ぎた・取り消された後は、次の呼び出し・やり直しをしない (#1422)。
                check_deadline()
                try:
                    return operation()
                except DeadlineExceeded:
                    raise
                except Exception as exc:
                    if is_invalid_structured_output(exc):
                        # モデルの出力の不合格も共通の待ちと枠で数える。
                        will_retry = retry_budget.take()
                        update_call_context({"pr.retry.reason": "invalid_structured_output"})
                        notify_invalid_output(exc, will_retry)
                        if will_retry:
                            _wait_before_retry(exc, retry_budget.used)
                            continue
                        raise
                    if not _is_retryable_error(exc) or not retry_budget.take():
                        raise
                    _wait_before_retry(exc, retry_budget.used)
                    update_call_context({"pr.retry.reason": "transport_retry"})
    finally:
        _llm_retry_budget.reset(token)


def _retry_transport[ResultT](
    operation: Callable[[], ResultT],
    *,
    retries: int,
    initial_wait_seconds: float,
    max_wait_seconds: float,
) -> ResultT:
    """埋め込み・rerank の通信系の再試行（指数の待ち。LLM の共通の枠は使わない）。"""
    retry_count = max(0, int(retries))
    initial_wait = max(0.0, float(initial_wait_seconds))
    max_wait = max(0.0, float(max_wait_seconds))
    attempt = 0
    while True:
        check_deadline()
        try:
            return operation()
        except DeadlineExceeded:
            raise
        except Exception as exc:
            if attempt >= retry_count or not _is_retryable_error(exc):
                raise
            _wait_before_retry(exc, attempt + 1, initial_wait_seconds=initial_wait, max_wait_seconds=max_wait)
            attempt += 1


def _retry_scheduled(will_retry: bool) -> dict[str, bool]:
    """試行の記録の属性（この試行の後にやり直すときだけ付ける。画面が次の試行を待つ間も失敗と出さない。#4736）。"""
    return {"pr.retry.scheduled": True} if will_retry else {}


class IncompleteResponse(RuntimeError):
    """Responses API が応答を最後まで返さなかった（`status=incomplete`）。

    `reason` は `incomplete_details.reason`（`max_output_tokens` / `content_filter` など）。
    オンライン生成では出力上限で停止する。固定予算・取り込みの既存の再試行は呼び出し側で管理する。

    送り直しても終わらなかった失敗（`_incomplete_error`）は、画面に出す利用者向けの文（``user_message``。
    何が起きたかと直し方）と、「詳細」に畳む技術的な文（``detail``。理由・出力の上限・使った token・推論の量）を
    分けて持つ（messaging.md §10.3）。``str(exc)`` は log 用に両方をつなげた文。
    """

    def __init__(self, message: str, reason: str, *, user_message: str = "", detail: str = ""):
        super().__init__(message)
        self.reason = reason
        self.provider_status = "incomplete"
        self.incomplete_reason = reason
        self.user_message = user_message
        self.detail = detail


class InvalidStructuredOutput(RuntimeError):
    """モデルの応答が構造化出力の契約を満たさなかった（JSON が閉じない・必須項目欠落）。

    OCI Generative AI 経路は応答を自前で JSON 解析・検証するため、この型で送出して `_parse_with_retry` の
    再実行（#663）の対象にする。OpenAI 互換経路は pydantic の ValidationError が素通しで届く (#835)。
    """


def is_invalid_structured_output(exc: Exception) -> bool:
    """モデルの応答が構造化出力の schema を満たさなかった例外か（pydantic の ValidationError か InvalidStructuredOutput）。"""
    if isinstance(exc, InvalidStructuredOutput):
        return True
    try:
        from pydantic import ValidationError
    except ImportError:  # pragma: no cover - pydantic は必須依存
        return False
    return isinstance(exc, ValidationError)


def _response_wire_request(request: dict[str, Any]) -> dict[str, Any]:
    """SDK が送る structured output の schema を JSON にそろえる。"""
    from openai.lib._parsing._responses import type_to_text_format_param

    return {
        **{key: value for key, value in request.items() if key != "text_format"},
        "text": {"format": type_to_text_format_param(request["text_format"])},
    }


def _parse_text_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_text_once(system_prompt, prompt, settings, text_format, provider)


def _parse_image_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_path: Path,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_image_once(system_prompt, prompt, image_path, settings, text_format, provider)


def _parse_multimodal_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_multimodal_once(system_prompt, prompt, image_paths, settings, text_format, provider)


# 出力上限で未完了になったときに予算を広げる倍率と、広げた送信の回数（1 回だけ）。広げた上限はモデルの出力の
# 上限（`LlmProviderSettings.max_output_tokens`。#1719）を超えない。
INCOMPLETE_OUTPUT_BUDGET_FACTOR = 2
INCOMPLETE_OUTPUT_REASON = "max_output_tokens"


def _model_output_limit(provider: LlmProviderSettings) -> int:
    """呼ぶモデルの出力の上限（tokens。#1719。backend が platform のモデル設定から渡す）。"""
    return max(1, int(getattr(provider, "max_output_tokens", DEFAULT_MODEL_OUTPUT_TOKENS) or DEFAULT_MODEL_OUTPUT_TOKENS))


# 出力の上限で未完了になった呼び出しを送り直すときに 1 段下げる推論の量（#2023）。platform の
# `pr_system_settings.model.REASONING_EFFORT_STEP_DOWN` と同じ（rag_engine は platform に依存しない）。
# high → medium → low。low は下げない（下の none は「送らない」＝endpoint の既定で、推論が増えることがある）。
REASONING_EFFORT_STEP_DOWN = {"high": "medium", "medium": "low"}
INCOMPLETE_CONTENT_FILTER_REASON = "content_filter"


def lower_reasoning_effort(effort: str) -> str:
    """推論の量を 1 段下げた、送る値（#2023）。下げられない（low・空）ときは ""。"""
    return REASONING_EFFORT_STEP_DOWN.get((effort or "").strip().casefold(), "")


def _incomplete_error(
    exc: IncompleteResponse,
    response: Any,
    provider: LlmProviderSettings,
    max_output_tokens: int,
    *,
    retried: bool,
    lowered_effort: bool = False,
    reasoning_effort: str = "",
    attempts: int | None = None,
) -> IncompleteResponse:
    """未完了の応答の失敗を、利用者向けの文と「詳細」の文に分けて作る（#1719・#2023）。

    利用者向けの上限到達の文は platform の共有部品を使い、診断の値には応答の本文を含めない。
    ``retried`` は出力の上限を広げて送り直したか、``lowered_effort`` は推論の
    量を下げて送り直したか、``reasoning_effort`` は最後に送った推論の量（空は送っていない）。
    """
    usage = response_usage(response)
    detail = (
        f"status={_value(response, 'status') or '-'}・理由={exc.reason or '不明'}・出力の上限={max_output_tokens} tokens"
        f"・使った出力={usage['output_tokens']} tokens（うち推論 {usage['reasoning_tokens']}）"
        f"・推論の量={reasoning_effort or '送らない'}"
    )
    if exc.reason == INCOMPLETE_OUTPUT_REASON:
        user_message = output_limit_message(
            model=provider.model,
            attempts=attempts if attempts is not None else 1 + int(retried) + int(lowered_effort),
            capacity=_model_output_limit(provider),
        )
    elif exc.reason == INCOMPLETE_CONTENT_FILTER_REASON:
        user_message = (
            f"モデル「{provider.model}」の安全の判定で、回答が途中で止められました。質問の表現を変えるか、質問を分けてやり直してください。"
        )
    else:
        user_message = (
            f"モデル「{provider.model}」の回答が途中で止まりました。"
            "時間をおいてやり直し、続くときはシステム設定 › モデル の設定を確かめてください。"
        )
    return IncompleteResponse(f"{user_message}（{detail}）", exc.reason, user_message=user_message, detail=detail)


def _log_incomplete(
    exc: IncompleteResponse,
    response: Any,
    provider: LlmProviderSettings,
    *,
    max_output_tokens: int,
    reasoning_effort: str,
    next_action: str,
) -> None:
    """未完了の応答の理由・出力の上限・推論の量・使った token と、次にすること（#2023）を WARNING で残す。

    `llm_call_usage`（INFO）は配備の log の level で出ないことがあるため、未完了は別の event で残す。
    ``next_action`` は `widen`（上限を広げて送り直す）/ `lower_effort`（推論の量を下げて送り直す）/ `fail`。
    """
    usage = response_usage(response)
    _logger.warning(
        "LLM の応答が未完了: model=%s reason=%s max_output_tokens=%d reasoning_effort=%s output_tokens=%d "
        "reasoning_tokens=%d next_action=%s",
        provider.model,
        exc.reason,
        max_output_tokens,
        reasoning_effort or "-",
        usage["output_tokens"],
        usage["reasoning_tokens"],
        next_action,
        extra={
            "event": "llm_incomplete_response",
            "llm_model": str(provider.model),
            "llm_incomplete_reason": exc.reason,
            "llm_max_output_tokens": max_output_tokens,
            "llm_reasoning_effort": reasoning_effort or "none",
            "llm_next_action": next_action,
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
        },
    )


# 回答の期限の残りに合わせた出力の上限（#1548）。同じ接続・モデルの直近の完了した呼び出しの出力の速さ
# （出力の token ÷ 往復の秒）の最大に余裕の倍率を掛け、残りの秒で出せる token の数を上限にする。速さは
# 往復の秒（入力の処理・待ちを含む）から測るので、実際の生成の速さより遅めに出る。最大と余裕の倍率で、
# 期限までに終わる呼び出しを上限で切らないようにする。出力の短い呼び出し（入力の処理が主）は速さに数えない。
OUTPUT_RATE_MARGIN = 1.25
OUTPUT_RATE_WINDOW = 8
OUTPUT_RATE_MIN_TOKENS = 256
_output_rates: dict[tuple[str, str], deque[float]] = {}
_output_rates_lock = threading.Lock()


def _rate_key(provider: LlmProviderSettings) -> tuple[str, str]:
    return (str(getattr(provider, "base_url", "") or ""), str(provider.model))


def _note_output_rate(provider: LlmProviderSettings, output_tokens: int, seconds: float) -> None:
    """完了した呼び出しの出力の速さ（token/秒）を、接続・モデルごとに直近の分だけ残す（#1548）。"""
    if output_tokens < OUTPUT_RATE_MIN_TOKENS or seconds <= 0:
        return
    with _output_rates_lock:
        _output_rates.setdefault(_rate_key(provider), deque(maxlen=OUTPUT_RATE_WINDOW)).append(output_tokens / seconds)


# 呼び出しの役目に合わせた出力の上限（`bind_output_cap`）。目録の番号を選ぶなどの短い構造化出力は数百 token で
# 終わるが、まれにモデルが同じ出力を繰り返して回答の上限（32768 token）まで書き続け、1 回の呼び出しが数分かかる。
_output_cap: ContextVar[int | None] = ContextVar("rag_engine_llm_output_cap", default=None)


@contextmanager
def bind_output_cap(max_output_tokens: int | None) -> Iterator[None]:
    """この文脈の構造化出力の呼び出しの出力の上限を `max_output_tokens` に抑え、上限を広げて送り直さない。

    None・0 以下は抑えない（`llm_output_token_limit` のまま）。上限で未完了になっても上限を広げず（推論の量を
    下げる送り直しは従来どおり）、終わらなければ `IncompleteResponse` を送出する。worker thread は文脈を
    引き継ぐ（`contextvars.copy_context`）ので、thread の中の呼び出しにも効く。
    """
    token = _output_cap.set(max_output_tokens if max_output_tokens and max_output_tokens > 0 else None)
    try:
        yield
    finally:
        _output_cap.reset(token)


def deadline_output_budget(budget: int, provider: LlmProviderSettings) -> tuple[int, bool]:
    """回答の期限がある文脈で、残りの秒で出せる出力の token の数に上限を抑える（#1548）。

    戻り値は (送る上限, 期限で抑えたか)。期限が無い・速さの実測が無い・抑える必要が無いときは ``budget`` の
    まま。残りの時間で出し終えられない出力は、期限で timeout になるまで待っても使えない。上限で抑えると、
    同じ時刻までに応答が「出力の上限で未完了」として返り、その呼び出しの出力・推論の token が記録に残る。
    """
    remaining = remaining_seconds()
    if remaining is None:
        return budget, False
    if remaining <= 0:
        raise DeadlineExceeded("回答の期限を過ぎたため、モデルを呼び出しません。")
    with _output_rates_lock:
        rates = list(_output_rates.get(_rate_key(provider), ()))
    if not rates:
        return budget, False
    cap = max(1, int(max(rates) * OUTPUT_RATE_MARGIN * remaining))
    return (cap, True) if cap < budget else (budget, False)


def _openai_parsed[StructuredOutputT: BaseModel](
    input_messages: list[dict[str, Any]],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    """Responses API を呼び、構造化 output model を返します。

    オンライン生成は設定した出力予算を単一の要求に使い、登録容量と入力の残りを超えない。
    max_output_tokensによる未完了では送り直さない。固定した小予算のbind_output_capと
    画像の取り込み（PictureDescriptionOutput）は従来の予算と再試行を保つ。各試行のusageと理由を記録し、
    終わらないときは利用者向けの文と診断の詳細を分けたIncompleteResponseにする。

    この送り直しは 1 回の呼び出しの中で完結する。`pr_backend_core.bounded_calls` は出力の打ち切り
    （``ErrorKind.TRUNCATED``）を入力を小さくできる呼び出し（``adaptive``）だけで再試行するので、同じ要求を
    二重に送り直さない。

    役目に合わせた上限（`bind_output_cap`）がある文脈では、最初の上限をそれで抑え、上限を広げて送り直さない。

    回答の期限がある文脈では、出力の上限を残りの秒で出せる token の数に抑える（`deadline_output_budget`。
    #1548）。抑えた上限で未完了になったら、残りの時間では終わらないので送り直さず、今の期限を切って
    `DeadlineExceeded` にする（回答の時間の配分は、期限で切れた呼び出しと同じく確かめ終わった分で確定する）。
    応答が返らなかった呼び出し（timeout・接続の失敗）も、秒と理由を log と工程の記録に残す。

    回答の期限がある文脈で停滞の検出（`settings.llm_stall_detection`。#1926）が ON なら、応答を streaming で受け、
    応答の event が設定の秒だけ来なかった呼び出し（`stall_idle`。`_stream_parsed`）を打ち切って同じ要求を送り直す
    （#4736。止まった判定は無音だけで、遅いだけの出力は打ち切らない）。打ち切った呼び出しは理由を未完了の理由として残す。
    stream が失敗の event で終わった呼び出し（`LlmStreamFailed`。理由は `stream_error`）と、完了の event の応答に本文が無い
    呼び出し（`LlmStreamOutputMissing`。#4197）も、同じ要求を送り直す（#2814）。
    streaming の呼び出しで暴走の検出（`settings.llm_runaway_detection`）が ON なら、出力が同じ並びの繰り返し・空白だけで
    続いた呼び出しを打ち切る（`LlmRunawayOutput`）。オンラインでは再生成せず未完了を返し、固定課題だけ従来の枠で復旧する。
    固定課題の streaming しない呼び出しも、暴走の検出が ON なら、出力の上限で未完了の応答の出力の文に同じ暴走があるか
    （`_runaway_response_reason`）を確かめ、上限を広げずに同じ送り直しにする（#3835。上限を広げても空白・繰り返しが
    続くだけなので）。打ち切りの理由は工程の記録の `incomplete_reasons` と失敗の詳細（`cutoff_reason`）に残す。
    """
    budget = llm_output_token_limit(settings, provider)
    # 役目に合わせた上限（`bind_output_cap`）があれば、それで抑えて広げない。
    cap = _output_cap.get()
    online_policy = cap is None and text_format is not PictureDescriptionOutput
    if cap is not None:
        budget = min(budget, cap)
    elif online_policy:
        budget = online_output_budget(_model_output_limit(provider), configured=settings.online_max_output_tokens)
    # 推論(思考)の量はモデルの登録の値(#1425)。空は送らない(endpoint の既定)。出力の上限で未完了の送り直しで
    # 1 段下げることがある(#2023)。
    effort = str(provider.reasoning_effort or "")
    widened = False
    lowered = False
    from rag_engine.resources.call_context import observation_attributes, update_call_context

    streamed = _stall_detection_enabled(settings)
    attempt = 0
    # 外側の `_parse_with_retry` と同じ枠で数える（直接呼ばれたときだけ、この呼び出しの枠を持つ）。
    retry_budget = _llm_retry_budget.get() or _LlmRetryBudget(max_retries=settings.llm_max_retries)
    retry_messages = input_messages
    retry_input_reserve = 0
    detect_runaway = bool(getattr(settings, "llm_runaway_detection", False))
    runaway = streamed and detect_runaway
    # 次の送信の前に待つ、一時的な失敗。待ちは試行の記録（span）の時間に入れない。
    transient_failure: Exception | None = None
    while True:
        attempt = retry_budget.used
        if transient_failure is not None:
            _wait_before_retry(transient_failure, retry_budget.used)
            transient_failure = None
        with observe_call("chat", provider.model) as observation:
            check_deadline()
            reasoning: dict[str, Any] = {"reasoning": {"effort": effort}} if effort else {}
            send_budget, time_capped = deadline_output_budget(budget, provider)
            # 追加の入力は UTF-8 の byte 数を上限として出力予約から引く。予約に入らなければ元の入力で再送する。
            send_messages = input_messages
            if retry_messages is not input_messages and retry_input_reserve < send_budget:
                send_messages = retry_messages
                send_budget -= retry_input_reserve
            idle_seconds = float(settings.llm_stall_idle_seconds) if streamed else None
            request: dict[str, Any] = {
                "model": provider.model,
                "input": send_messages,
                "text_format": text_format,
                "max_output_tokens": send_budget,
                "temperature": LLM_TEMPERATURE,
                **reasoning,
            }
            wire_request = _response_wire_request(request)
            if online_policy:
                from rag_engine.generation.section_expansion import estimate_tokens

                input_tokens = estimate_tokens(json.dumps(wire_request, ensure_ascii=False))
                room = settings.answer_max_input_tokens - input_tokens
                if room <= 0:
                    from rag_engine.generation.input_budget import GenerationInputBudgetExceeded

                    raise GenerationInputBudgetExceeded(
                        {
                            "reason": "final_model_input_exceeds_limit",
                            "model_input_limit_tokens": settings.answer_max_input_tokens,
                            "estimated_input_tokens": input_tokens,
                            "available_input_tokens": 0,
                            "output_reserved_tokens": 0,
                            "fits": False,
                        }
                    )
                send_budget = online_output_budget(send_budget, configured=settings.online_max_output_tokens, room=room)
                wire_request["max_output_tokens"] = send_budget
            timing: _StreamTiming | None = None
            started = time.monotonic()
            try:
                # SDK に本文を検証させない（応答の状態を確かめてからコードで検証する。#4145・#4197）。schema だけを送る。
                if getattr(observation, "capture_enabled", True):
                    observation.request({**wire_request, **({"stream": True} if streamed else {})}, schema=wire_request["text"]["format"])
                else:
                    observation.request(None)
                capture = (
                    CallStreamCapture(observation.response, enabled=getattr(observation, "capture_enabled", True)) if streamed else None
                )
                if streamed:
                    response, timing = _stream_parsed(
                        _openai_client_for_provider(provider, settings),
                        wire_request,
                        settings,
                        idle_seconds,
                        text_format=text_format,
                        runaway=runaway,
                        capture=capture,
                    )
                else:
                    client = _openai_client_with_deadline(_openai_client_for_provider(provider, settings), settings)
                    # text_format を渡すと SDK が応答の組み立ての中で本文を検証し、上限で切れた応答（未完了）も検証の失敗になって
                    # 下の送り直しに届かない（#4145）。
                    response = client.responses.parse(**wire_request)
                if capture is None or not capture.has_terminal:
                    observation.response(response)
                observation_attributes(observation, {"pr.call.transport_status": "returned"})
                usage = _value(response, "usage")
                observation.usage(usage.model_dump() if hasattr(usage, "model_dump") else usage)
                if detect_runaway and not online_policy and not streamed and (cutoff := _runaway_response_reason(response)):
                    # streaming しない呼び出しは途中で打ち切れない。出力の上限まで出た暴走を streaming と同じ送り直しにする（#3835）。
                    raise LlmRunawayOutput(_runaway_message(cutoff), cutoff, None, response=response)
            except Exception as exc:
                observation_attributes(
                    observation,
                    {
                        "pr.call.transport_status": "returned"
                        if isinstance(exc, LlmRunawayOutput) and exc.response is not None
                        else "failed",
                        **({"pr.call.validation_status": "rejected"} if isinstance(exc, LlmRunawayOutput) else {}),
                        **_retry_scheduled(
                            retry_budget.available
                            and ((isinstance(exc, LlmRunawayOutput) and not online_policy) or _is_retryable_error(exc))
                        ),
                    },
                )
                if isinstance(exc, LlmRunawayOutput) and exc.response is not None:
                    # 応答（token の数）は返っているので、使った token と打ち切りの理由を残す。
                    _record_usage(
                        exc.response,
                        provider,
                        text_format,
                        time.monotonic() - started,
                        max_output_tokens=send_budget,
                        attempt=attempt,
                        deadline_capped=time_capped,
                        cutoff_reason=exc.reason,
                    )
                else:
                    _record_failed_call(
                        exc,
                        provider,
                        text_format,
                        time.monotonic() - started,
                        max_output_tokens=send_budget,
                        attempt=attempt,
                        deadline_capped=time_capped,
                        timing=getattr(exc, "timing", None),
                    )
                if isinstance(exc, LlmRunawayOutput):
                    if online_policy:
                        user_message = (
                            _runaway_message(exc.reason)
                            + "途中の回答は確定していません。質問を分けるか対象を絞って、もう一度お試しください。"
                        )
                        raise IncompleteResponse(user_message, exc.reason, user_message=user_message, detail=f"理由={exc.reason}") from exc
                    if not retry_budget.take():
                        raise
                    # 出力が暴走した（#2966）。推論の量を下げられれば 1 段下げ、送り直しの回数を工程の記録に残して送り直す
                    # （出力の上限で未完了の送り直しと同じく、推論の量の下げは 1 回だけ）。
                    retry_messages = _runaway_retry_messages(input_messages, text_format, exc.reason)
                    extra = retry_messages[len(input_messages) :]
                    retry_input_reserve = len(json.dumps(extra, ensure_ascii=False).encode("utf-8")) if extra else 0
                    lower = "" if lowered else lower_reasoning_effort(effort)
                    if lower:
                        effort, lowered = lower, True
                    record_llm_runaway_resend()
                    update_call_context({"pr.retry.reason": "runaway_output"})
                    transient_failure = exc
                    attempt += 1
                    continue
                if not isinstance(exc, LlmStreamFailed | LlmStreamStalled) or not _is_retryable_error(exc) or not retry_budget.take():
                    raise
                # 失敗の event で終わった（#2814）・無音で止まった（#1926。回数を工程の記録の llm_usage に残す）。
                # provider の一時的な失敗なので、待ってから同じ要求を送り直す。期限は次の送信の前に確かめる。
                if isinstance(exc, LlmStreamStalled):
                    record_llm_stall_resend()
                update_call_context({"pr.retry.reason": "stream_failed" if isinstance(exc, LlmStreamFailed) else "stream_stalled"})
                transient_failure = exc
                continue
            seconds = time.monotonic() - started
            record_usage = partial(
                _record_usage,
                response,
                provider,
                text_format,
                seconds,
                max_output_tokens=send_budget,
                attempt=attempt,
                deadline_capped=time_capped,
                timing=timing,
            )
            try:
                parsed = _parsed_response(response, text_format, provider)
            except IncompleteResponse as exc:
                observation_attributes(observation, {"pr.call.validation_status": "incomplete"})
                record_usage()
                if time_capped and exc.reason == INCOMPLETE_OUTPUT_REASON:
                    # 残りの時間で出せる分を出し切っても終わらなかった。送り直しても期限に間に合わない。
                    expire_deadline()
                    raise DeadlineExceeded(
                        f"回答の期限までに LLM の出力が終わりません（出力の上限 {send_budget} tokens に届きました）。"
                    ) from exc
                # オンラインの出力上限では停止し、固定予算・取り込みだけ既存の契約を保つ。
                next_action = "fail"
                wider = None
                lower = ""
                if exc.reason == INCOMPLETE_OUTPUT_REASON:
                    if not online_policy and cap is None and not widened:
                        legacy = min(send_budget * INCOMPLETE_OUTPUT_BUDGET_FACTOR, _model_output_limit(provider))
                        wider = legacy if legacy > send_budget else None
                    lower = lower_reasoning_effort(effort) if not online_policy and not lowered and wider is None else ""
                    next_action = "widen" if wider is not None else ("lower_effort" if lower else "fail")
                    if next_action != "fail" and not retry_budget.take():
                        next_action = "fail"
                observation_attributes(observation, _retry_scheduled(next_action != "fail"))
                _log_incomplete(exc, response, provider, max_output_tokens=send_budget, reasoning_effort=effort, next_action=next_action)
                if next_action == "fail":
                    raise _incomplete_error(
                        exc,
                        response,
                        provider,
                        send_budget,
                        retried=widened,
                        lowered_effort=lowered,
                        reasoning_effort=effort,
                        attempts=retry_budget.used + 1,
                    ) from exc
                if wider is not None:
                    budget, widened = wider, True
                    # 送り直しの回数を工程の記録（execution_steps の llm_usage）に残す（#1527。同じ問の 1 組の所要の
                    # ぶれが送り直しによるかを確かめる）。
                    record_llm_output_retry()
                else:
                    effort, lowered = lower, True
                    record_llm_effort_retry()
                update_call_context({"pr.retry.reason": "incomplete_output"})
                transient_failure = exc
                attempt += 1
                continue
            except Exception as exc:
                observation_attributes(
                    observation,
                    {
                        "pr.call.validation_status": "rejected",
                        **_retry_scheduled(retry_budget.available and is_invalid_structured_output(exc)),
                    },
                )
                record_usage(rejected=exc)
                raise
            record_usage()
            _note_output_rate(provider, response_usage(response)["output_tokens"], seconds)
            # 文字の品質は schema の違反と区別する。回答の要点の復旧は生成側で行う。
            if "\ufffd" not in json.dumps(input_messages, ensure_ascii=False) and "\ufffd" in parsed.model_dump_json():
                paths = replacement_char_paths(parsed.model_dump(mode="json"))
                observation_attributes(observation, {"pr.call.output_quality": "replacement_character"})
                logging.getLogger(__name__).warning(
                    "LLM の生成文に文字品質の警告があります: model=%s schema=%s paths=%s",
                    provider.model,
                    text_format.__name__,
                    ",".join(paths),
                    extra={
                        "event": "llm_output_quality_warning",
                        "llm_quality_paths": paths,
                        "llm_quality_reason": "replacement_character",
                    },
                )
            observation_attributes(observation, {"pr.call.validation_status": "accepted"})
            return parsed


# 退けた出力の欄の path を残す数の上限（#3849）。
REJECTED_PATHS_LIMIT = 8


def replacement_char_paths(value: Any, path: str = "", *, limit: int = REJECTED_PATHS_LIMIT) -> list[str]:
    """``U+FFFD`` を含む文字列の欄の JSON の path（例: ``items[2].text``）。本文は返さない。``limit`` 件まで。"""
    found: list[str] = []

    def walk(node: Any, where: str) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, str):
            if "\ufffd" in node:
                found.append(where or "$")
        elif isinstance(node, Mapping):
            for key, child in node.items():
                walk(child, f"{where}.{key}" if where else str(key))
        elif isinstance(node, list | tuple):
            for position, child in enumerate(node):
                walk(child, f"{where}[{position}]")

    walk(value, path)
    return found


def _log_rejected_output(provider: LlmProviderSettings, text_format: type, response: Any, *, reason: str, paths: Sequence[str]) -> None:
    """完了した応答を出力の検査で退けたことを WARNING で残す（#3849）。理由・欄の path・token の数だけで、本文は残さない。"""
    usage = response_usage(response)
    _logger.warning(
        "LLM の出力を退けました: model=%s schema=%s reason=%s paths=%s output_tokens=%d",
        provider.model,
        getattr(text_format, "__name__", str(text_format)),
        reason,
        ",".join(paths) or "-",
        usage["output_tokens"],
        extra={
            "event": "llm_output_rejected",
            "llm_model": str(provider.model),
            "llm_schema": getattr(text_format, "__name__", str(text_format)),
            "llm_rejected_reason": reason,
            "llm_rejected_paths": list(paths),
            "output_tokens": usage["output_tokens"],
        },
    )


@dataclass
class _StreamTiming:
    """streaming で受けた 1 回の呼び出しの時刻（秒は送信からの経過）。"""

    first_event_seconds: float | None = None
    first_delta_seconds: float | None = None
    last_delta_seconds: float | None = None
    deltas: int = 0
    max_gap_seconds: float = 0.0
    # 開始を知らせていない出力の項目を指す event の数（`_StreamEventTrail`。#4197）。
    unordered_events: int = 0

    @property
    def delta_rate(self) -> float | None:
        """出力の速さ（最初の delta から最後の delta までの delta の event/秒）。"""
        if self.first_delta_seconds is None or self.last_delta_seconds is None or self.deltas < 2:
            return None
        span = self.last_delta_seconds - self.first_delta_seconds
        return (self.deltas - 1) / span if span > 0 else None


class LlmStreamStalled(TimeoutError):
    """streaming の呼び出しの応答の event が来なくなった（#1926）。``reason`` は `stall_idle`。"""

    def __init__(self, message: str, reason: str, timing: _StreamTiming) -> None:
        super().__init__(message)
        self.reason = reason
        self.timing = timing


# 出力の暴走（#2966）。出力は速く続くが、同じ短い並びの繰り返し・空白が続くだけで、出力の上限まで書き続ける
# 生成（degenerate な出力）がある（記録: 32,768 token を 200 秒書き続けて未完了、推論を下げた送り直しは 2 秒で完了）。
# 停滞の検出（event の間隔・速さ）では見分けられないので、streaming の出力の文の末尾で見分けて早く打ち切る。
# 正当な長い出力（表・一覧）は行ごとに値が変わるので、末尾の RUNAWAY_WINDOW_CHARS 文字が周期
# RUNAWAY_MAX_PERIOD_CHARS 文字以下の完全な繰り返しになることはほぼ無い（周期の繰り返しは 8 回以上）。
RUNAWAY_WINDOW_CHARS = 2400
RUNAWAY_MAX_PERIOD_CHARS = 300
RUNAWAY_WHITESPACE_CHARS = 2000
# 末尾を確かめる間隔（出力の文字数）。
RUNAWAY_CHECK_EVERY_CHARS = 256


def _runaway_retry_messages(messages: list[dict[str, Any]], schema: Any, reason: str) -> list[dict[str, Any]]:
    """草稿の再送には反復の指摘を渡す。失敗した出力や呼び出し元の入力は書き換えない。"""
    if not isinstance(schema, type) or not issubclass(schema, GroundedDraft):
        return messages
    failure = "空白だけの出力" if reason == "runaway_whitespace" else "同じ内容の反復"
    feedback = (
        "## Step 1: 回答を作り直す\n"
        f"前の出力は{failure}が続いたため完了しませんでした。元の質問と根拠に戻り、必要な項目だけで回答してください。\n\n"
        "## Step 2: 出力を完了する\n"
        "同じ項目を同じ配列に繰り返さず、指定された構造化出力を最後まで返してください。"
    )
    return [*messages, {"role": "user", "content": [{"type": "input_text", "text": feedback}]}]


class LlmRunawayOutput(RuntimeError):
    """出力が暴走した（#2966）。``reason`` は `runaway_repetition`（繰り返し）か `runaway_whitespace`（空白）。

    streaming で打ち切った呼び出しは ``timing`` を、streaming しない呼び出しで出力の上限まで出た応答から見分けた
    ものは ``response``（未完了の応答。#3835）を持つ。
    """

    def __init__(self, message: str, reason: str, timing: _StreamTiming | None, *, response: Any = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.timing = timing
        self.response = response


def _runaway_message(reason: str) -> str:
    return f"LLM の出力が{'同じ並びの繰り返し' if reason == 'runaway_repetition' else '空白'}だけで続いたため、打ち切りました。"


_WHITESPACE_RUN = re.compile(rf"\s{{{RUNAWAY_WHITESPACE_CHARS},}}")


def _runaway_response_reason(response: Any) -> str | None:
    """出力の上限で未完了の応答の出力の文に、streaming で打ち切る暴走（`_RunawayMonitor` と同じ基準）があれば理由を返す。

    空白が ``RUNAWAY_WHITESPACE_CHARS`` 文字続いた箇所（途中でも）か、末尾の繰り返し（`repeating_tail`）。完了した
    応答・ほかの理由の未完了は見ない（#3835）。
    """
    if _value(response, "status") != "incomplete":
        return None
    if str(_value(_value(response, "incomplete_details"), "reason") or "") != INCOMPLETE_OUTPUT_REASON:
        return None
    text = _response_output_text(response)
    if _WHITESPACE_RUN.search(text):
        return "runaway_whitespace"
    return "runaway_repetition" if repeating_tail(text) else None


class _DraftArrayRepetition:
    """草稿で重複集約する配列の、完了した同一objectの連続だけを検出する。"""

    def __init__(self) -> None:
        self.buffer = ""
        self.cursor = 0
        self.in_array = False
        self.previous = ""
        self.count = self.repeated_chars = 0
        self.decoder = json.JSONDecoder()

    def feed(self, text: str) -> bool:
        self.buffer += text
        while self.cursor < len(self.buffer):
            if not self.in_array:
                match = re.search(r'(?<!\\)"(?:request_coverage|guide_coverage|items)"\s*:\s*\[', self.buffer[self.cursor :])
                if match is None:
                    return False
                self.cursor += match.end()
                self.in_array = True
                self.previous = ""
                self.count = self.repeated_chars = 0
            while self.cursor < len(self.buffer) and self.buffer[self.cursor] in " \t\r\n,":
                self.cursor += 1
            if self.cursor == len(self.buffer):
                return False
            if self.buffer[self.cursor] == "]":
                self.cursor += 1
                self.in_array = False
                continue
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.cursor)
            except json.JSONDecodeError:
                return False
            self.cursor = end
            canonical = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else ""
            self.count = self.count + 1 if canonical and canonical == self.previous else 1
            self.repeated_chars = self.repeated_chars + len(canonical) if canonical == self.previous else len(canonical)
            self.previous = canonical
            if self.count >= RUNAWAY_WINDOW_CHARS // RUNAWAY_MAX_PERIOD_CHARS and self.repeated_chars >= RUNAWAY_WINDOW_CHARS:
                return True
        return False


class _RunawayMonitor:
    """streaming の出力の文の末尾から、出力の暴走（同じ並びの繰り返し・空白の連続）を見分ける（#2966）。"""

    def __init__(self, *, draft_arrays: bool = False) -> None:
        self.draft_arrays = _DraftArrayRepetition() if draft_arrays else None
        self.tail = ""
        self.whitespace_run = 0
        self.unchecked = 0

    def feed(self, text: str) -> str | None:
        """出力の続き ``text`` を足し、暴走なら理由（`runaway_repetition` / `runaway_whitespace`）を返す。"""
        if not text:
            return None
        if self.draft_arrays is not None and self.draft_arrays.feed(text):
            return "runaway_repetition"
        stripped = text.rstrip()
        self.whitespace_run = self.whitespace_run + len(text) if not stripped else len(text) - len(stripped)
        if self.whitespace_run >= RUNAWAY_WHITESPACE_CHARS:
            return "runaway_whitespace"
        self.tail = (self.tail + text)[-(RUNAWAY_WINDOW_CHARS + RUNAWAY_MAX_PERIOD_CHARS) :]
        self.unchecked += len(text)
        if self.unchecked < RUNAWAY_CHECK_EVERY_CHARS or len(self.tail) < RUNAWAY_WINDOW_CHARS + RUNAWAY_MAX_PERIOD_CHARS:
            return None
        self.unchecked = 0
        return "runaway_repetition" if repeating_tail(self.tail) else None


def repeating_tail(text: str) -> bool:
    """末尾の ``RUNAWAY_WINDOW_CHARS`` 文字が、周期 ``RUNAWAY_MAX_PERIOD_CHARS`` 文字以下の完全な繰り返しか。"""
    if len(text) < RUNAWAY_WINDOW_CHARS + RUNAWAY_MAX_PERIOD_CHARS:
        return False
    window = text[-RUNAWAY_WINDOW_CHARS:]
    return any(window == text[-RUNAWAY_WINDOW_CHARS - period : -period] for period in range(1, RUNAWAY_MAX_PERIOD_CHARS + 1))


class LlmStreamFailed(ConnectionError):
    """streaming の応答が失敗の event で終わった・完了の event なしに切れた（通信の失敗として再試行の対象）。"""

    def __init__(self, message: str, timing: _StreamTiming) -> None:
        super().__init__(message)
        self.timing = timing


class LlmStreamOutputMissing(LlmStreamFailed):
    """完了の event の応答に本文も拒否も無かった（送り直す。送り直しても同じなら出力の不合格と同じに退避する。#4197）。

    ``stream_trace`` は本文を除いた event の並び（`_StreamEventTrail.trace`）。
    """

    def __init__(self, message: str, timing: _StreamTiming, stream_trace: dict[str, Any]) -> None:
        super().__init__(message, timing)
        self.stream_trace = stream_trace


# 本文を除いた event の記録の上限（delta は数えるだけで並びに残さない）。
STREAM_EVENT_TRAIL_LIMIT = 40


class _StreamEventTrail:
    """本文を除いた event の type と番号の並びと、開始を知らせていない出力の項目を指す event を記録する（#4197）。

    応答の本文は完了の event だけから読むので、event の順は結果に効かない。provider の順を記録から確かめるために残す。
    """

    def __init__(self, timing: _StreamTiming) -> None:
        self.timing = timing
        self.rows: list[dict[str, Any]] = []
        self.announced: set[int] = set()
        self.first_unordered: dict[str, Any] | None = None

    def feed(self, kind: str, event: Any) -> None:
        output_index = _value(event, "output_index")
        row = {"type": kind[:80], "output_index": output_index, "content_index": _value(event, "content_index")}
        if kind == "response.output_item.added":
            self.announced.add(output_index)
        elif output_index is not None and output_index not in self.announced:
            self.timing.unordered_events += 1
            self.first_unordered = self.first_unordered or row
        if not kind.endswith(".delta") and len(self.rows) < STREAM_EVENT_TRAIL_LIMIT:
            self.rows.append(row)

    def trace(self, final: Any) -> dict[str, Any]:
        return {
            "events": self.rows,
            "delta_events": self.timing.deltas,
            "unordered_events": self.timing.unordered_events,
            "final_output_types": [str(_value(item, "type") or "")[:80] for item in _value(final, "output", []) or []],
        }


def _stall_detection_enabled(settings: Settings) -> bool:
    """停滞の検出をするか（回答の期限がある文脈だけ。取込などの期限の無い呼び出しは従来どおり）。"""
    return bool(getattr(settings, "llm_stall_detection", False)) and current_deadline() is not None


class _StreamTextRecovery:
    """同じ応答の完全本文だけを保持し、最終スナップショットの置換文字破損を復旧する。"""

    def __init__(self) -> None:
        self.response_id: str | None = None
        self.invalid = False
        self.closed = False
        self.texts: dict[tuple[str, int, int], str | None] = {}

    def feed(self, kind: str, event: Any) -> None:
        if self.closed:
            return
        response = _value(event, "response")
        if response is not None:
            response_id = _value(response, "id")
            if not isinstance(response_id, str) or not response_id:
                self.invalid = True
            elif self.response_id is None and kind == "response.created":
                self.response_id = response_id
            elif response_id != self.response_id:
                self.invalid = True
        if kind in {"response.completed", "response.incomplete", "response.failed", "error"}:
            self.closed = True
        if kind != "response.output_text.done" or self.response_id is None:
            return
        item_id = _value(event, "item_id")
        output_index = _value(event, "output_index")
        content_index = _value(event, "content_index")
        text = _value(event, "text")
        if (
            not isinstance(item_id, str)
            or not item_id
            or type(output_index) is not int
            or type(content_index) is not int
            or output_index < 0
            or content_index < 0
            or not isinstance(text, str)
        ):
            return
        key = (item_id, output_index, content_index)
        self.texts[key] = text if key not in self.texts or self.texts[key] == text else None

    def recover(self, final: Any) -> Any:
        if (
            self.invalid
            or self.response_id is None
            or _value(final, "id") != self.response_id
            or _value(final, "status") != "completed"
            or _value(final, "error")
            or _value(final, "incomplete_details")
            or _response_refusal(final)
            or "\ufffd" not in _response_output_text(final)
        ):
            return final
        recovered = deepcopy(final.model_dump(mode="python") if isinstance(final, BaseModel) else final)
        changed = 0
        for output_index, item in enumerate(_value(recovered, "output", []) or []):
            if _value(item, "type") != "message" or _value(item, "status") != "completed":
                continue
            for content_index, part in enumerate(_value(item, "content", []) or []):
                if _value(part, "type") != "output_text":
                    continue
                damaged = _value(part, "text")
                clean = self.texts.get((_value(item, "id"), output_index, content_index))
                if not isinstance(damaged, str) or not clean or "\ufffd" not in damaged or "\ufffd" in clean:
                    continue
                differences = SequenceMatcher(None, damaged, clean).get_opcodes()
                if any(tag != "equal" and (tag != "replace" or set(damaged[a:b]) != {"\ufffd"}) for tag, a, b, _, _ in differences):
                    continue
                part["text"] = clean
                changed += 1
        if not changed:
            return final
        # SDK の便利属性が存在しても、本文の正本と食い違わせない。元の event は診断用に保持する。
        if "output_text" in recovered:
            recovered["output_text"] = "".join(
                str(_value(part, "text") or "") for part in _iter_response_content(recovered) if _value(part, "type") == "output_text"
            )
        _logger.warning("LLM stream text recovered from matching done events: parts=%d", changed)
        return recovered


def _stream_parsed(
    client: Any,
    request: dict[str, Any],
    settings: Settings,
    idle_seconds: float | None,
    *,
    text_format: type | None = None,
    runaway: bool = False,
    capture: CallStreamCapture | None = None,
) -> tuple[Any, _StreamTiming]:
    """Responses API を streaming で呼び、完了（または未完了）の event の応答と時刻を返す（#1926）。

    SDK の組み立て（`responses.stream`）を通さず、event をそのまま読む。応答は完了の event が持つ全体を使うので、provider の
    event の順（項目の開始より前の content_part・delta など）では失敗しない（#4197）。同じ item/content の完全本文が届いていれば、
    完了本文の置換文字だけが破損した場合に限り復旧する。``request`` は wire の形（schema は ``text``）。

    1 回の待ちの上限（httpx の read timeout。event の間隔）は ``idle_seconds``（検出しないときの None は全体の上限）。
    全体の上限（LLM 1 回の timeout と回答の期限の残りの短いほう）と取り消しは event ごとに確かめる。無音を検出したら
    stream を閉じて（接続を切って）`LlmStreamStalled` を送出する（#4736。出力の速さでは打ち切らない）。
    ``runaway`` なら出力の文の末尾で暴走（`_RunawayMonitor`）を確かめ、暴走なら stream を閉じて `LlmRunawayOutput` を
    送出する（#2966）。失敗の event と、完了の event なしに切れた stream は `LlmStreamFailed`、完了の event の応答に本文も
    拒否も無ければ `LlmStreamOutputMissing`。本文の検証は呼び出し側が応答の状態を確かめてから行う。
    """
    import httpx

    started = time.monotonic()
    total = float(settings.llm_request_timeout_seconds)
    remaining = remaining_seconds()
    if remaining is not None:
        total = min(total, remaining)
    total = max(0.001, total)
    read_timeout = min(idle_seconds, total) if idle_seconds is not None else total
    idle_bound = idle_seconds is not None and idle_seconds < total
    timing = _StreamTiming()
    trail = _StreamEventTrail(timing)
    text_recovery = _StreamTextRecovery()
    draft_arrays = isinstance(text_format, type) and issubclass(text_format, GroundedDraft)
    monitor = _RunawayMonitor(draft_arrays=draft_arrays) if runaway else None
    final: Any = None
    last = started
    try:
        with client.with_options(timeout=httpx.Timeout(total, read=read_timeout), max_retries=0).responses.create(
            stream=True, **request
        ) as stream:
            for event in stream:
                if capture is not None:
                    capture.feed(event)
                now = time.monotonic()
                elapsed = now - started
                timing.max_gap_seconds = max(timing.max_gap_seconds, now - last)
                last = now
                if timing.first_event_seconds is None:
                    timing.first_event_seconds = elapsed
                kind = str(_value(event, "type") or "")
                trail.feed(kind, event)
                text_recovery.feed(kind, event)
                if kind.endswith(".delta"):
                    timing.deltas += 1
                    timing.last_delta_seconds = elapsed
                    if timing.first_delta_seconds is None:
                        timing.first_delta_seconds = elapsed
                    if monitor is not None and kind == "response.output_text.delta":
                        reason = monitor.feed(str(_value(event, "delta") or ""))
                        if reason is not None:
                            raise LlmRunawayOutput(_runaway_message(reason), reason, timing)
                elif kind in {"response.completed", "response.incomplete"}:
                    final = _value(event, "response")
                elif kind in {"response.failed", "error"}:
                    raise LlmStreamFailed(f"LLM の応答が失敗の event（{kind}）で終わりました: {_stream_error_message(event)}", timing)
                check_deadline()
                if elapsed >= total:
                    raise TimeoutError(f"LLM の応答が {total:.0f} 秒で終わりませんでした。")
    except (LlmStreamStalled, LlmStreamFailed, LlmRunawayOutput, DeadlineExceeded):
        raise
    except Exception as exc:
        if idle_bound and _is_read_timeout(exc):
            raise LlmStreamStalled(f"LLM の応答の event が {read_timeout:.0f} 秒来ませんでした。", "stall_idle", timing) from exc
        if _is_stream_error_event(exc):
            # OpenAI SDK は stream の error の event を event として返さず APIError として送出する（#2814）。
            raise LlmStreamFailed(f"LLM の応答が失敗の event で終わりました: {str(exc)[:300]}", timing) from exc
        with suppress(AttributeError):
            exc.timing = timing  # type: ignore[attr-defined]
        raise
    finally:
        if capture is not None:
            capture.flush(interrupted=final is None)
    if final is None:
        raise LlmStreamFailed("LLM の応答の stream が完了の event なしに終わりました。", timing)
    if timing.unordered_events:
        _logger.warning(
            "LLM stream events out of order: unordered_events=%d first=%s final_output_types=%s",
            timing.unordered_events,
            json.dumps(trail.first_unordered, ensure_ascii=False),
            ",".join(trail.trace(final)["final_output_types"]) or "-",
        )
    if _value(final, "status") == "completed" and not _response_output_text(final) and not _response_refusal(final):
        trace = trail.trace(final)
        _logger.warning("LLM stream completed without output: trace=%s", json.dumps(trace, ensure_ascii=False))
        raise LlmStreamOutputMissing("LLM の応答の完了の event に本文がありません。", timing, trace)
    return text_recovery.recover(final), timing


def _is_stream_error_event(exc: BaseException) -> bool:
    """stream の中の error の event を OpenAI SDK が包んだ ``APIError``（HTTP の status の無いもの）か。"""
    try:
        from openai import APIConnectionError, APIError, APIStatusError
    except ImportError:  # pragma: no cover - openai は必須依存
        return False
    return isinstance(exc, APIError) and not isinstance(exc, (APIStatusError, APIConnectionError))


def _is_read_timeout(exc: BaseException) -> bool:
    """httpx の timeout（stream の途中）か、openai SDK が包んだ timeout（応答の開始まで）か。"""
    import httpx

    try:
        from openai import APITimeoutError
    except ImportError:  # pragma: no cover - openai は必須依存
        return isinstance(exc, httpx.TimeoutException)
    return isinstance(exc, (httpx.TimeoutException, APITimeoutError))


def _stream_error_message(event: Any) -> str:
    error = _value(_value(event, "response"), "error") or _value(event, "error") or _value(event, "message")
    return str(_value(error, "message") or error or "不明")[:300]


_logger = logging.getLogger(__name__)


def _record_usage(
    response: Any,
    provider: LlmProviderSettings,
    text_format: type,
    seconds: float,
    *,
    max_output_tokens: int | None = None,
    attempt: int = 0,
    deadline_capped: bool = False,
    timing: _StreamTiming | None = None,
    cutoff_reason: str = "",
    rejected: Exception | None = None,
) -> None:
    """1 回の呼び出しの token の数と秒を、工程の記録の累積と log に残す（#1516。遅い工程の理由を確かめる）。

    応答が未完了（`status=incomplete`）なら理由（`incomplete_details.reason`）も残す。``attempt`` は出力の上限を
    広げた送り直しの何回目か（0 は最初の送信）、``max_output_tokens`` はその送信の出力の上限（#1527）、
    ``deadline_capped`` はその上限を回答の期限の残りで抑えたか（#1548）。streaming で受けた呼び出しは ``timing``
    （最初の event までの秒・出力の速さ・最大の間隔。#1926）も残す。``cutoff_reason`` は、出力の暴走と見分けた
    未完了の応答の理由（`runaway_whitespace` など。#3835）で、未完了の理由の代わりに残す。``rejected`` は応答の本文を
    検証した例外で、構造化出力の不合格なら失敗として類別（本文を含まない）を残す。使った token の数は残る。
    """
    usage = response_usage(response)
    status = str(_value(response, "status") or "")
    reason = str(_value(_value(response, "incomplete_details"), "reason") or "unknown") if status == "incomplete" else ""
    reason = cutoff_reason or reason
    invalid = rejected if rejected is not None and is_invalid_structured_output(rejected) else None
    _log_call(
        usage,
        seconds,
        provider,
        text_format,
        status="failed" if invalid is not None else status,
        reason=type(invalid).__name__ if invalid is not None else reason,
        failure_details=structured_failure_details(invalid, text_format) if invalid is not None else None,
        attempt=attempt,
        max_output_tokens=max_output_tokens,
        deadline_capped=deadline_capped,
        timing=timing,
    )


def _record_failed_call(
    exc: Exception,
    provider: LlmProviderSettings,
    text_format: type,
    seconds: float,
    *,
    max_output_tokens: int | None = None,
    attempt: int = 0,
    deadline_capped: bool = False,
    timing: _StreamTiming | None = None,
) -> None:
    """応答が返らなかった呼び出し（timeout・接続の失敗）の秒と理由を残す（#1548）。

    打ち切った呼び出しは usage が返らないので token の数は 0 で、理由（例外の型。timeout は ``APITimeoutError``）を
    未完了の理由として数える。期限で打ち切った呼び出しが何秒かかっていたかを、工程の記録と log で確かめる。
    無音で打ち切った呼び出し（#1926）の理由は `stall_idle`（event が来ない）、暴走で打ち切った
    呼び出し（#2966）は `runaway_repetition` / `runaway_whitespace`。
    """
    usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    _log_call(
        usage,
        seconds,
        provider,
        text_format,
        status="failed",
        reason=exc.reason
        if isinstance(exc, LlmStreamStalled | LlmRunawayOutput)
        else ("stream_error" if isinstance(exc, LlmStreamFailed) else type(exc).__name__),
        attempt=attempt,
        max_output_tokens=max_output_tokens,
        deadline_capped=deadline_capped,
        timing=timing,
        failure_details=structured_failure_details(exc, text_format if issubclass(text_format, BaseModel) else None),
    )


def _timing_fields(timing: _StreamTiming | None) -> dict[str, float]:
    """streaming の時刻の記録の項目（#1926）。最初の event までの秒（待ち）・出力の速さ（停滞）・最大の間隔。"""
    if timing is None:
        return {}
    fields: dict[str, float] = {"max_gap_seconds": round(timing.max_gap_seconds, 2), "delta_events": timing.deltas}
    if timing.unordered_events:
        fields["unordered_events"] = timing.unordered_events
    if timing.first_event_seconds is not None:
        fields["first_event_seconds"] = round(timing.first_event_seconds, 2)
    if timing.first_delta_seconds is not None:
        fields["first_delta_seconds"] = round(timing.first_delta_seconds, 2)
    if (rate := timing.delta_rate) is not None:
        fields["delta_rate"] = round(rate, 1)
    return fields


def _log_call(
    usage: dict[str, int],
    seconds: float,
    provider: LlmProviderSettings,
    text_format: type,
    *,
    status: str,
    reason: str,
    attempt: int,
    max_output_tokens: int | None,
    deadline_capped: bool = False,
    timing: _StreamTiming | None = None,
    failure_details: dict[str, Any] | None = None,
) -> None:
    stream = _timing_fields(timing)
    record_llm_usage(
        {
            **usage,
            "seconds": seconds,
            **({"incomplete_reason": reason} if reason else {}),
            **({"streamed": True, **stream} if timing is not None else {}),
        }
    )
    # backend の構造化 log は、外部の logger の本文を出さず extra の項目だけを残すので、値は extra で渡す。
    _logger.info(
        "LLM の呼び出し: model=%s schema=%s seconds=%.1f input_tokens=%d cached_tokens=%d output_tokens=%d "
        "reasoning_tokens=%d status=%s incomplete_reason=%s attempt=%d max_output_tokens=%s deadline_capped=%s "
        "first_event_seconds=%s delta_rate=%s max_gap_seconds=%s",
        provider.model,
        getattr(text_format, "__name__", str(text_format)),
        seconds,
        usage["input_tokens"],
        usage["cached_tokens"],
        usage["output_tokens"],
        usage["reasoning_tokens"],
        status,
        reason or "-",
        attempt,
        max_output_tokens if max_output_tokens is not None else "-",
        deadline_capped,
        stream.get("first_event_seconds", "-"),
        stream.get("delta_rate", "-"),
        stream.get("max_gap_seconds", "-"),
        extra={
            "event": "llm_call_usage",
            "llm_model": str(provider.model),
            "llm_schema": getattr(text_format, "__name__", str(text_format)),
            "llm_seconds": round(seconds, 1),
            "llm_status": status,
            **({"llm_failure": failure_details} if failure_details else {}),
            "llm_incomplete_reason": reason,
            "llm_attempt": attempt,
            **({"llm_max_output_tokens": max_output_tokens} if max_output_tokens is not None else {}),
            **({"llm_deadline_capped": True} if deadline_capped else {}),
            **{f"llm_{key}": value for key, value in stream.items()},
            **usage,
        },
    )


def _parse_openai_text_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _openai_parsed(
        [
            {"role": "system", "content": str(system_prompt or "")},
            {"role": "user", "content": str(prompt or "")},
        ],
        settings,
        text_format,
        provider,
    )


def _parse_openai_image_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_path: Path,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _openai_parsed(
        [
            {"role": "system", "content": str(system_prompt or "")},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": str(prompt or "")},
                    {"type": "input_image", "image_url": _image_data_url(image_path)},
                ],
            },
        ],
        settings,
        text_format,
        provider,
    )


def _parse_openai_multimodal_once[StructuredOutputT: BaseModel](
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    content: list[dict[str, str]] = [{"type": "input_text", "text": str(prompt or "")}]
    content.extend({"type": "input_image", "image_url": _image_data_url(path)} for path in image_paths)
    return _openai_parsed(
        [
            {"role": "system", "content": str(system_prompt or "")},
            {"role": "user", "content": content},
        ],
        settings,
        text_format,
        provider,
    )


def _openai_client_for_provider(provider: LlmProviderSettings, settings: Settings) -> Any:
    return _openai_client(
        provider.provider_id,
        provider.base_url,
        provider.api_key,
        provider.project_id,
        settings.llm_request_timeout_seconds,
    )


def _openai_client_with_deadline(client: Any, settings: Settings) -> Any:
    """回答の期限がある文脈では、1 回の timeout を残り時間に縮め、SDK の再試行を切った client を返す。

    打ち切った回答の thread が LLM 1 回の timeout（600 秒）と再試行のまま LLM を呼び続けないように
    する (#1422)。再試行は `_parse_with_retry` が残り時間と取り消しを確かめながら行う（時刻の期限が無く
    取り消しだけを伝える期限でも、SDK の再試行は切る）。期限が無い文脈では client をそのまま返す。
    """
    deadline = current_deadline()
    if deadline is None:
        return client
    timeout = float(settings.llm_request_timeout_seconds)
    remaining = deadline.remaining()
    if remaining is not None:
        timeout = min(timeout, remaining)
    return client.with_options(timeout=max(0.001, timeout), max_retries=0)


def _validated_llm_provider(
    settings: Settings,
    provider_id: str | None,
    *,
    require_vision: bool,
) -> LlmProviderSettings:
    provider = get_llm_provider(settings, provider_id)
    if require_vision and not provider.supports_vision:
        raise RuntimeError(f"{provider.label} は Vision 入力に対応していません。")
    prefix = provider.provider_id.upper()
    missing = []
    # base URL は endpoint だけで決める(#1440)。
    # project は OCI の OpenAI 互換 API だけが要求するため必須にしない。
    if not provider.base_url:
        missing.append(f"{prefix}_RAG_ENDPOINT")
    if not provider.api_key:
        missing.append(f"{prefix}_RAG_API_KEY")
    if not provider.model:
        missing.append(f"{prefix}_RAG_MODEL")
    if missing:
        raise RuntimeError(f"{provider.label} の LLM 設定が不足しています: {', '.join(missing)}")
    return provider


def _parsed_response[StructuredOutputT: BaseModel](
    response: Any,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    status = _value(response, "status")
    if status == "incomplete":
        reason = str(_value(_value(response, "incomplete_details"), "reason") or "unknown")
        raise IncompleteResponse(f"{provider.label} の Responses API 応答が未完了です: {reason}", reason)

    refusal = _response_refusal(response)
    if refusal:
        raise RuntimeError(f"{provider.label} の Responses API が拒否を返しました: {refusal}")

    text = _response_output_text(response)
    if not text:
        raise RuntimeError(f"{provider.label} の Responses API 応答に本文がありません。")
    # 完了した応答の本文を検証する。不正な JSON は pydantic の ValidationError（再実行の対象）のまま。
    output = text_format.model_validate_json(text)
    if "_provider_output_text" in output.__private_attributes__:
        cast(Any, output)._provider_output_text = text
    return output


def _response_refusal(response: Any) -> str:
    for content in _iter_response_content(response):
        if _value(content, "type") == "refusal":
            refusal = _value(content, "refusal") or _value(content, "text") or ""
            if str(refusal).strip():
                return str(refusal).strip()
        refusal = _value(content, "refusal")
        if refusal:
            return str(refusal).strip()
    return ""


def _response_output_text(response: Any) -> str:
    """応答の出力の文（``output_text``。無ければ出力の content の output_text をつなぐ）。"""
    text = _value(response, "output_text")
    if isinstance(text, str) and text:
        return text
    return "".join(
        str(_value(content, "text") or "") for content in _iter_response_content(response) if _value(content, "type") == "output_text"
    )


def _iter_response_content(response: Any):
    for output in _value(response, "output", []) or []:
        yield from _value(output, "content", []) or []


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _is_retryable_error(exc: Exception) -> bool:
    classified = classify_error(exc)
    if classified.kind in {
        ErrorKind.QUOTA_EXHAUSTED,
        ErrorKind.AUTHENTICATION,
        ErrorKind.PERMISSION,
        ErrorKind.NOT_FOUND,
        ErrorKind.TLS,
        ErrorKind.BAD_REQUEST,
        ErrorKind.TOO_LARGE,
        ErrorKind.SAFETY,
        ErrorKind.NOT_SUPPORTED,
    }:
        return False
    # HTTP status の無い SDK の error event も、構造化した code があれば同じ規則で判定する。
    if classified.status is not None and classified.status >= 400:
        return classified.status == 429 or classified.status >= 500
    if classified.kind in {ErrorKind.RATE_LIMITED, ErrorKind.SERVER_ERROR, ErrorKind.UNAVAILABLE, ErrorKind.NETWORK, ErrorKind.TIMEOUT}:
        return True
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    try:
        from requests.exceptions import ConnectionError as RequestsConnectionError
        from requests.exceptions import Timeout as RequestsTimeout
    except ImportError:
        request_errors: tuple[type[BaseException], ...] = ()
    else:
        request_errors = (RequestsConnectionError, RequestsTimeout)
    try:
        import httpx
    except ImportError:
        httpx_errors: tuple[type[BaseException], ...] = ()
    else:
        httpx_errors = (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)
    # SDK の再試行を切った呼び出し（回答の期限がある文脈。#1422）では、OpenAI SDK が包んだ接続・
    # timeout の失敗（`APIConnectionError` / `APITimeoutError`）もここで再試行する。
    try:
        from openai import APIConnectionError
    except ImportError:
        openai_errors: tuple[type[BaseException], ...] = ()
    else:
        openai_errors = (APIConnectionError,)
    # OCI SDK は同梱の `oci._vendor.requests` を使うため、接続タイムアウトの `oci.exceptions.ConnectTimeout` と
    # 読み取りタイムアウトを含むその他の通信失敗 `oci.exceptions.RequestException` は、top-level の requests 例外とも
    # builtin の ConnectionError とも別クラスになる（両者に共通の親は vendored の RequestException だけで、
    # ConnectTimeout は oci.exceptions.RequestException の派生ではない）。全 OCI 呼び出しは NoneRetryStrategy で
    # SDK 側の再試行を切っているので、ここで拾わないと無再試行になる (#743)。
    try:
        from oci.exceptions import ConnectTimeout as OciConnectTimeout
        from oci.exceptions import RequestException as OciRequestException
    except ImportError:
        oci_errors: tuple[type[BaseException], ...] = ()
    else:
        oci_errors = (OciConnectTimeout, OciRequestException)
    return isinstance(exc, (TimeoutError, ConnectionError, *request_errors, *httpx_errors, *openai_errors, *oci_errors))


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


def describe_picture(
    image_path: Path,
    metadata: dict[str, Any],
    settings: Settings,
    *,
    context_image_paths: Sequence[str | Path] = (),
    target_kind: str = "picture",
    rendered_prompt: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """対象 crop と文脈から Vision 説明を生成します。

    target_kind は picture/table。アプリ側の処理種別として使い、metadata 由来の命令は
    実行しません。不正値は ValueError。rendered_promptはcheckpointと送信内容を一致させる
    内部用snapshotであり、指定時はその文字列を送信します。選択された provider へのネットワーク通信を伴います。
    """
    if target_kind not in {"picture", "table"}:
        raise ValueError(f"Unknown Vision target kind: {target_kind}")
    system_prompt = system_prompt if system_prompt is not None else resolve_prompt("picture_description.system", VISION_SYSTEM_PROMPT)
    prompt = rendered_prompt if rendered_prompt is not None else _render_prompt(metadata, target_kind=target_kind)
    image_paths = [Path(image_path)]
    image_paths.extend(Path(path) for path in context_image_paths if Path(path).is_file())
    if len(image_paths) > 1:
        parsed = parse_multimodal_response(
            system_prompt,
            prompt,
            image_paths,
            settings,
            PictureDescriptionOutput,
            provider_id=settings.default_vision_llm,
            purpose="document.read",
        )
    else:
        parsed = call_with_model_purpose(
            "document.read",
            parse_image_response,
            system_prompt,
            prompt,
            image_path,
            settings,
            PictureDescriptionOutput,
            provider_id=settings.default_vision_llm,
        )
    return parsed.model_dump(mode="json")


def chat_text(
    system_prompt: str,
    prompt: str,
    settings: Settings,
    *,
    provider_id: str | None = None,
) -> str:
    """設定済み provider へ text chat prompt を送り回答 text を返します。"""
    return parse_text_response(system_prompt, prompt, settings, TextOutput, provider_id=provider_id).text


def rerank_text(query: str, documents: list[str], settings: Settings, top_n: int | None = None) -> list[int]:
    """rerank model で上位 document の index だけを返します。"""
    return [rank.index for rank in rerank_text_with_scores(query, documents, settings, top_n=top_n)]


def rerank_text_with_scores(
    query: str,
    documents: list[str],
    settings: Settings,
    top_n: int | None = None,
) -> list[RerankTextRank]:
    """rerank model で document index と relevance score を返します。"""
    return _rerank_text_with_retry(query, documents, settings, top_n=top_n)


def embed_texts(
    texts: list[str],
    settings: Settings,
    *,
    input_type: str = EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT,
) -> list[list[float]]:
    """検索 text を件数・UTF-8 byte 数で分割して embedding を生成します。

    入力順を保持し、本文は切り捨てません。単体で byte 目安を超える入力は単独送信し、
    実 token 超過は OCI のエラーとして呼び出し元へ伝播します。
    """
    normalized_texts = [str(text or "") for text in texts]
    if not normalized_texts:
        return []

    batch_size = min(96, max(1, int(settings.embedding_batch_size or 1)))
    embeddings: list[list[float]] = []
    batch: list[str] = []
    batch_bytes = 0
    for text in normalized_texts:
        text_bytes = len(text.encode("utf-8"))
        if batch and (len(batch) >= batch_size or batch_bytes + text_bytes > EMBEDDING_BATCH_MAX_UTF8_BYTES):
            embeddings.extend(_embed_text_batch_with_retry(batch, settings, input_type=input_type))
            batch = []
            batch_bytes = 0
        batch.append(text)
        batch_bytes += text_bytes
    if batch:
        embeddings.extend(_embed_text_batch_with_retry(batch, settings, input_type=input_type))
    return embeddings


def embed_query(text: str, settings: Settings) -> list[float]:
    """検索 query 用 embedding を 1 件生成します。"""
    embeddings = embed_texts(
        [str(text or "")],
        settings,
        input_type=EMBEDDING_INPUT_TYPE_SEARCH_QUERY,
    )
    if not embeddings:
        raise RuntimeError("OCI embedding query response was empty.")
    return embeddings[0]


def embed_images(
    image_paths: Sequence[str | Path],
    settings: Settings,
    *,
    texts: Sequence[str] | None = None,
    input_type: str = EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT,
) -> list[list[float]]:
    """画像と任意 text caption から multimodal embedding を生成します。"""
    paths = []
    for path in image_paths:
        raw_path = str(path or "").strip()
        if not raw_path:
            raise RuntimeError("Image embedding path is empty.")
        paths.append(Path(raw_path))
    if not paths:
        return []
    normalized_texts = [str(text or "") for text in texts] if texts is not None else [""] * len(paths)
    if len(normalized_texts) != len(paths):
        raise RuntimeError(f"Image embedding input mismatch: expected {len(paths)} text payloads, got {len(normalized_texts)}")
    return [
        _embed_image_with_retry(path, text, settings, input_type=input_type) for path, text in zip(paths, normalized_texts, strict=False)
    ]


def _embed_text_batch_with_retry(
    texts: list[str],
    settings: Settings,
    *,
    input_type: str,
) -> list[list[float]]:
    return _retry_transport(
        lambda: _embed_text_batch_once(texts, settings, input_type=input_type),
        retries=EMBEDDING_RETRIES,
        initial_wait_seconds=EMBEDDING_RETRY_INITIAL_WAIT_SECONDS,
        max_wait_seconds=EMBEDDING_RETRY_MAX_WAIT_SECONDS,
    )


def _embed_text_batch_once(
    texts: list[str],
    settings: Settings,
    *,
    input_type: str,
) -> list[list[float]]:
    if not settings.embedding_model:
        raise RuntimeError("EMBEDDING_MODEL が未設定です。")
    if not settings.oci_compartment_id:
        raise RuntimeError("OCI_COMPARTMENT_ID が未設定です。")

    runtime = _runtime(
        settings.oci_config_file,
        settings.oci_profile,
        settings.oci_compartment_id,
        _embedding_endpoint(settings),
        settings.llm_request_timeout_seconds,
    )
    models = runtime.models
    input_type_value = _embedding_input_type(models, input_type)
    dimensions = _embedding_output_dimensions(settings)
    request = models.EmbedTextDetails(
        inputs=texts,
        serving_mode=_serving_mode(models, settings.embedding_model),
        compartment_id=runtime.compartment_id,
        is_echo=False,
        embedding_types=[models.EmbedTextDetails.EMBEDDING_TYPES_FLOAT],
        output_dimensions=dimensions,
        truncate=models.EmbedTextDetails.TRUNCATE_NONE,
        input_type=input_type_value,
    )
    with (
        model_call_purpose(
            model_purpose_attributes(
                default={
                    models.EmbedTextDetails.INPUT_TYPE_SEARCH_QUERY: "query.embed",
                    models.EmbedTextDetails.INPUT_TYPE_SEARCH_DOCUMENT: "document.embed",
                }.get(input_type_value)
            ).get("pr.gen_ai.purpose")
        ),
        observe_call("embeddings", settings.embedding_model) as observation,
    ):
        observation.request(request)
        response = runtime.client.embed_text(
            embed_text_details=request,
            retry_strategy=runtime.oci.retry.NoneRetryStrategy(),
        )
        observation.response(response.data)
    embeddings = _embedding_vectors(response.data, expected_count=len(texts), expected_dimensions=dimensions)
    return embeddings


def _embed_image_with_retry(
    image_path: Path,
    text: str,
    settings: Settings,
    *,
    input_type: str,
) -> list[float]:
    return _retry_transport(
        lambda: _embed_image_once(image_path, text, settings, input_type=input_type),
        retries=EMBEDDING_RETRIES,
        initial_wait_seconds=EMBEDDING_RETRY_INITIAL_WAIT_SECONDS,
        max_wait_seconds=EMBEDDING_RETRY_MAX_WAIT_SECONDS,
    )


def _embed_image_once(
    image_path: Path,
    text: str,
    settings: Settings,
    *,
    input_type: str,
) -> list[float]:
    if not settings.embedding_model:
        raise RuntimeError("EMBEDDING_MODEL が未設定です。")
    if not settings.oci_compartment_id:
        raise RuntimeError("OCI_COMPARTMENT_ID が未設定です。")
    if not image_path.is_file():
        raise RuntimeError(f"Image embedding asset was not found: {image_path}")

    runtime = _runtime(
        settings.oci_config_file,
        settings.oci_profile,
        settings.oci_compartment_id,
        _embedding_endpoint(settings),
        settings.llm_request_timeout_seconds,
    )
    models = runtime.models
    if not hasattr(models, "EmbedImageContent") or not hasattr(models, "EmbedTextContent"):
        raise RuntimeError("OCI SDK does not support EmbedTextDetails.embed_contents image embeddings.")
    input_type_value = _embedding_input_type(models, input_type)
    dimensions = _embedding_output_dimensions(settings)
    contents = []
    normalized_text = str(text or "").strip()
    if normalized_text:
        contents.append(models.EmbedTextContent(text=normalized_text))
    contents.append(models.EmbedImageContent(image_url=models.ImageUrl(url=_image_data_url(image_path), detail="AUTO")))
    request = models.EmbedTextDetails(
        embed_contents=contents,
        serving_mode=_serving_mode(models, settings.embedding_model),
        compartment_id=runtime.compartment_id,
        is_echo=False,
        embedding_types=[models.EmbedTextDetails.EMBEDDING_TYPES_FLOAT],
        output_dimensions=dimensions,
        truncate=models.EmbedTextDetails.TRUNCATE_NONE,
        input_type=input_type_value,
    )
    with (
        model_call_purpose(
            model_purpose_attributes(
                default={
                    models.EmbedTextDetails.INPUT_TYPE_SEARCH_QUERY: "query.embed",
                    models.EmbedTextDetails.INPUT_TYPE_SEARCH_DOCUMENT: "document.embed",
                }.get(input_type_value)
            ).get("pr.gen_ai.purpose")
        ),
        observe_call("embeddings", settings.embedding_model) as observation,
    ):
        observation.request(request)
        response = runtime.client.embed_text(
            embed_text_details=request,
            retry_strategy=runtime.oci.retry.NoneRetryStrategy(),
        )
        observation.response(response.data)
    embeddings = _embedding_vectors(response.data, expected_count=1, expected_dimensions=dimensions)
    return embeddings[0]


def _embedding_endpoint(settings: Settings) -> str:
    if settings.embedding_endpoint:
        return settings.embedding_endpoint
    region = settings.embedding_region.strip()
    if not region:
        raise RuntimeError("EMBEDDING_REGION が未設定です。")
    return oci_inference_endpoint(region)


def _embedding_input_type(models: Any, input_type: str) -> str:
    normalized = str(input_type or "").strip().upper()
    aliases = {
        "DOCUMENT": EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT,
        "SEARCH_DOCUMENT": EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT,
        "QUERY": EMBEDDING_INPUT_TYPE_SEARCH_QUERY,
        "SEARCH_QUERY": EMBEDDING_INPUT_TYPE_SEARCH_QUERY,
        "IMAGE": EMBEDDING_INPUT_TYPE_IMAGE,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized == EMBEDDING_INPUT_TYPE_SEARCH_DOCUMENT:
        return models.EmbedTextDetails.INPUT_TYPE_SEARCH_DOCUMENT
    if normalized == EMBEDDING_INPUT_TYPE_SEARCH_QUERY:
        return models.EmbedTextDetails.INPUT_TYPE_SEARCH_QUERY
    if normalized == EMBEDDING_INPUT_TYPE_IMAGE:
        return models.EmbedTextDetails.INPUT_TYPE_IMAGE
    raise RuntimeError(f"Unsupported embedding input type: {input_type}")


def _embedding_output_dimensions(settings: Settings) -> int:
    dimensions = int(settings.embedding_output_dimensions or DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS)
    if dimensions not in SUPPORTED_EMBEDDING_OUTPUT_DIMENSIONS:
        raise RuntimeError("EMBEDDING_OUTPUT_DIMENSIONS は 256 / 512 / 1024 / 1536 のいずれかで指定してください。")
    return dimensions


def _embedding_vectors(data: Any, *, expected_count: int, expected_dimensions: int) -> list[list[float]]:
    raw_embeddings = _value(data, "embeddings")
    if not raw_embeddings:
        by_type = _value(data, "embeddings_by_type")
        if isinstance(by_type, dict):
            raw_embeddings = by_type.get("float") or by_type.get("FLOAT")
        else:
            raw_embeddings = _value(by_type, "float") or _value(by_type, "FLOAT")
    if not isinstance(raw_embeddings, list):
        raise RuntimeError("OCI embedding response に embeddings がありません。")
    if len(raw_embeddings) != expected_count:
        raise RuntimeError(f"OCI embedding response count mismatch: expected {expected_count}, got {len(raw_embeddings)}")

    embeddings: list[list[float]] = []
    for index, raw_vector in enumerate(raw_embeddings):
        if not isinstance(raw_vector, list):
            raise RuntimeError(f"OCI embedding response vector #{index + 1} が配列ではありません。")
        vector = [float(value) for value in raw_vector]
        if len(vector) != expected_dimensions:
            raise RuntimeError(
                f"OCI embedding dimension mismatch: expected {expected_dimensions}, got {len(vector)} for vector #{index + 1}"
            )
        embeddings.append(vector)
    return embeddings


def _rerank_text_once(
    query: str,
    documents: list[str],
    settings: Settings,
    top_n: int | None = None,
) -> list[RerankTextRank]:
    model = settings.rerank_model
    if not model:
        raise RuntimeError("RERANK_MODEL が未設定です。")
    runtime = _runtime(
        settings.oci_config_file,
        settings.oci_profile,
        settings.oci_compartment_id,
        settings.oci_rerank_endpoint,
        settings.rerank_request_timeout_seconds,
    )
    models = runtime.models
    # v4 の長文を default の 1 chunk で取りこぼさないよう、内部 chunk 数を確保する。
    # UTF-8 byte 数は token 実測値ではない。余裕を取った分割目安としてのみ使用する。
    chunk_options = {}
    if model.startswith("cohere.rerank-v4.0"):
        document_budget = RERANK_V4_DOCUMENT_BUDGET_UTF8_BYTES - len(str(query).encode("utf-8")) - 4
        if document_budget <= 0:
            raise ValueError("Rerank query exceeds the conservative input budget.")
        max_bytes = max((len(str(document).encode("utf-8")) for document in documents), default=0)
        chunks = max(1, (max_bytes + document_budget - 1) // document_budget)
        if chunks * len(documents) > RERANK_MAX_DOCUMENT_CHUNKS_PER_REQUEST:
            raise ValueError("Rerank input exceeds the document/chunk request budget.")
        chunk_options["max_chunks_per_document"] = chunks
    request = models.RerankTextDetails(
        input=str(query or ""),
        compartment_id=runtime.compartment_id,
        serving_mode=_serving_mode(models, model),
        documents=[str(document or "") for document in documents],
        top_n=top_n if top_n is not None else len(documents),
        **chunk_options,
    )
    receipt = current_rerank_receipt()
    with observe_call("rerank", settings.rerank_model) as observation:
        observation.request(request)
        strategy = runtime.oci.retry.NoneRetryStrategy()
        attempt = receipt.begin_sdk(current_rerank_attempt()) if receipt is not None else None
        try:
            response = runtime.client.rerank_text(request, retry_strategy=strategy)
            observation.response(response.data)
        except BaseException as exc:
            if receipt is not None and attempt is not None:
                receipt.finish_sdk(attempt, "cancelled" if type(exc).__name__ == "CancelledError" else "error", exc)
            raise
        else:
            if receipt is not None and attempt is not None:
                receipt.finish_sdk(attempt, "completed")
        finally:
            if receipt is not None:
                snapshot = {
                    **receipt.to_dict(),
                    "status_scope": "sdk_attempt",
                    "status": attempt["status"] if attempt is not None else "not_run",
                }
                observation.display(
                    details={"code": [{"label": "rerank", "text": json.dumps(snapshot, ensure_ascii=False), "language": "json"}]}
                )
    ranks = getattr(response.data, "document_ranks", None) or []
    ranks_with_scores: list[RerankTextRank] = []
    for rank in ranks:
        try:
            index = int(rank.index)
        except (TypeError, ValueError):
            continue
        relevance_score = _optional_float(getattr(rank, "relevance_score", None))
        ranks_with_scores.append(RerankTextRank(index=index, relevance_score=relevance_score))
    return ranks_with_scores


def _rerank_text_with_retry(
    query: str,
    documents: list[str],
    settings: Settings,
    top_n: int | None = None,
) -> list[RerankTextRank]:
    receipt = current_rerank_receipt() or RerankCallReceipt()
    receipt.request(query, documents, model=settings.rerank_model, top_n=top_n if top_n is not None else len(documents))
    receipt.cache_state = "disabled"
    attempt_number = 0

    def operation() -> list[RerankTextRank]:
        nonlocal attempt_number
        attempt_number += 1
        with bind_rerank_attempt(attempt_number):
            return _rerank_text_once(query, documents, settings, top_n=top_n)

    with use_rerank_receipt(receipt), observe_call("stage", "rerank.request") as observation:
        if getattr(observation, "capture_enabled", True):
            observation.request(
                {
                    "query": query,
                    "documents": documents,
                    "model": settings.rerank_model,
                    "top_n": top_n if top_n is not None else len(documents),
                    "retries": settings.rerank_retries,
                }
            )
        try:
            result = _retry_transport(
                operation,
                retries=settings.rerank_retries,
                initial_wait_seconds=settings.rerank_retry_initial_wait_seconds,
                max_wait_seconds=settings.rerank_retry_max_wait_seconds,
            )
            receipt.status = "completed"
            if getattr(observation, "capture_enabled", True):
                observation.response({"rankings": [vars(rank) for rank in result], "receipt": receipt.to_dict()})
            return result
        except BaseException as exc:
            receipt.status = "cancelled" if type(exc).__name__ == "CancelledError" else "error"
            receipt.error_type = type(exc).__name__
            if getattr(observation, "capture_enabled", True):
                observation.response({"rankings": None, "receipt": receipt.to_dict()})
            raise
        finally:
            observation.display(
                details={"code": [{"label": "rerank", "text": json.dumps(receipt.to_dict(), ensure_ascii=False), "language": "json"}]}
            )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
