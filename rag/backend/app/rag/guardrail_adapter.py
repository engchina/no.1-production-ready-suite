"""Guardrail アダプター(安全ポリシーの手動選択プリセット)。

`retrieval_adapter.py` と同型で、選択された安全ポリシーと利用可能なプリセット一覧を非機密の
runtime snapshot として返す。NeMo Guardrails / Llama Guard 的な概念を、外部 SaaS や
追加 LLM 呼び出しなしの決定論ヒューリスティック(prompt injection / PII マスク /
groundedness 閾値)へ再マップする。`standard` は既存 `guardrail_*` 設定をそのまま尊重する。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import GuardrailPolicyName, Settings

# evaluate_groundedness の既定閾値。standard はこの値を再現する。
DEFAULT_GROUNDING_MIN_OVERLAP = 3
DEFAULT_GROUNDING_MIN_RATIO = 0.12

GuardrailPolicy = GuardrailPolicyName
DEFAULT_GUARDRAIL_POLICY: GuardrailPolicy = "standard"
GUARDRAIL_POLICY_ORDER: tuple[GuardrailPolicy, ...] = (
    "standard",
    "strict",
    "lenient",
    "regulated",
)


@dataclass(frozen=True)
class GuardrailPolicySpec:
    """1 安全ポリシーの由来と groundedness 厳格度。"""

    name: GuardrailPolicy
    origin: str
    recommended_for: tuple[str, ...]
    grounding_min_overlap: int | None  # None は standard(settings 既定値)を使う
    grounding_min_ratio: float | None
    audit_emphasis: bool = False


GUARDRAIL_ADAPTER_SPECS: dict[GuardrailPolicy, GuardrailPolicySpec] = {
    "standard": GuardrailPolicySpec(
        name="standard",
        origin="reference_policy",
        recommended_for=("general", "balanced"),
        grounding_min_overlap=None,
        grounding_min_ratio=None,
    ),
    "strict": GuardrailPolicySpec(
        name="strict",
        origin="nemo_guardrails_strict",
        recommended_for=("low_hallucination", "sensitive"),
        grounding_min_overlap=5,
        grounding_min_ratio=0.30,
    ),
    "lenient": GuardrailPolicySpec(
        name="lenient",
        origin="recall_first",
        recommended_for=("exploratory", "internal"),
        grounding_min_overlap=2,
        grounding_min_ratio=0.05,
    ),
    "regulated": GuardrailPolicySpec(
        name="regulated",
        origin="compliance_audit",
        recommended_for=("compliance", "regulated"),
        grounding_min_overlap=5,
        grounding_min_ratio=0.30,
        audit_emphasis=True,
    ),
}


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
    normalized = str(value).casefold()
    if normalized in GUARDRAIL_ADAPTER_SPECS:
        return normalized
    return DEFAULT_GUARDRAIL_POLICY


def resolve_guardrail_adapter(settings: Settings) -> GuardrailAdapterParams:
    """Settings から Guardrail アダプターの effective パラメータを作る。"""
    policy = normalize_guardrail_policy(
        getattr(settings, "rag_guardrail_policy", DEFAULT_GUARDRAIL_POLICY)
    )
    spec = GUARDRAIL_ADAPTER_SPECS[policy]
    return GuardrailAdapterParams(
        policy=policy,
        block_prompt_injection=bool(getattr(settings, "guardrail_block_prompt_injection", True)),
        mask_sensitive_identifiers=bool(
            getattr(settings, "guardrail_mask_sensitive_identifiers", True)
        ),
        max_query_chars=int(getattr(settings, "guardrail_max_query_chars", 2000)),
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


def guardrail_adapter_runtime_settings(settings: Settings) -> GuardrailAdapterRuntimeSettings:
    """Settings から Guardrail アダプター readiness snapshot を作る。"""
    params = resolve_guardrail_adapter(settings)
    statuses = tuple(
        GuardrailPolicyStatus(
            name=spec.name,
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
        for spec in (GUARDRAIL_ADAPTER_SPECS[name] for name in GUARDRAIL_POLICY_ORDER)
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
