"""KB 単位の構築設定(per-knowledge-base build overrides)。

業界の RAG 製品(Dify / RAGFlow / FastGPT 等)に倣い、Parser / Chunking /
索引構築系の既定値を **ナレッジベース単位** で上書きできるようにする。
ただし確定スタック(OCI Enterprise AI / OCI Generative AI Cohere / Oracle 26ai)は
不変で、上書きは既存 preset の選択に限定する。

設計:

* KB が持つ正規の上書きは ``ingestion`` のみ。文書取込の瞬間に owning KB の設定で
  確定し(取込時スナップショット)、後から KB を変えても既存チャンクは作り直さない。
* ``query`` は V1 の互換読み取り用 legacy フィールドとして受け取るが、KB から runtime
  settings へは反映しない。検索・回答方針は Business View が正本。
* Embedding/Rerank(Cohere v4/1536)・DB・OCI・Object Storage はグローバル固定で
  KB 別にしない(ベクトル次元混在不可など物理制約)。
* 永続化は既存 ``rag_knowledge_bases.retrieval_config`` JSON カラムを再利用し、
  DDL 変更を避ける。保存形は :func:`dump_adapter_config` 参照。
* 検索時の解決順は request 明示 > Business View > グローバル既定。KB query 設定は使わない。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import (
    ChunkingStrategy,
    GenerationProfile,
    GraphProfile,
    GuardrailPolicyName,
    ParserAdapterBackend,
    PostRetrievalPipeline,
    PreprocessProfile,
    RetrievalStrategy,
    Settings,
    VectorIndexProfile,
)
from app.config import (
    EvaluationSuite as EvaluationSuiteName,
)

logger = logging.getLogger(__name__)

ADAPTER_CONFIG_VERSION = 2

AdapterConfigScope = Literal["ingestion", "query"]

# KB 設定フィールド -> Settings フィールドのマッピング(scope ごとの allowlist)。
# ここに載っていない Settings フィールドは KB 単位で上書きできない。
_INGESTION_FIELD_MAP: dict[str, str] = {
    "preprocess_profile": "rag_preprocess_profile",
    "parser_adapter_backend": "rag_parser_adapter_backend",
    "parser_docling_enabled": "rag_parser_docling_enabled",
    "parser_marker_enabled": "rag_parser_marker_enabled",
    "parser_unstructured_enabled": "rag_parser_unstructured_enabled",
    "parser_unlimited_ocr_enabled": "rag_parser_unlimited_ocr_enabled",
    "parser_mineru_enabled": "rag_parser_mineru_enabled",
    "parser_dots_ocr_enabled": "rag_parser_dots_ocr_enabled",
    "parser_glm_ocr_enabled": "rag_parser_glm_ocr_enabled",
    "chunking_strategy": "rag_chunking_strategy",
    "chunk_size": "rag_chunk_size",
    "chunk_overlap": "rag_chunk_overlap",
    "chunk_child_size": "rag_chunk_child_size",
    "chunk_sentence_window_size": "rag_chunk_sentence_window_size",
    "chunk_min_chars": "rag_chunk_min_chars",
    # 取込側の高度軸(現状グローバルのみだった adapter を KB 上書き対象へ拡張)。
    # いずれも取込パイプラインが self._settings から読むため、KB 上書きが取込に効く。
    "graph_profile": "rag_graph_profile",
    "field_extraction_enabled": "rag_field_extraction_enabled",
    "asset_summary_enabled": "rag_asset_summary_enabled",
    "navigation_summary_enabled": "rag_navigation_summary_enabled",
    "auto_chunk_after_extract_enabled": "rag_auto_chunk_after_extract_enabled",
    "auto_index_after_chunk_enabled": "rag_auto_index_after_chunk_enabled",
}
_QUERY_FIELD_MAP: dict[str, str] = {
    "retrieval_strategy": "rag_retrieval_strategy",
    "post_retrieval_pipeline": "rag_post_retrieval_pipeline",
    "generation_profile": "rag_generation_profile",
    "guardrail_policy": "rag_guardrail_policy",
    "vector_index_profile": "rag_vector_index_profile",
    "evaluation_suite": "rag_evaluation_suite",
}

# 外部 parser adapter backend -> その有効化 feature flag(Settings フィールド名)。
# KB が backend を明示選択したのに flag が無効だと取込が Enterprise AI へ fallback
# してしまうため、backend 選択を flag 有効化の意思表示として扱う。
_EXTERNAL_PARSER_BACKEND_FLAGS: dict[str, str] = {
    "docling": "rag_parser_docling_enabled",
    "marker": "rag_parser_marker_enabled",
    "unstructured": "rag_parser_unstructured_enabled",
    "unlimited_ocr": "rag_parser_unlimited_ocr_enabled",
    "mineru": "rag_parser_mineru_enabled",
    "dots_ocr": "rag_parser_dots_ocr_enabled",
    "glm_ocr": "rag_parser_glm_ocr_enabled",
}


class KbAdapterConfigError(ValueError):
    """KB 構築設定がグローバル設定と整合しないときに送出する。"""


class KnowledgeBaseIngestionConfig(BaseModel):
    """取込時(Parser / Chunking)の KB 上書き。None はグローバル継承。"""

    model_config = ConfigDict(extra="ignore")

    preprocess_profile: PreprocessProfile | None = None
    parser_adapter_backend: ParserAdapterBackend | None = None
    parser_docling_enabled: bool | None = None
    parser_marker_enabled: bool | None = None
    parser_unstructured_enabled: bool | None = None
    parser_unlimited_ocr_enabled: bool | None = None
    parser_mineru_enabled: bool | None = None
    parser_dots_ocr_enabled: bool | None = None
    parser_glm_ocr_enabled: bool | None = None
    chunking_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(default=None, ge=200, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    chunk_child_size: int | None = Field(default=None, ge=80, le=4000)
    chunk_sentence_window_size: int | None = Field(default=None, ge=1, le=20)
    chunk_min_chars: int | None = Field(default=None, ge=0, le=2000)
    # 取込側の高度軸(KB 上書き対象へ拡張)。None はグローバル継承。
    graph_profile: GraphProfile | None = None
    field_extraction_enabled: bool | None = None
    asset_summary_enabled: bool | None = None
    navigation_summary_enabled: bool | None = None
    auto_chunk_after_extract_enabled: bool | None = None
    auto_index_after_chunk_enabled: bool | None = None


class KnowledgeBaseQueryConfig(BaseModel):
    """検索・回答時の上書き。

    Business View の query 設定として使う。KB 内に残る同形の値は legacy として読み取りは
    できるが、KB から検索 runtime へは反映しない。
    """

    model_config = ConfigDict(extra="ignore")

    retrieval_strategy: RetrievalStrategy | None = None
    post_retrieval_pipeline: PostRetrievalPipeline | None = None
    generation_profile: GenerationProfile | None = None
    guardrail_policy: GuardrailPolicyName | None = None
    vector_index_profile: VectorIndexProfile | None = None
    evaluation_suite: EvaluationSuiteName | None = None


class KnowledgeBaseAdapterConfig(BaseModel):
    """KB 単位の構築設定。query は legacy 互換読み取り用。"""

    model_config = ConfigDict(extra="ignore")

    version: int = ADAPTER_CONFIG_VERSION
    ingestion: KnowledgeBaseIngestionConfig = Field(default_factory=KnowledgeBaseIngestionConfig)
    query: KnowledgeBaseQueryConfig = Field(default_factory=KnowledgeBaseQueryConfig)

    def is_empty(self) -> bool:
        """正規の KB 構築上書きが 1 件も無ければ True。legacy query は判定に含めない。"""
        return not self._scope_overrides("ingestion")

    def _scope_overrides(self, scope: AdapterConfigScope) -> dict[str, object]:
        """scope の非 None 上書きを {KB フィールド名: 値} で返す。"""
        section = self.ingestion if scope == "ingestion" else self.query
        return {key: value for key, value in section.model_dump().items() if value is not None}

    def settings_overrides(self, scope: AdapterConfigScope) -> dict[str, object]:
        """scope の上書きを {Settings フィールド名: 値} へ変換する。"""
        if scope == "query":
            # KB query は legacy。検索・回答方針は Business View が正本なので反映しない。
            return {}
        field_map = _INGESTION_FIELD_MAP if scope == "ingestion" else _QUERY_FIELD_MAP
        overrides = {
            field_map[key]: value
            for key, value in self._scope_overrides(scope).items()
            if key in field_map
        }
        if scope == "ingestion":
            _ensure_external_parser_backend_enabled(overrides)
        return overrides


def _ensure_external_parser_backend_enabled(overrides: dict[str, object]) -> None:
    """外部 parser backend を選択したら対応 feature flag も有効化する。

    KB 設定 UI は backend だけを選ばせ、feature flag は別に持たない。グローバルの
    flag 既定は無効なので、backend 選択だけでは取込時に Enterprise AI へ fallback
    してしまう。backend 選択を「その adapter を使いたい」という明示の意思として扱い、
    KB が flag を明示的に False に上書きしていない限り True を注入する。
    パッケージ未導入時は parser registry 側が安全に fallback する。
    """
    backend = overrides.get("rag_parser_adapter_backend")
    if not isinstance(backend, str):
        return
    flag_field = _EXTERNAL_PARSER_BACKEND_FLAGS.get(backend.strip().casefold())
    if flag_field is None:
        return
    overrides.setdefault(flag_field, True)


def parse_adapter_config(raw: Mapping[str, object] | None) -> KnowledgeBaseAdapterConfig:
    """``retrieval_config`` JSON から KB 構築設定を寛容に復元する。

    旧来の free-form ``retrieval_config`` や未知キーは ``extra=ignore`` で捨て、
    壊れた値があっても空設定へ縮退して取込/検索を止めない。
    """
    if not raw:
        return KnowledgeBaseAdapterConfig()
    try:
        return KnowledgeBaseAdapterConfig.model_validate(dict(raw))
    except Exception:  # noqa: BLE001 - 壊れた永続値は空設定へ縮退する
        logger.warning("KB 構築設定の復元に失敗したため空設定へ縮退します。", exc_info=True)
        return KnowledgeBaseAdapterConfig()


def dump_adapter_config(config: KnowledgeBaseAdapterConfig) -> dict[str, object]:
    """KB 構築設定を ``retrieval_config`` カラムへ保存する dict へ変換する。

    V2 では ``query`` を保存しない。既存 JSON に残る query は parse できるが、次回保存で
    自然に落とす。
    """
    return {
        "version": ADAPTER_CONFIG_VERSION,
        "ingestion": config.ingestion.model_dump(mode="json"),
    }


def _validate_chunk_consistency(settings: Settings) -> None:
    """overlay 後の chunk パラメータ整合性を再検査する(Settings の起動時検証と同等)。"""
    if settings.rag_chunk_overlap >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_overlap は chunk_size より小さくしてください。")
    if settings.rag_chunk_child_size >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_child_size は chunk_size より小さくしてください。")
    if settings.rag_chunk_min_chars >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_min_chars は chunk_size より小さくしてください。")


def resolve_effective_settings(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
    *,
    scope: AdapterConfigScope,
) -> Settings:
    """グローバル設定へ KB の scope 上書きを重ねた有効 Settings を返す。

    上書きが無ければ ``global_settings`` をそのまま返す。``model_copy`` は
    バリデータを再実行しないため、ingestion scope では chunk 整合性を明示的に
    再検査し、矛盾があれば :class:`KbAdapterConfigError` を送出する。
    """
    if scope == "query":
        return global_settings
    overrides = config.settings_overrides(scope)
    if not overrides:
        return global_settings
    merged = global_settings.model_copy(update=overrides)
    if scope == "ingestion":
        _validate_chunk_consistency(merged)
    return merged


def _resolved_field_value(
    section: KnowledgeBaseIngestionConfig | KnowledgeBaseQueryConfig,
    kb_field: str,
    global_settings: Settings,
    settings_field: str,
) -> object:
    """override があればその値、無ければグローバル設定の対応値を返す。"""
    override = getattr(section, kb_field, None)
    if override is not None:
        return override
    return getattr(global_settings, settings_field)


def resolve_effective_adapter_config(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
) -> KnowledgeBaseAdapterConfig:
    """KB 構築上書きをグローバル既定で埋めた「解決済み」設定を返す(UI 表示用)。

    ingestion 各フィールドは override があればその値、無ければグローバル設定の対応値で埋める。
    query は KB では legacy ignored のため空で返す。
    ``materialize`` には使わず **表示専用**。継承行に「実際に効く値」を出すために使う。
    """
    ingestion = {
        kb_field: _resolved_field_value(config.ingestion, kb_field, global_settings, settings_field)
        for kb_field, settings_field in _INGESTION_FIELD_MAP.items()
    }
    return KnowledgeBaseAdapterConfig(
        ingestion=KnowledgeBaseIngestionConfig(**ingestion),
        query=KnowledgeBaseQueryConfig(),
    )


def apply_adapter_config_or_global(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
    *,
    scope: AdapterConfigScope,
) -> tuple[Settings, bool]:
    """有効 Settings と「上書きが適用されたか」を返す堅牢版。

    KB 設定がグローバルと矛盾する場合はグローバルへ縮退し、取込/検索を止めない。
    戻り値 2 番目は上書きが実際に効いたかどうか。
    """
    try:
        merged = resolve_effective_settings(global_settings, config, scope=scope)
    except KbAdapterConfigError:
        logger.warning(
            "KB 構築設定(%s)がグローバル設定と矛盾するためグローバルへ縮退します。",
            scope,
            exc_info=True,
        )
        return global_settings, False
    return merged, merged is not global_settings


def compose_query_settings(
    global_settings: Settings,
    overlays: Sequence[KnowledgeBaseQueryConfig],
) -> tuple[Settings, bool]:
    """Business View の query 上書きを precedence 順(後勝ち)に重ねた Settings を返す。

    各 overlay の非 None フィールドだけが上位を上書きする(per-field merge)。解決順
    呼び出し側が ``overlays`` を **低優先 → 高優先** の順で渡すことで表現する
    (後の overlay が前を上書き)。

    1 件でも有効な上書きがあれば 2 番目は True。
    """
    merged_overrides: dict[str, object] = {}
    for overlay in overlays:
        # 後の overlay(高優先)が前を上書きする per-field merge。
        merged_overrides.update(query_settings_overrides(overlay))
    if not merged_overrides:
        return global_settings, False
    return global_settings.model_copy(update=merged_overrides), True


def query_settings_overrides(query: KnowledgeBaseQueryConfig) -> dict[str, object]:
    """Business View query 設定を {Settings フィールド名: 値} へ変換する。"""
    values = {key: value for key, value in query.model_dump().items() if value is not None}
    return {
        settings_field: values[key]
        for key, settings_field in _QUERY_FIELD_MAP.items()
        if key in values
    }
