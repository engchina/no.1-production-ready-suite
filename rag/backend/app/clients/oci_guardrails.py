"""OCI Generative AI Guardrails クライアント。

OCI Generative AI の **ApplyGuardrails API**(`oci.generative_ai_inference`)を呼び、
テキストの content moderation / PII 検出 / prompt injection 検出を行う。回答生成は行わない
**検出専用 API** であり、AGENTS.md §1 の「chat 推論 API を LLM/VLM に使わない」には抵触しない
(ユーザ明示要望による OCI サービスの追加)。確定スタック(embedding/rerank=OCI GenAI、
回答 LLM/VLM=Enterprise AI、ベクトル DB=Oracle 26ai)は不変で、別 LLM provider・外部
ベクトル DB は導入しない。

未設定・SDK 失敗・呼び出し失敗時は **None を返して安全に縮退**し、呼び出し側は既存の
in-process(local)ガードレールにフォールバックする。privacy: 検出結果は **PII の値そのものを
保持せず、label と件数のみ** を返す。
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

from app.clients.oci_auth import load_oci_config_without_prompt
from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardrailInspection:
    """ApplyGuardrails の非機密サマリ(PII の値は保持しない)。"""

    prompt_injection: bool = False
    prompt_injection_score: float = 0.0
    moderation_categories: tuple[str, ...] = ()
    pii_labels: tuple[str, ...] = field(default_factory=tuple)

    @property
    def flagged(self) -> bool:
        """いずれかの検出があったか。"""
        return bool(self.prompt_injection or self.moderation_categories or self.pii_labels)


class OciGuardrailsClient:
    """ApplyGuardrails を薄くラップする。全ての失敗は None へ縮退する。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _compartment_id(self) -> str:
        return (
            str(getattr(self._settings, "oci_guardrails_compartment_id", "") or "").strip()
            or str(getattr(self._settings, "oci_compartment_id", "") or "").strip()
        )

    def is_configured(self) -> bool:
        """compartment が解決でき、かつ backend が oci_guardrails のとき True。"""
        backend = str(getattr(self._settings, "rag_guardrail_backend", "local") or "local")
        return backend == "oci_guardrails" and bool(self._compartment_id())

    def inspect_text(
        self,
        text: str,
        *,
        language_code: str = "ja",
        prompt_injection_threshold: float | None = None,
    ) -> GuardrailInspection | None:
        """text を ApplyGuardrails で検査する。未設定・失敗時は None(呼び出し側で local へ)。"""
        if not text.strip() or not self.is_configured():
            return None
        try:
            return self._inspect(text, language_code, prompt_injection_threshold)
        except Exception as exc:  # noqa: BLE001 - 失敗は安全に local へ縮退する境界
            logger.warning(
                "oci_guardrails_inspect_failed",
                extra={"error": type(exc).__name__},
            )
            return None

    def _client(self) -> object:
        # 既存 oci client と同じく importlib + oci_auth で SDK を遅延解決する
        # (mypy の import-untyped を避け、暗号化 PEM の対話プロンプトも防ぐ)。
        oci_config = importlib.import_module("oci.config")
        inference = importlib.import_module("oci.generative_ai_inference")
        config = load_oci_config_without_prompt(
            oci_config,
            getattr(self._settings, "oci_config_file", "~/.oci/config") or "~/.oci/config",
            getattr(self._settings, "oci_config_profile", "DEFAULT") or "DEFAULT",
        )
        endpoint = str(getattr(self._settings, "oci_guardrails_endpoint", "") or "").strip()
        kwargs: dict[str, object] = {}
        if endpoint:
            kwargs["service_endpoint"] = endpoint
        return inference.GenerativeAiInferenceClient(config, **kwargs)

    def _inspect(
        self, text: str, language_code: str, prompt_injection_threshold: float | None
    ) -> GuardrailInspection | None:
        models = importlib.import_module("oci.generative_ai_inference.models")

        threshold = (
            prompt_injection_threshold
            if prompt_injection_threshold is not None
            else float(getattr(self._settings, "oci_guardrails_prompt_injection_threshold", 0.5))
        )
        details = models.ApplyGuardrailsDetails(
            compartment_id=self._compartment_id(),
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
        return _parse_results(results, threshold)


def _parse_results(results: object, threshold: float) -> GuardrailInspection:
    """ApplyGuardrails の results を非機密サマリへ変換する(PII の値は捨てる)。"""
    moderation = getattr(results, "content_moderation", None)
    categories = tuple(getattr(moderation, "categories", None) or ()) if moderation else ()

    injection_result = getattr(results, "prompt_injection", None)
    score = float(getattr(injection_result, "score", 0.0) or 0.0) if injection_result else 0.0
    flagged_modalities = (
        tuple(getattr(injection_result, "flagged_modalities", None) or ())
        if injection_result
        else ()
    )
    prompt_injection = bool(score >= threshold or flagged_modalities)

    pii_items = getattr(results, "personally_identifiable_information", None) or ()
    pii_labels = tuple(
        str(getattr(item, "label", "") or "PII") for item in pii_items if item is not None
    )
    return GuardrailInspection(
        prompt_injection=prompt_injection,
        prompt_injection_score=score,
        moderation_categories=tuple(str(c) for c in categories),
        pii_labels=pii_labels,
    )
