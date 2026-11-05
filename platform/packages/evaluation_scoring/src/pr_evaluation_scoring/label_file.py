"""人手の判定ファイルの日本語表記。判定・ID・元本文を変えず、入出力境界だけで変換する。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

FORMAT_NAME = "判定の記入データ"
FORMAT_VERSION = 1
TOP_FIELDS = {
    "rubric_id": "評価基準",
    "description": "説明",
    "provenance": "管理用情報",
    "items": "項目",
}
ITEM_FIELDS = {
    "id": "項目ID",
    "split": "区分",
    "question": "質問",
    "answer": "回答",
    "standard_answer": "標準回答",
    "evidence": "根拠",
    "labels": "判定",
    "labeler": "記入者ID",
    "note": "メモ",
    "origin": "作成元",
    "perturbation": "変更情報",
    "error_types": "誤りの型",
    "annotator_labels": "記入者別の判定",
}
EVIDENCE_FIELDS = {"id": "根拠ID", "text": "原文"}
PERTURBATION_FIELDS = {"kind": "変更の種類", "base_item_id": "元の項目ID", "description": "説明"}
ASPECTS = {"requirement_coverage": "網羅", "answer_correctness": "正確性", "claim_support": "根拠"}
LABELS = {"pass": "合格", "fail": "不合格", "cannot_judge": "判定できない"}
SPLITS = {"dev": "調整用データ", "holdout": "検証用データ"}
ORIGINS = {"human": "人手", "synthetic": "合成"}
RUBRICS = {"answer_quality": "回答品質"}
ERROR_TYPES = {
    "attribute_swap": "取り違え",
    "general_rule_overreach": "一般の規則の当てはめすぎ",
    "step_order": "手順の順序",
    "scope_expansion": "段階・単位の拡大",
    "unsupported_claim": "根拠に無い主張",
    "partial_omission": "論点の不足",
    "correct_paraphrase": "正しい言い換え",
    "correct_refusal": "正しい拒答・確認",
    "partial_answer": "正しい部分回答",
}
PERTURBATIONS = {
    "swap_values": "数値の入れ替え",
    "drop_condition": "条件の欠落",
    "add_unsupported_claim": "根拠に無い断定",
    "reverse_steps": "手順の逆順",
}


def _mapping(names: dict[str, str], encode: bool) -> dict[str, str]:
    return names if encode else {alias: key for key, alias in names.items()}


def _fields(value: Any, names: dict[str, str], encode: bool) -> Any:
    if not isinstance(value, Mapping):
        return value
    table = _mapping(names, encode)
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("記入データの項目名は文字列で指定してください。")
        if not encode and key in names:
            raise ValueError("日本語形式の項目名に英語形式が混在しています。")
        if encode and key in names.values() and key not in names:
            raise ValueError("日本語の表示名と識別子が衝突しています。")
        target = table.get(key, key)
        if target in result:
            raise ValueError("記入データに同じ項目の重複があります。")
        result[target] = item
    return result


def _value(value: Any, names: dict[str, str], encode: bool) -> Any:
    return _mapping(names, encode).get(value, value) if isinstance(value, str) else value


def _labels(value: Any, encode: bool) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {
        key: _value(label, LABELS, encode) for key, label in _fields(value, ASPECTS, encode).items()
    }


def _item(value: Any, encode: bool) -> Any:
    raw = value if encode else _fields(value, ITEM_FIELDS, False)
    if not isinstance(raw, Mapping):
        return raw
    result = dict(raw)
    for field, names in (("split", SPLITS), ("origin", ORIGINS)):
        if field in raw:
            result[field] = _value(raw[field], names, encode)
    if isinstance(raw.get("evidence"), list):
        result["evidence"] = [_fields(row, EVIDENCE_FIELDS, encode) for row in raw["evidence"]]
    if "labels" in raw:
        result["labels"] = _labels(raw["labels"], encode)
    if isinstance(raw.get("annotator_labels"), Mapping):
        result["annotator_labels"] = {
            identity: _labels(labels, encode)
            for identity, labels in raw["annotator_labels"].items()
        }
    if isinstance(raw.get("error_types"), list):
        result["error_types"] = [_value(kind, ERROR_TYPES, encode) for kind in raw["error_types"]]
    if isinstance(raw.get("perturbation"), Mapping):
        change = (
            dict(raw["perturbation"])
            if encode
            else _fields(raw["perturbation"], PERTURBATION_FIELDS, False)
        )
        if "kind" in change:
            change["kind"] = _value(change["kind"], PERTURBATIONS, encode)
        result["perturbation"] = _fields(change, PERTURBATION_FIELDS, True) if encode else change
    return _fields(result, ITEM_FIELDS, True) if encode else result


def encode_label_file(value: Mapping[str, Any]) -> dict[str, Any]:
    """日本語の固定名と値に変える。任意の管理用情報、ID、本文は触らない。"""
    result = dict(value)
    original_rubric = result.get("rubric_id")
    result["rubric_id"] = (
        RUBRICS.get(original_rubric, "その他の評価基準")
        if isinstance(original_rubric, str)
        else "その他の評価基準"
    )
    result["provenance"] = {"評価基準ID": original_rubric, "作成情報": result.get("provenance", {})}
    if isinstance(result.get("items"), list):
        result["items"] = [_item(item, True) for item in result["items"]]
    return {"形式": FORMAT_NAME, "形式の版": FORMAT_VERSION, **_fields(result, TOP_FIELDS, True)}


def decode_label_file(value: Any) -> Any:
    """旧英語形式と版付き日本語形式を読む。既存の型検証へ戻すための逆変換。"""
    if not isinstance(value, Mapping):
        return value
    if not any(key in value for key in ("形式", "形式の版", "項目")):
        return value
    if (
        value.get("形式") != FORMAT_NAME
        or type(value.get("形式の版")) is not int
        or value["形式の版"] != FORMAT_VERSION
    ):
        raise ValueError("対応していない記入データの形式または版です。")
    result = _fields(
        {key: item for key, item in value.items() if key not in {"形式", "形式の版"}},
        TOP_FIELDS,
        False,
    )
    management = result.get("provenance")
    if not isinstance(management, Mapping) or set(management) != {"評価基準ID", "作成情報"}:
        raise ValueError("記入データの管理用情報が正しくありません。")
    result["rubric_id"] = management["評価基準ID"]
    result["provenance"] = management["作成情報"]
    if isinstance(result.get("items"), list):
        result["items"] = [_item(item, False) for item in result["items"]]
    return result


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("記入データに同じ項目名が重複しています。")
        result[key] = value
    return result


def load_label_file(content: str | bytes) -> Any:
    """JSONの二重定義を拒否してから表記を解決する。"""
    return decode_label_file(json.loads(content, object_pairs_hook=_unique_fields))
