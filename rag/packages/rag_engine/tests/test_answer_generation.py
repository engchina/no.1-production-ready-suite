"""answer generation の挙動を保護するテスト。"""

import json
import re
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from grounded_stub import AnswerOutput, CitedEvidenceOutput

from rag_engine.adapters.oracle.store import AdbHybridSearchUnavailable, HybridSearchResult, StoredChunk
from rag_engine.chunking import ChunkingConfig, create_chunk_run
from rag_engine.config import get_settings
from rag_engine.generation.answering import (
    RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
    AnswerContext,
    AnswerRecord,
    _cited_evidence_uses,
    answer_evidence_items,
    answer_image_evidence,
    answer_question,
    answer_question_result,
    build_answer_context,
    build_chunk_answer_context,
    format_answer_response,
    load_answer_records,
    parse_answer_response,
    preferred_records,
    rank_records,
    rank_records_with_rrf,
    rerank_records,
    validate_run_id,
)
from rag_engine.models.llm import RerankTextRank
from rag_engine.retrieval.context_builder import ContextChildEvidence, ContextParentEvidence


def heading(name: str) -> re.Pattern:
    """実行記録の見出し（「1. 名前」「1.2 名前」、任意で「［LLM n 回 / x 秒］」）に一致し、詳細文の同じ語には一致しない。"""
    return re.compile(rf"(?m)^ *\d+(?:\.\d+)*\.? {re.escape(name)}(?: ［[^］]*］)?$")


def display_offset(display: str, section) -> int:
    """文字列はそのまま、見出し pattern は先頭一致の位置を返す。無ければ例外で失敗させる。"""
    if isinstance(section, re.Pattern):
        match = section.search(display)
        if match is None:
            raise AssertionError(f"見出しが見つかりません: {section.pattern}")
        return match.start()
    return display.index(section)


class AnswerGenerationTests(unittest.TestCase):
    def setUp(self):
        # このfixtureは従来の分割・SQL契約を検証する。原生方式は専用の回帰で扱う。
        backend = patch.dict("os.environ", {"RAG_HYBRID_SEARCH_BACKEND": "oracle_separate_sql"})
        backend.start()
        self.addCleanup(backend.stop)
        """主張と引用の生成は専用テストで検証し、検索・実行フローのテストから分離する。"""
        from grounded_stub import stub_generation

        stub_generation(self)

    def test_bare_abstention_text_is_not_rewritten(self):
        """短い拒答文の完全一致の置換はしない。モデルの本文をそのまま返す（#4668）。"""
        for text in ("わかりません。", "I don't know.", "不知道。"):
            for structured in (False, True):
                with self.subTest(text=text, structured=structured):
                    raw = json.dumps({"answer": text, "confidence": "high", "needs_human_review": False}) if structured else text
                    response = parse_answer_response(raw)
                    self.assertEqual(response.answer_text, text)
                    self.assertEqual(response.raw_text, raw)

    def test_partial_answer_preserves_evidence_and_marks_missing_data_for_review(self):
        answer = "文書ではログを確認します。今回の原因はログがないためわかりません。"
        response = parse_answer_response(
            json.dumps(
                {
                    "answer": answer,
                    "confidence": "medium",
                    "needs_human_review": False,
                    "insufficient_reason": "該当実行のログを確認してください。",
                    "cited_evidence": [{"evidence_id": "r1", "source": "manual.pdf", "page": "1"}],
                }
            )
        )
        self.assertEqual(response.answer_text, answer)
        self.assertEqual(response.confidence, "medium")
        self.assertTrue(response.needs_human_review)
        self.assertEqual(response.cited_evidence[0]["evidence_id"], "r1")
        self.assertIn("該当実行のログ", response.insufficient_reason)

    def test_supported_answer_does_not_gain_unnecessary_review_or_caveat(self):
        response = parse_answer_response(
            json.dumps(
                {
                    "answer": "設定画面で保存を押します。",
                    "confidence": "high",
                    "insufficient_reason": "",
                    "needs_human_review": False,
                }
            )
        )
        self.assertEqual(response.answer_text, "設定画面で保存を押します。")
        self.assertEqual(response.confidence, "high")
        self.assertFalse(response.needs_human_review)
        self.assertEqual(response.insufficient_reason, "")
        self.assertEqual(format_answer_response("表示文言は「わかりません」です。"), "表示文言は「わかりません」です。")

    def test_empty_retrieval_returns_structured_gap_without_answer_llm(self):
        for context in (
            AnswerContext(records=[], text="", status="insufficient", insufficient_reason="internal detail"),
            AnswerContext(records=[_chunk_record("c1", "child", 1, "p1", "")], text=" "),
        ):
            with (
                self.subTest(has_records=bool(context.records)),
                TemporaryDirectory() as tmp,
                patch("rag_engine.generation.answering.check_adb_hybrid_search_ready"),
                patch("rag_engine.generation.answering.build_adb_hybrid_answer_context", return_value=context),
                patch("rag_engine.generation.answering.parse_text_response") as parse,
            ):
                result = answer_question_result(
                    "今回の原因は？",
                    "",
                    ["docling"],
                    replace(get_settings(), output_dir=Path(tmp)),
                    retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
                )
            self.assertIn("十分な根拠", result.answer_text)
            self.assertIn("検索範囲", result.answer_text)
            self.assertNotIn("internal detail", result.answer)
            self.assertEqual(result.confidence, "low")
            self.assertTrue(result.needs_human_review)
            self.assertTrue(result.insufficient_reason)
            self.assertEqual(result.cited_evidence, ())
            self.assertEqual(result.evidence_items, ())
            parse.assert_not_called()

    def test_legacy_schema_documents_are_reported_in_the_record_and_the_answer(self):
        """旧 schema の文書は検索 SQL で無言で除外されるため、件数を工程記録と回答本文に出す (#948)。"""
        from rag_engine.retrieval.scope import AdbSearchReadiness

        context = AnswerContext(records=[], text="", status="insufficient")
        with (
            TemporaryDirectory() as tmp,
            patch(
                "rag_engine.generation.answering.check_adb_hybrid_search_ready", return_value=AdbSearchReadiness(legacy_document_count=2)
            ),
            patch("rag_engine.generation.answering.build_adb_hybrid_answer_context", return_value=context),
            patch("rag_engine.generation.answering.parse_text_response") as parse,
        ):
            result = answer_question_result(
                "今回の原因は？",
                "",
                ["docling"],
                replace(get_settings(), output_dir=Path(tmp)),
                retrieval_scope=RETRIEVAL_SCOPE_KNOWLEDGE_BASE,
            )
        notice = "※ 検索範囲に旧 schema の文書が 2 件あり、検索対象外です。"
        self.assertIn(notice, result.answer_text)
        self.assertIn(notice, result.answer)
        self.assertIn(notice, result.question_display)
        parse.assert_not_called()

    def test_validate_run_id_rejects_path_like_values(self):
        with self.assertRaisesRegex(ValueError, "不正"):
            validate_run_id("../abcdef")

    def test_load_answer_records_uses_viewer_data_and_sentence_fallback(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            _write_viewer_data(
                output_dir,
                "abcdef",
                records=[
                    {"id": "r1", "engine": "docling", "page": 1, "seq_no": 2, "text": "本文", "category": "Text"},
                    {"id": "r2", "engine": "archived_parser", "page": 1, "seq_no": 1, "sentence": "見出し"},
                    {"id": "empty", "engine": "docling", "page": 2, "seq_no": 1, "text": ""},
                ],
            )

            records = load_answer_records(output_dir, "abcdef")

        self.assertEqual(
            [(record.id, record.engine_label, record.text) for record in records],
            [
                ("r2", "Archived parser", "見出し"),
                ("r1", "Docling", "本文"),
            ],
        )
        self.assertEqual([record.source for record in records], ["manual.pdf", "manual.pdf"])

    def test_preferred_records_selects_current_engines_with_fallback(self):
        records = [
            _record("d", "docling", 1, 1, "Docling text"),
            _record("m", "archived_parser", 1, 2, "Archived text"),
        ]

        self.assertEqual([record.id for record in preferred_records(records, ["docling"])], ["d"])
        self.assertEqual([record.id for record in preferred_records(records, ["missing"])], ["d", "m"])

    def test_rank_records_prefers_lexical_matches(self):
        records = [
            _record("a", "docling", 1, 1, "住所変更の説明です"),
            _record("b", "docling", 2, 3, "契約区分の登録手順を説明しています"),
            _record("c", "docling", 1, 2, "概要"),
        ]

        ranked = rank_records("契約区分を登録するには", records)

        self.assertEqual(ranked[0].id, "b")

    def test_rank_records_prefers_unique_answer_terms_over_repetition(self):
        records = [
            _record("repeated", "docling", 9, 1, "数量割引額 " * 12),
            _record(
                "parameter",
                "docling",
                2,
                1,
                "キャンペーン設定 DiscQtyRule 03 設定値 0 算出対象外 ボリュームディスカウント",
            ),
        ]

        ranked = rank_records(
            "送料無料キャンペーンの受注だけ数量割引額が計算されない。どうしてか。",
            records,
        )

        self.assertEqual(ranked[0].id, "parameter")

    def test_rank_records_uses_explicit_question_terms(self):
        records = [
            _record("other", "docling", 1, 1, "別の設定の説明です。"),
            _record("direct", "docling", 4, 2, "SAMPLE_SETTING の設定値を確認します。"),
        ]

        ranked = rank_records("SAMPLE_SETTING の設定値", records)

        self.assertEqual(ranked[0].id, "direct")

    def test_rank_records_with_rrf_prefers_cross_query_matches(self):
        records = [
            _record("alpha", "docling", 1, 1, "alpha " * 12),
            _record("beta", "docling", 1, 2, "beta " * 12),
            _record("both", "docling", 1, 3, "alpha beta source evidence"),
        ]

        ranked = rank_records_with_rrf(["alpha", "beta"], records, limit=3)

        self.assertEqual(ranked[0].id, "both")

    def test_rerank_records_reorders_candidates_and_appends_missing(self):
        records = [
            _record("a", "docling", 1, 1, "first evidence"),
            _record("b", "docling", 1, 2, "second evidence"),
            _record("c", "docling", 1, 3, "third evidence"),
        ]
        settings = _rerank_ready_settings()

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(1, 0.91), RerankTextRank(99, 0.1), RerankTextRank(1, 0.9)],
        ):
            reranked = rerank_records("question", records, settings, enabled=True)

        self.assertEqual([record.id for record in reranked], ["b", "a", "c"])
        self.assertEqual(reranked[0].metadata["rerank"]["relevance_score"], 0.91)

    def test_rerank_records_falls_back_to_initial_order_on_error(self):
        records = [
            _record("a", "docling", 1, 1, "first evidence"),
            _record("b", "docling", 1, 2, "second evidence"),
        ]
        settings = _rerank_ready_settings()

        with patch("rag_engine.generation.answering.rerank_text_with_scores", side_effect=RuntimeError("rerank down")):
            reranked = rerank_records("question", records, settings, enabled=True)

        self.assertEqual([record.id for record in reranked], ["a", "b"])

    def test_rerank_records_filters_everything_below_threshold(self):
        records = [
            _record("a", "docling", 1, 1, "first evidence"),
            _record("b", "docling", 1, 2, "second evidence"),
        ]
        settings = replace(_rerank_ready_settings(), rerank_min_relevance_score=0.9)

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(1, 0.2), RerankTextRank(0, 0.1)],
        ):
            reranked = rerank_records("question", records, settings, enabled=True)

        self.assertEqual(reranked, [])

    def test_rerank_records_does_not_depend_on_vision_endpoint(self):
        records = [_record("a", "docling", 1, 1, "first evidence")]
        settings = replace(_rerank_ready_settings(), oci_vision_endpoint="", oci_rerank_endpoint="")

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(0, 0.91)],
        ) as rerank:
            reranked = rerank_records("question", records, settings, enabled=True)

        rerank.assert_called_once()
        self.assertEqual([record.id for record in reranked], ["a"])

    def test_rerank_records_preserves_image_vector_only_candidates(self):
        image_record = replace(
            _record("image", "docling", 1, 1, "Image evidence | id=red-button"),
            metadata={
                "adb_hybrid": {"retrieval_channels": ["image_vector:q1"]},
                "image_evidence": [{"image_id": "red-button", "embedding_modality": "image_evidence"}],
            },
        )
        text_record = replace(
            _record("text", "docling", 1, 2, "textual evidence"),
            metadata={"adb_hybrid": {"retrieval_channels": ["vector:q1"]}},
        )
        settings = _rerank_ready_settings()

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(0, 0.95)],
        ) as rerank:
            reranked = rerank_records("red button", [image_record, text_record], settings, enabled=True)

        rerank.assert_called_once()
        self.assertEqual(len(rerank.call_args.args[1]), 1)
        self.assertEqual([record.id for record in reranked], ["image", "text"])
        self.assertTrue(reranked[0].metadata["rerank"]["skipped"])
        self.assertEqual(reranked[0].metadata["rerank"]["skip_reason"], "image_vector_only")

    def test_rerank_preserves_long_body_and_recovers_legacy_search_text_tail(self):
        body = "条件の説明\n" * 600 + "例外: 対象外"
        record = replace(_record("long", "docling", 1, 1, "Source file: manual\nChild text: " + body[:1500]), body_text=body)
        with patch("rag_engine.generation.answering.rerank_text_with_scores", return_value=[]) as rerank:
            rerank_records("例外は？", [record], _rerank_ready_settings(), enabled=True)
        document = rerank.call_args.args[1][0]
        self.assertTrue(document.startswith(body))
        self.assertIn("例外: 対象外", document)
        self.assertLessEqual(len(document) - len(body), 702)

    def test_rerank_document_starts_with_the_section_heading(self):
        """rerank の文書は見出しで始める。cross-encoder が画面・機能の一致を順位に反映しやすい (#1118)。"""
        from rag_engine.generation.answer_records import _rerank_document

        record = replace(
            _record("steps", "docling", 1, 1, "①抽出条件を入力します。"),
            metadata={"section_path": ["架空管理", "（２）伝票印刷設定", "B 【操作説明】"]},
        )
        plain = replace(_record("plain", "docling", 1, 1, "①抽出条件を入力します。"), metadata={})

        self.assertTrue(_rerank_document(record).startswith("見出し: 架空管理 > （２）伝票印刷設定 > B 【操作説明】\n"))
        self.assertIn("①抽出条件を入力します。", _rerank_document(record))
        self.assertFalse(_rerank_document(plain).startswith("見出し:"))

    def test_rerank_document_includes_visual_context_for_image_hits(self):
        record = replace(
            _record("visual", "docling", 1, 1, "screen evidence"),
            metadata={
                "adb_hybrid": {"retrieval_channels": ["vector:q1", "image_vector:q1"]},
                "image_evidence": [
                    {
                        "image_id": "red-button",
                        "page": 1,
                        "text_preview": "赤枠の実行ボタン",
                        "embedding_modality": "image_caption_fallback",
                    }
                ],
            },
        )
        settings = _rerank_ready_settings()

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(0, 0.95)],
        ) as rerank:
            rerank_records("赤枠のボタン", [record], settings, enabled=True)

        document = rerank.call_args.args[1][0]
        self.assertIn("Visual retrieval channels: image_vector:q1", document)
        self.assertIn("赤枠の実行ボタン", document)

    def test_build_answer_context_includes_same_page_cautions_for_top_record(self):
        records = [
            _record(
                "main",
                "docling",
                8,
                2,
                "回答用本文: 担当者付替画面で付替先と適用日を入力して営業担当を変更します。",
            ),
            _record(
                "caution",
                "docling",
                8,
                12,
                "指定した適用日以降に受注がない場合は得意先マスタから個別に営業担当を変更してください。",
            ),
            _record("noise", "docling", 9, 1, "営業担当 " * 20),
        ]

        context = build_answer_context("営業担当の付替で担当者をまとめて変更したい。", records, max_records=2)

        self.assertEqual([record.id for record in context.records], ["main", "caution"])

    def test_answer_image_evidence_reads_visuals_nested_in_table_context(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "run123" / "docling" / "vision" / "table-picture.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"png")
            record = replace(
                _record("chunk-1", "docling", 2, 3, "表内画像の説明"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id="chunk-1",
                metadata={
                    "table_context": [
                        {
                            "table_id": "table-1",
                            "page": 2,
                            "seq_no": 3,
                            "visual_evidence": [
                                {
                                    "image_id": "table-ocr",
                                    "record_id": "table-ocr",
                                    "page": 2,
                                    "seq_no": 5,
                                    "raw_type": "picture_ocr_text",
                                },
                                {
                                    "image_id": "table-picture-1",
                                    "record_id": "table-picture-1",
                                    "page": 2,
                                    "seq_no": 4,
                                    "vision_crop": "docling/vision/table-picture.png",
                                    "relationship": "contained_in_table",
                                },
                            ],
                        }
                    ]
                },
            )

            images = answer_image_evidence([record], root)

        self.assertEqual(images[0]["image_id"], "table-picture-1")
        self.assertEqual(images[0]["prompt_path"], str(crop))
        self.assertEqual(images[0]["asset_kind"], "crop")

    def test_answer_image_evidence_uses_native_table_crop(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "run123" / "docling" / "visuals" / "table.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"png")
            record = replace(
                _record("chunk-1", "docling", 2, 3, "表の構造化テキスト"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id="chunk-1",
                metadata={
                    "image_evidence": [
                        {
                            "image_id": "table-1",
                            "record_id": "table-1",
                            "source_run_id": "run123",
                            "page": 2,
                            "seq_no": 3,
                            "raw_type": "table",
                            "relationship": "source_table_crop",
                            "crop_path": "docling/visuals/table.png",
                            "bbox": [1, 2, 80, 90],
                            "embedding_modality": "image_caption_fallback",
                        }
                    ]
                },
            )

            images = answer_image_evidence([record], root)

        self.assertEqual(images[0]["image_id"], "table-1")
        self.assertEqual(images[0]["raw_type"], "table")
        self.assertEqual(images[0]["relationship"], "source_table_crop")
        self.assertEqual(images[0]["prompt_path"], str(crop))
        self.assertEqual(images[0]["asset_kind"], "crop")

    def test_answer_image_evidence_skips_decorative_visuals_from_legacy_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = replace(
                _record("chunk-1", "docling", 2, 3, "操作説明"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id="chunk-1",
                metadata={
                    "image_evidence": [
                        {
                            "image_id": "footer-logo",
                            "source_run_id": "run123",
                            "page": 2,
                            "seq_no": 22,
                            "visual_role": "decorative",
                            "rag_excluded": True,
                        },
                        {
                            "image_id": "ocr-aggregate",
                            "source_run_id": "run123",
                            "page": 2,
                            "seq_no": 23,
                            "raw_type": "picture_ocr_text",
                        },
                        {
                            "image_id": "body-picture",
                            "source_run_id": "run123",
                            "page": 2,
                            "seq_no": 5,
                            "bbox": [10, 10, 50, 50],
                        },
                    ]
                },
            )

            images = answer_image_evidence([record], root)

        self.assertEqual([image["image_id"] for image in images], ["body-picture"])

    def test_answer_image_evidence_counts_pictures_known_only_by_their_ocr(self):
        """OCR の entry は持ち主の図の画像として数え、図と OCR が両方あれば 1 回だけ（#3455）。"""

        def ocr(page, bbox, text, **extra):
            return {"image_id": f"ocr-{page}", "raw_type": "picture_ocr_text", "page": page, "bbox": bbox, "text_preview": text, **extra}

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for page in (1, 2):
                path = root / "run123" / "pages" / f"page_{page:04d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"png")
            ocr_only = replace(
                _record("chunk-1", "docling", 1, 1, "操作の説明"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id="chunk-1",
                metadata={"image_evidence": [ocr(1, [10, 10, 50, 50], "OCR抽出テキスト: 保存 取消", picture_record_id="picture-1")]},
            )
            both = replace(
                _record("chunk-2", "docling", 2, 1, "操作の説明"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id="chunk-2",
                metadata={
                    "image_evidence": [
                        {"image_id": "picture-2", "raw_type": "picture", "page": 2, "bbox": [10, 10, 50, 50]},
                        ocr(2, [10.4, 10, 50, 50], "OCR抽出テキスト: 印刷"),
                        ocr(2, [60, 60, 90, 90], "OCR抽出テキスト: 社章", visual_role="decorative", rag_excluded=True),
                        {"image_id": "ocr-unplaced", "raw_type": "picture_ocr_text", "page": 2, "text_preview": "位置の無い OCR"},
                    ]
                },
            )

            images = answer_image_evidence([ocr_only, both], root, question="保存の操作は？")

        self.assertEqual([image["image_id"] for image in images], ["picture-1", "picture-2"])
        self.assertEqual([image["raw_type"] for image in images], ["picture", "picture"])
        self.assertEqual(images[0]["prompt_path"], str(root / "run123" / "pages" / "page_0001.png"))
        self.assertEqual(images[0]["bbox"], [10, 10, 50, 50])
        # OCR の文字が図の関連度に入り、質問の語を含む図が先に並ぶ。
        self.assertGreater(images[0]["selection_score"], images[1]["selection_score"])

    def test_answer_image_evidence_keeps_the_attachment_budget_for_ocr_only_pictures(self):
        """OCR だけの図も上限（MAX_ANSWER_IMAGE_ATTACHMENTS=4）と根拠の順を守る（#3455）。"""
        records = [
            replace(
                _record(f"chunk-{index}", "docling", index, 1, "操作の説明"),
                source="manual.pdf",
                source_run_id="run123",
                chunk_id=f"chunk-{index}",
                metadata={
                    "image_evidence": [
                        {
                            "image_id": f"ocr-{index}",
                            "raw_type": "picture_ocr_text",
                            "picture_record_id": f"picture-{index}",
                            "page": index,
                            "bbox": [10, 10, 50, 50],
                            "text_preview": "OCR抽出テキスト: 保存",
                        }
                    ]
                },
            )
            for index in range(1, 6)
        ]

        with TemporaryDirectory() as tmp:
            images = answer_image_evidence(records, tmp)

        self.assertEqual([image["image_id"] for image in images], ["picture-1", "picture-2", "picture-3", "picture-4"])

    def test_format_answer_response_extracts_json_answer(self):
        raw = json.dumps(
            {
                "answer": "登録手順を確認してください。",
                "confidence": "high",
                "question_type": ["操作手順を聞く"],
                "cited_evidence": [
                    {
                        "evidence_id": "r1",
                        "page": "2",
                        "look_at": "中央",
                        "visible_evidence": "契約区分",
                    }
                ],
                "reasoning_summary": "手順が読めます。",
                "insufficient_reason": "",
                "needs_human_review": False,
            },
            ensure_ascii=False,
        )

        formatted = format_answer_response(f"```json\n{raw}\n```")

        self.assertIn("登録手順を確認してください。", formatted)
        self.assertIn("信頼度: high", formatted)
        self.assertIn("問い合わせ型: 操作手順を聞く", formatted)
        self.assertIn("- r1 / p.2 / 中央: 契約区分", formatted)
        self.assertIn("人手確認: 不要", formatted)

    def test_format_answer_response_keeps_plain_text(self):
        self.assertEqual(format_answer_response("そのままの回答"), "そのままの回答")

    def test_format_answer_response_accepts_control_chars_in_json_strings(self):
        raw = '{"answer":"列A\t列B","confidence":"high","needs_human_review":false}'

        formatted = format_answer_response(raw)

        self.assertIn("列A\t列B", formatted)
        self.assertIn("信頼度: high", formatted)
        self.assertIn("人手確認: 不要", formatted)

    def test_answer_question_handles_no_records_without_calling_oci(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            _write_viewer_data(output_dir, "abcdef", records=[])
            settings = replace(get_settings(), output_dir=output_dir)

            with patch("rag_engine.generation.answering.parse_text_response") as parse:
                answer = answer_question("概要は？", "abcdef", ["docling"], settings)

        self.assertIn("ADB hybrid search", answer)
        self.assertIn("チャンキング", answer)
        parse.assert_not_called()

    def test_rerank_retains_queries_with_additional_conditions(self):
        """原質問を含んでいても追加条件を持つ検索文は別に比べる。"""
        from types import SimpleNamespace

        from rag_engine.generation.answering import _best_rerank_ranks

        called = []

        def fake_rerank(query, documents, settings, top_n=None):
            called.append(query)
            return [RerankTextRank(0, 0.5)]

        with patch("rag_engine.generation.answering.rerank_text_with_scores", side_effect=fake_rerank):
            _best_rerank_ranks("原質問", ["原質問\n追加条件", "要求の一部"], ["a"], None, SimpleNamespace(add=lambda m: None))
        self.assertEqual(called, ["原質問", "原質問\n追加条件", "要求の一部"])

    def test_rerank_uses_best_score_over_original_and_extra_queries(self):
        """要求の一部にだけ答える候補も、検索文との関連度が高ければ上位にする。追加の検索文の失敗は飛ばす（#2161）。"""
        from types import SimpleNamespace

        from rag_engine.generation.answering import _best_rerank_ranks

        scores = {
            "長い事例の質問": [RerankTextRank(0, 0.9), RerankTextRank(1, 0.4), RerankTextRank(2, 0.3)],
            "要求の一部": [RerankTextRank(1, 0.95), RerankTextRank(0, 0.5), RerankTextRank(2, 0.2)],
        }

        def fake_rerank(query, documents, settings, top_n=None):
            if query == "失敗する検索文":
                raise RuntimeError("rerank failed")
            return scores[query]

        step = SimpleNamespace(add=lambda message: None)
        with patch("rag_engine.generation.answering.rerank_text_with_scores", side_effect=fake_rerank):
            ranks = _best_rerank_ranks("長い事例の質問", ["要求の一部", "失敗する検索文", "長い事例の質問"], ["a", "b", "c"], None, step)
        self.assertEqual([(rank.index, rank.relevance_score) for rank in ranks], [(1, 0.95), (0, 0.9), (2, 0.3)])
        # 原質問だけの点数も残す（文書の絞り込みが原質問の点数でも比べる。#3504）。
        self.assertEqual([rank.question_relevance_score for rank in ranks], [0.4, 0.9, 0.3])

    def test_chunk_context_reranks_child_candidates_before_expansion(self):
        records = [
            _chunk_record("first-child", "child", 1, "parent-1", "needle first child"),
            _chunk_record("second-child", "child", 2, "parent-1", "needle second child"),
            _chunk_record("parent-1", "parent", 1, "", "parent evidence", child_ids=["first-child", "second-child"]),
        ]

        with patch(
            "rag_engine.generation.answering.rerank_text_with_scores",
            return_value=[RerankTextRank(1, 0.92), RerankTextRank(0, 0.77)],
        ):
            context = build_chunk_answer_context(
                "needle",
                records,
                top_k=1,
                neighbor_child_count=0,
                max_records=3,
                rerank_enabled=True,
                settings=_rerank_ready_settings(),
            )

        self.assertEqual([record.id for record in context.records], ["parent-1"])
        self.assertEqual(context.evidence_tree[0].anchor_child_ids, ("second-child",))

    def test_build_chunk_answer_context_expands_parent_and_neighbor_children(self):
        records = [
            _chunk_record("chunk-docling-c000001", "child", 1, "parent-1", "前の注意"),
            _chunk_record("chunk-docling-c000002", "child", 2, "parent-1", "契約区分の登録手順 needle"),
            _chunk_record("chunk-docling-c000003", "child", 3, "parent-1", "後の注意"),
            _chunk_record(
                "parent-1",
                "parent",
                1,
                "",
                "親チャンク全文 needle",
                child_ids=["chunk-docling-c000001", "chunk-docling-c000002", "chunk-docling-c000003"],
            ),
        ]

        context = build_chunk_answer_context("needle", records, top_k=1, neighbor_child_count=1, max_records=4)

        self.assertEqual([record.id for record in context.records], ["parent-1"])
        self.assertIn("ParentChunk", context.text)
        self.assertIn("chunk-docling-c000002 / retrieved_anchor", context.text)
        self.assertEqual(context.evidence_tree[0].anchor_child_ids, ("chunk-docling-c000002",))

    def test_build_chunk_answer_context_filters_inactive_chunks(self):
        records = [
            _chunk_record("inactive-child", "child", 1, "inactive-parent", "needle を含む無効チャンク", active=False),
            _chunk_record("inactive-parent", "parent", 1, "", "needle を含む無効親チャンク", child_ids=["inactive-child"], active=False),
            _chunk_record("active-child", "child", 2, "active-parent", "有効チャンクだけを使います。"),
            _chunk_record("active-parent", "parent", 2, "", "有効親チャンクです。", child_ids=["active-child"]),
        ]

        context = build_chunk_answer_context("needle", records, top_k=1, neighbor_child_count=1, max_records=4)

        self.assertEqual([record.id for record in context.records], ["active-parent"])
        self.assertNotIn("無効チャンク", context.text)

    def test_table_row_image_usage_marks_owning_child_and_parent_only(self):
        image_id = "table-row-2-image-1"
        for metadata in (
            {"image_evidence": [{"image_id": image_id, "record_id": "table"}]},
            {"table_context": [{"visual_evidence": [{"image_id": image_id, "record_id": "table"}]}]},
        ):
            child = replace(_chunk_record("child-1", "child", 1, "parent-1", "画像の説明"), metadata=metadata)
            other = _chunk_record("child-2", "child", 2, "parent-1", "別の行")
            parent = _chunk_record("parent-1", "parent", 1, "", "表全体", child_ids=["child-1", "child-2"])
            tree = (
                ContextParentEvidence(
                    record=parent,
                    role="synthesis_parent",
                    reason="parent_of_retrieved_child",
                    children=tuple(
                        ContextChildEvidence(record=c, role="retrieved_anchor", reason="top_retrieved_child", retrieval_rank=i)
                        for i, c in enumerate([child, other], 1)
                    ),
                ),
            )
            items = answer_evidence_items([parent], [image_id], evidence_tree=tree)
            self.assertTrue(items[0]["is_model_used"])
            self.assertEqual({c["id"]: c["is_model_used"] for c in items[0]["children"]}, {"child-1": True, "child-2": False})

    def test_answer_evidence_items_carry_answer_use_and_parent_prefers_confirmed(self):
        # 回答に使った箇所の使われ方（#2040）。親は子のうち 1 つでも確かめた説明で使えば confirmed。
        confirmed = _chunk_record("child-1", "child", 1, "parent-1", "確かめた説明の箇所")
        reference = _chunk_record("child-2", "child", 2, "parent-1", "参考の箇所")
        unused = _chunk_record("child-3", "child", 3, "parent-1", "使っていない箇所")
        parent = _chunk_record("parent-1", "parent", 1, "", "親", child_ids=["child-1", "child-2", "child-3"])
        tree = (
            ContextParentEvidence(
                record=parent,
                role="synthesis_parent",
                reason="parent_of_retrieved_child",
                children=tuple(
                    ContextChildEvidence(record=c, role="retrieved_anchor", reason="top_retrieved_child", retrieval_rank=i)
                    for i, c in enumerate([confirmed, reference, unused], 1)
                ),
            ),
        )
        uses = _cited_evidence_uses(
            [
                {"evidence_id": "child-2", "answer_use": "reference"},
                {"evidence_id": "child-1", "answer_use": "reference"},
                {"evidence_id": "child-1", "answer_use": "confirmed"},
                {"evidence_id": "child-3", "answer_use": "unknown"},
            ]
        )
        self.assertEqual(uses, {"child-1": "confirmed", "child-2": "reference"})
        items = answer_evidence_items([parent], ["child-1", "child-2"], evidence_tree=tree, answer_uses=uses)
        self.assertEqual(
            {c["id"]: c["answer_use"] for c in items[0]["children"]},
            {"child-1": "confirmed", "child-2": "reference", "child-3": None},
        )
        self.assertEqual(items[0]["answer_use"], "confirmed")
        # 使われ方が分からない（記録の無い回答の流れ）ときは None のまま。
        self.assertIsNone(answer_evidence_items([confirmed], ["child-1"])[0]["answer_use"])

    def test_answer_evidence_items_sort_model_used_by_usage_priority(self):
        records = [
            replace(
                _chunk_record("unused-low", "child", 4, "", "unused low score evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.1}},
            ),
            replace(
                _chunk_record("unused-high", "child", 1, "", "unused high score evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.99}},
            ),
            replace(
                _chunk_record("used-second", "child", 2, "", "used second priority evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.99}},
            ),
            replace(
                _chunk_record("used-first", "child", 3, "", "used first priority evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.2}},
            ),
        ]

        items = answer_evidence_items(records, ["used-first", "used-second"])

        self.assertEqual([item["id"] for item in items], ["used-first", "used-second", "unused-high", "unused-low"])
        self.assertEqual(
            {item["id"]: item["model_usage_rank"] for item in items},
            {
                "used-first": 1,
                "used-second": 2,
                "unused-high": None,
                "unused-low": None,
            },
        )

    def test_answer_evidence_items_sort_unused_chunks_by_relevance_score(self):
        records = [
            replace(
                _chunk_record("unused-low", "child", 1, "", "unused low score evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.1}},
            ),
            _chunk_record("unused-no-score", "child", 2, "", "unused source-order evidence"),
            replace(
                _chunk_record("unused-high", "child", 3, "", "unused high score evidence"),
                metadata={"adb_hybrid": {"rrf_score": 0.88}},
            ),
        ]

        items = answer_evidence_items(records, [])

        self.assertEqual([item["id"] for item in items], ["unused-high", "unused-low", "unused-no-score"])

    def test_answer_evidence_items_expose_hybrid_channel_diagnostics(self):
        record = replace(
            _chunk_record("chunk-1", "child", 1, "", "needle evidence"),
            metadata={
                "adb_hybrid": {
                    "rrf_score": 0.42,
                    "vector_rank": 2,
                    "text_rank": 1,
                    "profile_rank": 1,
                    "vector_distance": 0.12,
                    "text_score": 9.0,
                    "profile_score": 3.5,
                    "retrieval_channels": ["vector:q1", "profile:file_data_confirmation"],
                    "channel_ranks": {"vector:q1": 2, "profile:file_data_confirmation": 1},
                    "channel_scores": {"vector:q1": 0.89, "profile:file_data_confirmation": 3.5},
                }
            },
        )

        item = answer_evidence_items([record])[0]

        self.assertEqual(item["hybrid_score"], 0.42)
        self.assertEqual(item["profile_rank"], 1)
        self.assertEqual(item["retrieval_channels"], ["vector:q1", "profile:file_data_confirmation"])
        self.assertEqual(item["channel_ranks"]["profile:file_data_confirmation"], 1)

    def test_answer_evidence_items_emit_parent_child_tree_payload(self):
        child = _chunk_record(
            "child-1",
            "child",
            1,
            "parent-1",
            "needle child",
            chunk_uid="chunk-run:child-1",
            parent_chunk_uid="chunk-run:parent-1",
        )
        parent = _chunk_record(
            "parent-1",
            "parent",
            1,
            "",
            "complete parent synthesis",
            child_ids=["child-1"],
            chunk_uid="chunk-run:parent-1",
        )
        tree = (
            ContextParentEvidence(
                record=parent,
                role="synthesis_parent",
                reason="parent_of_retrieved_child",
                children=(
                    ContextChildEvidence(
                        record=child,
                        role="retrieved_anchor",
                        reason="top_retrieved_child",
                        retrieval_rank=1,
                    ),
                ),
            ),
        )

        items = answer_evidence_items([parent], ["child-1"], evidence_tree=tree)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "parent-1")
        self.assertEqual(items[0]["chunk_uid"], "chunk-run:parent-1")
        self.assertEqual(items[0]["chunk_level"], "parent")
        self.assertEqual(items[0]["anchor_child_chunk_ids"], ["child-1"])
        self.assertEqual(items[0]["children"][0]["id"], "child-1")
        self.assertEqual(items[0]["children"][0]["chunk_uid"], "chunk-run:child-1")
        self.assertEqual(items[0]["children"][0]["parent_chunk_uid"], "chunk-run:parent-1")
        self.assertEqual(items[0]["children"][0]["retrieval_role"], "retrieved_anchor")
        self.assertEqual(items[0]["children"][0]["retrieval_rank"], 1)

    def test_answer_question_result_does_not_fallback_to_record_evidence(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            _write_viewer_data(output_dir, "abcdef", records=[_raw_record("r1", 1, 1, "record fallback の本文です。")])
            settings = replace(get_settings(), output_dir=output_dir)

            with (
                patch("rag_engine.generation.answering.load_answer_records") as load_records,
                patch("rag_engine.generation.answering.parse_text_response") as parse,
            ):
                result = answer_question_result(
                    "fallback は？",
                    "abcdef",
                    ["docling"],
                    settings,
                )

        self.assertIn("ADB hybrid search", result.answer_text)
        self.assertEqual(result.evidence_items, ())
        load_records.assert_not_called()
        parse.assert_not_called()

    def test_answer_question_does_not_fallback_to_records_when_adb_unavailable(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            _write_viewer_data(
                output_dir,
                "abcdef",
                records=[
                    _raw_record("r1", 1, 1, "record fallback の本文です。"),
                ],
            )
            result = create_chunk_run(
                output_dir=output_dir,
                run_id="abcdef",
                preferred_engine_ids=["docling"],
                config=ChunkingConfig(),
            )
            _set_chunk_run_active(Path(result.json_path), False)
            settings = replace(get_settings(), output_dir=output_dir)

            with (
                patch(
                    "rag_engine.generation.answering.check_adb_hybrid_search_ready",
                    side_effect=AdbHybridSearchUnavailable("schema not created"),
                ),
                patch("rag_engine.generation.answering.load_answer_records") as load_records,
                patch("rag_engine.generation.answering.parse_text_response") as parse,
            ):
                answer = answer_question(
                    "fallback は？",
                    "abcdef",
                    ["docling"],
                    settings,
                )

        self.assertIn("ADB hybrid search を使用できません", answer)
        self.assertIn("schema not created", answer)
        load_records.assert_not_called()
        parse.assert_not_called()


def _answer_output(
    *,
    answer: str = "answer",
    confidence: str = "",
    question_type: list[str] | None = None,
    cited_evidence_id: str = "r1",
    reasoning_summary: str = "",
    insufficient_reason: str = "",
    needs_human_review: bool | None = None,
) -> AnswerOutput:
    cited_evidence = []
    if cited_evidence_id:
        cited_evidence.append(
            CitedEvidenceOutput(
                evidence_id=cited_evidence_id,
                source="manual.pdf",
                page="1",
                look_at="本文",
                visible_evidence="evidence",
            )
        )
    return AnswerOutput(
        answer=answer,
        confidence=confidence,
        question_type=question_type or [],
        cited_evidence=cited_evidence,
        reasoning_summary=reasoning_summary,
        insufficient_reason=insufficient_reason,
        needs_human_review=needs_human_review,
    )


def _record(record_id: str, engine: str, page: int, seq_no: int, text: str) -> AnswerRecord:
    return AnswerRecord(
        id=record_id,
        engine=engine,
        engine_label=engine,
        page=page,
        seq_no=seq_no,
        category="Text",
        text=text,
    )


def _chunk_record(
    record_id: str,
    level: str,
    chunk_seq: int,
    parent_id: str,
    text: str,
    *,
    child_ids: list[str] | None = None,
    active: bool = True,
    metadata: dict | None = None,
    source_run_id: str = "",
    source: str = "",
    chunk_uid: str = "",
    parent_chunk_uid: str = "",
) -> AnswerRecord:
    return AnswerRecord(
        id=record_id,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=chunk_seq,
        category="ParentChunk" if level == "parent" else "ChildChunk",
        text=text,
        source=source,
        source_run_id=source_run_id,
        chunk_id=record_id,
        chunk_uid=chunk_uid,
        chunk_level=level,
        chunk_seq=chunk_seq,
        parent_chunk_id=parent_id,
        parent_chunk_uid=parent_chunk_uid,
        child_chunk_ids=tuple(child_ids or []),
        page_end=1,
        source_seq_ranges=({"page": 1, "seq_start": chunk_seq, "seq_end": chunk_seq},),
        source_record_refs=({"record_id": f"r{chunk_seq}", "page": 1, "seq_no": chunk_seq, "category": "Text"},),
        metadata=metadata
        if metadata is not None
        else {
            "active": active,
            "file_name": "manual.pdf",
            "lifecycle_status": "active" if active else "inactive",
        },
    )


def _chunk_run_stub():
    return SimpleNamespace(chunk_run_id="abcdef-1234567890ab")


def _rerank_ready_settings():
    """外部環境に依存しない rerank 有効設定を返します。"""
    return replace(
        get_settings(),
        default_rerank_enabled=True,
        rerank_model="cohere.rerank-v4.0-fast",
        oci_compartment_id="ocid1.compartment.oc1..test",
        oci_rerank_endpoint="https://inference.generativeai.ap-osaka-1.oci.oraclecloud.com",
        rerank_min_relevance_score=0.0,
    )


def _answer_context(records: list[AnswerRecord]) -> AnswerContext:
    lines = [f"[{record.citation} / {record.engine_label} / {record.category}] {record.text}" for record in records]
    return AnswerContext(records=list(records), text="\n".join(lines))


def _hybrid_result(*children: StoredChunk) -> HybridSearchResult:
    return HybridSearchResult(child_chunks=list(children), all_chunks=list(children))


def _stored_child(chunk_id: str, chunk_seq: int, text: str, *, metadata: dict | None = None) -> StoredChunk:
    return StoredChunk(
        chunk_uid=f"abcdef-1234567890ab:{chunk_id}",
        chunk_id=chunk_id,
        chunk_level="child",
        chunk_seq=chunk_seq,
        parent_chunk_uid="",
        parent_chunk_id="",
        child_chunk_ids=(),
        text=text,
        retrieval_text=text,
        source_run_id="abcdef",
        source_file_name="manual.pdf",
        source_engine_id="docling",
        source_engine_label="Docling",
        page_start=1,
        page_end=1,
        source_seq_ranges=({"page": 1, "seq_start": chunk_seq, "seq_end": chunk_seq},),
        source_record_refs=(
            {
                "record_id": f"r{chunk_seq}",
                "page": 1,
                "seq_no": chunk_seq,
                "category": "Text",
                "bbox": [0, 0, 10, 10],
            },
        ),
        metadata=metadata or {"active": True},
    )


def _raw_record(record_id: str, page: int, seq_no: int, text: str) -> dict:
    return {
        "id": record_id,
        "engine": "docling",
        "page": page,
        "seq_no": seq_no,
        "text": text,
        "category": "Text",
        "bbox": [0, 0, 10, 10],
        "coord_system": "image_top_left",
        "page_width": 100,
        "page_height": 100,
        "raw_type": "text",
        "raw": {},
    }


def _set_chunks_inactive_when_text_contains(json_path: Path, text: str) -> None:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    inactive_child_ids = set()
    for chunk in payload["chunks"]:
        if chunk["chunk_level"] == "child" and text in chunk["text"]:
            chunk["metadata"]["active"] = False
            inactive_child_ids.add(chunk["chunk_id"])
    for chunk in payload["chunks"]:
        if chunk["chunk_level"] == "parent" and inactive_child_ids & set(chunk.get("child_chunk_ids") or []):
            chunk["metadata"]["active"] = False
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _set_chunk_run_active(json_path: Path, active: bool) -> None:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["active"] = active
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_viewer_data(output_dir: Path, run_id: str, records: list[dict]) -> None:
    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True)
    payload = {
        "run_id": run_id,
        "pdf_name": "manual.pdf",
        "pages": [],
        "records": records,
        "engines": [
            {"engine": "docling", "label": "Docling"},
            {"engine": "archived_parser", "label": "Archived parser"},
        ],
    }
    (run_dir / "viewer-data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
