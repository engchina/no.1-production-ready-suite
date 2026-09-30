"""派生情報レイヤー(項目抽出・ナビゲーション)の入力の指紋と「作り直しが必要」の判定(#550)。

レイヤー(``rag_artifact_layers``)を作ったときに、その結果を実際に変える入力だけを指紋として
保存し、画面の表示のたびに今の設定と比べる。設計の判断:

- **入れる入力は結果を変えるものだけ**: 項目の定義(``field_schema_hash``)、DocRAG の chunk
  metadata の契約(``docrag_chunk_contract``。``CHUNK_METADATA_SCHEMA_VERSION`` /
  ``SEARCH_TEXT_SCHEMA_VERSION`` / ``INQUIRY_CHUNK_METADATA_SCHEMA_VERSION`` /
  ``inquiry_profile_contract_hash``。rag_poc の ``schema_version`` による古い chunk の検出に
  当たる)、
  ナビゲーション要約の上限数(``navigation_summary_max_nodes``)。モデルの切り替えは入れない
  (すべての文書が一斉に「作り直しが必要」になり、定義の変更と区別できなくなるため)。
  レイヤーの有効・無効や関係情報の方式は、レイヤー ID(``variant_keys``)に入っているため、
  変わると別のレイヤーとして扱われ、ここでは比べない。
- **作ったときに実際に使った値を記録する**: 項目の定義とナビ要約の上限数は、抽出の工程で
  抽出結果の ``parser_artifacts`` に刻み(``*_ARTIFACT_KEY``)、レイヤーを記録するときはそれを読む。
  レシピの再実行は抽出を使い回して分割・索引だけをやり直せるため、記録の時点の設定を使うと、
  古い定義で抽出したままの項目を「最新」と誤って記録してしまう。
- **状態(status)は増やさない**: 「作り直しが必要」は今の設定との比較で決まる派生の値で、
  設定を元に戻せば消える。保存する状態(作成の状態 5 種)とは別の印として返す。
- **指紋の無い行は「不明」**: この変更より前に作ったレイヤーや、刻みの無い抽出結果から作った
  レイヤーは、どの定義で作ったか分からない。誤警告を避けるため「作り直しが必要」を出さない。
  記録された入力のうち、今のコードが知らない入力(削除した入力)も比べない。
- **自動では作り直さない**: 判定だけを返す。作り直しは既存のレシピの「再処理」の導線で行う。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from functools import lru_cache

from app.config import Settings
from app.rag.docrag_chunking import DOCRAG_CHUNKING_STRATEGY
from app.rag.extraction_field_adapter import FieldDefinition, load_field_schema

# 指紋の入力の名前(API の ``rebuild_inputs`` にもそのまま返し、画面が i18n で表示名にする)。
FIELD_SCHEMA_INPUT = "field_schema_hash"
DOCRAG_CHUNK_CONTRACT_INPUT = "docrag_chunk_contract"
NAVIGATION_SUMMARY_MAX_NODES_INPUT = "navigation_summary_max_nodes"

# 抽出の工程で抽出結果の parser_artifacts に刻む key(作ったときに実際に使った値)。
FIELD_SCHEMA_HASH_ARTIFACT_KEY = "layer_input_field_schema_hash"
NAVIGATION_SUMMARY_MAX_NODES_ARTIFACT_KEY = "layer_input_navigation_summary_max_nodes"


def current_field_definitions() -> list[FieldDefinition]:
    """今の項目の定義。指紋の「今の値」はここだけから読む。

    #548 で項目の定義を KB ごとに DB へ移したら、この関数だけを差し替える(抽出の工程の刻みは
    実際に使った定義から作るため、そちらは差し替え不要)。
    """
    return list(load_field_schema().fields)


def field_schema_hash(fields: Sequence[FieldDefinition]) -> str:
    """項目の定義の hash。並び順も抽出の指示に入るため、順序どおりに hash する。"""
    payload = [field.model_dump(mode="json") for field in fields]
    return _sha256_json(payload)


@lru_cache(maxsize=1)
def docrag_chunk_contract_hash() -> str:
    """DocRAG の chunk metadata の契約の hash(docrag_core の chunk run の契約と同じ入力)。"""
    from docrag.chunking.constants import (
        CHUNK_METADATA_SCHEMA_VERSION,
        SEARCH_TEXT_SCHEMA_VERSION,
    )
    from docrag.retrieval.inquiry_conditions import (
        INQUIRY_CHUNK_METADATA_SCHEMA_VERSION,
        inquiry_profile_contract_hash,
    )

    return _sha256_json(
        {
            "chunk_metadata_schema_version": CHUNK_METADATA_SCHEMA_VERSION,
            "search_text_schema_version": SEARCH_TEXT_SCHEMA_VERSION,
            "inquiry_chunk_metadata_schema_version": INQUIRY_CHUNK_METADATA_SCHEMA_VERSION,
            "inquiry_profile_contract_hash": inquiry_profile_contract_hash(),
        }
    )


def recorded_layer_fingerprint(
    layer: str,
    extraction: Mapping[str, object] | None,
    settings: Settings,
) -> dict[str, object] | None:
    """レイヤーを記録するときに保存する指紋。入力が 1 つも分からなければ None(不明)。"""
    raw_artifacts = extraction.get("parser_artifacts") if extraction else None
    artifacts = raw_artifacts if isinstance(raw_artifacts, Mapping) else {}
    fingerprint: dict[str, object] = {}
    if layer == "metadata":
        schema_hash = artifacts.get(FIELD_SCHEMA_HASH_ARTIFACT_KEY)
        if isinstance(schema_hash, str) and schema_hash:
            fingerprint[FIELD_SCHEMA_INPUT] = schema_hash
        # chunk の metadata は分割の工程で作り、索引まで同じ job の中で進むため、記録の時点の
        # コードの契約を使う。DocRAG 以外の分割方式は docrag の metadata を持たない。
        if settings.rag_chunking_strategy == DOCRAG_CHUNKING_STRATEGY:
            fingerprint[DOCRAG_CHUNK_CONTRACT_INPUT] = docrag_chunk_contract_hash()
    elif layer == "navigation":
        max_nodes = artifacts.get(NAVIGATION_SUMMARY_MAX_NODES_ARTIFACT_KEY)
        if isinstance(max_nodes, int) and not isinstance(max_nodes, bool):
            fingerprint[NAVIGATION_SUMMARY_MAX_NODES_INPUT] = max_nodes
    # 関係情報(graph)は実体化しない(計画だけ)ため、比べる入力を持たない。
    return fingerprint or None


def current_layer_inputs(settings: Settings) -> dict[str, object]:
    """今の設定での各入力の値(記録された指紋と同じ名前)。"""
    return {
        FIELD_SCHEMA_INPUT: field_schema_hash(current_field_definitions()),
        DOCRAG_CHUNK_CONTRACT_INPUT: docrag_chunk_contract_hash(),
        NAVIGATION_SUMMARY_MAX_NODES_INPUT: settings.rag_navigation_summary_max_nodes,
    }


def changed_layer_inputs(
    recorded: object,
    current: Mapping[str, object],
) -> list[str]:
    """記録された指紋のうち、今の値と違う入力の名前を返す。空なら作り直し不要(または不明)。"""
    if not isinstance(recorded, Mapping) or not recorded:
        return []
    return sorted(
        str(name) for name, value in recorded.items() if name in current and current[name] != value
    )


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
