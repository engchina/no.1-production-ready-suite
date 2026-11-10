"""検索準備の実入力・実出力を工程ごとに記録する合成回帰。"""

from collections import defaultdict
from contextlib import contextmanager

import pytest
from test_runtime_knowledge_steps import _RULE, _TERM, _run, _write

from rag_engine.generation import answering


class Recorder:
    def __init__(self):
        self.calls = defaultdict(list)

    @contextmanager
    def observe(self, operation, model):
        value = {}
        self.calls[(operation, model)].append(value)

        class Observation:
            def request(self, payload, *, schema=None):
                value["input"] = payload

            def response(self, payload):
                value["output"] = payload

            def usage(self, payload):
                pass

            def display(self, *args, **kwargs):
                pass

        yield Observation()

    def stage(self, name):
        values = self.calls[("stage", name)]
        assert len(values) == 1
        return values[0]


@pytest.mark.parametrize("search,answer", [(False, False), (True, False), (False, True), (True, True)])
def test_stage_io_matches_actual_enabled_inputs_and_queries(tmp_path, search, answer):
    recorder = Recorder()
    result = _run(_write(tmp_path / "knowledge.json", terms=[_TERM], rules=[_RULE]), observer=recorder, search=search, answer=answer)
    for name, enabled in [(answering.TERM_SEARCH_STEP, search), (answering.TERM_ANSWER_STEP, answer)]:
        call = recorder.stage(name)
        assert call["input"]["原質問"] == result.runtime_knowledge.original_question
        assert call["input"]["検索への使用"] is search
        assert call["input"]["回答への使用"] is answer
        assert [term["term"] for term in call["output"]["一致した用語"]] == ([_TERM["term"]] if enabled else [])
        assert call["output"]["状態"] == ("照合完了" if enabled else "設定オフ")
    # payload に残った登録ルールは使わず、その工程も無い。
    assert not result.runtime_knowledge.matched_rules
    merged = recorder.stage("検索文をまとめ、検索語を作る")
    actual = [item["本文"] for item in merged["output"]["採用した検索文"]]
    assert actual == result.retrieval_query_plan["text_query_variants"]
    assert merged["output"]["全文検索の入力"]["oracle_text_queries"] == result.text_search_queries
    assert all(item["出どころ"] for item in merged["output"]["採用した検索文"])


def test_no_term_match_is_completed_with_empty_output(tmp_path):
    recorder = Recorder()
    _run(_write(tmp_path / "knowledge.json", terms=[_TERM]), question="合成の別の質問", observer=recorder, search=True)
    output = recorder.stage(answering.TERM_SEARCH_STEP)["output"]
    assert output == {"状態": "照合完了", "読込エラー": "", "一致した用語": [], "追加検索文": []}


def test_term_load_failure_is_recorded_without_inventing_a_match(tmp_path, monkeypatch):
    from rag_engine.knowledge import runtime_knowledge

    def fail(*args):
        raise ValueError("synthetic")

    monkeypatch.setattr(runtime_knowledge, "_matched_terms", fail)
    recorder = Recorder()
    _run(_write(tmp_path / "knowledge.json", terms=[_TERM]), observer=recorder, search=True)
    call = recorder.stage(answering.TERM_SEARCH_STEP)
    assert call["input"]["原質問"]
    assert call["output"]["状態"] == "読込失敗"
    assert call["output"]["読込エラー"]
    assert call["output"]["一致した用語"] == []
