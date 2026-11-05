"""検索候補・未送達・画像・部分記録から全範囲の検査済みを推定しない。"""

import hashlib
from copy import deepcopy

import pytest

from rag_engine.generation.provided_scope import capture_provided_scope


def trace(*, numbered=True, background=False):
    text = "送達した原文\n未選択の隣接文"
    block = "[E1] p.1\n1| 送達した原文" if numbered else "[E1] p.1\n" + text
    heading = "=== 機能 F1: 見出しの情報 ==="
    prompt = heading + "\n" + block
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    return {
        "input": {
            "snapshot_status": "recorded",
            "expected_evidence_count": 1,
            "image_payload_capture": "not_applicable",
            "prompt_sha256": digest,
            "prompt": prompt,
            "evidence": [
                {
                    "text": text,
                    "citation_units": [] if background else [[0, 6]],
                    "numbered": numbered,
                    "source": "synthetic.txt",
                    "view_sha256": "view-hash",
                    "present_in_prompt": True,
                    "prompt_block": block,
                    "prompt_block_sha256": hashlib.sha256(block.encode()).hexdigest(),
                    "group_heading": heading,
                }
            ],
        },
        "outputs": [{"purpose": "answer.generate", "input_prompt_sha256": digest}],
    }


def test_only_sent_units_are_saved_as_provided_scope():
    result = capture_provided_scope(trace())
    assert result["status"] == "complete"
    assert "送達した原文" in result["evidence_items"][0]["text"]
    assert "見出しの情報" in result["evidence_items"][0]["text"]
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
    value = trace(numbered=False)
    result = capture_provided_scope(value)
    assert result["status"] == "complete"
    assert "送達した原文\n未選択の隣接文" in result["evidence_items"][0]["text"]


def test_background_text_without_citation_units_is_still_in_provided_scope():
    value = trace(numbered=False, background=True)
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


@pytest.mark.parametrize("field", ["group_heading", "prompt_block", "prompt_block_sha256"])
def test_missing_or_changed_actual_block_is_partial(field):
    value = trace()
    value["input"]["evidence"][0][field] = "not in sent prompt"
    assert capture_provided_scope(value)["status"] == "partial"


def test_source_context_outside_citation_units_is_kept():
    value = trace()
    row = value["input"]["evidence"][0]
    row["prompt_block"] = "補助情報: 項目Cの値は20です。\n" + row["prompt_block"]
    row["prompt_block_sha256"] = hashlib.sha256(row["prompt_block"].encode()).hexdigest()
    value["input"]["prompt"] = row["group_heading"] + "\n" + row["prompt_block"]
    digest = hashlib.sha256(value["input"]["prompt"].encode()).hexdigest()
    value["input"]["prompt_sha256"] = value["outputs"][0]["input_prompt_sha256"] = digest
    result = capture_provided_scope(value)
    assert result["status"] == "complete"
    assert "項目Cの値は20" in result["evidence_items"][0]["text"]
