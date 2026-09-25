"""#369: 過去の経路に現在の一覧出力を固定しない。"""
from docrag.retrieval.task_contract import task_contract,current_request_text
from docrag.retrieval.evidence_selection import evidence_relevance

QUESTION='以前は仕入先の一覧を作るときに帳票出力＞条件指定から抽出式を入れて出力する方法を教えてもらったが、配送業者の連絡先の一覧を出力したい。'


def test_current_object_and_result_are_separate_from_history():
    contract=task_contract(QUESTION)
    assert contract['current_request']=='配送業者の連絡先の一覧を出力したい。'
    assert contract['goal']=='recipient_list'
    assert '抽出式' in contract['historical_context']
    assert contract['original_question']==QUESTION
    assert evidence_relevance(QUESTION,'配送業者の各種連絡先の一覧を選択しファイル出力します。') > evidence_relevance(QUESTION,'仕入先 帳票出力 条件指定 抽出式')


def test_explicit_current_method_constraint_is_retained():
    q='以前は仕入先を出力した。今回も抽出式を使って連絡先の一覧を出力したい。'
    assert '今回も抽出式を使って' in current_request_text(q)
    assert current_request_text('連絡先一覧の出力方法は？')=='連絡先一覧の出力方法は？'
