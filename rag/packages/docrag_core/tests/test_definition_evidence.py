"""短い定義項目の取得後欠落と、回答の片側だけの誤承認を再現する。"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from docrag.retrieval.definition_evidence import definition_labels, definition_ranges, label_mentioned
from docrag.retrieval.task_contract import task_contract
from docrag.retrieval.text_search_tokenizer import tokenize_text_search_query, TextSearchTokenizerConfig
from docrag.retrieval.evidence_selection import evidence_spans, evidence_excerpt, format_evidence_packet
from docrag.generation.answering import AnswerRecord
from docrag.generation.operation_audit import answer_passages
from grounded_stub import AnswerOutput

FIXTURE = json.loads((Path(__file__).parent/'fixtures/definition_short_labels.json').read_text())
QUESTION = FIXTURE['question']
PARENT = AnswerRecord(**FIXTURE['parent'])
CHILD = AnswerRecord(**FIXTURE['child'])


@pytest.mark.parametrize('question,expected', [
    (QUESTION,['休','限']), ('商品検索画面の「休」「限」の＊の意味は？',['休','限']),
    ('商品検索画面の「休」「限」の*の意味は？',['休','限']), ('「休」の意味は？',['休']),
    ('限の意味は？',['限']), ('「登録」列の意味は？',['登録']),
    ('商品検索画面の限の項目に付いている＊は、どういった商品を表すのか。今月も販売期間内で在庫も残っている商品である。', ['限']),
    ('在庫画面で保留の欄に印があるのはどんな商品ですか。', ['保留']),
    ('一覧で期限列に印があるのはどのような状態ですか。', ['期限']),
    ('限の項目を変更したい', []),
    ('取消方法を教えてください',[]), ('文言を「，」から「、」に変更したい',[]),
])
def test_explicit_definition_targets(question,expected):
    assert definition_labels(question)==expected
    contract=task_contract(question)
    assert [t['label'] for t in contract['definition_targets']]==expected
    assert contract['request_units'][0]['id']=='Q1'
    if expected:
        assert contract['goal']=='rule'
        assert all(t['id'].startswith('Q1.D') for t in contract['definition_targets'])


@pytest.mark.parametrize('mode',['regex','sudachi'])
def test_query_keeps_explicit_single_character_items_without_wildcard(mode):
    tokens=tokenize_text_search_query(QUESTION,config=TextSearchTokenizerConfig(mode=mode))
    assert '休' in tokens and '限' in tokens
    assert '*' not in tokens and '＊' not in tokens
    assert not label_mentioned('休止しました','休')
    assert not definition_ranges('検索語: 休 / 限\n表構造: 休 / 限 / *',['休','限'])


def selected_spans():
    records=[replace(PARENT,id=f'noise{i}',chunk_uid=f'noise:{i}',text='検索画面の項目の検索語。'*200) for i in range(14)]
    return evidence_spans(QUESTION,[*records,PARENT],anchors=[CHILD])


def test_saved_retrieval_keeps_both_definitions_and_child_page_in_budget():
    assert '休…販売を一時的' not in FIXTURE['old_excerpt']
    assert '限…販売期間' not in FIXTURE['old_excerpt']
    spans=selected_spans(); packet=format_evidence_packet(spans)
    assert '休…販売を一時的' in packet and '限…販売期間' in packet
    assert '本日以前' in packet and sum(len(s['text']) for s in spans)<=48000
    definitions=[s for s in spans if definition_ranges(s['text'],['休','限'])]
    assert definitions and all(s['page']==6 for s in definitions)
    assert all(s['text'] in PARENT.text for s in definitions)


def test_definition_condition_is_not_published_as_a_cut_fragment():
    source='一覧の項目「印」について\n印…期限あり、または開始日から\n終了日までが対象の場合に＊が表示されます。\n別の説明。'
    text=evidence_excerpt('「印」の意味は？',source,24)
    assert '印…' not in text
    assert '終了日まで' not in text


def test_definition_only_recovery_keeps_conditions_and_skips_neighbor_operations():
    spans=evidence_spans(QUESTION,[PARENT],anchors=[CHILD],definitions_only=True)
    packet=format_evidence_packet(spans)
    assert '休…販売を一時的' in packet and '本日以前' in packet
    assert 'ポップアップ' not in packet and '画面遷移' not in packet
    assert all(s['page']==6 for s in spans)


def test_child_from_another_version_cannot_supply_page_or_text():
    spans=evidence_spans(QUESTION,[PARENT],anchors=[replace(CHILD,source_run_id='other',page=99)])
    assert all(s['page']!=99 for s in spans)


def draft(answer):
    return AnswerOutput(answer=answer,confidence='high',question_type=['規則'],used_images=[],
        reasoning_summary='',insufficient_reason='',needs_human_review=False)


def alleged_audit(answer,spans):
    eid=next(s['evidence_id'] for s in spans if '休…販売を一時的' in s['text'])
    passages=answer_passages(answer)
    return dict(status='completed',complete=True,goal_alignment='aligned',
        reviews=[dict(passage_id=p['id'],status='supported',applicability='matched',evidence_ids=[eid],answer_quote=p['text']) for p in passages],
        request_reviews=[dict(request_id=u['id'],status='addressed',answer_passage_ids=[p['id'] for p in passages],evidence_ids=[eid]) for u in task_contract(QUESTION)['request_units']])


def test_definition_with_operation_request_retains_operation_goal():
    assert task_contract('「登録」列の意味と変更方法を教えてください')['goal']=='procedure'
