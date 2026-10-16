"""rag_engine の数・単位・元号・文末の書き方は言語資源から作る(#2181 段 1b-2)。"""

import unittest

from rag_parser_core.language import bind_pack, builtin_pack, resolve

from rag_engine.chunking.headings import _heading_patterns
from rag_engine.generation.value_grounding import _date
from rag_engine.retrieval.enumeration import enumeration_request
from rag_engine.retrieval.inquiry_conditions import _date_pattern


def _custom(**sections):
    return resolve(builtin_pack(), sections)


class LanguagePackEngineTest(unittest.TestCase):
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

    def test_enumeration_chapter_range_follows_connectors(self):
        question = "第2章〜第4章の手順をすべて挙げてください"
        self.assertEqual(enumeration_request(question).chapters, (2, 3, 4))
        question = "第2章 to 第4章の手順をすべて挙げてください"
        self.assertNotEqual(enumeration_request(question).chapters, (2, 3, 4))
        with bind_pack(_custom(numbers={"range_connectors": ["から", "to"]})):
            self.assertEqual(enumeration_request(question).chapters, (2, 3, 4))

    def test_dates_use_era_words_not_single_characters(self):
        self.assertEqual([m.group(0) for m in _date_pattern().finditer("令和6年4月の申請")], ["令和6年4月"])
        # 以前は元号を字の集合で書いていて、元号の 1 字だけでも日付にしていた。
        self.assertEqual([m.group(0) for m in _date_pattern().finditer("和6年4月の申請")], [])
        self.assertEqual(_date().sub("", "R6年度の上限は100"), "の上限は100")
        with bind_pack(_custom(numbers={"era_letters": ["K=令和"]})):
            self.assertEqual(_date().sub("", "K6年度の上限は100"), "の上限は100")

    def test_content_labels_follow_the_pack(self):
        """表・図の内容のラベルは書く側と読む側が同じ言語資源から作る(#2181 段 2c)。"""
        from rag_engine.generation.grounded import _without_screen_examples
        from rag_engine.parsing.content_labels import content_labels
        from rag_engine.parsing.picture_descriptions import vision_description_text
        from rag_engine.parsing.table_structure import _fixed_group_pieces
        from rag_engine.parsing.vision_prompt_rules import is_vision_enumeration_line

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
            self.assertTrue(is_vision_enumeration_line("押すもの: 保存"))
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


if __name__ == "__main__":
    unittest.main()
