"""最終検証は実送達単位だけを選び、原文をコードで復元する。"""

import json

import pytest

TEXT = "対象を選びます。\n確認を押します。"
EVIDENCE = [{"id": "synthetic-chunk-a", "source": "合成資料A", "text": TEXT}]


def test_final_unit_ids_bind_source_version_and_reject_previous_subset():
    from rag_engine.evaluation.final_validation_units import prepare_unit_request

    fragment = {
        "id": "synthetic-chunk",
        "evidence_id": "synthetic-fragment",
        "source": "合成資料",
        "text": TEXT,
        "fragment_start": 600,
        "fragment_end": 600 + len(TEXT),
        "source_run_id": "synthetic-run-v1",
    }
    old = prepare_unit_request({"evidence_items": [fragment]})
    new = prepare_unit_request({"evidence_items": [{**fragment, "source_run_id": "synthetic-run-v2"}]})
    assert set(old.units).isdisjoint(new.units)
    with pytest.raises(ValueError):
        new.bind(list(old.units))
    selected = old.bind([next(reversed(old.units))])
    assert selected[0]["text"] == "確認を押します。" and selected[0]["fragment_start"] == 600


def test_unit_internal_metadata_and_scope_never_enter_wire():
    from rag_engine.evaluation.final_validation_units import prepare_unit_request

    prepared = prepare_unit_request(
        {
            "evidence_items": [
                {
                    "id": "chunk-a",
                    "evidence_id": "E-a",
                    "text": TEXT,
                    "document_id": "private-document",
                    "chunk_set_id": "private-set",
                    "internal_asset_path": "private-path",
                }
            ]
        }
    )
    wire = json.dumps(prepared.inputs)
    assert all(value not in wire for value in ("private-document", "private-set", "private-path"))
    assert prepared.bind([next(iter(prepared.units))])[0]["scope"]["document_id"] == "private-document"


    # スタブの成功は旧指示の意味的な適合を証明しない。保存資源の更新は別の受入条件。


@pytest.mark.parametrize("text, folded", [("ｶﾞＸ１２", "ガX12"), ("ﾊﾟ９", "パ9"), ("①Ⅴ㈱１２", "①Ⅴ㈱12")])
def test_wire_folds_width_while_bound_original_and_purpose_offsets_stay_exact(text, folded):
    from rag_engine.evaluation.final_validation_units import prepare_unit_request

    request = prepare_unit_request(
        {
            "evidence_items": [
                {
                    "id": "synthetic-image",
                    "evidence_id": "E-synthetic",
                    "text": text,
                    "image_purposes": [{"start": 0, "end": len(text), "purpose": "record"}],
                }
            ]
        }
    )
    unit = request.inputs["evidence_items"][0]["units"][0]
    assert unit["text"] == folded
    assert unit["image_purposes"] == [{"start": 0, "end": len(folded), "purpose": "record"}]
    bound = request.bind([unit["unit_id"]])[0]
    assert bound["text"] == text and (bound["start"], bound["end"]) == (0, len(text))
    assert bound["image_purposes"] == [{"start": 0, "end": len(text), "purpose": "record"}]


