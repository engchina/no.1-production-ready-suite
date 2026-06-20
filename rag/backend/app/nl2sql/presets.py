"""NL2SQL パイプライン preset アダプターのレジストリ(決定論)。

router/guardrail/cache のような「挙動が大きい」アダプターとは別に、**設定で選ぶだけ**の
preset 群(schema_source / schema_linking / knowledge / clarify / generation / correction /
agentic / result / evaluation)を 1 つのレジストリで束ねる。各既定は現行(最小)挙動と一致する。

外部依存なし・決定論。実際の挙動反映はパイプライン各段が解決済み選択値を参照して行う
(本モジュールは「どの preset が選ばれているか」を一貫して提供することに専念する)。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class PresetOption:
    """1 preset 選択肢のメタ情報。"""

    name: str
    origin: str
    recommended_for: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class PipelineAdapter:
    """1 パイプラインアダプター(settings フィールドと選択肢群)。"""

    key: str
    settings_field: str
    label: str
    default: str
    options: tuple[PresetOption, ...]

    @property
    def env_key(self) -> str:
        return self.settings_field.upper()

    @property
    def option_names(self) -> tuple[str, ...]:
        return tuple(option.name for option in self.options)


@dataclass(frozen=True)
class PresetOptionStatus:
    """選択状態付きの preset 選択肢。"""

    name: str
    origin: str
    recommended_for: tuple[str, ...]
    summary: str
    selected: bool


@dataclass(frozen=True)
class PipelineAdapterRuntime:
    """1 アダプターの runtime snapshot。"""

    key: str
    settings_field: str
    label: str
    selected: str
    options: tuple[PresetOptionStatus, ...]


def _opt(name: str, origin: str, recommended_for: tuple[str, ...], summary: str) -> PresetOption:
    return PresetOption(name=name, origin=origin, recommended_for=recommended_for, summary=summary)


# パイプライン順。各 options[0] が既定(現行挙動)。
PIPELINE_ADAPTERS: tuple[PipelineAdapter, ...] = (
    PipelineAdapter(
        key="schema_source",
        settings_field="nl2sql_schema_source",
        label="スキーマ取込 (Schema Source)",
        default="full",
        options=(
            _opt("full", "default", ("既定", "全許可表"), "全許可表を object_list へ含める"),
            _opt("curated", "manual", ("精選",), "明示選択した表のみ"),
            _opt("sampled", "enriched", ("精度重視",), "M-Schema 風: 値例 + 説明 + 値索引"),
        ),
    ),
    PipelineAdapter(
        key="schema_linking",
        settings_field="nl2sql_schema_linking",
        label="スキーマリンク (Schema Linking)",
        default="enforce_all",
        options=(
            _opt("enforce_all", "default", ("既定",), "enforce_object_list=true で全許可表"),
            _opt("curated", "manual", ("精選",), "明示選択した範囲のみ"),
            _opt("auto_prune", "vector", ("大規模スキーマ",), "ベクトル多段で関連表/列のみ抽出"),
        ),
    ),
    PipelineAdapter(
        key="knowledge",
        settings_field="nl2sql_knowledge_profile",
        label="知識/例示 (Knowledge)",
        default="off",
        options=(
            _opt("off", "default", ("既定",), "知識注入なし"),
            _opt("glossary", "glossary", ("業務用語",), "用語集を注入"),
            _opt("few_shot", "vector", ("反復質問",), "類似 NL-SQL 例をベクトル検索で注入"),
            _opt("rag_trained", "rag", ("ドキュメント",), "Select AI RAG profile 併用"),
        ),
    ),
    PipelineAdapter(
        key="clarify",
        settings_field="nl2sql_clarify_policy",
        label="曖昧性解決 (Clarify)",
        default="off",
        options=(
            _opt("off", "default", ("既定",), "曖昧性チェックなし"),
            _opt("detect", "heuristic", ("警告",), "曖昧さを検出して警告"),
            _opt("interactive", "hitl", ("対話",), "確認質問で意図を確定してから生成"),
        ),
    ),
    PipelineAdapter(
        key="generation",
        settings_field="nl2sql_generation_profile",
        label="回答スタイル (Generation)",
        default="grounded_sql",
        options=(
            _opt("grounded_sql", "default", ("既定",), "純 SQL のみ"),
            _opt("sql_with_explanation", "explained", ("学習",), "SQL + 日本語解説"),
            _opt("narrated", "narrate", ("BI",), "結果を日本語要約"),
            _opt("structured_json", "structured", ("連携",), "構造化 JSON"),
            _opt("bilingual_ja_en", "bilingual", ("国際",), "日英併記"),
        ),
    ),
    PipelineAdapter(
        key="correction",
        settings_field="nl2sql_correction_profile",
        label="自己修正 (Self-Correction)",
        default="off",
        options=(
            _opt("off", "default", ("既定",), "修正なし"),
            _opt("retry_on_error", "retry", ("実行エラー",), "エラー文言を戻して再生成"),
            _opt("verified", "verified", ("本番",), "execute→検証→修正 + 逆翻訳突合"),
        ),
    ),
    PipelineAdapter(
        key="agentic",
        settings_field="nl2sql_agentic_profile",
        label="エージェント計画 (Agentic)",
        default="off",
        options=(
            _opt("off", "default", ("既定",), "単段"),
            _opt("decompose", "decompose", ("複合質問",), "sub-question 分解"),
            _opt("multi_hop", "multi_hop", ("多段",), "複数ステップ(上限あり)"),
        ),
    ),
    PipelineAdapter(
        key="result",
        settings_field="nl2sql_result_profile",
        label="結果整形 (Result)",
        default="table",
        options=(
            _opt("table", "default", ("既定",), "表形式"),
            _opt("narrate", "narrate", ("要約",), "日本語要約"),
            _opt("chart", "chart", ("可視化",), "チャート"),
            _opt("bilingual_ja_en", "bilingual", ("国際",), "日英併記"),
        ),
    ),
    PipelineAdapter(
        key="evaluation",
        settings_field="nl2sql_evaluation_suite",
        label="評価 (Evaluation)",
        default="request_only",
        options=(
            _opt("request_only", "default", ("既定",), "プリセット閾値なし"),
            _opt("execution_focused", "ci", ("実行精度",), "execution_accuracy 重視"),
            _opt("balanced", "ci", ("均衡",), "実行/一致/レイテンシ均衡"),
            _opt("strict_ci", "ci", ("厳格 CI",), "高い閾値で gate"),
            _opt("bird_like", "ci", ("BIRD 風",), "BIRD 風の実行精度 + 効率"),
        ),
    ),
)

_ADAPTERS_BY_KEY: dict[str, PipelineAdapter] = {
    adapter.key: adapter for adapter in PIPELINE_ADAPTERS
}


def adapter_keys() -> tuple[str, ...]:
    """パイプライン順のアダプターキー。"""
    return tuple(adapter.key for adapter in PIPELINE_ADAPTERS)


def get_adapter(key: str) -> PipelineAdapter | None:
    """キーからアダプター定義を取得する(未知は None)。"""
    return _ADAPTERS_BY_KEY.get(key)


def normalize_selection(key: str, value: object) -> str:
    """未知の選択値はそのアダプターの既定へ寄せる。"""
    adapter = _ADAPTERS_BY_KEY.get(key)
    if adapter is None:
        return ""
    text = str(value or "").strip().lower()
    return text if text in adapter.option_names else adapter.default


def resolve_selection(settings: Settings, key: str) -> str:
    """Settings から現在の選択値を解決する。"""
    adapter = _ADAPTERS_BY_KEY.get(key)
    if adapter is None:
        return ""
    return normalize_selection(key, getattr(settings, adapter.settings_field, adapter.default))


def adapter_runtime(settings: Settings, key: str) -> PipelineAdapterRuntime | None:
    """1 アダプターの runtime snapshot を作る(未知は None)。"""
    adapter = _ADAPTERS_BY_KEY.get(key)
    if adapter is None:
        return None
    selected = resolve_selection(settings, key)
    options = tuple(
        PresetOptionStatus(
            name=option.name,
            origin=option.origin,
            recommended_for=option.recommended_for,
            summary=option.summary,
            selected=option.name == selected,
        )
        for option in adapter.options
    )
    return PipelineAdapterRuntime(
        key=adapter.key,
        settings_field=adapter.settings_field,
        label=adapter.label,
        selected=selected,
        options=options,
    )


def pipeline_runtime(settings: Settings) -> tuple[PipelineAdapterRuntime, ...]:
    """全アダプターの runtime snapshot をパイプライン順で作る。"""
    runtimes = []
    for adapter in PIPELINE_ADAPTERS:
        runtime = adapter_runtime(settings, adapter.key)
        if runtime is not None:
            runtimes.append(runtime)
    return tuple(runtimes)
