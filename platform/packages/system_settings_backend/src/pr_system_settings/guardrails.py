"""3 製品共通の安全チェック（OCI Guardrails で質問・回答を検査する。#5061）。

- 判定: OCI Generative AI の ApplyGuardrails の結果で、個人情報は位置どおりにマスクし、
  プロンプト攻撃・不適切な内容は停止する。検査を完了できないときも停止する。
- 設定: 入力・出力の有効化は 3 製品で 1 つ（`platform/.env` の `PLATFORM_GUARDRAILS_INPUT_ENABLED`
  / `PLATFORM_GUARDRAILS_OUTPUT_ENABLED`。既定は無効）。画面からは `/settings/guardrail` で
  保存する。

ApplyGuardrails は検出専用の API で、回答は生成しない。検出した値そのものは保持せず、
位置（offset / length）とラベルだけを使う。
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, params
from pr_backend_core.schemas import ApiResponse
from pydantic import BaseModel, ConfigDict

from .env_file import write_env_values
from .oci import test_oci_config
from .oci_auth import load_oci_config_without_prompt

logger = logging.getLogger(__name__)

SENSITIVE_VALUE_MASK = "[機微情報]"
# content moderation・prompt injection を検出とみなす score（OCI の応答は 0.0 / 1.0）。
DETECTION_THRESHOLD = 0.5
# 検査の待ち時間の上限（秒）。SDK の再試行は切るので、届かないときはこの時間で止める。
INSPECTION_TIMEOUT_SECONDS = 5.0


# --------------------------------------------------------------------------- OCI の呼出


class OciGuardrailsUnavailableError(RuntimeError):
    """OCI Guardrails が検査を完了できなかったことだけを表す非機密例外。"""


@dataclass(frozen=True)
class PiiSpan:
    """入力中の個人情報の位置。検出した文字列そのものは保持しない。"""

    offset: int
    length: int
    label: str


@dataclass(frozen=True)
class GuardrailInspection:
    """ApplyGuardrails の非機密のまとめ（個人情報の値は保持しない）。"""

    prompt_injection: bool = False
    prompt_injection_score: float = 0.0
    moderation_categories: tuple[str, ...] = ()
    pii_spans: tuple[PiiSpan, ...] = ()

    @property
    def pii_labels(self) -> tuple[str, ...]:
        return tuple(span.label for span in self.pii_spans)

    @property
    def flagged(self) -> bool:
        return bool(self.prompt_injection or self.moderation_categories or self.pii_labels)


def guardrails_enabled(settings: Any) -> bool:
    """入力・出力のどちらかの検査が有効か。"""
    return bool(
        getattr(settings, "guardrails_input_enabled", False)
        or getattr(settings, "guardrails_output_enabled", False)
    )


def _compartment_id(settings: Any) -> str:
    return str(getattr(settings, "oci_compartment_id", "") or "").strip()


class OciGuardrailsClient:
    """ApplyGuardrails を薄く包み、失敗を非機密な 1 つの例外へ変換する。"""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        return guardrails_enabled(self._settings) and bool(_compartment_id(self._settings))

    def inspect_text(self, text: str, *, language_code: str = "ja") -> GuardrailInspection | None:
        """text を検査する。失敗したときは非機密な例外を送る。"""
        if not text.strip():
            return None
        if not self.is_configured():
            raise OciGuardrailsUnavailableError("OCI Guardrails を利用できません。")
        try:
            return self._inspect(text, language_code)
        except OciGuardrailsUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK の詳細を境界の外へ出さない
            logger.warning("oci_guardrails_inspect_failed", extra={"error": type(exc).__name__})
            raise OciGuardrailsUnavailableError("OCI Guardrails を利用できません。") from exc

    def _client(self) -> object:
        # SDK は遅延して読み、暗号化 PEM の対話プロンプトも防ぐ。
        oci_config = importlib.import_module("oci.config")
        inference = importlib.import_module("oci.generative_ai_inference")
        config = load_oci_config_without_prompt(
            oci_config,
            getattr(self._settings, "oci_config_file", "~/.oci/config") or "~/.oci/config",
            getattr(self._settings, "oci_config_profile", "DEFAULT") or "DEFAULT",
        )
        return inference.GenerativeAiInferenceClient(
            config,
            timeout=(min(3.0, INSPECTION_TIMEOUT_SECONDS), INSPECTION_TIMEOUT_SECONDS),
            # 既定の再試行を切る。残すと届かないときに timeout を何度も待つ（#2061）。
            retry_strategy=importlib.import_module("oci.retry").NoneRetryStrategy(),
            # 既定の circuit breaker も切る。失敗の扱いは呼出側が決める（#2211）。
            circuit_breaker_strategy=importlib.import_module(
                "oci.circuit_breaker"
            ).NoCircuitBreakerStrategy(),
        )

    def _inspect(self, text: str, language_code: str) -> GuardrailInspection | None:
        models = importlib.import_module("oci.generative_ai_inference.models")
        details = models.ApplyGuardrailsDetails(
            compartment_id=_compartment_id(self._settings),
            input=models.GuardrailsTextInput(
                type="TEXT", content=text, language_code=language_code
            ),
            guardrail_configs=models.GuardrailConfigs(
                content_moderation_config=models.ContentModerationConfiguration(),
                personally_identifiable_information_config=(
                    models.PersonallyIdentifiableInformationConfiguration()
                ),
                prompt_injection_config=models.PromptInjectionConfiguration(),
            ),
        )
        response = self._client().apply_guardrails(details)  # type: ignore[attr-defined]
        results = getattr(getattr(response, "data", None), "results", None)
        if results is None:
            return None
        return parse_guardrail_results(results)


def parse_guardrail_results(results: object) -> GuardrailInspection:
    """ApplyGuardrails の results を非機密のまとめへ変換する（個人情報の値は捨てる）。"""
    moderation = getattr(results, "content_moderation", None)
    categories = _flagged_moderation_categories(
        getattr(moderation, "categories", None) if moderation else None
    )
    injection = getattr(results, "prompt_injection", None)
    score = float(getattr(injection, "score", 0.0) or 0.0) if injection else 0.0
    modalities = tuple(getattr(injection, "flagged_modalities", None) or ()) if injection else ()
    items = getattr(results, "personally_identifiable_information", None) or ()
    spans = tuple(
        span for item in items if item is not None for span in [_pii_span(item)] if span is not None
    )
    return GuardrailInspection(
        prompt_injection=bool(score >= DETECTION_THRESHOLD or modalities),
        prompt_injection_score=score,
        moderation_categories=categories,
        pii_spans=spans,
    )


def _flagged_moderation_categories(categories: Iterable[object] | None) -> tuple[str, ...]:
    """検出したカテゴリの名前だけを返す。

    検出が無くても `OVERALL` / `BLOCKLIST` が score 0.0 で返るので、score が閾値以上か
    `flagged_modalities` があるものだけを検出とする（#2061）。
    """
    flagged: list[str] = []
    for category in categories or ():
        if category is None:
            continue
        score = float(getattr(category, "score", 0.0) or 0.0)
        if score < DETECTION_THRESHOLD and not (
            getattr(category, "flagged_modalities", None) or ()
        ):
            continue
        flagged.append(str(getattr(category, "name", "") or "").strip() or "UNKNOWN")
    return tuple(dict.fromkeys(flagged))


def _pii_span(item: object) -> PiiSpan | None:
    """SDK の版の差を吸収して、個人情報の位置だけを取り出す。"""
    offset = getattr(item, "offset", None)
    if offset is None:
        offset = getattr(item, "start_offset", None)
    length = getattr(item, "length", None)
    if length is None and offset is not None:
        end_offset = getattr(item, "end_offset", None)
        if end_offset is not None:
            length = int(end_offset) - int(offset)
    if offset is None or length is None or int(offset) < 0 or int(length) <= 0:
        return None
    return PiiSpan(
        offset=int(offset), length=int(length), label=str(getattr(item, "label", "") or "PII")
    )


# --------------------------------------------------------------------------- 判定


class _InspectorLike(Protocol):
    def inspect_text(self, text: str) -> GuardrailInspection | None: ...


@dataclass(frozen=True)
class GuardrailFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    sanitized_text: str
    findings: list[GuardrailFinding]
    backend_degraded: bool = False
    skipped_reason: Literal["disabled", "empty_text", "client_unavailable"] | None = None

    @property
    def warnings(self) -> list[str]:
        return [finding.message for finding in self.findings]


class GuardrailPolicy:
    """有効なときだけ検査し、個人情報はマスク、攻撃・不適切な内容は停止する。"""

    def __init__(self, settings: Any, oci_client: _InspectorLike | None = None) -> None:
        self._input_enabled = bool(getattr(settings, "guardrails_input_enabled", False))
        self._output_enabled = bool(getattr(settings, "guardrails_output_enabled", False))
        self._oci_client = (
            (oci_client if oci_client is not None else OciGuardrailsClient(settings))
            if self._input_enabled or self._output_enabled
            else None
        )

    def validate_query(self, query: str) -> GuardrailResult:
        """有効なときだけ質問を検査する。"""
        return self._inspect(query, enabled=self._input_enabled)

    def validate_answer(self, answer: str, context: str | None = None) -> GuardrailResult:
        """有効なときだけ回答を検査する。根拠との語の重なりは判定しない。"""
        return self._inspect(answer, enabled=self._output_enabled)

    def _inspect(self, text: str, *, enabled: bool) -> GuardrailResult:
        if not enabled or self._oci_client is None or not text.strip():
            return GuardrailResult(
                allowed=True,
                sanitized_text=text,
                findings=[],
                skipped_reason="disabled"
                if not enabled
                else "empty_text"
                if not text.strip()
                else "client_unavailable",
            )
        try:
            inspection = self._oci_client.inspect_text(text)
        except Exception:  # noqa: BLE001 - SDK の詳細や入力を応答へ出さない
            inspection = None
        if inspection is None:
            return GuardrailResult(
                allowed=False,
                sanitized_text="安全検査を完了できないため、処理を停止しました。",
                findings=[
                    GuardrailFinding(
                        code="guardrail_backend_unavailable",
                        severity="error",
                        message="OCI Guardrails を利用できないため、処理を停止しました。",
                    )
                ],
                backend_degraded=True,
            )
        findings: list[GuardrailFinding] = []
        if inspection.prompt_injection:
            findings.append(
                GuardrailFinding(
                    code="oci_prompt_injection",
                    severity="error",
                    message="OCI Guardrails がプロンプト攻撃を検出しました。",
                )
            )
        if inspection.moderation_categories:
            findings.append(
                GuardrailFinding(
                    code="oci_content_moderation",
                    severity="error",
                    message="OCI Guardrails が不適切な内容を検出しました。",
                )
            )
        blocked = bool(findings)
        sanitized = text
        if inspection.pii_spans:
            sanitized = mask_spans(text, inspection.pii_spans)
            findings.append(
                GuardrailFinding(
                    code="oci_pii_detected",
                    severity="warning",
                    message="OCI Guardrails が個人情報の可能性を検出し、マスクしました。",
                )
            )
        return GuardrailResult(
            allowed=not blocked,
            sanitized_text="安全チェックにより処理を停止しました。" if blocked else sanitized,
            findings=findings,
        )


def mask_spans(text: str, spans: Iterable[object]) -> str:
    """個人情報の位置を後ろから置き換える。重なる位置はまとめ、原値は保持しない。"""
    normalized: list[tuple[int, int]] = []
    for span in spans:
        offset = int(getattr(span, "offset", -1))
        length = int(getattr(span, "length", 0))
        if offset < 0 or length <= 0 or offset + length > len(text):
            continue
        normalized.append((offset, offset + length))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(normalized)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    masked = text
    for start, end in reversed(merged):
        masked = f"{masked[:start]}{SENSITIVE_VALUE_MASK}{masked[end:]}"
    return masked


# --------------------------------------------------------------------------- 設定の API

GuardrailWarningCode = Literal[
    "oci_guardrails_compartment_missing", "oci_guardrails_credentials_invalid"
]


class GuardrailSettingsData(BaseModel):
    input_enabled: bool
    output_enabled: bool
    oci_configured: bool
    oci_warning_code: GuardrailWarningCode | None = None


class GuardrailSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_enabled: bool
    output_enabled: bool


def guardrail_readiness_code(settings: Any) -> GuardrailWarningCode | None:
    """OCI Guardrails を使うときの準備の問題（なければ None）。OCI へは問い合わせない。"""
    if not _compartment_id(settings):
        return "oci_guardrails_compartment_missing"
    if test_oci_config(settings, verify_with_oci=False).status != "success":
        return "oci_guardrails_credentials_invalid"
    return None


def guardrail_settings_data(settings: Any) -> GuardrailSettingsData:
    code = guardrail_readiness_code(settings)
    return GuardrailSettingsData(
        input_enabled=bool(getattr(settings, "guardrails_input_enabled", False)),
        output_enabled=bool(getattr(settings, "guardrails_output_enabled", False)),
        oci_configured=code is None,
        oci_warning_code=code,
    )


def build_guardrail_router(
    *,
    get_settings: Callable[[], Any],
    env_file: Callable[[], Path],
    write_dependencies: Sequence[params.Depends] = (),
) -> APIRouter:
    """`/guardrail` の router を作る。製品側で `/settings` の下に include する。"""
    router = APIRouter()

    @router.get("/guardrail", response_model=ApiResponse[GuardrailSettingsData])
    def get_guardrail_settings() -> ApiResponse[GuardrailSettingsData]:
        return ApiResponse(data=guardrail_settings_data(get_settings()))

    @router.patch(
        "/guardrail",
        response_model=ApiResponse[GuardrailSettingsData],
        dependencies=list(write_dependencies),
    )
    def update_guardrail_settings(
        payload: GuardrailSettingsUpdate,
    ) -> ApiResponse[GuardrailSettingsData]:
        settings = get_settings()
        if (payload.input_enabled or payload.output_enabled) and guardrail_readiness_code(settings):
            raise HTTPException(
                status_code=422,
                detail=(
                    "OCI Guardrails を有効にする前に、OCI 認証と compartment を設定してください。"
                ),
            )
        try:
            write_env_values(
                env_file(),
                {
                    "PLATFORM_GUARDRAILS_INPUT_ENABLED": str(payload.input_enabled).lower(),
                    "PLATFORM_GUARDRAILS_OUTPUT_ENABLED": str(payload.output_enabled).lower(),
                },
                section_comment="# 安全チェック（入力・出力）",
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="安全チェックの設定を platform/.env へ保存できませんでした。",
            ) from exc
        # 保存に成功してから実行中の設定へ反映する。
        settings.guardrails_input_enabled = payload.input_enabled
        settings.guardrails_output_enabled = payload.output_enabled
        return ApiResponse(data=guardrail_settings_data(settings))

    return router
