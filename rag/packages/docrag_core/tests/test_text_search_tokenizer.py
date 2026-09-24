"""text search tokenizer の挙動を保護するテスト。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from docrag.retrieval.text_search_tokenizer import (
    TEXT_SEARCH_TOKENIZER_AUTO,
    TEXT_SEARCH_TOKENIZER_REGEX,
    TEXT_SEARCH_TOKENIZER_SUDACHI,
    TextSearchTokenizerConfig,
    TextSearchTokenizerUnavailable,
    build_oracle_text_query,
    escape_oracle_text_term,
    normalize_text_search_text,
    tokenize_text_search_query,
    tokenize_text_search_query_with_trace,
    tokenizer_fingerprint,
)


class TextSearchTokenizerTests(unittest.TestCase):
    def test_normalize_text_unifies_width_space_and_dash_variants(self):
        self.assertEqual(normalize_text_search_text("ＯＲＡ−０１５５５　再起動"), "ora-01555 再起動")

    def test_regex_tokenizer_prioritizes_domain_keywords_and_keeps_codes(self):
        terms = tokenize_text_search_query(
            "AND ORA-01555が発生。契約区分とindexesを確認してください",
            domain_keywords=["契約区分"],
            config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_REGEX, latin_stemmer="light"),
        )

        self.assertEqual(terms[:3], ["契約区分", "ora-01555", "発生"])
        self.assertIn("indexes", terms)
        self.assertIn("index", terms)
        self.assertIn("確認", terms)
        self.assertNotIn("確認してください", terms)
        self.assertNotIn("and", terms)

    def test_oracle_text_query_escapes_reserved_characters_with_braces(self):
        self.assertEqual(escape_oracle_text_term("ORA-01555"), "{ora-01555}")
        self.assertEqual(escape_oracle_text_term("50%"), "{50%}")
        self.assertEqual(build_oracle_text_query(["ora-01555", "契約区分"]), "{ora-01555} OR {契約区分}")

    def test_forced_sudachi_mode_reports_missing_dependency(self):
        with patch(
            "docrag.retrieval.text_search_tokenizer._sudachi_tokens",
            side_effect=ImportError("no sudachi"),
        ):
            with self.assertRaises(TextSearchTokenizerUnavailable):
                tokenize_text_search_query(
                    "契約区分",
                    config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_SUDACHI),
                )

    def test_auto_mode_falls_back_to_regex_when_sudachi_is_missing(self):
        with patch(
            "docrag.retrieval.text_search_tokenizer._sudachi_tokens",
            side_effect=ImportError("no sudachi"),
        ):
            terms = tokenize_text_search_query(
                "契約区分 ORA-01555",
                config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_AUTO),
            )

        self.assertEqual(terms, ["契約区分", "ora-01555"])

    def test_sudachi_path_filters_pos_and_adds_multi_granularity_tokens(self):
        config = TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_SUDACHI, latin_stemmer="none")
        tokenizer = _FakeSudachiTokenizer(
            {
                "東京都知事選挙": [
                    _morpheme(
                        "東京都知事選挙",
                        "東京都知事選挙",
                        ("名詞", "普通名詞"),
                        subs=[
                            _morpheme("東京", "東京", ("名詞", "普通名詞")),
                            _morpheme("知事", "知事", ("名詞", "普通名詞")),
                            _morpheme("選挙", "選挙", ("名詞", "普通名詞")),
                        ],
                    )
                ],
                "について": [
                    _morpheme("に", "に", ("助詞", "*")),
                    _morpheme("つい", "つく", ("動詞", "非自立可能")),
                    _morpheme("て", "て", ("助詞", "*")),
                ],
                "こと": [_morpheme("こと", "事", ("名詞", "普通名詞"))],
            }
        )

        with (
            patch("docrag.retrieval.text_search_tokenizer._get_sudachi_tokenizer", return_value=tokenizer),
            patch("docrag.retrieval.text_search_tokenizer._sudachi_split_mode", side_effect=lambda mode: mode),
            patch("docrag.retrieval.text_search_tokenizer._sudachi_stopwords", return_value=frozenset({"こと", "事"})),
        ):
            terms = tokenize_text_search_query("東京都知事選挙についてのこと", config=config)

        self.assertEqual(terms, ["東京都知事選挙", "東京", "知事", "選挙"])

    def test_sudachi_path_keeps_surface_form_for_oracle_text_raw_index(self):
        config = TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_SUDACHI, latin_stemmer="none")
        tokenizer = _FakeSudachiTokenizer(
            {
                "サーバ": [_morpheme("サーバ", "サーバー", ("名詞", "普通名詞"))],
                "再起動": [_morpheme("再起動", "再起動", ("名詞", "普通名詞"))],
            }
        )

        with (
            patch("docrag.retrieval.text_search_tokenizer._get_sudachi_tokenizer", return_value=tokenizer),
            patch("docrag.retrieval.text_search_tokenizer._sudachi_split_mode", side_effect=lambda mode: mode),
            patch("docrag.retrieval.text_search_tokenizer._sudachi_stopwords", return_value=frozenset()),
        ):
            terms = tokenize_text_search_query("サーバの再起動", config=config)

        self.assertEqual(terms, ["サーバー", "サーバ", "再起動"])

    def test_truncation_ranks_specific_terms_before_generic_early_terms(self):
        terms = tokenize_text_search_query(
            "表示 確認 画面 情報 倉庫マスタ登録 伝票様式設定 拠点倉庫名",
            config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_REGEX),
            max_tokens=4,
        )

        self.assertEqual(terms, ["倉庫マスタ登録", "伝票様式設定", "拠点倉庫名", "表示"])
        self.assertNotIn("確認", terms)

    def test_tokenization_trace_exposes_truncated_candidates(self):
        result = tokenize_text_search_query_with_trace(
            "表示 確認 画面 情報 倉庫マスタ登録 伝票様式設定 拠点倉庫名",
            config=TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_REGEX),
            max_tokens=3,
        )

        self.assertEqual(result.tokens, ("倉庫マスタ登録", "伝票様式設定", "拠点倉庫名"))
        self.assertTrue(result.truncated)
        self.assertEqual(result.candidate_count, 7)
        self.assertEqual(result.truncated_count, 4)
        self.assertIn("表示", result.truncated_tokens)

    def test_fingerprint_is_stable_for_same_config(self):
        config = TextSearchTokenizerConfig(mode=TEXT_SEARCH_TOKENIZER_REGEX, latin_stemmer="none")

        self.assertEqual(tokenizer_fingerprint(config), tokenizer_fingerprint(config))


def _morpheme(surface, normalized, pos, subs=None):
    return SimpleNamespace(
        surface=lambda: surface,
        normalized_form=lambda: normalized,
        part_of_speech=lambda: pos,
        split=lambda mode: list(subs or []),
    )


class _FakeSudachiTokenizer:
    def __init__(self, mapping):
        self._mapping = mapping

    def tokenize(self, text, mode):
        result = []
        remaining = text
        while remaining:
            matched = False
            for key, morphemes in self._mapping.items():
                if remaining.startswith(key):
                    result.extend(morphemes)
                    remaining = remaining[len(key) :]
                    matched = True
                    break
            if matched:
                continue
            char = remaining[0]
            remaining = remaining[1:]
            if char.isspace() or char in "の":
                continue
            result.append(_morpheme(char, char, ("名詞", "普通名詞")))
        return result


if __name__ == "__main__":
    unittest.main()
