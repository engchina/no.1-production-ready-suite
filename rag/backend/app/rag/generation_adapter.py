"""Generation アダプター(回答生成の手動選択プリセット)。

profile→system prompt 変種の静的解決は共有パッケージ ``rag_pipeline_core.generation`` を単一
ソースとして使い、backend と generation マイクロサービスが同一結果を返す。
`rag_generation_service_enabled` が真のとき静的解決を pipeline-generation サービスへ委譲し、
無効時と remote 未到達時は in-process(同一ロジック)へ縮退する。応答済み remote の HTTP error /
不正応答は処理停止する。custom(prompt version store)と
業務ビュー persona override は backend 固有のため解決後に上乗せする。外部 provider なし。
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError
from rag_pipeline_core.generation import (
    GENERATION_PROFILES,
    GENERATION_SPECS,
    resolve_generation,
)
from rag_pipeline_core.generation import (
    normalize_generation_profile as _core_normalize,
)

from app.config import GenerationProfile, Settings

GenerationProfileName = GenerationProfile
DEFAULT_GENERATION_PROFILE: GenerationProfileName = "grounded_concise"
GENERATION_PROFILE_ORDER: tuple[GenerationProfileName, ...] = GENERATION_PROFILES  # type: ignore[assignment]


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
    return _core_normalize(value)  # type: ignore[return-value]


def resolve_generation_adapter(settings: Settings) -> GenerationAdapterParams:
    """Settings から Generation アダプターの解決済みパラメータを作る。

    静的 system prompt は core / サービスで解決し、custom(prompt version store)と
    業務ビュー persona override を backend 側で上乗せする。
    """
    profile = normalize_generation_profile(
        getattr(settings, "rag_generation_profile", DEFAULT_GENERATION_PROFILE)
    )
    system_prompt, structured_output = _resolve_static(settings, profile)
    if profile == "custom":
        # 遅延 import で循環依存を避ける。有効版が無ければ None=client 既定 prompt。
        from app.rag.prompt_versions import active_custom_system_prompt

        system_prompt = active_custom_system_prompt()
    # 業務ビュー(Business View)の persona 上書きは profile prompt より優先する。
    override = getattr(settings, "rag_generation_system_prompt_override", None)
    if isinstance(override, str) and override.strip():
        system_prompt = override.strip()
    return GenerationAdapterParams(
        profile=profile,
        system_prompt=system_prompt,
        structured_output=structured_output,
    )


def _resolve_static(settings: Settings, profile: str) -> tuple[str | None, bool]:
    """静的 (system_prompt, structured_output) を opt-in service / disabled 時 local で解決する。"""
    from rag_pipeline_core.stage import GenerationStageRequest

    from app.clients.pipeline_stage import PipelineStageClient

    client = PipelineStageClient(settings)
    if client.is_enabled("generation"):
        response = client.run_generation(GenerationStageRequest(profile=profile))
        if response is not None:
            return response.system_prompt, response.structured_output
    resolved = resolve_generation(profile)
    return resolved.system_prompt, resolved.structured_output


def generation_adapter_runtime_settings(settings: Settings) -> GenerationAdapterRuntimeSettings:
    """Settings から Generation アダプター readiness snapshot を作る。"""
    params = resolve_generation_adapter(settings)
    statuses = tuple(
        GenerationProfileStatus(
            name=spec.name,  # type: ignore[arg-type]
            origin=spec.origin,
            recommended_for=spec.recommended_for,
            selected=spec.name == params.profile,
            structured_output=spec.structured_output,
        )
        for spec in (GENERATION_SPECS[name] for name in GENERATION_PROFILES)
    )
    return GenerationAdapterRuntimeSettings(
        profile=params.profile,
        structured_output=params.structured_output,
        profiles=statuses,
    )


class StructuredAnswer(BaseModel):
    """structured_json プロファイルの回答スキーマ(machine-consumable)。"""

    answer: str
    evidence: list[str] = []
    sources: list[str] = []


def validate_structured_answer(text: str) -> str:
    """structured_json の生成結果を JSON として parse / スキーマ検証し正規化 JSON を返す。

    ```json フェンスや前後の説明文に寛容(最初の ``{`` から最後の ``}`` までを抽出)。
    検証失敗は ValueError を投げる(fail-fast、外部 provider なし)。
    """
    candidate = text.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("構造化 JSON 回答に JSON オブジェクトが見つかりません。")
    candidate = candidate[start : end + 1]
    try:
        model = StructuredAnswer.model_validate_json(candidate)
    except ValidationError as exc:
        raise ValueError(f"構造化 JSON 回答がスキーマに一致しません: {exc}") from exc
    return model.model_dump_json()
