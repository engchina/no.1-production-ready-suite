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


def _picture_and_body(tmp_path):
    """本文の段落と、本文の空の Picture 1 件の合成の頁（VLM の前の形）。"""
    path = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(path)
    body = LayoutRecord(
        "body-1", "docling", 1, 1, [5, 2, 95, 8], "image_top_left", 100, 100, "Text", "合成の本文の段落です。", raw_type="text"
    )
    picture = LayoutRecord("picture-1", "docling", 1, 2, [10, 10, 80, 80], "image_top_left", 100, 100, "Picture", raw_type="picture")
    return body, picture, PageImage(1, 100, 100, 100, 100, str(path))


def _describe_without_answer_field(records, page, run_dir):
    """回答本文の field を持たない VLM の応答（検索語だけ）を返す決定論のスタブで読む。"""
    with patch.object(pictures, "describe_picture", return_value={"search_keywords": ["索引専用甲"]}):
        return pictures.describe_docling_pictures(
            records,
            [page],
            run_dir=run_dir,
            pdf_name="synthetic.pdf",
            settings=get_settings(environ={"OCI_ENTERPRISE_AI_VLM_MODEL": "vendor.synthetic"}, dotenv_path=None),
        )


def test_vlm_without_answer_field_leaves_no_projection_and_marks_the_picture_failed(tmp_path):
    """回答本文の field の無い応答は投影を書かず、その図だけを「VLM の本文なし」にする（#4570）。"""
    from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY

    from rag_engine.chunking.records import _vision_projection_status

    body, picture, page = _picture_and_body(tmp_path)
    result = _describe_without_answer_field([body, picture], page, tmp_path / "run")

    assert result.failed == 1 and result.succeeded == 0
    assert picture.raw["vision_status"] == "failed"
    assert "説明フィールド" in picture.raw["vision_error"]
    assert VISION_TEXT_PROJECTION_KEY not in picture.raw
    assert picture.text == ""
    assert _vision_projection_status(picture.to_dict()) == "vision_failed"


def test_failed_picture_does_not_stop_chunking_of_the_document(tmp_path):
    """VLM の失敗した図があっても文書全体の分割は続き、その図は VLM の本文を出さない（#4570）。"""
    from rag_parser_core.vision_text import is_unprojected_vision_source

    body, picture, page = _picture_and_body(tmp_path)
    _describe_without_answer_field([body, picture], page, tmp_path / "run")

    chunks = chunks_for([body.to_dict(), picture.to_dict()])

    assert any("合成の本文の段落" in chunk.text for chunk in chunks)
    assert all("索引専用甲" not in chunk.text and "索引専用甲" not in chunk.retrieval_text for chunk in chunks)
    picture_refs = [ref for chunk in chunks for ref in chunk.source_record_refs if ref["record_id"] == "picture-1"]
    assert picture_refs
    for ref in picture_refs:
        assert ref["vision_text_projection_status"] == "vision_failed"
        assert "説明フィールド" in ref["vision_error"]
        assert "vision_text_origin" not in ref
    for chunk in chunks:
        # 検索で旧い画像説明（回答本文の未分離）として止めない。
        assert not is_unprojected_vision_source(chunk.metadata, chunk.source_record_refs)
        assert "answer_text_projection" not in chunk.metadata


def test_failed_picture_answer_record_is_not_treated_as_old_format(tmp_path):
    """VLM の失敗した図の record は、回答の record の組み立てで旧い画像説明として止めない。"""
    from rag_engine.generation.answer_records import _answer_record

    body, picture, page = _picture_and_body(tmp_path)
    _describe_without_answer_field([body, picture], page, tmp_path / "run")

    assert _answer_record(picture.to_dict(), {"docling": "Docling"}) is None


def test_projection_left_by_the_old_failure_is_read_as_no_vlm_text():
    """修正前の失敗が残した回答本文の空の投影も、失敗の記録があれば「VLM の本文なし」として再分割できる。"""
    from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY, VisionTextProjection

    from rag_engine.chunking.records import _vision_projection_status

    left = record(
        "Picture",
        "",
        raw={
            "vision_description": {"search_keywords": ["索引専用甲"]},
            "vision_status": "failed",
            "vision_error": "RuntimeError: Vision 応答に利用可能な説明フィールドがありません。",
            VISION_TEXT_PROJECTION_KEY: VisionTextProjection("", "検索語: 索引専用甲").dumps(),
        },
    )
    assert _vision_projection_status(left) == "vision_failed"
    for chunk in chunks_for([record("Text", "合成の本文の段落です。", seq=2, bbox=[5, 82, 95, 90]), left]):
        assert "索引専用甲" not in chunk.text and "索引専用甲" not in chunk.retrieval_text


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"vision_status": "succeeded"}, id="empty-without-failure"),
        pytest.param({"vision_status": "failed", "vision_error": "RuntimeError: 合成の失敗"}, id="nonempty-with-failure"),
    ],
)
def test_projection_without_a_matching_failure_stays_invalid(raw):
    """失敗の記録の無い空の投影と、失敗の記録と矛盾する本文つきの投影は、これまでどおり不正（#4569 の規則）。"""
    from rag_parser_core.vision_text import VISION_TEXT_PROJECTION_KEY, VisionTextProjection

    from rag_engine.chunking.records import _vision_projection_status

    answer = "合成の画像の説明" if "vision_error" in raw else ""
    invalid = record("Picture", answer, raw={**raw, VISION_TEXT_PROJECTION_KEY: VisionTextProjection(answer, answer).dumps()})
    with pytest.raises(ValueError, match="投影が不正"):
        _vision_projection_status(invalid)


def test_failed_table_supplement_keeps_verified_contained_pictures():
    """表の補足の VLM が失敗しても、表内画像の検証済みの投影はそのまま使い、混在として止めない。"""
    from rag_engine.chunking.records import _vision_projection_status

    project = pictures.vision_text_projection(description())
    table = record(
        "Table",
        "<table><tr><td>合成のセル</td></tr></table>",
        raw={
            "vision_status": "failed",
            "vision_error": "RuntimeError: 合成の失敗",
            "table_visual_texts": {"image-1": project.answer_description},
            "table_visual_text_projections": {"image-1": project.dumps()},
        },
    )
    assert _vision_projection_status(table) == "verified"


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("一段落目。\n\n二段落目。", id="blank-line"),
        pytest.param("一段落目。  \n二段落目。", id="trailing-space"),
        pytest.param("\n一段落目。\n\n\n二段落目。\n", id="surrounding-newlines"),
    ],
)
def test_contained_picture_projection_matches_normalized_table_text(answer):
    """表内画像の説明に空行や行末の空白があっても、整形の違いだけでは不正にしない（#4710）。"""
    from rag_parser_core.vision_text import VisionTextProjection

    from rag_engine.chunking.records import _record_text_for_chunk, _vision_projection_status
    from rag_engine.chunking.tables import _table_record_with_metadata

    project = VisionTextProjection(answer, "検索専用の本文")
    table = record("Table", "<table><tr><td>合成のセル</td></tr></table>", bbox=[0, 0, 90, 90])
    picture = record("Picture", answer, raw={"vision_description": description(), "vision_text_projection": project.dumps()}, seq=2)
    table = _table_record_with_metadata(table, [table, picture])
    assert table["raw"]["table_visual_text_projections"]["source-2"] == project.dumps()
    assert _vision_projection_status(table) == "verified"
    body = _record_text_for_chunk(table)
    assert "一段落目。" in body and "二段落目。" in body


def test_contained_picture_projection_with_other_content_stays_invalid():
    """整形の違いを超えて内容が違う投影は、これまでどおり不正として止める（#4710）。"""
    from rag_parser_core.vision_text import VisionTextProjection

    from rag_engine.chunking.records import _vision_projection_status

    project = VisionTextProjection("一段落目。\n\n二段落目。", "検索専用の本文")
    table = record(
        "Table",
        "<table><tr><td>合成のセル</td></tr></table>",
        raw={"table_visual_texts": {"image-1": "別の本文。"}, "table_visual_text_projections": {"image-1": project.dumps()}},
    )
    with pytest.raises(ValueError, match="投影が不正"):
        _vision_projection_status(table)
