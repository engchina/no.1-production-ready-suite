"""類似機能の入口、補助根拠の公平な選択、最終公開回答の除去を検証する。"""

import pytest

from docrag.retrieval.task_contract import task_contract
from docrag.retrieval.operation_context import operation_labels, alternative_operation_queries
from docrag.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle
from grounded_stub import AnswerOutput
from test_rag_evidence_recovery import rec


@pytest.mark.parametrize('question,expected', [
    ('文言で「，」を「、」に変更したい。', 'procedure'), ('タイトルを修正したい', 'procedure'),
    ('文言を変えたい', 'procedure'), ('変更履歴を知りたい', 'rule'), ('変更内容を知りたい', 'rule'),
    ('摘要欄に注記を表示させたい', 'procedure'),
])
def test_change_intent(question, expected):
    assert task_contract(question)['goal'] == expected
    assert ('procedure' in task_contract(question)['required_aspects']) is (expected == 'procedure')


def test_numbered_functions_do_not_match_shared_subheadings():
    a=rec('a','parent',1,'画面',headings=['B管理 － （1）帳票設定','印字内容設定'])
    b=rec('b','parent',2,'画面',headings=['B管理 － （2）伝票印刷設定','印字内容設定'])
    assert not operation_labels(a) & operation_labels(b)
    queries=alternative_operation_queries('文言を変更したい',[a,b])
    assert len(queries)==2
    assert all(q.startswith('文言を変更したい') for q in queries)
    assert '(1)帳票設定' in queries[0] and '(2)伝票印刷設定' in queries[1]


@pytest.mark.parametrize('reverse',[False,True])
def test_supplement_budget_keeps_relevant_other_function(reverse):
    rows=[];anchors=[]
    for prefix,start,label in [('print',60,'B管理 － （2）伝票印刷設定'),('form',54,'B管理 － （1）帳票設定')]:
        for offset in range(3):
            key=f'{prefix}{offset}'
            text='文言と注記の設定で行数を入力し、ボタンを押して文言を入力します。' if prefix=='form' and offset==2 else '表示位置と行番号の設定'
            parent=rec(key,'parent',start+offset,text,page=start+offset,headings=[label])
            child=rec(key+'c','child',start+offset,text,parent=key,page=start+offset)
            rows.extend([parent,child])
            if offset==0:anchors.append(child)
    if reverse: rows.reverse();anchors.reverse()
    bundle=build_chunk_context_bundle(ContextBuildRequest(question='通知書の文言を変更したい',active_records=rows,
        ranked_children=anchors,top_k=2,neighbor_child_count=3,max_records=2,support_record_limit=3,max_chars=12000))
    assert 'form2' in {r.id for r in bundle.records}
    assert len(bundle.records)<=5


def draft(answer):
    return AnswerOutput(answer=answer,confidence='high',question_type=['操作手順'],used_images=[],
        reasoning_summary='未監査の修正操作',insufficient_reason='',needs_human_review=False,
        actionable_steps=['間違った画面で保存します。'],known_rules=['古い操作の説明'])


def test_mixed_function_boundary_is_not_a_neighbor():
    from docrag.retrieval.context_builder import _operation_parent_neighbors
    a=rec('a','parent',1,'文言',page=54,headings=['B管理－（1）帳票設定'])
    b=rec('b','parent',2,'文言',page=55,headings=['B管理－（1）帳票設定','B管理－（2）伝票印刷設定'])
    assert _operation_parent_neighbors(a,[b])==[]
    assert _operation_parent_neighbors(b,[a])==[]


def test_field_heading_recovers_an_independent_function_not_in_primary():
    primary=rec('p','parent',1,'固定文言を入力する。',page=60,headings=['B管理－（2）伝票印刷設定'])
    child=rec('c','child',1,'固定文言を入力する。',parent='p',page=60)
    entry=rec('entry','parent',2,'B 【操作説明】\n帳票を選ぶ。',page=54,headings=['B管理－（1）帳票設定'])
    editing=rec('editing','parent',3,'●文言と注記の設定\n文言を直接入力する。',page=56,headings=['B管理－（1）帳票設定'])
    unrelated=rec('other','parent',4,'●文言設定\n別文書の文言を入力する。',page=56,source='other.pdf',headings=['B管理－（1）帳票設定'])
    screenshots=[rec(f'image{i}','parent',10+i,'文言\n帳票の表示例。',page=55,headings=['B管理－（1）帳票設定']) for i in range(4)]
    result=build_chunk_context_bundle(ContextBuildRequest(question='通知書の文言を変更したい',
        active_records=[primary,child,entry,editing,unrelated,*screenshots],ranked_children=[child],top_k=1,
        neighbor_child_count=3,support_record_limit=3,max_records=1,max_chars=12000))
    assert {'p','entry','editing'} <= {r.id for r in result.records}
    assert 'other' not in {r.id for r in result.records}
    assert next(p.reason for p in result.evidence_tree if p.record.id=='editing')=='alternative_operation_context'


def test_manual_heading_and_steps_survive_repeated_search_labels():
    from docrag.retrieval.evidence_selection import evidence_excerpt
    text=('検索語: 文言変更 通知書 修正 設定 方法\n' * 40
          + '\n●文言と注記の設定\n・行数を入力し、編集ボタンを押して直接入力します。\n※コピーしないでください。\n\n')
    excerpt=evidence_excerpt('通知書の文言を変更したい',text,240)
    assert '●文言と注記の設定\n・行数を入力し、編集ボタンを押して直接入力します。' in excerpt


def test_upper_section_heading_survives_with_editing_instructions():
    from docrag.retrieval.evidence_selection import evidence_excerpt
    text='検索語: 通知書 文言\n'*60+'\n設定値２\n※対象帳票のみ有効。\n●文言と注記の設定\n・編集ボタンを押して入力します。\n\n'
    excerpt=evidence_excerpt('通知書の文言を変更したい',text,240)
    assert '設定値２' in excerpt and '編集ボタンを押して入力します' in excerpt


def test_public_reference_list_excludes_removed_operations_but_keeps_retrieval_records():
    from docrag.generation.answering import _published_reference_records, format_references
    records=[rec('wrong','parent',1,'別画面'),rec('right','parent',2,'別項目が先頭')]
    trace=dict(finalization=dict(filtered=True,retained_evidence_ids=['E2']),selected_evidence=[dict(evidence_id='E1',source_id=records[0].chunk_uid or records[0].id),dict(evidence_id='E2',source_id=records[1].chunk_uid or records[1].id,text='採用した項目の原文')])
    published=_published_reference_records(records,trace)
    assert [r.id for r in published]==['right'] and published[0].text=='採用した項目の原文'
    assert records[1].text=='別項目が先頭' and records[1].source in format_references(published,include_source=True)
    assert len(records)==2 and _published_reference_records(records,{})==records
    trace['finalization']['retained_evidence_ids']=[]
    assert _published_reference_records(records,trace)==[]
