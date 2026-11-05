"""日本語ファイルは元の判定/識別子/文章へ可逆で、不正形式を成功にしない。"""

import json

import pytest

from pr_evaluation_scoring.label_file import (
    ERROR_TYPES,
    PERTURBATIONS,
    decode_label_file,
    encode_label_file,
    load_label_file,
)


def document():
    return {
        "rubric_id": "answer_quality",
        "description": "Original pass/dev text は原文を保つ",
        "provenance": {"custom_field": {"question": "原文", "評価基準ID": "arbitrary"}},
        "items": [
            {
                "id": "item-a",
                "split": "holdout",
                "question": "値は？",
                "answer": "原文passは翻訳しない。",
                "standard_answer": None,
                "labels": {"claim_support": "fail", "answer_correctness": "pass"},
                "evidence": [{"id": "e-a", "text": "元の本文。dev / pass。"}],
                "labeler": "reviewer-a",
                "note": "note/holdout",
                "origin": "synthetic",
                "perturbation": {
                    "kind": "swap_values",
                    "base_item_id": "base-a",
                    "description": "原文",
                },
                "error_types": list(ERROR_TYPES),
                "annotator_labels": {"pass": {"claim_support": "cannot_judge"}},
            }
        ],
    }


def test_japanese_fields_and_values_round_trip_without_translating_original_data():
    original = document()
    encoded = encode_label_file(original)
    assert encoded["評価基準"] == "回答品質"
    assert encoded["管理用情報"] == {
        "評価基準ID": "answer_quality",
        "作成情報": original["provenance"],
    }
    item = encoded["項目"][0]
    assert item["区分"] == "検証用データ"
    assert item["判定"] == {"根拠": "不合格", "正確性": "合格"}
    assert item["記入者別の判定"] == {"pass": {"根拠": "判定できない"}}
    assert item["回答"] == original["items"][0]["answer"]
    assert load_label_file(json.dumps(encoded, ensure_ascii=False)) == original
    assert document() == original


@pytest.mark.parametrize("kind", list(PERTURBATIONS))
def test_all_change_kinds_preserve_their_identifiers(kind):
    original = document()
    original["items"][0]["perturbation"]["kind"] = kind
    assert decode_label_file(encode_label_file(original)) == original


@pytest.mark.parametrize("rubric", ["custom-rubric", "回答品質"])
def test_unknown_rubric_identifiers_remain_exactly_the_same(rubric):
    original = document()
    original["rubric_id"] = rubric
    encoded = encode_label_file(original)
    assert encoded["評価基準"] == "その他の評価基準"
    assert decode_label_file(encoded) == original


def test_old_english_file_is_accepted_unchanged():
    original = document()
    assert load_label_file(json.dumps(original)) == original


@pytest.mark.parametrize("bad", ["version", "mixed", "management", "duplicate"])
def test_invalid_or_ambiguous_file_is_rejected(bad):
    encoded = encode_label_file(document())
    if bad == "version":
        encoded["形式の版"] = 99
    elif bad == "mixed":
        encoded["items"] = []
    elif bad == "management":
        encoded["管理用情報"] = {}
    else:
        with pytest.raises(ValueError, match="重複"):
            load_label_file('{"rubric_id":"a","rubric_id":"b"}')
        return
    with pytest.raises(ValueError):
        load_label_file(json.dumps(encoded))


def test_alias_collision_is_not_silently_overwritten():
    original = document()
    original["items"][0]["labels"]["根拠"] = "pass"
    with pytest.raises(ValueError, match="衝突"):
        encode_label_file(original)
