"""Guardrail アダプター(安全ポリシーの手動選択プリセット)。

policy→groundedness 厳格度 + audit_emphasis の静的解決は共有パッケージ
``rag_pipeline_core.guardrail`` を単一ソースとして使い、backend と guardrail マイクロサービスが
同一結果を返す。`rag_guardrail_service_enabled` が真のとき静的解決を pipeline-guardrail サービスへ
委譲し、未達/失敗時は in-process(同一ロジック)へ安全縮退する。block_prompt_injection / PII
マスク / max_query_chars は backend 設定由来のため解決後に上乗せする。OCI Generative AI
Guardrails backend(app.clients.oci_guardrails)は別レイヤーで共存。外部安全 SaaS は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from rag_pipeline_core.guardrail import (
    DEFAULT_GROUNDING_MIN_OVERLAP,
    DEFAULT_GROUNDING_MIN_RATIO,
    GUARDRAIL_POLICIES,
    GUARDRAIL_SPECS,
    resolve_guardrail,
)
from rag_pipeline_core.guardrail import (
    normalize_guardrail_policy as _core_normalize,
)

from app.config import GuardrailPolicyName, Settings

GuardrailPolicy = GuardrailPolicyName
DEFAULT_GUARDRAIL_POLICY: GuardrailPolicy = "standard"
GUARDRAIL_POLICY_ORDER: tuple[GuardrailPolicy, ...] = GUARDRAIL_POLICIES  # type: ignore[assignment]


@dataclass(frozen=True)
class GuardrailAdapterParams:
    """ガードレール段へ渡す解決済み effective パラメータ。"""

    policy: GuardrailPolicy
    block_prompt_injection: bool
    mask_sensitive_identifiers: bool
    max_query_chars: int
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool


@dataclass(frozen=True)
class GuardrailPolicyStatus:
    """1 安全ポリシーの選択状態と groundedness 厳格度。"""

    name: GuardrailPolicy
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool


@dataclass(frozen=True)
class GuardrailAdapterRuntimeSettings:
    """Guardrail アダプターの非機密 runtime snapshot。"""

    policy: GuardrailPolicy
    block_prompt_injection: bool
    mask_sensitive_identifiers: bool
    max_query_chars: int
    grounding_min_overlap: int
    grounding_min_ratio: float
    audit_emphasis: bool
    policies: tuple[GuardrailPolicyStatus, ...]


def normalize_guardrail_policy(value: object) -> GuardrailPolicy:
    """未知のポリシー名は既定 standard へ寄せる。"""
    return _core_normalize(value)  # type: ignore[return-value]


def resolve_guardrail_adapter(settings: Settings) -> GuardrailAdapterParams:
    """Settings から Guardrail アダプターの effective パラメータを作る。

    静的 groundedness/audit は core / サービスで解決し、backend 設定由来の安全レバーを上乗せする。
    """
    policy = normalize_guardrail_policy(
        getattr(settings, "rag_guardrail_policy", DEFAULT_GUARDRAIL_POLICY)
    )
    overlap, ratio, audit = _resolve_static(settings, policy)
    return GuardrailAdapterParams(
        policy=policy,
        block_prompt_injection=bool(getattr(settings, "guardrail_block_prompt_injection", True)),
        mask_sensitive_identifiers=bool(
            getattr(settings, "guardrail_mask_sensitive_identifiers", True)
        ),
        max_query_chars=int(getattr(settings, "guardrail_max_query_chars", 2000)),
        grounding_min_overlap=overlap,
        grounding_min_ratio=ratio,
        audit_emphasis=audit,
    )


def _resolve_static(settings: Settings, policy: str) -> tuple[int, float, bool]:
    """静的 (grounding_min_overlap, grounding_min_ratio, audit_emphasis) を解決する。"""
    from rag_pipeline_core.stage import GuardrailStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("guardrail"):
        response = client.run_guardrail(GuardrailStageRequest(policy=policy))
        if response is not None:
            return (
                response.grounding_min_overlap,
                response.grounding_min_ratio,
                response.audit_emphasis,
            )
    resolved = resolve_guardrail(policy)
    return resolved.grounding_min_overlap, resolved.grounding_min_ratio, resolved.audit_emphasis


def guardrail_adapter_runtime_settings(settings: Settings) -> GuardrailAdapterRuntimeSettings:
    """Settings から Guardrail アダプター readiness snapshot を作る。"""
    params = resolve_guardrail_adapter(settings)
    statuses = tuple(
        GuardrailPolicyStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.policy,
            grounding_min_overlap=(
                spec.grounding_min_overlap
                if spec.grounding_min_overlap is not None
                else DEFAULT_GROUNDING_MIN_OVERLAP
            ),
            grounding_min_ratio=(
                spec.grounding_min_ratio
                if spec.grounding_min_ratio is not None
                else DEFAULT_GROUNDING_MIN_RATIO
            ),
            audit_emphasis=spec.audit_emphasis,
        )
        for spec in (GUARDRAIL_SPECS[name] for name in GUARDRAIL_POLICIES)
    )
    return GuardrailAdapterRuntimeSettings(
        policy=params.policy,
        block_prompt_injection=params.block_prompt_injection,
        mask_sensitive_identifiers=params.mask_sensitive_identifiers,
        max_query_chars=params.max_query_chars,
        grounding_min_overlap=params.grounding_min_overlap,
        grounding_min_ratio=params.grounding_min_ratio,
        audit_emphasis=params.audit_emphasis,
        policies=statuses,
    )
