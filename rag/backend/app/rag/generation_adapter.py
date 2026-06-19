"""Generation アダプター(回答生成の手動選択プリセット)。

`retrieval_adapter.py` と同型で、選択された回答生成プロファイルと利用可能なプリセット一覧を
非機密の runtime snapshot として返す。各 preset は OCI Enterprise AI へ渡す system prompt 変種と
生成パラメータへ決定論的に解決する。追加 LLM 呼び出しや外部 provider は導入しない。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import GenerationProfile, Settings

GenerationProfileName = GenerationProfile
DEFAULT_GENERATION_PROFILE: GenerationProfileName = "grounded_concise"
GENERATION_PROFILE_ORDER: tuple[GenerationProfileName, ...] = (
    "grounded_concise",
    "detailed_cited",
    "strict_extractive",
    "structured_json",
    "bilingual_ja_en",
    "custom",
)

# grounded_concise は client 既定 system prompt(LLM_SYSTEM_PROMPT)をそのまま使うため None。
_DETAILED_CITED_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使って日本語で回答し、"
    "主張ごとに対応する出典を [Evidence n | source#chunk_id] の形式で本文末尾に明示してください。"
    "context にない情報は補わず、確証がない点は不明と述べてください。"
)
_STRICT_EXTRACTIVE_PROMPT = (
    "あなたは厳密な抽出型 QA アシスタントです。検索根拠(context)に明示的に書かれている"
    "事実だけを日本語で簡潔に回答してください。推測・一般知識による補完は禁止します。"
    "context に答えがない場合は「提供された根拠には該当する情報がありません。」と回答してください。"
)
_STRUCTURED_JSON_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使い、次の JSON だけを"
    'output してください(前後に説明文を付けない): {"answer": string, "evidence": [string], '
    '"sources": [string]}。answer は日本語、sources は使用した source#chunk_id。'
    "context にない情報は含めないでください。"
)
_BILINGUAL_PROMPT = (
    "あなたは社内ナレッジ検索アシスタントです。検索根拠(context)だけを使って回答してください。"
    "まず日本語で回答し、続けて「English summary:」として 1-2 文の英語要約を付けてください。"
    "context にない情報は補わないでください。"
)


@dataclass(frozen=True)
class GenerationProfileSpec:
    """1 回答生成プロファイルの由来と system prompt 変種。"""

    name: GenerationProfileName
    origin: str
    recommended_for: tuple[str, ...]
    system_prompt: str | None  # None は client 既定 system prompt を使う
    structured_output: bool = False


GENERATION_ADAPTER_SPECS: dict[GenerationProfileName, GenerationProfileSpec] = {
    "grounded_concise": GenerationProfileSpec(
        name="grounded_concise",
        origin="default_rag_prompt",
        recommended_for=("general", "faq"),
        system_prompt=None,
    ),
    "detailed_cited": GenerationProfileSpec(
        name="detailed_cited",
        origin="ragflow_cited_answer",
        recommended_for=("audit", "policy"),
        system_prompt=_DETAILED_CITED_PROMPT,
    ),
    "strict_extractive": GenerationProfileSpec(
        name="strict_extractive",
        origin="extractive_qa",
        recommended_for=("compliance", "factual"),
        system_prompt=_STRICT_EXTRACTIVE_PROMPT,
    ),
    "structured_json": GenerationProfileSpec(
        name="structured_json",
        origin="dify_structured_output",
        recommended_for=("integration", "api"),
        system_prompt=_STRUCTURED_JSON_PROMPT,
        structured_output=True,
    ),
    "bilingual_ja_en": GenerationProfileSpec(
        name="bilingual_ja_en",
        origin="multilingual",
        recommended_for=("global", "bilingual"),
        system_prompt=_BILINGUAL_PROMPT,
    ),
    # custom は prompt version store の有効版 system_prompt を実行時に解決する
    # (PoweRAG の prompt versioning 由来)。有効版が無ければ client 既定 prompt。
    "custom": GenerationProfileSpec(
        name="custom",
        origin="prompt_version_store",
        recommended_for=("custom", "tuning"),
        system_prompt=None,
    ),
}


@dataclass(frozen=True)
class GenerationAdapterParams:
    """回答生成段へ渡す解決済みパラメータ。"""

    profile: GenerationProfileName
    system_prompt: str | None
    structured_output: bool


@dataclass(frozen=True)
class GenerationProfileStatus:
    """1 回答生成プロファイルの選択状態と適用場面。"""

    name: GenerationProfileName
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool
    structured_output: bool


@dataclass(frozen=True)
class GenerationAdapterRuntimeSettings:
    """Generation アダプターの非機密 runtime snapshot。"""

    profile: GenerationProfileName
    structured_output: bool
    profiles: tuple[GenerationProfileStatus, ...]


def normalize_generation_profile(value: object) -> GenerationProfileName:
    """未知のプロファイル名は既定 grounded_concise へ寄せる。"""
    normalized = str(value).casefold()
    if normalized in GENERATION_ADAPTER_SPECS:
        return normalized
    return DEFAULT_GENERATION_PROFILE


def resolve_generation_adapter(settings: Settings) -> GenerationAdapterParams:
    """Settings から Generation アダプターの解決済みパラメータを作る。"""
    profile = normalize_generation_profile(
        getattr(settings, "rag_generation_profile", DEFAULT_GENERATION_PROFILE)
    )
    spec = GENERATION_ADAPTER_SPECS[profile]
    system_prompt = spec.system_prompt
    if profile == "custom":
        # 遅延 import で循環依存を避ける。有効版が無ければ None=client 既定 prompt。
        from app.rag.prompt_versions import active_custom_system_prompt

        system_prompt = active_custom_system_prompt()
    # 業務アシスタント(Business View)の persona 上書きは profile prompt より優先する。
    override = getattr(settings, "rag_generation_system_prompt_override", None)
    if isinstance(override, str) and override.strip():
        system_prompt = override.strip()
    return GenerationAdapterParams(
        profile=profile,
        system_prompt=system_prompt,
        structured_output=spec.structured_output,
    )


def generation_adapter_runtime_settings(settings: Settings) -> GenerationAdapterRuntimeSettings:
    """Settings から Generation アダプター readiness snapshot を作る。"""
    params = resolve_generation_adapter(settings)
    statuses = tuple(
        GenerationProfileStatus(
            name=spec.name,
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            structured_output=spec.structured_output,
        )
        for spec in (GENERATION_ADAPTER_SPECS[name] for name in GENERATION_PROFILE_ORDER)
    )
    return GenerationAdapterRuntimeSettings(
        profile=params.profile,
        structured_output=params.structured_output,
        profiles=statuses,
    )
