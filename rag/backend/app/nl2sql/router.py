"""NL2SQL Router アダプター(profile 自動選択 / 複雑度ルーティングの手動選択プリセット)。

off(既定)/ classifier / complexity_aware を束ねる。

- ``classifier``: 質問を分類器(本番は OCI Cohere 埋め込み + LogisticRegression、決定論)で
  domain 予測し ``domain→profile`` で Select AI profile を自動選択する。本モジュールは分類器を
  ``QuestionClassifier`` プロトコルで受け取り、未指定時は決定論キーワード分類器へ縮退する。
- ``complexity_aware``: 質問の複雑度シグナル数で ``select_ai``(単段)↔``select_ai_agent``(多段)を
  振り分けコストを最適化する(EllieSQL 風)。

このモジュールは **非 network・決定論**(埋め込み/学習済モデルは呼び出し側が注入)。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.config import Nl2SqlGenerationBackend, Nl2SqlRouterProfile, Settings

RouterProfile = Nl2SqlRouterProfile
DEFAULT_ROUTER_PROFILE: RouterProfile = "off"
ROUTER_PROFILE_ORDER: tuple[RouterProfile, ...] = ("off", "classifier", "complexity_aware")

_GENERATION_BACKENDS: tuple[Nl2SqlGenerationBackend, ...] = (
    "select_ai_agent",
    "select_ai",
    "app_enterprise_ai",
)
DEFAULT_GENERATION_BACKEND: Nl2SqlGenerationBackend = "select_ai_agent"

# 複雑度シグナル(category → 正規表現)。1 カテゴリ 1 点で score とする。
_COMPLEXITY_SIGNALS: dict[str, re.Pattern[str]] = {
    "aggregate": re.compile(
        r"合計|平均|件数|総数|最大|最小|\bsum\b|\bavg\b|\bcount\b|\bmax\b|\bmin\b", re.IGNORECASE
    ),
    "grouping": re.compile(r"ごと|別に|グループ|分類|内訳|\bgroup\s+by\b|breakdown", re.IGNORECASE),
    "join_multi": re.compile(
        r"紐づ|関連|それぞれの|を跨|および|\bjoin\b|複数の表|テーブルを", re.IGNORECASE
    ),
    "ranking": re.compile(
        r"上位|トップ|ランキング|多い順|少ない順|\btop\b|\border\s+by\b|並べ", re.IGNORECASE
    ),
    "temporal_compare": re.compile(
        r"前年|前月|推移|比較|トレンド|増減|月次|日次|year[- ]over[- ]year", re.IGNORECASE
    ),
    "subquery": re.compile(
        r"より多い|より大きい|以上の平均|平均より|を超える|した上で|かつ", re.IGNORECASE
    ),
}


class QuestionClassifier(Protocol):
    """質問→domain ラベルの分類器(本番は埋め込み + 学習済モデル)。"""

    def predict(self, question: str, embedding: Sequence[float] | None) -> str:
        """domain ラベルを返す。"""
        ...


@dataclass(frozen=True)
class KeywordDomainClassifier:
    """決定論キーワード分類器(分類器未注入時の安全縮退)。

    ``rules`` は (domain, キーワード列)。最初に一致した domain を返し、未一致は ``default_domain``。
    """

    rules: tuple[tuple[str, tuple[str, ...]], ...] = ()
    default_domain: str = "default"

    def predict(self, question: str, embedding: Sequence[float] | None) -> str:
        text = (question or "").lower()
        for domain, keywords in self.rules:
            if any(kw.lower() in text for kw in keywords):
                return domain
        return self.default_domain


@dataclass(frozen=True)
class RouterDecision:
    """ルーティング結果。"""

    profile_selected: str | None
    generation_backend: Nl2SqlGenerationBackend
    complexity_score: int
    matched_signals: tuple[str, ...]
    used_classifier: bool
    reason: str


@dataclass(frozen=True)
class RouterProfileStatus:
    """1 ルーティングプロファイルの選択状態と説明。"""

    name: RouterProfile
    origin: str
    recommended_for: tuple[str, ...]
    selected: bool


@dataclass(frozen=True)
class RouterAdapterRuntimeSettings:
    """Router アダプターの非機密 runtime snapshot。"""

    profile: RouterProfile
    default_generation_backend: Nl2SqlGenerationBackend
    complexity_threshold: int
    profiles: tuple[RouterProfileStatus, ...] = field(default_factory=tuple)


_PROFILE_SPECS: dict[RouterProfile, dict[str, object]] = {
    "off": {"origin": "default", "recommended_for": ("単一ドメイン", "既定")},
    "classifier": {"origin": "embedding", "recommended_for": ("多ドメイン", "profile 自動選択")},
    "complexity_aware": {"origin": "heuristic", "recommended_for": ("コスト最適化", "単段↔多段")},
}


def normalize_router_profile(value: object) -> RouterProfile:
    """未知のルーティング名は既定 off へ寄せる。"""
    text = str(value or "").strip().lower()
    if text in _PROFILE_SPECS:
        return text
    return DEFAULT_ROUTER_PROFILE


def normalize_generation_backend(value: object) -> Nl2SqlGenerationBackend:
    """未知の生成バックエンド名は既定 select_ai_agent へ寄せる。"""
    text = str(value or "").strip().lower()
    if text in _GENERATION_BACKENDS:
        return text
    return DEFAULT_GENERATION_BACKEND


def complexity_signals(question: str) -> tuple[str, ...]:
    """質問に含まれる複雑度シグナルのカテゴリ名(昇順)を返す。"""
    text = question or ""
    matched = [name for name, pattern in _COMPLEXITY_SIGNALS.items() if pattern.search(text)]
    return tuple(sorted(matched))


def _default_backend(settings: Settings) -> Nl2SqlGenerationBackend:
    return normalize_generation_backend(
        getattr(settings, "nl2sql_generation_backend", DEFAULT_GENERATION_BACKEND)
    )


def _complexity_threshold(settings: Settings) -> int:
    return int(getattr(settings, "nl2sql_router_complexity_threshold", 2))


def route(
    settings: Settings,
    question: str,
    *,
    embedding: Sequence[float] | None = None,
    classifier: QuestionClassifier | None = None,
    domain_to_profile: Mapping[str, str] | None = None,
) -> RouterDecision:
    """Settings と質問からルーティング判断を返す(決定論)。

    - off: 固定 backend(設定既定)を使い profile 自動選択もしない。
    - classifier: 分類器で domain を予測し ``domain_to_profile`` で profile を選ぶ。
    - complexity_aware: 複雑度シグナル数 >= 閾値 なら select_ai_agent、未満なら select_ai。
      分類器が与えられていれば profile も同時に選ぶ。
    """
    profile = normalize_router_profile(
        getattr(settings, "nl2sql_router_profile", DEFAULT_ROUTER_PROFILE)
    )
    default_backend = _default_backend(settings)
    signals = complexity_signals(question)
    score = len(signals)

    if profile == "off":
        return RouterDecision(
            profile_selected=None,
            generation_backend=default_backend,
            complexity_score=score,
            matched_signals=signals,
            used_classifier=False,
            reason="router_off",
        )

    # 分類器による profile 自動選択(classifier / complexity_aware 共通)。
    selected_profile: str | None = None
    used_classifier = False
    if (
        profile in {"classifier", "complexity_aware"}
        and classifier is not None
        and domain_to_profile
    ):
        domain = classifier.predict(question, embedding)
        selected_profile = domain_to_profile.get(domain) or domain_to_profile.get("default")
        used_classifier = True

    if profile == "classifier":
        reason = "classifier_selected" if selected_profile else "classifier_unavailable"
        return RouterDecision(
            profile_selected=selected_profile,
            generation_backend=default_backend,
            complexity_score=score,
            matched_signals=signals,
            used_classifier=used_classifier,
            reason=reason,
        )

    # complexity_aware
    threshold = _complexity_threshold(settings)
    backend: Nl2SqlGenerationBackend = "select_ai_agent" if score >= threshold else "select_ai"
    return RouterDecision(
        profile_selected=selected_profile,
        generation_backend=backend,
        complexity_score=score,
        matched_signals=signals,
        used_classifier=used_classifier,
        reason=f"complexity_{'multi' if backend == 'select_ai_agent' else 'single'}_stage",
    )


def router_adapter_runtime_settings(settings: Settings) -> RouterAdapterRuntimeSettings:
    """Settings から Router アダプター readiness snapshot を作る。"""
    profile = normalize_router_profile(
        getattr(settings, "nl2sql_router_profile", DEFAULT_ROUTER_PROFILE)
    )
    statuses = tuple(
        RouterProfileStatus(
            name=name,
            origin=str(_PROFILE_SPECS[name]["origin"]),
            recommended_for=tuple(_PROFILE_SPECS[name]["recommended_for"]),  # type: ignore[arg-type]
            selected=name == profile,
        )
        for name in ROUTER_PROFILE_ORDER
    )
    return RouterAdapterRuntimeSettings(
        profile=profile,
        default_generation_backend=_default_backend(settings),
        complexity_threshold=_complexity_threshold(settings),
        profiles=statuses,
    )
