"""rag_engine の数・単位・元号・文末の書き方は言語資源から作る(#2181 段 1b-2)。"""

import unittest

from rag_parser_core.language import bind_pack, builtin_pack, resolve

from rag_engine.chunking.headings import _heading_patterns


def _custom(**sections):
    return resolve(builtin_pack(), sections)


class LanguagePackEngineTest(unittest.TestCase):
    def test_acquisition_notice_uses_display_pack_without_changing_ingest(self):
        from rag_engine.generation.answer_labels import answer_labels

        original = answer_labels().guide_acquisition_incomplete_template
        custom = _custom(answer_labels={"guide_acquisition_incomplete_template": "合成の未完了: {steps}"})
        with bind_pack(custom):
            self.assertEqual(answer_labels().guide_acquisition_incomplete_template.format(steps="s1"), "合成の未完了: s1")
            self.assertEqual(custom.ingest_hash, builtin_pack().ingest_hash)
        self.assertEqual(answer_labels().guide_acquisition_incomplete_template, original)

    def test_heading_levels_follow_units(self):
        def levels(text):
            return [level for pattern, level in _heading_patterns().levels if pattern.match(text)]

        self.assertEqual(levels("第2部 概要"), [1])
        self.assertEqual(levels("第2巻 概要"), [])
        with bind_pack(_custom(numbers={"part_units": ["編", "部", "巻"]})):
            self.assertEqual(levels("第2巻 概要"), [1])

    def test_sentence_like_heading_follows_endings(self):
        self.assertTrue(_heading_patterns().sentence_heading.search("入力します。"))
        self.assertFalse(_heading_patterns().sentence_heading.search("〜とは？"))
        with bind_pack(_custom(symbols={"sentence_like_endings": ["。", "ます", "とは？"]})):
            self.assertTrue(_heading_patterns().sentence_heading.search("〜とは？"))

    def test_content_labels_follow_the_pack(self):
        """表・図の内容のラベルは書く側と読む側が同じ言語資源から作る(#2181 段 2c)。"""
        from rag_engine.generation.grounded import _without_screen_examples
        from rag_engine.parsing.content_labels import content_labels
        from rag_engine.parsing.picture_descriptions import vision_description_text
        from rag_engine.parsing.table_structure import _fixed_group_pieces

        payload = {"retrieval_text": "画面の説明", "visible_buttons": ["保存"], "visible_values": ["ID=A1"]}
        structure = {"caption": "一覧", "column_headers": [{"column_index": 0, "text": "名前"}]}
        self.assertIn("■ 要点\n回答用本文: 画面の説明", vision_description_text(payload))
        self.assertEqual(_fixed_group_pieces(structure, ""), ["表タイトル: 一覧", "列見出し: 名前"])
        custom = _custom(
            content_labels={
                "table_title": "表の名前",
                "vision_group_prefix": "◆",
                "vision_groups": ["概要=summary", "見えるもの=visible"],
                "vision_fields": ["説明=retrieval_text", "押すもの=visible_buttons", "例の値=visible_values"],
            }
        )
        with bind_pack(custom):
            text = vision_description_text(payload)
            self.assertIn("◆ 概要\n説明: 画面の説明", text)
            self.assertIn("押すもの: 保存", text)
            self.assertEqual(_fixed_group_pieces(structure, "")[0], "表の名前: 一覧")
            self.assertTrue(content_labels().generated.search(text))
            self.assertNotIn("A1", _without_screen_examples(text))

    def test_search_terms_follow_the_pack(self):
        """全文検索の質問の語（止め語・1 字の語・語の端の助詞）は言語資源から作る(#2181 段 3)。"""
        from rag_engine.retrieval.text_search_tokenizer import (
            TextSearchTokenizerConfig,
            _keep_token,
            _trim_japanese_particle_edges,
            tokenize_text_search_query,
            tokenizer_fingerprint,
        )

        self.assertFalse(_keep_token("こと", frozenset({"こと"})))
        self.assertTrue(_keep_token("図", frozenset()))
        self.assertEqual(_trim_japanese_particle_edges("申請を"), "申請")
        before = tokenizer_fingerprint(TextSearchTokenizerConfig())
        custom = _custom(
            search_terms={
                "excluded_terms": ["申請"],
                "particle_edge_chars": ["が"],
            }
        )
        with bind_pack(custom):
            self.assertFalse(_keep_token("申請", frozenset()))
            self.assertEqual(_trim_japanese_particle_edges("申請を"), "申請を")
            self.assertNotIn("申請", tokenize_text_search_query("申請の期限"))
            self.assertNotEqual(tokenizer_fingerprint(TextSearchTokenizerConfig()), before)

    def test_subquestion_markers_follow_the_pack(self):
        """質問の小問の印は言語資源から作る(#2181 段 4)。"""
        from rag_engine.retrieval.task_contract import subquestion_markers

        self.assertEqual([label for _, label in subquestion_markers("(1) 受注 (2) 請求")], ["(1)", "(2)"])
        with bind_pack(_custom(question_cues={"subquestion_number_patterns": ["[N]"]})):
            self.assertEqual(subquestion_markers("(1) 受注 (2) 請求"), [])

    def test_evidence_cues_follow_the_pack(self):
        """根拠の選び方の手がかり（機能番号の線）は言語資源から作る(#2181 段 4)。"""
        from types import SimpleNamespace

        from rag_engine.retrieval.operation_context import alternative_operation_queries, operation_labels

        record = SimpleNamespace(metadata={"section_path": ["合成の章の名前", "業務~(1)登録画面"]})
        # 線の字に「~」が無ければ番号付きの機能見出しと見なさず、章の名前も残す。
        self.assertEqual(operation_labels(record), {"合成の章の名前", "業務~(1)登録画面"})
        with bind_pack(_custom(evidence_cues={"function_number_dashes": ["-", "~"]})):
            self.assertEqual(operation_labels(record), {"業務~(1)登録画面"})
        records = [
            SimpleNamespace(metadata={"section_path": ["業務~(1)登録画面"]}),
            SimpleNamespace(metadata={"section_path": ["業務~(2)削除画面"]}),
        ]
        self.assertEqual(alternative_operation_queries("登録したい", records), ())
        with bind_pack(_custom(evidence_cues={"function_number_dashes": ["-", "~"]})):
            self.assertEqual(len(alternative_operation_queries("登録したい", records)), 2)

    def test_answer_labels_follow_the_pack(self):
        """回答の表示（見出し・出典・条件の型）は言語資源から作り、検査も同じ語で読む(#2181 段 5)。"""
        from rag_engine.generation.answer_labels import answer_labels
        from rag_engine.generation.grounded import is_structural_line

        self.assertTrue(is_structural_line("資料により確認できた事項"))
        self.assertTrue(is_structural_line("根拠：a.pdf p.3"))
        custom = _custom(
            answer_labels={
                "rules_section": "確かめた事項",
                "applications_section": "合成の導出結論",
                "citation_prefix": "出典: ",
                "page_location": "{page} 頁",
            }
        )
        with bind_pack(custom):
            self.assertTrue(is_structural_line("確かめた事項"))
            self.assertTrue(is_structural_line("合成の導出結論"))
            self.assertEqual(answer_labels().applications_section, "合成の導出結論")
            self.assertFalse(is_structural_line("資料により確認できた事項"))
            self.assertEqual(answer_labels().page(3), "3 頁")

    def test_page_text_is_fixed_to_nfkc_like_the_previous_default(self):
        """頁番号の比較は NFKC に固定し、既定（NFKC）で取り込んだ文書と同じ形にする(#4784)。"""
        import re
        import unicodedata

        from rag_engine.chunking.constants import _compact_page_text

        for text in ("－ ３ －", "Ⅳ", "Ｐ．１２ / ２０", "ii"):
            self.assertEqual(_compact_page_text(text), re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)))

    def test_added_question_stopwords_are_dropped_from_the_query(self):
        """同梱の止め語の英語 5 語と疑問の副詞 3 語は質問の語にしない(#4784)。足す前の一覧では語に残る。"""
        from rag_engine.retrieval.text_search_tokenizer import tokenize_text_search_query

        added = {"as", "into", "through", "whom", "whose", "なぜ", "どうして", "いかが"}
        questions = (
            "なぜ区画の上限を超えるのですか",
            "設定はいかがですか",
            "records into the archive through the console",
            "whom and whose records as default",
        )
        self.assertTrue(added <= set(builtin_pack().words("search_terms", "stopwords")))
        current = {term for question in questions for term in tokenize_text_search_query(question)}
        self.assertFalse(added & current)
        before = [word for word in builtin_pack().words("search_terms", "stopwords") if word not in added]
        with bind_pack(_custom(search_terms={"stopwords": before})):
            previous = {term for question in questions for term in tokenize_text_search_query(question)}
        self.assertTrue({"なぜ", "いかが", "into", "through", "whom", "as"} <= previous)


if __name__ == "__main__":
    unittest.main()
