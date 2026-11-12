"""KB 単位の構築設定(per-knowledge-base build overrides)。

業界の RAG 製品(Dify / RAGFlow / FastGPT 等)に倣い、Parser / Chunking /
索引構築系の既定値を **ナレッジベース単位** で上書きできるようにする。
ただし確定スタック(OCI Enterprise AI / OCI Generative AI Cohere / Oracle AI Database)は
不変で、上書きは既存 preset の選択に限定する。

設計:

* KB が持つ正規の上書きは ``ingestion`` のみ。文書取込の瞬間に owning KB の設定で
  確定し(取込時スナップショット)、後から KB を変えても既存チャンクは作り直さない。
* 検索・回答の上書き(``query``)は KB では持たない。検索・回答方針は Search Answer Profile が正本
  (:class:`KnowledgeBaseQueryConfig` はプロファイルの ``query`` の型)。
* Embedding/Rerank(Cohere v4/1536)・DB・OCI・Object Storage はグローバル固定で
  KB 別にしない(ベクトル次元混在不可など物理制約)。
* 永続化は既存 ``rag_knowledge_bases.retrieval_config`` JSON カラムを再利用し、
  DDL 変更を避ける。保存形は :func:`dump_adapter_config` 参照。
* 検索時の解決順は request 明示 > Search Answer Profile > グローバル既定。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from rag_parser_core.image_purpose import ImagePurpose
from rag_parser_core.sheet_records import ExcelOptions
from rag_pipeline_core.chunking import FixedDelimiters

from app.config import (
    CHUNK_CHILD_TARGET_CHARS_MAX,
    CHUNK_CHILD_TARGET_CHARS_MIN,
    CHUNK_OVERLAP_MAX_CHARS,
    CHUNK_PARENT_MAX_CHILDREN_MAX,
    CHUNK_PARENT_MAX_CHILDREN_MIN,
    CHUNK_PARENT_MAX_PAGES_MAX,
    CHUNK_PARENT_MAX_PAGES_MIN,
    CHUNK_PARENT_TARGET_CHARS_MAX,
    CHUNK_PARENT_TARGET_CHARS_MIN,
    CHUNK_SIZE_MAX_CHARS,
    CHUNK_SIZE_MIN_CHARS,
    CHUNK_TABLE_CHILD_TARGET_CHARS_MAX,
    CHUNK_TABLE_CHILD_TARGET_CHARS_MIN,
    REMOVED_PARSER_ADAPTER_BACKENDS,
    ChunkingStrategy,
    GraphProfile,
    ParserAdapterBackend,
    PreprocessProfile,
    Settings,
    is_roadmap_parser_adapter_backend,
    normalize_legacy_chunking_strategy_value,
    normalize_removed_preprocess_profile,
    reject_roadmap_parser_adapter_backend,
)

logger = logging.getLogger(__name__)

ADAPTER_CONFIG_VERSION = 2

# 実体の抽出で名前・属性にする列名（文書レシピの選択肢。#1362）。
EntityColumnName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]

# KB の設定が上書きできる範囲。検索・回答の上書きは検索・回答プロファイルだけが持つ(#4784)。
AdapterConfigScope = Literal["ingestion"]

# KB 設定フィールド -> Settings フィールドのマッピング(allowlist)。
# ここに載っていない Settings フィールドは KB 単位で上書きできない。
_INGESTION_FIELD_MAP: dict[str, str] = {
    "preprocess_profile": "rag_preprocess_profile",
    # 前処理 excel_to_jsonl の選択肢(#1221。前処理が excel_to_jsonl のときだけ効く)。
    "excel_options": "rag_preprocess_excel_options",
    "parser_adapter_backend": "rag_parser_adapter_backend",
    "parser_docling_enabled": "rag_parser_docling_enabled",
    "parser_unstructured_enabled": "rag_parser_unstructured_enabled",
    "parser_dots_ocr_enabled": "rag_parser_dots_ocr_enabled",
    "parser_structured_records_jsonl_enabled": "rag_parser_structured_records_jsonl_enabled",
    "parser_xml_tree_json_enabled": "rag_parser_xml_tree_json_enabled",
    # 図・画像を AI で読み取る(Vision)。解析エンジンに関係なく効く(#497)。
    "vision_enabled": "rag_vision_enabled",
    "vision_image_purpose": "rag_vision_image_purpose",
    # 見出しの階層の推定(#2277)。原本全体の整理の後に抽出結果の見出しの階層を変える。
    "heading_level_inference_enabled": "rag_heading_level_inference_enabled",
    "chunking_strategy": "rag_chunking_strategy",
    "chunk_size": "rag_chunk_size",
    "chunk_overlap": "rag_chunk_overlap",
    "chunk_min_chars": "rag_chunk_min_chars",
    # 固定分割の分割符(優先度順。分割方式が fixed_delimiter のときだけ効く。#2457)。
    "chunk_delimiter": "rag_chunk_delimiter",
    # 親子階層（small-to-big）の分割パラメータ(分割方式が small_to_big のときだけ効く)。
    "chunk_child_target_chars": "rag_chunk_child_target_chars",
    "chunk_table_child_target_chars": "rag_chunk_table_child_target_chars",
    "chunk_parent_target_chars": "rag_chunk_parent_target_chars",
    "chunk_parent_max_pages": "rag_chunk_parent_max_pages",
    "chunk_parent_max_children": "rag_chunk_parent_max_children",
    # 取込側の高度軸(現状グローバルのみだった adapter を KB 上書き対象へ拡張)。
    # いずれも取込パイプラインが self._settings から読むため、KB 上書きが取込に効く。
    "graph_profile": "rag_graph_profile",
    # 実体の層（#1362。既定 ON。#1388）。索引の保存の後に実体と「実体と chunk の関連」を作る。
    "entity_index_enabled": "rag_entity_index_enabled",
    "entity_name_columns": "rag_entity_name_columns",
    "entity_attribute_columns": "rag_entity_attribute_columns",
    "field_extraction_enabled": "rag_field_extraction_enabled",
    "navigation_summary_enabled": "rag_navigation_summary_enabled",
    "auto_parse_after_preprocess_enabled": "rag_auto_parse_after_preprocess_enabled",
    "auto_chunk_after_extract_enabled": "rag_auto_chunk_after_extract_enabled",
    "auto_index_after_chunk_enabled": "rag_auto_index_after_chunk_enabled",
}
_QUERY_FIELD_MAP: dict[str, str] = {
    # rerank の後、起点に採る同じ文書の chunk の上限(#2167)。None は Settings の既定(5)。
    "max_anchors_per_document": "rag_max_anchors_per_document",
    # 業務ガイドの使用(#2398)。未指定・None は OFF(resolve_search_answer_profile_settings)。
    "support_guide_enabled": "rag_support_guide_enabled",
    "wiki_search_enabled": "rag_wiki_search_enabled",
}

# 外部 parser adapter backend -> その有効化 feature flag(Settings フィールド名)。
# KB が backend を明示選択したのに flag が無効だと取込が Enterprise AI へ fallback
# してしまうため、backend 選択を flag 有効化の意思表示として扱う。
_EXTERNAL_PARSER_BACKEND_FLAGS: dict[str, str] = {
    "docling": "rag_parser_docling_enabled",
    "unstructured": "rag_parser_unstructured_enabled",
    "structured_records_jsonl": "rag_parser_structured_records_jsonl_enabled",
    "xml_tree_json": "rag_parser_xml_tree_json_enabled",
    "dots_ocr": "rag_parser_dots_ocr_enabled",
}


class KbAdapterConfigError(ValueError):
    """KB 構築設定がグローバル設定と整合しないときに送出する。"""


# 削除した文書解析エンジン(#270)の有効化フラグ。保存済みの KB 構築設定・文書レシピ・
# 取込ジョブの snapshot に残っていても読めるよう、検証前に取り除く。
_REMOVED_PARSER_FLAG_FIELDS = frozenset(
    f"parser_{backend}_enabled" for backend in REMOVED_PARSER_ADAPTER_BACKENDS
)
# Vision を解析エンジンに依存しない ``vision_enabled`` へ統合した(#497)ときの旧 key。
# 読み込むときに ``vision_enabled`` へ移す。優先は新しい key の明示値 > Docling の Vision >
# 図表 VLM 要約。
_LEGACY_VISION_FIELDS: tuple[str, ...] = ("parser_docling_vision_enabled", "asset_summary_enabled")


def _migrate_legacy_vision_fields(data: dict[object, object]) -> dict[object, object]:
    """旧 key(Docling の Vision・図表 VLM 要約)を ``vision_enabled`` へ移し、旧 key は捨てる。"""
    if not any(key in data for key in _LEGACY_VISION_FIELDS):
        return data
    migrated = {key: value for key, value in data.items() if key not in _LEGACY_VISION_FIELDS}
    if migrated.get("vision_enabled") is None:
        for key in _LEGACY_VISION_FIELDS:
            value = data.get(key)
            if value is not None:
                migrated["vision_enabled"] = value
                break
    return migrated


def _clean_persisted_ingestion_config(data: object) -> object:
    """保存値・入力の取込設定から削除済みの項目を除き、旧値を読み替える(KnowledgeBaseIngestionConfig)。

    削除済みエンジン(#270)と準備中(ロードマップ)のエンジン(#3374)は「global 既定の文書解析エンジンを
    継承」(None)へ寄せる。準備中のエンジンを選ぶ保存(処理レシピの PUT)は、入力のモデル
    (``DocumentProcessingConfigInput``)が検証のエラーにする。
    """
    if not isinstance(data, dict):
        return data
    cleaned = {
        key: value
        for key, value in _migrate_legacy_vision_fields(data).items()
        if key not in _REMOVED_PARSER_FLAG_FIELDS
    }
    backend = cleaned.get("parser_adapter_backend")
    if isinstance(backend, str) and backend.strip().casefold() in REMOVED_PARSER_ADAPTER_BACKENDS:
        cleaned["parser_adapter_backend"] = None
    elif is_roadmap_parser_adapter_backend(backend):
        logger.warning(
            "保存済みの処理レシピの解析エンジン %s は準備中（ロードマップ）のため使わず、"
            "全体の既定を継承します。",
            backend,
        )
        cleaned["parser_adapter_backend"] = None
    # 削除したファイル準備の変換方式(#2042)は「変換なし」へ寄せる(ログに出す)。
    if "preprocess_profile" in cleaned:
        cleaned["preprocess_profile"] = normalize_removed_preprocess_profile(
            cleaned["preprocess_profile"]
        )
    return cleaned


def reject_roadmap_parser_backend_input(data: object) -> object:
    """処理レシピの保存の入力で、準備中(ロードマップ)の解析エンジンを検証のエラーにする(#3374)。"""
    if isinstance(data, Mapping):
        reject_roadmap_parser_adapter_backend(data.get("parser_adapter_backend"))
    return data


class KnowledgeBaseIngestionConfig(BaseModel):
    """取込時(Parser / Chunking)の KB 上書き。None はグローバル継承。"""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def drop_removed_parser_engines(cls, data: object) -> object:
        """削除済みエンジン(#270)の保存値を「global 既定の文書解析エンジンを継承」へ寄せる。

        保存値は KB の ``retrieval_config``、文書・レシピの ``processing_config``、取込ジョブの
        ``settings_overrides`` に JSON で残る。読み込みは全てこのモデルの検証を通るため、ここで
        正規化すれば取込・画面の両方が壊れず、次回保存時に旧値は消える。文書レシピの
        ``DocumentProcessingConfig`` は ``extra="forbid"`` なので、旧フラグも検証前に取り除く。
        Vision の旧 key(#497)も同じ理由でここで ``vision_enabled`` へ移す。
        Oracle 上の JSON を書き換える migration は持たない(値の置換だけで済み、再保存で消えるため)。
        """
        return _clean_persisted_ingestion_config(data)

    preprocess_profile: PreprocessProfile | None = None
    # 前処理 excel_to_jsonl の選択肢(#1221)。None はグローバル継承。
    excel_options: ExcelOptions | None = None
    parser_adapter_backend: ParserAdapterBackend | None = None
    parser_docling_enabled: bool | None = None
    parser_unstructured_enabled: bool | None = None
    parser_dots_ocr_enabled: bool | None = None
    parser_structured_records_jsonl_enabled: bool | None = None
    parser_xml_tree_json_enabled: bool | None = None
    vision_image_purpose: ImagePurpose | None = None
    vision_enabled: bool | None = None
    chunking_strategy: ChunkingStrategy | None = None
    chunk_size: int | None = Field(
        default=None,
        ge=CHUNK_SIZE_MIN_CHARS,
        le=CHUNK_SIZE_MAX_CHARS,
    )
    chunk_overlap: int | None = Field(default=None, ge=0, le=CHUNK_OVERLAP_MAX_CHARS)
    chunk_min_chars: int | None = Field(default=None, ge=0, le=2000)
    # 固定分割の分割符(escape 表現。#2457)。None はグローバル継承。
    chunk_delimiter: FixedDelimiters | None = None
    chunk_child_target_chars: int | None = Field(
        default=None, ge=CHUNK_CHILD_TARGET_CHARS_MIN, le=CHUNK_CHILD_TARGET_CHARS_MAX
    )
    chunk_table_child_target_chars: int | None = Field(
        default=None,
        ge=CHUNK_TABLE_CHILD_TARGET_CHARS_MIN,
        le=CHUNK_TABLE_CHILD_TARGET_CHARS_MAX,
    )
    chunk_parent_target_chars: int | None = Field(
        default=None, ge=CHUNK_PARENT_TARGET_CHARS_MIN, le=CHUNK_PARENT_TARGET_CHARS_MAX
    )
    chunk_parent_max_pages: int | None = Field(
        default=None, ge=CHUNK_PARENT_MAX_PAGES_MIN, le=CHUNK_PARENT_MAX_PAGES_MAX
    )
    chunk_parent_max_children: int | None = Field(
        default=None, ge=CHUNK_PARENT_MAX_CHILDREN_MIN, le=CHUNK_PARENT_MAX_CHILDREN_MAX
    )
    # 取込側の高度軸(KB 上書き対象へ拡張)。None はグローバル継承。
    graph_profile: GraphProfile | None = None
    # 実体の層（#1362）。None はグローバル継承（既定 ON。#1388）。列名は表の行の名前・属性にする列。
    entity_index_enabled: bool | None = None
    entity_name_columns: list[EntityColumnName] | None = Field(default=None, max_length=20)
    entity_attribute_columns: list[EntityColumnName] | None = Field(default=None, max_length=40)
    heading_level_inference_enabled: bool | None = None
    field_extraction_enabled: bool | None = None
    navigation_summary_enabled: bool | None = None
    auto_parse_after_preprocess_enabled: bool | None = None
    auto_chunk_after_extract_enabled: bool | None = None
    auto_index_after_chunk_enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_removed_chunking_values(cls, data: object) -> object:
        """削除した分割方式の保存値を読み替える(文書レシピ・KB の保存済み JSON)。

        親子階層(hierarchical_parent_child)は親子階層（small-to-big）として扱い、その専用値
        ``chunk_child_size`` は捨てる。次に保存すると新しい値だけが残る(#271)。
        """
        if not isinstance(data, Mapping):
            return data
        values = {key: value for key, value in data.items() if key != "chunk_child_size"}
        if "chunking_strategy" in values:
            values["chunking_strategy"] = normalize_legacy_chunking_strategy_value(
                values["chunking_strategy"]
            )
        return values


class KnowledgeBaseQueryConfig(BaseModel):
    """検索・回答プロファイルの query の上書き。

    検索・回答で実際に適用する欄だけを持つ(#4784)。ほかの検索方法は全体の設定だけで決める。
    定義に無い欄は検証エラーにする(保存済みの値は migration
    ``20261110_4784_search_answer_profile_retired_fields`` で消す)。
    """

    model_config = ConfigDict(extra="forbid")

    # rerank の後、起点(上位 top_k 件)に採る同じ文書の chunk の上限(#2167)。None は既定(5)、
    # 0 は上限なし。LLM は呼ばない。
    max_anchors_per_document: int | None = Field(default=None, ge=0, le=20)
    # 業務ガイドの使用(#2398)。未指定・null は OFF。既存プロファイルを自動的に有効へ変更しない。
    support_guide_enabled: bool | None = None
    # 未指定は無効。KB の背景構築の指定とは独立している。
    wiki_search_enabled: bool | None = None


class KnowledgeBaseAdapterConfig(BaseModel):
    """KB 単位の構築設定。"""

    model_config = ConfigDict(extra="ignore")

    version: int = ADAPTER_CONFIG_VERSION
    ingestion: KnowledgeBaseIngestionConfig = Field(default_factory=KnowledgeBaseIngestionConfig)

    def is_empty(self) -> bool:
        """KB 構築上書きが 1 件も無ければ True。"""
        return not self._ingestion_overrides()

    def _ingestion_overrides(self) -> dict[str, object]:
        """非 None の上書きを {KB フィールド名: 値} で返す。"""
        return {
            key: value for key, value in self.ingestion.model_dump().items() if value is not None
        }

    def settings_overrides(self) -> dict[str, object]:
        """上書きを {Settings フィールド名: 値} へ変換する。"""
        overrides = {
            _INGESTION_FIELD_MAP[key]: value
            for key, value in self._ingestion_overrides().items()
            if key in _INGESTION_FIELD_MAP
        }
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


def parse_adapter_config(
    raw: Mapping[str, object] | None,
) -> KnowledgeBaseAdapterConfig:
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
    """KB 構築設定を ``retrieval_config`` カラムへ保存する dict へ変換する。"""
    return {
        "version": ADAPTER_CONFIG_VERSION,
        "ingestion": config.ingestion.model_dump(mode="json"),
    }


def _validate_chunk_consistency(settings: Settings) -> None:
    """overlay 後の chunk パラメータ整合性を再検査する(Settings の起動時検証と同等)。"""
    if settings.rag_chunk_overlap >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_overlap は chunk_size より小さくしてください。")
    if settings.rag_chunk_min_chars >= settings.rag_chunk_size:
        raise KbAdapterConfigError("chunk_min_chars は chunk_size より小さくしてください。")


def resolve_effective_settings(
    global_settings: Settings,
    config: KnowledgeBaseAdapterConfig,
    *,
    scope: AdapterConfigScope,
) -> Settings:
    """グローバル設定へ KB の上書きを重ねた有効 Settings を返す。

    上書きが無ければ ``global_settings`` をそのまま返す。``model_copy`` は
    バリデータを再実行しないため、chunk 整合性を明示的に
    再検査し、矛盾があれば :class:`KbAdapterConfigError` を送出する。
    """
    overrides = config.settings_overrides()
    if not overrides:
        return global_settings
    merged = global_settings.model_copy(update=overrides)
    _validate_chunk_consistency(merged)
    return merged


def _resolved_field_value(
    section: KnowledgeBaseIngestionConfig,
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
    ``materialize`` には使わず **表示専用**。継承行に「実際に効く値」を出すために使う。
    """
    ingestion = {
        kb_field: _resolved_field_value(config.ingestion, kb_field, global_settings, settings_field)
        for kb_field, settings_field in _INGESTION_FIELD_MAP.items()
    }
    return KnowledgeBaseAdapterConfig(ingestion=KnowledgeBaseIngestionConfig(**ingestion))


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
    """Search Answer Profile の query 上書きを precedence 順(後勝ち)に重ねた Settings を返す。

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
    """Search Answer Profile query 設定を {Settings フィールド名: 値} へ変換する。"""
    values = {key: value for key, value in query.model_dump().items() if value is not None}
    return {
        settings_field: values[key]
        for key, settings_field in _QUERY_FIELD_MAP.items()
        if key in values
    }
