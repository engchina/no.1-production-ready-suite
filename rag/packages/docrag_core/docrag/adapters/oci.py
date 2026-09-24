"""OCI/OpenAI 互換 LLM、rerank、embedding の呼び出しを抽象化する。"""

from __future__ import annotations
from docrag.models.llm import (
    RerankTextRank,
    PictureDescriptionOutput,
    QueryRoutingOutput,
    QueryExpansionOutput,
    CragRetrievalGradeOutput,
    UsedImageOutput,
    TextOutput,
)


import base64
import json
import mimetypes
import re
import time
from dataclasses import dataclass
from docrag.resources.cache import application_cache
from pathlib import Path
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from docrag.knowledge.prompt_files import IMAGE_RETRIEVAL_PROMPT_KEY, read_prompt, render_prompt_template
from docrag.parsing.vision_prompt_rules import TABLE_EXTRACTION_INSTRUCTION, refine_image_retrieval_prompt
from docrag.config import (
    DEFAULT_EMBEDDING_OUTPUT_DIMENSIONS,
    OPENAI_RESPONSES_LLM_API_MODE,
    LlmProviderSettings,
    Settings,
    get_llm_provider,
    oci_inference_endpoint,
)


VISION_SYSTEM_PROMPT = "あなたは問い合わせRAG用の画像説明作成担当です。JSONだけを返してください。"
API_SIGNING_KEY_FIELDS = ("user", "tenancy", "fingerprint", "key_file", "region")
StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


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
        raise RuntimeError(
            "OCI API signing key profile に必要な設定がありません: " + ", ".join(missing)
        )
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
    )
    return _Runtime(
        oci=oci,
        models=oci.generative_ai_inference.models,
        client=client,
        compartment_id=compartment_id,
    )


# 生成・監査・質問ルーティング・Vision 説明の全呼出で同じ入力から同じ出力を得るための設定。
# OCI ネイティブ経路と OpenAI Responses 経路で共有する。Responses API に seed の引数はないため extra_body で渡す。
LLM_TEMPERATURE = 0.0
LLM_SEED = 42


@application_cache(maxsize=16)
def _openai_client(
    provider_id: str,
    base_url: str,
    api_key: str,
    project_id: str,
    timeout_seconds: int,
    max_retries: int,
) -> Any:
    """OpenAI 互換 Responses 経路の client を生成します（provider ごとに再利用）。

    `max_retries` は SDK 内蔵の再試行回数で、429 / 5xx / 接続エラー / タイムアウトを
    `Retry-After` を尊重した指数バックオフで送り直します。通信系の再試行はこの client に
    一本化するため、呼び出し側では自前の再試行を重ねません。

    `project_id` は OCI の OpenAI 互換 API 専用の設定です。空のときは `OpenAI-Project`
    ヘッダを付けない（`project=None`）ことで、project の概念を持たない OpenAI 互換 API でも
    そのまま使えるようにします。
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("OpenAI Python SDK が未インストールです。`openai` をインストールしてください。") from exc
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        project=project_id or None,
        timeout=max(1, int(timeout_seconds)),
        max_retries=max(0, int(max_retries)),
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
    prompt = render_prompt_template(
        template,
        {
            "image_metadata": _json_dumps(metadata),
            "image": _image_prompt_note(metadata),
        },
    )
    # 保存済みテンプレートを書き換えず、古い JSON 例にも現在の API 出力契約を補います。
    instruction = TABLE_EXTRACTION_INSTRUCTION if target_kind == "table" else "実行対象: 画像1の独立した画像領域。"
    return prompt + "\n\n" + instruction + "\n\n" + _picture_output_contract()


def _picture_output_contract() -> str:
    """実際の検証モデルから契約を生成し、プロンプト内の固定 JSON 例との乖離を防ぎます。"""
    schema = _compact_schema(PictureDescriptionOutput.model_json_schema())
    return (
        "実行時の構造化出力契約（出力形式は以下を優先。上記の読取・業務説明方針は維持）:\n"
        "required の全キーを返し、該当しない文字列は空文字、配列は空配列にしてください。\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """検証制約を保持し、prompt 用コピーから title 等の重複した注釈だけを除きます。"""
    compact = {}
    for key, value in schema.items():
        if key in {"title", "description", "default"}:
            continue
        if key in {"properties", "$defs"}:
            # description という実際の field 名は注釈ではないため、辞書のキーを保持します。
            compact[key] = {name: _compact_schema(item) for name, item in value.items()}
        elif isinstance(value, dict):
            compact[key] = _compact_schema(value)
        elif isinstance(value, list):
            compact[key] = [_compact_schema(item) if isinstance(item, dict) else item for item in value]
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


def parse_text_response(
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    *,
    provider_id: str | None = None,
) -> StructuredOutputT:
    """text-only LLM 呼び出しを構造化 output model として解析します。"""
    provider = _validated_llm_provider(
        settings,
        provider_id or settings.default_answer_llm,
        require_vision=False,
    )
    return _parse_text_with_retry(system_prompt, prompt, settings, text_format, provider)


def parse_image_response(
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


def parse_multimodal_response(
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[str | Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    *,
    provider_id: str | None = None,
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
        )
    provider = _validated_llm_provider(
        settings,
        provider_id or settings.default_answer_llm,
        require_vision=True,
    )
    return _parse_multimodal_with_retry(system_prompt, prompt, paths, settings, text_format, provider)


def _parse_text_with_retry(
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    label = "Responses text"
    return _parse_with_retry(
        lambda: _parse_text_once(system_prompt, prompt, settings, text_format, provider),
        label,
        settings,
        retries=_llm_transport_retries(settings),
    )


def _parse_image_with_retry(
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
        retries=_llm_transport_retries(settings),
    )


def _parse_multimodal_with_retry(
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
        retries=_llm_transport_retries(settings),
    )


def _llm_transport_retries(settings: Settings) -> int | None:
    """LLM 呼び出しで `_parse_with_retry` に掛けさせる通信系の再試行回数。

    OpenAI 互換 Responses 経路は OpenAI SDK 内蔵の再試行（`_openai_client` の `max_retries`）に
    任せるため 0 を返し、自前のバックオフを二重に掛けません。OCI Generative AI 経路は全呼び出しを
    `NoneRetryStrategy` で送っていて SDK 側が再試行しないため、既定（`LLM_RETRIES`）のまま
    自前で再試行します (#743)。
    """
    return 0


def _parse_with_retry(
    operation,
    label: str,
    settings: Settings,
    *,
    retries: int | None = None,
    initial_wait_seconds: float | None = None,
    max_wait_seconds: float | None = None,
) -> StructuredOutputT:
    retry_count = max(0, settings.llm_retries if retries is None else int(retries))
    initial_wait = max(
        0.0,
        settings.llm_retry_initial_wait_seconds
        if initial_wait_seconds is None
        else float(initial_wait_seconds),
    )
    max_wait = max(
        0.0,
        settings.llm_retry_max_wait_seconds
        if max_wait_seconds is None
        else float(max_wait_seconds),
    )
    # 構造化出力の検証エラー（JSON を最後まで返さない・必須項目欠落）は通信系とは別に、同じ入力で 1 回だけ
    # やり直す (#663)。モデルが空白行を延々と出力して JSON が閉じないことがあり、再実行では再現しない。
    invalid_output_retries = 0
    attempt = 0
    while True:
        try:
            return operation()
        except Exception as exc:
            if _is_invalid_structured_output(exc) and invalid_output_retries < INVALID_OUTPUT_RETRIES:
                invalid_output_retries += 1  # 通信系の再試行回数は消費しない
                continue
            if attempt >= retry_count or not _is_retryable_error(exc):
                raise
            time.sleep(min(initial_wait * (2**attempt), max_wait))
            attempt += 1


# 構造化出力の検証エラーで同じ入力をやり直す回数（通信系の再試行回数とは別）。
INVALID_OUTPUT_RETRIES = 1


class IncompleteResponse(RuntimeError):
    """Responses API が応答を最後まで返さなかった（`status=incomplete`）。

    `reason` は `incomplete_details.reason`（`max_output_tokens` / `content_filter` など）。
    出力上限が理由のときだけ、呼び出し側が予算を広げて 1 回だけ送り直す (#987)。
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


class InvalidStructuredOutput(RuntimeError):
    """モデルの応答が構造化出力の契約を満たさなかった（JSON が閉じない・必須項目欠落）。

    OCI Generative AI 経路は応答を自前で JSON 解析・検証するため、この型で送出して `_parse_with_retry` の
    再実行（#663）の対象にする。OpenAI 互換経路は pydantic の ValidationError が素通しで届く (#835)。
    """


def _is_invalid_structured_output(exc: Exception) -> bool:
    """モデルの応答が構造化出力の schema を満たさなかった例外か（pydantic の ValidationError か InvalidStructuredOutput）。"""
    if isinstance(exc, InvalidStructuredOutput):
        return True
    try:
        from pydantic import ValidationError
    except ImportError:  # pragma: no cover - pydantic は必須依存
        return False
    return isinstance(exc, ValidationError)


def _parse_text_once(
    system_prompt: str,
    prompt: str,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_text_once(system_prompt, prompt, settings, text_format, provider)


def _parse_image_once(
    system_prompt: str,
    prompt: str,
    image_path: Path,
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_image_once(system_prompt, prompt, image_path, settings, text_format, provider)


def _parse_multimodal_once(
    system_prompt: str,
    prompt: str,
    image_paths: Sequence[Path],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    return _parse_openai_multimodal_once(system_prompt, prompt, image_paths, settings, text_format, provider)


# 出力上限で未完了になったときに予算を広げる倍率と、広げた送信の回数（1 回だけ）。
INCOMPLETE_OUTPUT_BUDGET_FACTOR = 2
INCOMPLETE_OUTPUT_REASON = "max_output_tokens"


def _openai_parsed(
    input_messages: list[dict[str, Any]],
    settings: Settings,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    """Responses API を呼び、構造化 output model を返します。

    推論型モデルは推論 token が出力予算を消費するため、同じ入力でも実行ごとに `max_output_tokens` へ
    届くかどうかが変わる。上限が理由の未完了だけ予算を 2 倍にして 1 回だけ送り直す。別の理由の未完了と
    2 回目の失敗はそのまま送出する (#987)。
    """
    budget = max(1, int(settings.answer_max_tokens))
    for attempt in range(2):
        response = _openai_client_for_provider(provider, settings).responses.parse(
            model=provider.model,
            input=input_messages,
            text_format=text_format,
            max_output_tokens=budget,
            temperature=LLM_TEMPERATURE,
            extra_body={"seed": LLM_SEED},
        )
        try:
            return _parsed_response(response, text_format, provider)
        except IncompleteResponse as exc:
            if attempt or exc.reason != INCOMPLETE_OUTPUT_REASON:
                raise
            budget *= INCOMPLETE_OUTPUT_BUDGET_FACTOR
    raise AssertionError("unreachable")


def _parse_openai_text_once(
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
        settings, text_format, provider)


def _parse_openai_image_once(
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
        settings, text_format, provider)


def _parse_openai_multimodal_once(
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
        settings, text_format, provider)


def _openai_client_for_provider(provider: LlmProviderSettings, settings: Settings) -> Any:
    return _openai_client(
        provider.provider_id,
        provider.base_url,
        provider.api_key,
        provider.project_id,
        settings.llm_request_timeout_seconds,
        settings.llm_retries,
    )


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
    # endpoint を明示した provider は base URL の組み立てに region を使わない。
    # project は OCI の OpenAI 互換 API だけが要求するため必須にしない。
    if not provider.region and not provider.endpoint:
        missing.append(f"{prefix}_RAG_REGION")
    if not provider.api_key:
        missing.append(f"{prefix}_RAG_API_KEY")
    if not provider.model:
        missing.append(f"{prefix}_RAG_MODEL")
    if missing:
        raise RuntimeError(f"{provider.label} の LLM 設定が不足しています: {', '.join(missing)}")
    return provider


def _parsed_response(
    response: Any,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    status = _value(response, "status")
    if status == "incomplete":
        reason = str(_value(_value(response, "incomplete_details"), "reason") or "unknown")
        raise IncompleteResponse(f"{provider.label} の Responses API 応答が未完了です: {reason}", reason)

    parsed = _value(response, "output_parsed")
    if parsed is not None:
        return _coerce_parsed_output(parsed, text_format, provider)

    refusal = _response_refusal(response)
    if refusal:
        raise RuntimeError(f"{provider.label} の Responses API が拒否を返しました: {refusal}")

    for content in _iter_response_content(response):
        parsed = _value(content, "parsed")
        if parsed is not None:
            return _coerce_parsed_output(parsed, text_format, provider)

    raise RuntimeError(f"{provider.label} の Responses API 応答に parsed output がありません。")


def _coerce_parsed_output(
    parsed: Any,
    text_format: type[StructuredOutputT],
    provider: LlmProviderSettings,
) -> StructuredOutputT:
    if isinstance(parsed, text_format):
        return parsed
    try:
        return text_format.model_validate(parsed)
    except Exception as exc:
        raise InvalidStructuredOutput(f"{provider.label} の LLM parsed output 型が不正です。") from exc


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


def _iter_response_content(response: Any):
    for output in _value(response, "output", []) or []:
        for content in _value(output, "content", []) or []:
            yield content


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _image_data_url(image_path: Path) -> str:
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _is_retryable_error(exc: Exception) -> bool:
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
    return isinstance(exc, (TimeoutError, ConnectionError, *request_errors, *httpx_errors, *oci_errors))


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
) -> dict[str, Any]:
    """対象 crop と文脈から Vision 説明を生成します。

    target_kind は picture/table。アプリ側の処理種別として使い、metadata 由来の命令は
    実行しません。不正値は ValueError。rendered_promptはcheckpointと送信内容を一致させる
    内部用snapshotであり、指定時はその文字列を送信します。選択された provider へのネットワーク通信を伴います。
    """
    if target_kind not in {"picture", "table"}:
        raise ValueError(f"Unknown Vision target kind: {target_kind}")
    prompt = rendered_prompt if rendered_prompt is not None else _render_prompt(metadata, target_kind=target_kind)
    image_paths = [Path(image_path)]
    image_paths.extend(Path(path) for path in context_image_paths if Path(path).is_file())
    if len(image_paths) > 1:
        parsed = parse_multimodal_response(
            VISION_SYSTEM_PROMPT,
            prompt,
            image_paths,
            settings,
            PictureDescriptionOutput,
            provider_id=settings.default_vision_llm,
        )
    else:
        parsed = parse_image_response(
            VISION_SYSTEM_PROMPT,
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
        raise RuntimeError(
            f"Image embedding input mismatch: expected {len(paths)} text payloads, got {len(normalized_texts)}"
        )
    return [
        _embed_image_with_retry(path, text, settings, input_type=input_type)
        for path, text in zip(paths, normalized_texts)
    ]


def _embed_text_batch_with_retry(
    texts: list[str],
    settings: Settings,
    *,
    input_type: str,
) -> list[list[float]]:
    return _parse_with_retry(
        lambda: _embed_text_batch_once(texts, settings, input_type=input_type),
        "OCI embedding",
        settings,
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
    response = runtime.client.embed_text(
        embed_text_details=request,
        retry_strategy=runtime.oci.retry.NoneRetryStrategy(),
    )
    embeddings = _embedding_vectors(response.data, expected_count=len(texts), expected_dimensions=dimensions)
    return embeddings


def _embed_image_with_retry(
    image_path: Path,
    text: str,
    settings: Settings,
    *,
    input_type: str,
) -> list[float]:
    return _parse_with_retry(
        lambda: _embed_image_once(image_path, text, settings, input_type=input_type),
        "OCI image embedding",
        settings,
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
    contents.append(
        models.EmbedImageContent(
            image_url=models.ImageUrl(url=_image_data_url(image_path), detail="AUTO")
        )
    )
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
    response = runtime.client.embed_text(
        embed_text_details=request,
        retry_strategy=runtime.oci.retry.NoneRetryStrategy(),
    )
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
        raise RuntimeError(
            "EMBEDDING_OUTPUT_DIMENSIONS は 256 / 512 / 1024 / 1536 のいずれかで指定してください。"
        )
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
        raise RuntimeError(
            f"OCI embedding response count mismatch: expected {expected_count}, got {len(raw_embeddings)}"
        )

    embeddings: list[list[float]] = []
    for index, raw_vector in enumerate(raw_embeddings):
        if not isinstance(raw_vector, list):
            raise RuntimeError(f"OCI embedding response vector #{index + 1} が配列ではありません。")
        vector = [float(value) for value in raw_vector]
        if len(vector) != expected_dimensions:
            raise RuntimeError(
                "OCI embedding dimension mismatch: "
                f"expected {expected_dimensions}, got {len(vector)} for vector #{index + 1}"
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
    response = runtime.client.rerank_text(
        request,
        retry_strategy=runtime.oci.retry.NoneRetryStrategy(),
    )
    ranks = getattr(response.data, "document_ranks", None) or []
    ranks_with_scores: list[RerankTextRank] = []
    for rank in ranks:
        try:
            index = int(getattr(rank, "index"))
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
    return _parse_with_retry(
        lambda: _rerank_text_once(query, documents, settings, top_n=top_n),
        "OCI rerank",
        settings,
        retries=settings.rerank_retries,
        initial_wait_seconds=settings.rerank_retry_initial_wait_seconds,
        max_wait_seconds=settings.rerank_retry_max_wait_seconds,
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
