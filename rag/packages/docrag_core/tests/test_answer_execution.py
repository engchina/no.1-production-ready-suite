"""実処理の順序、選択肢との名称一致、中断・再実行・並行実行を検証する。"""

import re
import unittest
from grounded_stub import echo_model
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import docrag.generation.answering as generation
from docrag.config import get_settings
from test_answer_generation import (
    _answer_context, _chunk_run_stub, _crag_grade, _record, _rerank_ready_settings, heading,
)


class AnswerExecutionTests(unittest.TestCase):
    """外部サービスを置換し、実際の RAG 制御フローを通して記録を確認する。"""

    def setUp(self):
        self.settings = get_settings()
        self.enterContext(patch.object(generation, "load_latest_or_source_chunk_run", return_value=_chunk_run_stub()))
        self.enterContext(patch.object(generation, "check_adb_hybrid_search_ready"))
        self.context = _answer_context([_record("r1", "docling", 1, 1, "source evidence")])
        self.search = self.enterContext(patch.object(generation, "build_adb_hybrid_answer_context", return_value=self.context))
        self.parse = self.enterContext(patch.object(generation, "parse_text_response", side_effect=echo_model))

    def run_answer(self, question="質問", **kwargs):
        """実環境に接続せず、通常 RAG を既定として回答を作る。"""
        options = dict(query_strategy=generation.SIMPLE_RETRIEVAL_LABEL, answer_flow=generation.STANDARD_ANSWER_FLOW_LABEL)
        options.update(kwargs)
        return generation.answer_question_result(question, "abcdef", ["docling"], self.settings, **options)

    def assert_order(self, display, *sections):
        """文字列または見出し pattern の出現順を確認する。"""
        offsets = []
        for section in sections:
            if isinstance(section, re.Pattern):
                match = section.search(display)
                self.assertIsNotNone(match, section.pattern)
                offsets.append(match.start())
            else:
                offsets.append(display.index(section))
        self.assertEqual(offsets, sorted(offsets))

    def test_boundaries_pair_nested_steps_and_preserve_parent_details(self):
        display = generation.format_question_display("質問", [
            "回答生成フロー 開始", "  フローの設定",
            "文書検索（1回目） 開始", "  検索条件",
            "Rerank 開始", "  対象: 3 件", "Rerank 終了",
            "  回答に渡す根拠: 1 件", "文書検索（1回目） 終了 @1.2s/0",
            "回答生成フロー 終了 @25.6s/3",
        ])
        self.assertNotIn("今回の処理を実行順に表示します。", display)
        # 見出しは工程ごとに1回、番号付きの節と字下げで階層を示す。正常に終わった工程に終了行は付けない。
        self.assertIn("\n1. 回答生成フロー ［LLM 3 回 / 25.6 秒］\n   フローの設定\n", display)
        self.assertIn("\n   1.1 文書検索（1回目） ［1.2 秒］\n      検索条件\n", display)
        self.assertIn("\n      1.1.1 Rerank\n         対象: 3 件\n", display)  # 計測のない工程は見出しだけ
        self.assertIn("\n      回答に渡す根拠: 1 件", display)
        self.assertTrue(display.endswith("合計: LLM 3 回 / 25.6 秒"))
        self.assertNotIn("━", display)
        self.assertNotIn("【", display)
        self.assertNotIn(" 開始", display)
        self.assertNotIn(" 終了", display)
        self.assertNotIn("@", display)
        self.assert_order(display, "フローの設定", "検索条件", "対象: 3 件", "回答に渡す根拠: 1 件")
        self.assertEqual(generation.extract_original_question(display), "質問")

    def test_boundaries_keep_error_status_and_do_not_parse_detail_text(self):
        display = generation.format_question_display("質問", [
            "質問拡張戦略 開始", "  検索文: 手続き 開始",
            "質問拡張戦略 終了（エラーで中断）",
        ])
        self.assertRegex(display, heading("質問拡張戦略"))
        self.assertIn("\n   状態: エラーで中断", display)
        self.assertIn("検索文: 手続き 開始", display)  # 詳細文は工程として解釈しない
        self.assertNotIn("1.1 ", display)
        self.assertNotIn("合計:", display)  # 計測のない記録（server が組み立てる記録など）に合計は出さない
        empty = generation.format_question_display("質問", ["確認 開始", "確認 終了", "検索 開始", "  結果: 1 件", "検索 終了"])
        self.assertNotIn("確認", empty)  # 伝える内容のない工程は表示せず、番号も詰める
        self.assertIn("\n1. 検索\n", empty)
        interrupted = generation.format_question_display("質問", ["検索 開始", "  結果: 1 件"])
        self.assertIn("状態: 中断", interrupted)
        faq = generation.format_question_display("質問", [
            "Approved FAQ候補の確認 開始", "  候補: 1 件",
            "Approved FAQ候補の確認 終了", "候補の選択待ちです。",
        ])
        self.assertRegex(faq, heading("Approved FAQ候補の確認"))
        self.assertTrue(faq.endswith("候補の選択待ちです。"))

    def test_llm_calls_are_counted_per_step_and_reset_per_answer(self):
        from docrag.dependencies import AnswerDependencies, bind_dependencies, llm_call_count, parse_text_response
        deps = AnswerDependencies(search=None, check_ready=None, parse_text=lambda *a, **k: "ok",
                                  parse_images=None, rerank=None)
        lines = []
        token = generation._execution_lines.set(lines)
        try:
            with bind_dependencies(deps), generation._execution_step("判定") as step:
                parse_text_response("system", "prompt")
                parse_text_response("system", "prompt")
                step.result("2 回呼んだ")
        finally:
            generation._execution_lines.reset(token)
        self.assertRegex(lines[-1], r"^判定 終了 @\d+\.\d+s/2$")
        self.assertIn("\n1. 判定 ［LLM 2 回］\n", generation.format_question_display("質問", lines))
        # 回答ごとに 0 から数える。前の回答の呼出は次の回答へ持ち越さない。
        self.assertGreaterEqual(llm_call_count.get(), 2)
        self.run_answer()
        self.assertGreaterEqual(llm_call_count.get(), 2)

    def test_all_strategy_and_flow_names_match_options(self):
        for strategy, label in generation.QUERY_STRATEGIES:
            for flow, flow_label in generation.ANSWER_FLOWS:
                with self.subTest(strategy=strategy, flow=flow), patch.object(
                    generation, "build_query_expansion",
                    return_value=generation.QueryExpansionResult(
                        original_question="質問", selected_strategy=strategy,
                        effective_strategy=generation.HYDE_STRATEGY if strategy == generation.AUTO_ROUTING_STRATEGY else strategy,
                        generated_queries=("追加の検索文",),
                    ),
                ), patch.object(generation, "_grade_crag_retrieval", return_value=generation.CragRetrievalAttempt(
                    attempt=1, query="質問", retrieval_queries=("質問",), sufficient=True,
                )):
                    result = self.run_answer(query_strategy=label, answer_flow=flow_label)
                self.assertIn(f"設定: {label}", result.question_display)
                effective = generation.HYDE_LABEL if strategy == generation.AUTO_ROUTING_STRATEGY else label
                self.assertIn(f"結果: {effective}", result.question_display)
                self.assertIn(f"設定: {flow_label}", result.question_display)
                self.assert_order(result.question_display, heading("質問拡張戦略"), heading("回答生成フロー"),
                                  heading("回答文の生成と根拠確認（1回目）"))
                self.assertEqual(generation.answer_result_payload(result, answer_id="a1", run_id="abcdef")["question_display"],
                                 result.question_display)

    def test_mayor_intent_is_combined_and_auxiliary_query_is_visible(self):
        result = self.run_answer("倉庫を移転したので、拠点名を変更する方法を教えてほしい")
        display = result.question_display
        self.assertIn("結果: 質問の目的は「操作方法」", display)
        self.assertNotIn("回答方針", display)
        queries = result.inquiry_conditions.retrieval_queries
        self.assertIn(f"影響: 補助の検索文を {len(queries)} 本追加します。", display)
        for query in queries:  # 検索文は確定の工程に、番号と由来付きで1回だけ出る
            self.assertEqual(display.count(generation._display_query(query)), 1)
        self.assertIn("1. [原質問] ", display)

    def test_understanding_shows_only_what_changes_later_steps(self):
        lines = []
        token = generation._execution_lines.set(lines)
        try:
            question = "名称を変更する方法と、過去の記録への影響を教えてください。"
            plan = generation.plan_question(question)
            with generation._execution_step("質問の理解") as step:
                generation._record_question_understanding(step, question, plan, plan.inquiry_conditions)
        finally:
            generation._execution_lines.reset(token)
        display = "\n".join(lines)
        self.assertIn("結果: 質問の目的は「操作方法」", display)
        self.assertIn("答える要求は", display)
        self.assertIn("影響: 回答後、要求ごとに答えたかを確認します", display)
        self.assertNotIn("NLU", display)

    def test_definition_targets_are_shown_as_labels_and_do_not_stop_the_answer(self):
        """定義項目は構造化データなので、表示境界でラベルへ変換しないと記録が中断する (#1001)。"""
        for question, labels in (
            ("一覧画面の「区分甲」はどのような意味ですか。", ["区分甲"]),
            ("一覧画面の「区分甲」「区分乙」はそれぞれどのような意味ですか。", ["区分甲", "区分乙"]),
        ):
            with self.subTest(question=question):
                display = self.run_answer(question).question_display
                self.assertIn("意味を尋ねている項目: " + " / ".join(labels), display)
                self.assertNotIn("request_id", display)
                # 「質問の理解」で中断せず、外部呼出を伴う後続工程まで進む。
                self.assert_order(display, "質問の理解", "検索の準備確認", "文書検索")

    def test_keyword_display_includes_later_queries_and_more_than_ten_terms(self):
        info = generation.QuestionTextSearchInfo(
            tokens=tuple(f"語{i}" for i in range(12)),
            tokenization_traces=({"tokens": ["追加語", "語0"], "truncated": True},),
        )
        display = "\n".join(generation._text_search_details(info))
        self.assertIn("語11", display)
        self.assertIn("追加語", display)
        self.assertEqual(display.count("語0"), 1)
        self.assertIn("一部の候補は不採用", display)

    def test_same_keyword_reference_keeps_stage_boundaries_and_changed_terms(self):
        lines = []
        token = generation._execution_lines.set(lines)
        try:
            for name, words in (("初回", ("最初の語",)), ("再確認", ("最初の語",)), ("補正後", ("新しい語",))):
                with generation._execution_step(name) as step:
                    generation._add_text_search_details(step, generation.QuestionTextSearchInfo(tokens=words))
        finally:
            generation._execution_lines.reset(token)
        display = "\n".join(lines)
        self.assertEqual(display.count("最初の語"), 1)  # 同じ採用語は繰り返さない
        self.assertIn("新しい語", display)
        self.assert_order(display, "初回 終了", "再確認 開始", "再確認 終了", "補正後 開始", "補正後 終了")

    def test_steps_are_recorded_when_external_calls_execute(self):
        def search(*args, **kwargs):
            lines = "\n".join(generation._execution_lines.get())
            self.assertIn("質問拡張戦略 終了", lines)
            self.assertIn("文書検索 開始", lines)
            self.assertNotIn("文書検索 終了", lines)
            self.assertNotIn("回答文の生成", lines)
            return self.context

        def answer(*args, **kwargs):
            lines = "\n".join(generation._execution_lines.get())
            self.assertIn("文書検索 終了", lines)
            self.assertIn("回答文の生成と根拠確認（1回目） 開始", lines)
            self.assertNotIn("回答文の生成と根拠確認（1回目） 終了", lines)
            return echo_model(*args, **kwargs)

        self.search.side_effect = search
        self.parse.side_effect = answer
        self.run_answer()

    def test_missing_chunks_does_not_claim_strategy_or_flow_ran(self):
        with patch.object(generation, "load_latest_or_source_chunk_run", return_value=None):
            result = self.run_answer(query_strategy=generation.AUTO_ROUTING_LABEL)
        self.assert_order(result.question_display, heading("検索の準備確認"), "状態: 中止")
        self.assertNotRegex(result.question_display, heading("質問拡張戦略"))
        self.assertNotRegex(result.question_display, heading("回答生成フロー"))
        self.search.assert_not_called()
        self.parse.assert_not_called()

    def test_empty_retrieval_does_not_claim_answer_was_generated(self):
        self.search.return_value = generation.AnswerContext(records=[], text="")
        result = self.run_answer()
        self.assertIn("状態: 根拠不足で終了", result.question_display)
        self.assertNotIn("回答文の生成（", result.question_display)
        self.parse.assert_not_called()

    def test_adb_preflight_failure_does_not_run_expansion(self):
        with patch.object(generation, "check_adb_hybrid_search_ready",
                          side_effect=generation.AdbHybridSearchUnavailable("not ready")):
            result = self.run_answer()
        self.assert_order(result.question_display, heading("検索の準備確認"), "状態: 中止")
        self.assertNotRegex(result.question_display, heading("質問拡張戦略"))
        self.parse.assert_not_called()


    def test_rerank_failure_reports_fallback_without_reordering(self):
        lines = []
        token = generation._execution_lines.set(lines)
        try:
            with patch.object(generation, "rerank_text_with_scores", side_effect=RuntimeError("unavailable")):
                result = generation.rerank_records("質問", self.context.records, _rerank_ready_settings(), enabled=True)
        finally:
            generation._execution_lines.reset(token)
        self.assertEqual(result, self.context.records)
        self.assertTrue(any(line.startswith("Rerank 終了（失敗・取得順を保持）") for line in lines), lines)

    def test_second_crag_search_failure_keeps_first_attempt(self):
        self.search.side_effect = [self.context, generation.AdbHybridSearchUnavailable("search unavailable")]
        self.parse.side_effect = None
        self.parse.return_value = _crag_grade(sufficient=False, rewritten_query="追加検索")
        result = self.run_answer(answer_flow=generation.CRAG_ANSWER_FLOW_LABEL)
        self.assert_order(result.question_display, heading("文書検索（1回目）"), heading("根拠確認（1回目）"),
                          heading("補正検索の準備"), heading("文書検索（2回目）"), "状態: エラーで中断", "状態: 中止")
        self.assertNotRegex(result.question_display, heading("根拠確認（2回目）"))
        self.assertNotIn("回答文の生成（", result.question_display)

    def test_llm_error_has_trace_and_does_not_leak_into_next_request(self):
        self.parse.side_effect = RuntimeError("LLM unavailable")
        with self.assertRaises(generation.AnswerExecutionError) as caught:
            self.run_answer("失敗する質問")
        display = caught.exception.question_display
        self.assert_order(display, heading("回答文の生成と根拠確認（1回目）"), "状態: エラーで中断")
        self.assertEqual(display.count("状態: エラーで中断"), 2)  # 失敗した工程と、その親
        self.assertIsNone(generation._execution_lines.get())
        self.parse.side_effect = echo_model  # None にすると MagicMock の草稿・監査が生成経路へ流れる
        result = self.run_answer("次の質問")
        self.assertNotIn("失敗する質問", result.question_display)
        self.assertNotIn("エラーで中断", result.question_display)

    def test_new_and_legacy_memos_are_removed_on_retry(self):
        for separator in (generation.QUESTION_DISPLAY_METADATA_SEPARATOR, "--- 回答生成メモ ---",
                          generation.QUERY_STRATEGY_DISPLAY_SEPARATOR):
            with self.subTest(separator=separator):
                result = self.run_answer(f"原質問\n\n{separator}\n以前の記録")
                self.assertEqual(result.original_question, "原質問")
                self.assertNotIn("以前の記録", result.question_display)
                self.assertEqual(result.question_display.count(generation.QUESTION_DISPLAY_METADATA_SEPARATOR), 1)

    def test_concurrent_requests_keep_separate_records(self):
        barrier = Barrier(2)

        def search(*args, **kwargs):
            barrier.wait(timeout=5)
            return self.context

        self.search.side_effect = search
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.run_answer, ["並行質問AAA", "並行質問BBB"]))
        for own, other in ((results[0], results[1]), (results[1], results[0])):
            self.assertIn(own.original_question, own.question_display)
            self.assertNotIn(other.original_question, own.question_display)
            self.assertEqual(len(heading("回答生成フロー").findall(own.question_display)), 1)


if __name__ == "__main__":
    unittest.main()
