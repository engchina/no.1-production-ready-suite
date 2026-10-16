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
        levels = lambda text: [level for pattern, level in _heading_patterns().levels if pattern.match(text)]
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


if __name__ == "__main__":
    unittest.main()
