"""variant(取込レシピ)の決定論キー計算 — 層別 materialization の基盤。

設計判断(multi-recipe-variants-decision)の実装基盤。「重複は共有・差分は複製」を
**byte 単位の chunk 比較ではなく、artifact 層を「効く軸だけ」で hash** して実現する。
各層は影響する取込軸だけをキーに含めるため、下流軸だけ異なる variant は上流層
(chunk_set / embedding)を共有でき、無駄な複製を避けられる。

層の依存:
    chunk_set (preprocess/parser/chunking) ← embedding は chunk text 従属で自動共有
        ├─ metadata 層 (+ field_extraction)
        ├─ graph 層    (+ graph_profile)
        └─ nav 層      (+ navigation_summary / raptor)

例:
    KB1{chunk1000, graph off} と KB2{chunk1000, graph entities}
        → chunk_set_id 一致(chunk text + embedding を共有)、graph 層だけ別。
    KB1{chunk1000} と KB2{chunk2000}
        → chunk_set_id が別(chunk 境界が違うので差分複製が正しい)。

すべて決定論(canonical JSON + SHA1)で、CI で実 Oracle なしに検証できる。実際の
materialize / 永続化 / refcount / GC は別モジュール(要 DDL・実 Oracle 検証)で行う。
"""

from __future__ import annotations

import hashlib
import json

from app.config import Settings

# キー算法の版。算法やフィールド構成を変えるときに上げて、旧キーと衝突させない。
KEY_VERSION = "v1"

# 1 文書あたりの抽出(preprocess×parser 組合せ)上限。組合せ暴発の安全弁。
MAX_EXTRACTIONS_PER_DOCUMENT = 8

# 抽出(StructuredExtraction)を決める取込軸。preprocess/parser が同じなら抽出を共有する。
# chunk_set の親キー: chunk_set_id はこの軸 + chunking 軸で、chunk_set は自分の親抽出を計算できる。
_EXTRACTION_FIELDS: tuple[str, ...] = (
    "rag_preprocess_profile",
    "rag_parser_adapter_backend",
)

# chunk text(と、それに従属する embedding)を決める取込軸。
# これらが同じなら chunk 集合は同一とみなして共有する。
_CHUNK_SET_FIELDS: tuple[str, ...] = (
    "rag_preprocess_profile",
    "rag_parser_adapter_backend",
    "rag_chunking_strategy",
    "rag_chunk_size",
    "rag_chunk_overlap",
    "rag_chunk_child_size",
    "rag_chunk_sentence_window_size",
    "rag_chunk_min_chars",
)

# 各派生層が「追加で」依存する軸(chunk_set_id に重ねて hash する)。
_METADATA_FIELDS: tuple[str, ...] = ("rag_field_extraction_enabled",)
_GRAPH_FIELDS: tuple[str, ...] = ("rag_graph_profile",)
_NAV_FIELDS: tuple[str, ...] = ("rag_navigation_summary_enabled", "rag_raptor_enabled")

_HASH_HEX_LEN = 16


def _digest(prefix: str, payload: dict[str, object]) -> str:
    """canonical JSON(キー順非依存)から決定論ハッシュ ID を作る。"""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    # 暗号用途ではなく決定論的 ID 生成のための SHA1(衝突耐性のみ必要)。
    digest = hashlib.sha1(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[
        :_HASH_HEX_LEN
    ]
    return f"{prefix}_{digest}"


def _fields(settings: Settings, names: tuple[str, ...]) -> dict[str, object]:
    """Settings から対象フィールド値を取り出す(欠落は None)。"""
    return {name: getattr(settings, name, None) for name in names}


def compute_extraction_id(source_sha256: str, settings: Settings) -> str:
    """抽出(StructuredExtraction)層の決定論 ID。

    同一原本 + 同一の前処理/Parser なら同じ抽出 = 共有対象。chunk_set_id の親キーで、
    chunk_set は自分の設定(preprocess/parser)から親 extraction_id を計算できる。
    """
    payload: dict[str, object] = {
        "v": KEY_VERSION,
        "src": source_sha256,
        **_fields(settings, _EXTRACTION_FIELDS),
    }
    return _digest("ex", payload)


def compute_chunk_set_id(source_sha256: str, settings: Settings) -> str:
    """chunk 集合(text + embedding 層)の決定論 ID。

    同一原本 + 同一の前処理/Parser/Chunking なら同じ ID = 共有対象。
    """
    payload: dict[str, object] = {
        "v": KEY_VERSION,
        "src": source_sha256,
        **_fields(settings, _CHUNK_SET_FIELDS),
    }
    return _digest("cs", payload)


def compute_metadata_layer_id(chunk_set_id: str, settings: Settings) -> str:
    """メタデータ/項目抽出層の ID(chunk_set に field_extraction を重ねる)。"""
    return _digest(
        "md", {"v": KEY_VERSION, "cs": chunk_set_id, **_fields(settings, _METADATA_FIELDS)}
    )


def compute_graph_layer_id(chunk_set_id: str, settings: Settings) -> str:
    """GraphRAG 層の ID(chunk_set に graph_profile を重ねる)。"""
    return _digest("gr", {"v": KEY_VERSION, "cs": chunk_set_id, **_fields(settings, _GRAPH_FIELDS)})


def compute_nav_layer_id(chunk_set_id: str, settings: Settings) -> str:
    """ナビゲーション/RAPTOR 層の ID(chunk_set に nav 軸を重ねる)。"""
    return _digest("nv", {"v": KEY_VERSION, "cs": chunk_set_id, **_fields(settings, _NAV_FIELDS)})


def compute_layer_ids(source_sha256: str, settings: Settings) -> dict[str, str]:
    """1 取込レシピの全層 ID をまとめて返す(extraction → chunk_set → 派生層)。"""
    chunk_set_id = compute_chunk_set_id(source_sha256, settings)
    return {
        "extraction_id": compute_extraction_id(source_sha256, settings),
        "chunk_set_id": chunk_set_id,
        "metadata_layer_id": compute_metadata_layer_id(chunk_set_id, settings),
        "graph_layer_id": compute_graph_layer_id(chunk_set_id, settings),
        "nav_layer_id": compute_nav_layer_id(chunk_set_id, settings),
    }
