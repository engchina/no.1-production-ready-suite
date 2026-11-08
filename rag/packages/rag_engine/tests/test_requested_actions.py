"""質問契約の要求操作は、今回の要求文の語だけから取る。"""

import pytest

from rag_engine.generation.answering import QueryExpansionResult
from rag_engine.retrieval.task_contract import STEPS_REQUEST, explicit_requests, task_contract


def test_history_and_wish_do_not_add_a_separate_retrieval_query():
    """経緯と希望が混ざった質問でも検索文は原質問だけで、希望の節を別の検索文にしない。"""
    question = "取消を試した。CSVを使って令和10年7月の予約を追加したい。"
    assert QueryExpansionResult(question, "simple", "simple").retrieval_queries == (question,)
    assert "intent_parts" not in task_contract(question)


@pytest.mark.parametrize("verb", ["追加", "削除", "延長", "取消"])
def test_explicit_operation_wish_is_not_classified_as_a_definition(verb):
    contract = task_contract(f"予約を{verb}したい。")
    assert STEPS_REQUEST in explicit_requests(f"予約を{verb}したい。")
    assert verb in contract["requested_actions"]
    assert STEPS_REQUEST not in explicit_requests(f"{verb}料金の意味は？")
    assert [t["label"] for t in task_contract(f"{verb}料金の意味は？")["definition_targets"]]


def test_state_suffix_is_not_a_requested_action():
    # 「取消済み伝票」はデータの状態。要求操作にしない。
    question = (
        "地区別売上集計表を出力したところ、前月の取消済み伝票が集計に含まれてしまいます。"
        "除外する設定はありますか。また、CSV で出力する方法も教えてください。"
    )
    assert task_contract(question)["requested_actions"] == ["出力"]
    assert task_contract("予約は登録済み。予約期間を延長したい。")["requested_actions"] == ["延長"]
