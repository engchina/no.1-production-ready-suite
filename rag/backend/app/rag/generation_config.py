"""Oracle GLOBAL と request / 業務ビューの回答生成設定を解決する。"""

from __future__ import annotations

from typing import Literal

from app.clients.oracle import CustomPromptNotConfiguredError, OracleClient
from app.config import GenerationProfile, Settings
from app.rag.generation_adapter import normalize_generation_profile

GenerationConfigSource = Literal["request", "business_view", "global"]


async def resolve_oracle_generation_settings(
    base_settings: Settings,
    *,
    client: OracleClient | None = None,
) -> Settings:
    """Oracle を毎回読み、process-local Settings へ非破壊で overlay する。"""

    oracle = client or OracleClient(base_settings)
    stored, active_prompt = await oracle.get_generation_runtime_config()
    return base_settings.model_copy(
        update={
            "rag_generation_profile": normalize_generation_profile(stored.profile),
            "rag_generation_custom_prompt": (
                active_prompt.system_prompt if active_prompt is not None else None
            ),
            "rag_generation_custom_prompt_version_id": (
                active_prompt.version_id if active_prompt is not None else None
            ),
            "rag_generation_config_source": "global",
        }
    )


def apply_generation_profile(
    settings: Settings,
    profile: GenerationProfile,
    *,
    source: GenerationConfigSource,
) -> Settings:
    """最終 profile と選択元を設定し、custom の active Prompt を必須化する。"""

    normalized = normalize_generation_profile(profile)
    if normalized == "custom" and not (
        settings.rag_generation_custom_prompt and settings.rag_generation_custom_prompt_version_id
    ):
        raise CustomPromptNotConfiguredError(
            "カスタム回答スタイルを使う前に Prompt 版を作成して有効化してください。"
        )
    return settings.model_copy(
        update={
            "rag_generation_profile": normalized,
            "rag_generation_config_source": source,
        }
    )


def validate_effective_generation_settings(settings: Settings) -> Settings:
    """overlay 後の profile 契約を検査する。"""

    return apply_generation_profile(
        settings,
        normalize_generation_profile(settings.rag_generation_profile),
        source=settings.rag_generation_config_source,
    )
