"""検索候補・未送達・画像・部分記録から全範囲の検査済みを推定しない。"""

from copy import deepcopy

import pytest

from rag_engine.generation.provided_scope import capture_provided_scope


def trace():
    return {
        "input": {
            "snapshot_status": "recorded",
            "expected_evidence_count": 1,
            "image_payload_capture": "not_applicable",
            "prompt_sha256": "input-hash",
            "evidence": [
                {
                    "text": "送達した原文\n未選択の隣接文",
                    "citation_units": [[0, 6]],
                    "numbered": True,
                    "source": "synthetic.txt",
                    "view_sha256": "view-hash",
                    "present_in_prompt": True,
                }
            ],
        },
        "outputs": [{"purpose": "answer.generate", "input_prompt_sha256": "input-hash"}],
    }


def test_only_sent_units_are_saved_as_provided_scope():
    result = capture_provided_scope(trace())
    assert result["status"] == "complete"
    assert result["evidence_items"][0]["text"] == "送達した原文"
    assert "未選択" not in str(result)


@pytest.mark.parametrize("change", ["image", "missing_row", "not_sent", "invalid_range", "repair_only", "different_prompt", "old_record"])
def test_incomplete_or_old_snapshots_do_not_prove_full_scope(change):
    value = deepcopy(trace())
    if change == "image":
        value["input"]["image_payload_capture"] = "not_recorded"
    elif change == "missing_row":
        value["input"]["expected_evidence_count"] = 2
    elif change == "not_sent":
        value["input"]["evidence"][0]["present_in_prompt"] = False
    elif change == "invalid_range":
        value["input"]["evidence"][0]["citation_units"] = [[0, 999]]
    elif change == "repair_only":
        value["outputs"][0]["purpose"] = "answer.range_repair"
    elif change == "different_prompt":
        value["outputs"][0]["input_prompt_sha256"] = "other"
    elif change == "old_record":
        value["input"].pop("expected_evidence_count")
    assert capture_provided_scope(value)["status"] != "complete"


def test_missing_capture_is_unknown():
    assert capture_provided_scope({})["status"] == "unknown"


def test_unnumbered_input_keeps_the_full_sent_text():
    value = trace()
    value["input"]["evidence"][0]["numbered"] = False
    result = capture_provided_scope(value)
    assert result["status"] == "complete"
    assert result["evidence_items"][0]["text"] == "送達した原文\n未選択の隣接文"


def test_background_text_without_citation_units_is_still_in_provided_scope():
    value = trace()
    value["input"]["evidence"][0].update(numbered=False, citation_units=[])
    assert capture_provided_scope(value)["status"] == "complete"


@pytest.mark.parametrize("field", ["prompt_sha256", "numbered"])
def test_missing_formal_capture_fields_do_not_prove_scope(field):
    value = trace()
    if field == "numbered":
        del value["input"]["evidence"][0][field]
    else:
        value["input"].pop(field)
        value["outputs"][0].pop("input_prompt_sha256")
    assert capture_provided_scope(value)["status"] != "complete"
