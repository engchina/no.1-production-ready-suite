"""classification の挙動を保護するテスト。"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from docrag.knowledge.classification import (
    DEFAULT_CATEGORY_VALUE,
    classification_filter_from_values,
    classification_from_metadata,
    classification_from_selection,
    classification_options_from_share,
    infer_classification_from_path,
    middle_categories_for_large,
    normalize_classification_payload,
    small_categories_from_saved_metadata,
)


class ClassificationTests(unittest.TestCase):
    def test_infers_three_level_classification_from_share_path(self):
        path = Path(
            "share/20260101_20_在庫管理/20_在庫管理/20_操作説明書/Ver4.0/6_倉庫連携_41.pdf"
        )

        classification = infer_classification_from_path(path)

        self.assertEqual(classification.large_category, "20_在庫管理")
        self.assertEqual(classification.middle_category, "20_操作説明書")
        self.assertEqual(classification.small_category, "倉庫連携")
        self.assertEqual(classification.source, "path_default")
        self.assertIn("20_操作説明書", classification.path_parts)

    def test_manual_selection_keeps_blank_dependent_levels_blank(self):
        classification = classification_from_selection(
            source_path="share/20260101_10_販売管理/10_販売管理/20_操作説明書/Ver3.0/（４）受注入力.pdf",
            large_category="在庫管理",
            middle_category="",
            small_category="補助管理",
        )

        self.assertEqual(classification.large_category, "20_在庫管理")
        self.assertEqual(classification.middle_category, "")
        self.assertEqual(classification.small_category, "補助管理")
        self.assertEqual(classification.source, "manual")

    def test_manual_small_category_preserves_inferred_parent_categories(self):
        classification = classification_from_selection(
            source_path="share/10_販売管理/20_操作説明書/foo.pdf",
            small_category="拠点名変更",
        )

        self.assertEqual(classification.large_category, "10_販売管理")
        self.assertEqual(classification.middle_category, "20_操作説明書")
        self.assertEqual(classification.small_category, "拠点名変更")
        self.assertEqual(classification.source, "manual")

    def test_middle_category_accepts_free_text_for_storage_restore_and_filtering(self):
        classification = classification_from_selection(
            source_path="share/10_販売管理/20_操作説明書/foo.pdf",
            large_category="販売管理",
            middle_category="取引先独自マニュアル",
        )
        restored = classification_from_metadata(classification.to_metadata())
        filter_value = classification_filter_from_values(middle_category="取引先独自マニュアル")

        self.assertEqual(classification.large_category, "10_販売管理")
        self.assertEqual(classification.middle_category, "取引先独自マニュアル")
        self.assertEqual(restored.middle_category, "取引先独自マニュアル")
        self.assertEqual(filter_value.middle_category, "取引先独自マニュアル")
        self.assertTrue(filter_value.active)

    def test_unknown_file_defaults_to_uncategorized(self):
        classification = infer_classification_from_path("/tmp/manual.pdf", source_root="/tmp/share")

        self.assertEqual(classification.large_category, DEFAULT_CATEGORY_VALUE)
        self.assertEqual(classification.middle_category, DEFAULT_CATEGORY_VALUE)
        self.assertEqual(classification.small_category, "manual")

    def test_filter_values_are_optional_and_legacy_uncategorized_is_blank(self):
        self.assertFalse(classification_filter_from_values().active)

        filter_value = classification_filter_from_values(large_category=DEFAULT_CATEGORY_VALUE)

        self.assertFalse(filter_value.active)
        self.assertEqual(filter_value.to_metadata(), {})
        self.assertFalse(classification_filter_from_values(large_category="未分類").active)

    def test_legacy_category_values_are_canonicalized(self):
        classification = classification_from_metadata(
            {
                "large_category": "在庫管理",
                "middle_category": "操作説明書",
                "small_category": "未分類",
                "source": "legacy",
            }
        )

        self.assertEqual(classification.large_category, "20_在庫管理")
        self.assertEqual(classification.middle_category, "20_操作説明書")
        self.assertEqual(classification.small_category, "")
        self.assertEqual(classification.source, "legacy")

    def test_middle_categories_are_scoped_by_large_category(self):
        self.assertEqual(
            middle_categories_for_large("10_販売管理"),
            ("20_操作説明書", "30_運用説明書", "40_Q&A", "50_リリース通知"),
        )
        self.assertEqual(
            middle_categories_for_large("在庫管理"),
            ("20_操作説明書", "30_運用説明書", "40_Q&A", "50_リリース通知"),
        )
        self.assertEqual(middle_categories_for_large(""), ())

    def test_normalize_classification_payload_updates_nested_metadata(self):
        payload = {
            "classification": {
                "large_category": "在庫管理",
                "middle_category": "操作説明書",
                "small_category": "未分類",
                "source": "legacy",
            },
            "chunks": [
                {
                    "metadata": {
                        "classification": {
                            "large_category": "販売管理",
                            "middle_category": "Q&A",
                            "small_category": "受注入力",
                        }
                    }
                }
            ],
            "classification_filter": {
                "large_category": "在庫管理",
                "middle_category": "操作説明書",
            },
        }

        changed = normalize_classification_payload(payload)

        self.assertEqual(changed, 3)
        self.assertEqual(payload["classification"]["large_category"], "20_在庫管理")
        self.assertEqual(payload["classification"]["middle_category"], "20_操作説明書")
        self.assertEqual(payload["classification"]["small_category"], "")
        self.assertEqual(payload["chunks"][0]["metadata"]["classification"]["large_category"], "10_販売管理")
        self.assertEqual(payload["chunks"][0]["metadata"]["classification"]["middle_category"], "40_Q&A")
        self.assertEqual(payload["chunks"][0]["metadata"]["classification"]["small_category"], "受注入力")
        self.assertEqual(payload["classification_filter"]["large_category"], "20_在庫管理")
        self.assertEqual(payload["classification_filter"]["middle_category"], "20_操作説明書")

    def test_share_options_include_known_categories_and_detected_topics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "share"
            target = root / "20260101_10_販売管理" / "10_販売管理" / "20_操作説明書" / "Ver3.0"
            target.mkdir(parents=True)
            (target / "（２）売掛金管理.pdf").write_bytes(b"%PDF-1.4\n")

            options = classification_options_from_share(root)

        self.assertIn("10_販売管理", options.large_categories)
        self.assertIn("20_操作説明書", options.middle_categories)
        self.assertEqual(options.middle_categories_by_large["10_販売管理"][0], "20_操作説明書")
        self.assertIn("売掛金管理", options.small_categories)

    def test_saved_metadata_small_categories_are_collected(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "runs"
            run_dir = output_dir / "abcdef"
            run_dir.mkdir(parents=True)
            (run_dir / "viewer-data.json").write_text(
                json.dumps(
                    {
                        "classification": {
                            "large_category": "在庫管理",
                            "middle_category": "操作説明書",
                            "small_category": "倉庫連携",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "answer.json").write_text(
                json.dumps({"classification": {"small_category": "未分類"}}, ensure_ascii=False),
                encoding="utf-8",
            )

            small_categories = small_categories_from_saved_metadata(output_dir)

        self.assertEqual(small_categories, ("倉庫連携",))


if __name__ == "__main__":
    unittest.main()
