"""視覚本文と検索専用情報を、原文の語を見ずに分離する。"""

from unittest.mock import patch

import pytest
from PIL import Image

from rag_engine.config import get_settings
from rag_engine.models.layout import LayoutRecord, PageImage
from rag_engine.parsing import picture_descriptions as pictures


def description():
    return {
        "retrieval_text": "設定画面には保存ボタンがあります。",
        "visible_buttons": ["保存"],
        "visible_fields": ["検索語"],
        "search_keywords": ["索引専用甲"],
        "query_rewrites": ["索引専用乙"],
        "answerable_questions": ["索引専用丙"],
    }


def test_projection_keeps_index_bytes_and_literal_visible_text():
    payload = description()
    project = pictures.vision_text_projection(payload)
    assert project.index_text == pictures.vision_description_text(payload)
    assert "検索語" in project.answer_description
    assert all(word not in project.answer_description for word in ["索引専用甲", "索引専用乙", "索引専用丙"])
    assert all(word in project.index_text for word in ["索引専用甲", "索引専用乙", "索引専用丙"])
    assert project.origin == "image_generated_description"


def test_actual_picture_producer_saves_answer_and_index_separately(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(path)
    record = LayoutRecord("picture-1", "docling", 1, 1, [10, 10, 80, 80], "image_top_left", 100, 100, "Picture", raw_type="picture")
    page = PageImage(1, 100, 100, 100, 100, str(path))
    with patch.object(pictures, "describe_picture", return_value=description()):
        result = pictures.describe_docling_pictures(
            [record],
            [page],
            run_dir=tmp_path,
            pdf_name="synthetic.pdf",
            settings=get_settings(environ={"OCI_ENTERPRISE_AI_VLM_MODEL": "vendor.synthetic"}, dotenv_path=None),
        )
    assert result.succeeded == 1
    assert "索引専用甲" not in record.text
    assert "検索語" in record.text
    assert "索引専用甲" in record.raw["vision_text_projection"]


@pytest.mark.parametrize("bad", [None, {}, "{}", '{"version":1}'])
def test_unknown_or_incomplete_projection_does_not_infer_a_source(bad):
    from rag_parser_core.vision_text import load_vision_text_projection

    assert load_vision_text_projection(bad) is None


def record(category, text, *, raw=None, seq=1, bbox=None):
    return {
        "id": f"source-{seq}",
        "engine": "docling",
        "page": 1,
        "seq_no": seq,
        "bbox": bbox or [10, 10, 80, 80],
        "coord_system": "image_top_left",
        "page_width": 100,
        "page_height": 100,
        "category": category,
        "text": text,
        "raw_type": category.lower(),
        "raw": raw or {},
    }


def chunks_for(records):
    from rag_engine.chunking import ChunkingConfig, build_small_to_big_chunks

    return build_small_to_big_chunks(
        {"records": records, "engines": [{"engine": "docling", "label": "Docling"}]},
        source_run_id="synthetic-run",
        selected_engine_ids=["docling"],
        config=ChunkingConfig(),
    )


def test_child_parent_answer_units_cannot_select_search_only_fields():
    from rag_engine.generation.answer_records import _chunk_answer_record
    from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot
    from rag_engine.generation.grounded import citation_units

    project = pictures.vision_text_projection(description())
    chunks = chunks_for(
        [
            record(
                "Picture",
                project.answer_description,
                raw={
                    "vision_description": description(),
                    "vision_text_projection": project.dumps(),
                },
            )
        ]
    )
    assert {chunk.chunk_level for chunk in chunks} == {"parent", "child"}
    for chunk in chunks:
        assert "索引専用甲" in chunk.retrieval_text
        assert "索引専用甲" not in chunk.text
        answer = _chunk_answer_record(chunk, chunk_run_id="synthetic-chunks")
        assert answer.text == chunk.text
        span = {"source_id": chunk.chunk_id, "text": answer.text}
        snapshot = EvidenceUnitSnapshot.create(span, citation_units(answer.text, image_text=True), "synthetic-v1")
        assert "索引専用甲" not in snapshot.prompt_record()["text"]
        assert "検索語" in snapshot.prompt_record()["text"]
        assert chunk.metadata["answer_text_projection"]["body_sha256"]


def test_native_text_named_search_words_keeps_old_answer_and_index_contract():
    from rag_engine.generation.answer_records import _chunk_answer_record

    text = "「検索語」と「言い換え」は画面の項目です。"
    for chunk in chunks_for([record("Text", text)]):
        assert text in chunk.text and text in chunk.retrieval_text
        assert "answer_text_projection" not in chunk.metadata
        if chunk.chunk_level == "child":
            assert _chunk_answer_record(chunk).text == chunk.retrieval_text


def test_table_supplement_and_contained_picture_keep_index_only_text_outside_body():
    from rag_engine.chunking.records import _record_text_for_chunk
    from rag_engine.chunking.tables import _table_record_with_metadata

    project = pictures.vision_text_projection(description())
    html = "<table><tr><td>原生文字123</td></tr></table>"
    table = record(
        "Table", html, raw={"table_vision_text": project.answer_description, "vision_text_projection": project.dumps()}, bbox=[0, 0, 90, 90]
    )
    assert "索引専用甲" not in _record_text_for_chunk(table)
    assert "索引専用甲" in _record_text_for_chunk(table, for_retrieval=True)
    picture = record(
        "Picture", project.answer_description, raw={"vision_description": description(), "vision_text_projection": project.dumps()}, seq=2
    )
    table = _table_record_with_metadata(table, [table, picture])
    assert "原生文字123" in _record_text_for_chunk(table)
    assert "索引専用甲" not in _record_text_for_chunk(table)
    assert "索引専用甲" in _record_text_for_chunk(table, for_retrieval=True)


def test_body_marker_is_bound_to_exact_body_and_survives_metadata_roundtrip():
    import json

    from rag_parser_core.vision_text import has_answer_text_projection, text_sha256

    from rag_engine.chunking.metadata import _project_chunk_metadata

    project = pictures.vision_text_projection(description())
    chunk = next(
        c
        for c in chunks_for(
            [
                record(
                    "Picture",
                    project.answer_description,
                    raw={"vision_description": description(), "vision_text_projection": project.dumps()},
                )
            ]
        )
        if c.chunk_level == "child"
    )
    metadata = json.loads(json.dumps(_project_chunk_metadata(chunk.metadata)))
    assert has_answer_text_projection(metadata, chunk.text)
    assert not has_answer_text_projection(metadata, chunk.text + "別の原文")
    metadata["answer_text_projection"]["body_sha256"] = text_sha256("別の原文")
    assert not has_answer_text_projection(metadata, chunk.text)


def test_index_only_vision_response_does_not_become_answer_body(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(path)
    image = LayoutRecord("picture-1", "docling", 1, 1, [10, 10, 80, 80], "image_top_left", 100, 100, "Picture", raw_type="picture")
    page = PageImage(1, 100, 100, 100, 100, str(path))
    with patch.object(pictures, "describe_picture", return_value={"search_keywords": ["索引専用甲"]}):
        result = pictures.describe_docling_pictures(
            [image],
            [page],
            run_dir=tmp_path,
            pdf_name="synthetic.pdf",
            settings=get_settings(environ={"OCI_ENTERPRISE_AI_VLM_MODEL": "vendor.synthetic"}, dotenv_path=None),
        )
    assert result.failed == 1 and not image.text
    from rag_engine.chunking.records import _record_text_for_chunk

    assert not _record_text_for_chunk(image.to_dict(), for_retrieval=True)


def test_exact_old_raw_can_be_reprojected_without_mutating_saved_artifact():
    import copy

    payload = description()
    old = record(
        "Picture",
        pictures.vision_description_text(payload),
        raw={
            "vision_description": payload,
            "vision_status": "succeeded",
        },
    )
    saved = copy.deepcopy(old)
    for chunk in chunks_for([old]):
        assert "索引専用甲" not in chunk.text
        assert "索引専用甲" in chunk.retrieval_text
    assert old == saved


@pytest.mark.parametrize("change", ["different_body", "missing_raw", "native", "failed"])
def test_unprovable_old_body_is_not_rewritten_by_guessing(change):
    import copy

    original = record(
        "Picture",
        pictures.vision_description_text(description()),
        raw={
            "vision_description": description(),
            "vision_status": "succeeded",
        },
    )
    if change == "different_body":
        original["text"] += "\n原文の追加記載"
    elif change == "missing_raw":
        original["raw"].pop("vision_description")
    elif change == "native":
        original["category"] = "Text"
    else:
        original["raw"]["vision_status"] = "failed"
    before = copy.deepcopy(original)
    pictures.reproject_saved_vision_record(original)
    assert original == before


@pytest.mark.parametrize("bad_marker", [{}, {"version": 99}, {"version": 1, "body_sha256": "wrong"}, {"version": True}, "invalid"])
def test_present_invalid_chunk_projection_never_falls_back_to_index_text(bad_marker):
    from rag_engine.generation.answer_records import _chunk_answer_record

    project = pictures.vision_text_projection(description())
    child = next(
        c
        for c in chunks_for(
            [
                record(
                    "Picture",
                    project.answer_description,
                    raw={"vision_description": description(), "vision_text_projection": project.dumps()},
                )
            ]
        )
        if c.chunk_level == "child"
    )
    child.metadata["answer_text_projection"] = bad_marker
    assert "索引専用甲" in child.retrieval_text
    with pytest.raises(ValueError, match="投影"):
        _chunk_answer_record(child)


def test_mixed_valid_and_unrecorded_visuals_do_not_certify_whole_parent():
    project = pictures.vision_text_projection(description())
    valid = record(
        "Picture",
        project.answer_description,
        raw={"vision_description": description(), "vision_text_projection": project.dumps()},
        seq=1,
        bbox=[10, 10, 40, 40],
    )
    unknown = record(
        "Picture", "旧画像説明の本文", raw={"vision_description": description(), "vision_status": "succeeded"}, seq=2, bbox=[50, 50, 90, 90]
    )
    with pytest.raises(ValueError, match="投影"):
        chunks_for([valid, unknown])


@pytest.mark.parametrize("value", ["{}", "invalid", '{"version":99}'])
def test_present_invalid_record_projection_stops_before_creating_citable_body(value):
    invalid = record("Picture", "未知の画像本文", raw={"vision_text_projection": value})
    with pytest.raises(ValueError, match="投影"):
        chunks_for([invalid])


def test_native_record_does_not_gain_generated_origin_from_arbitrary_raw_projection():
    from rag_parser_core.vision_text import VisionTextProjection

    from rag_engine.chunking.records import _record_text_for_chunk, _vision_projection_status

    project = VisionTextProjection("原生本文の検索語", "索引専用甲")
    native = record("Text", project.answer_description, raw={"vision_text_projection": project.dumps()})
    assert _vision_projection_status(native) is None
    assert _record_text_for_chunk(native, for_retrieval=True) == native["text"]


def _picture_with_visual_text(tmp_path):
    """本文が空で、同じ bbox の画像内 OCR に操作の語がある Picture（解析の直後の形）。"""
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(path)
    bbox = [10.0, 10.0, 80.0, 80.0]
    picture = LayoutRecord("picture-1", "docling", 1, 1, list(bbox), "image_top_left", 100, 100, "Picture", raw_type="picture")
    ocr = LayoutRecord(
        "picture-ocr-1", "docling", 1, 2, list(bbox), "image_top_left", 100, 100, "Picture", "登録ボタン", raw_type="picture_ocr_text"
    )
    return picture, ocr, PageImage(1, 100, 100, 100, 100, str(path))


def test_crop_step_before_vision_does_not_write_an_empty_projection(tmp_path):
    """crop の保存は VLM の前に走るため、説明が無い図に投影も本文も作らない（#4569）。"""
    from rag_engine.chunking.records import _vision_projection_status
    from rag_engine.parsing.visual_artifacts import persist_semantic_visual_crops

    picture, ocr, page = _picture_with_visual_text(tmp_path)
    persist_semantic_visual_crops([picture, ocr], [page], run_dir=tmp_path / "run")

    assert picture.raw["visual_role_reason"] == "meaningful_text_in_visual"
    assert picture.raw["crop_path"]
    assert "vision_text_projection" not in picture.raw
    assert picture.text == ""
    assert _vision_projection_status(picture.to_dict()) is None


def test_crop_step_then_vision_verifies_projection_and_chunks(tmp_path):
    """crop の保存 → VLM の書き戻し → 分割が、空の投影で止まらずに通る（#4569）。"""
    from rag_engine.chunking.records import _vision_projection_status
    from rag_engine.parsing.visual_artifacts import persist_semantic_visual_crops

    picture, ocr, page = _picture_with_visual_text(tmp_path)
    persist_semantic_visual_crops([picture, ocr], [page], run_dir=tmp_path / "run")
    with patch.object(pictures, "describe_picture", return_value=description()):
        result = pictures.describe_docling_pictures(
            [picture, ocr],
            [page],
            run_dir=tmp_path / "run",
            pdf_name="synthetic.pdf",
            settings=get_settings(environ={"OCI_ENTERPRISE_AI_VLM_MODEL": "vendor.synthetic"}, dotenv_path=None),
        )

    assert result.succeeded == 1
    assert _vision_projection_status(picture.to_dict()) == "verified"
    chunks = chunks_for([picture.to_dict(), ocr.to_dict()])
    assert any("検索語" in chunk.text for chunk in chunks)


def test_crop_step_keeps_projection_of_a_saved_vision_description(tmp_path):
    """保存済みの VLM の説明がある図（保存済みの解析結果の復元）は、その説明から投影を作る。"""
    from rag_engine.chunking.records import _vision_projection_status
    from rag_engine.parsing.visual_artifacts import persist_semantic_visual_crops

    picture, ocr, page = _picture_with_visual_text(tmp_path)
    picture.raw["vision_description"] = description()
    persist_semantic_visual_crops([picture, ocr], [page], run_dir=tmp_path / "run")

    assert picture.text == pictures.vision_text_projection(description()).answer_description
    assert _vision_projection_status(picture.to_dict()) == "verified"


def test_invalid_projection_error_is_safe_for_the_job_message():
    """不正な投影の失敗は、汎用の「取込処理に失敗しました。」ではなく、直し方を示す文で返す。"""
    from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY, VisionTextProjection

    from rag_engine.chunking.records import _vision_projection_status

    invalid = record("Picture", "", raw={VISION_TEXT_PROJECTION_KEY: VisionTextProjection("", "").dumps()})
    with pytest.raises(ValueError, match="再解析") as caught:
        _vision_projection_status(invalid)
    assert getattr(caught.value, "safe_for_user", False) is True
