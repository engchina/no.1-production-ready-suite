"""目的逸脱、監査漏れ、原文ID、修正回数と出典保存の反例を検証する。"""
import json
from unittest.mock import patch

import pytest

from docrag.retrieval.task_contract import task_contract, filter_queries
from docrag.retrieval.evidence_selection import evidence_spans
from docrag.generation.answering import (AnswerRecord, AnswerContext, _refine_crag_context,
    _grade_crag_retrieval, build_crag_answer_context)
from docrag.models.llm import CragRetrievalGradeOutput
from docrag.evaluation.answer_eval import (evaluate_answer_payload, _evidence_fragments)
from test_evidence_first_answering import evaluation_case


def record(uid, text="入口を押すと編集画面が開く。保存ボタンで確定する。", source="guide.pdf"):
    return AnswerRecord(id="local", chunk_id="local", chunk_uid=uid, engine="docling", engine_label="Docling",
        page=1, seq_no=1, category="Chunk", text=text, source=source)


@pytest.mark.parametrize("question,bad,good", [
    ("調査対象者を確認したい", "調査加入人数の集計", "対象者一覧の取得方法"),
    ("項目の選択肢に該当するものがない", "項目が表示されない原因", "候補にない値の入力方法"),
    ("顧客からCSV提供の依頼があった", "地域別CSVの出力", "CSVの出力方法"),
    ("顧客から在庫数をCSVで受け取れるかの問い合わせがあった", "地域別在庫CSVの出力", "在庫CSVの出力方法"),
    ("申請の更新ができない", "ソフトウェアのバージョン更新", "申請の更新条件"),
])
def test_query_guard_preserves_goal_without_disabling_valid_expansion(question, bad, good):
    accepted, rejected = filter_queries(question, [question, bad, good])
    assert accepted == (question, good)
    assert rejected[0]["query"] == bad and rejected[0]["reason"]


def test_explicit_count_request_and_regional_filter_are_not_rejected():
    question = "地域別の取引先数を確認したい"
    assert filter_queries(question, ["地域別の件数集計"])[1] == ()
    assert task_contract("登録がないためではないかと思う。履歴を確認したい")["hypotheses"]


def test_duplicate_parse_is_consolidated_without_merging_different_versions():
    a = record("run-a:1"); b = record("run-b:1"); changed = record("run-c:1", "取消ボタンで戻る。")
    spans = evidence_spans("保存操作", [a, b, changed])
    assert len(spans) == 2
    assert spans[0]["source_aliases"] == ["run-a:1", "run-b:1"]
    assert spans[0]["evidence_id"] != spans[1]["evidence_id"]
    assert a.text[spans[0]["start"]:spans[0]["end"]] == spans[0]["text"]
    assert len(evidence_spans("保存操作", [record("x", source=""), record("y", source="")])) == 2


def test_crag_keeps_support_when_one_candidate_is_selected():
    a, b = record("a"), record("b", "入力不可の場合は別の条件を確認する。")
    context = AnswerContext(records=[a, b], text=a.text+b.text)
    result = _refine_crag_context(context, ["a"], max_chars=2000)
    assert {r.chunk_uid for r in result.records} == {"a", "b"}


def test_crag_rejects_high_confidence_without_required_evidence():
    r = record("a")
    grade = CragRetrievalGradeOutput(sufficient=True, confidence=0.99, candidate_verdicts=[dict(chunk_uid="a", relevant=True, reason="関連")], reason="関連", rewritten_query="", aspect_checks=[])
    with patch("docrag.generation.answering.parse_text_response", return_value=grade):
        result = _grade_crag_retrieval(1, "登録方法", "登録方法", ["登録方法"], AnswerContext(records=[r], text=r.text), object())
    assert not result.sufficient


def test_crag_does_not_resolve_ambiguous_local_ids():
    a, b = record("a", source="a.pdf"), record("b", source="b.pdf")
    checks = [dict(aspect=k, status="supported", source_ids=["local"], reason="関連") for k in task_contract("登録方法")["required_aspects"]]
    grade = CragRetrievalGradeOutput(sufficient=True, confidence=0.99, candidate_verdicts=[dict(chunk_uid="local", relevant=True, reason="関連")], reason="関連", rewritten_query="", aspect_checks=checks)
    with patch("docrag.generation.answering.parse_text_response", return_value=grade):
        result = _grade_crag_retrieval(1,"登録方法","登録方法",["登録方法"],AnswerContext(records=[a,b],text="text"),object())
    assert result.relevant_chunk_ids == () and not result.sufficient


def test_identical_retry_stops_before_another_grade_call():
    a = record("a"); context = AnswerContext(records=[a], text=a.text)
    grade = CragRetrievalGradeOutput(sufficient=False, confidence=.1, reason="不足", rewritten_query="入力の前提")
    with patch("docrag.generation.answering.build_adb_hybrid_answer_context", return_value=context) as search, patch("docrag.generation.answering.parse_text_response", return_value=grade) as parse:
        result, _, attempts = build_crag_answer_context("登録方法", "", [], object())
    assert search.call_count == 2 and parse.call_count == 1
    assert attempts[-1].stop_reason == "no_new_evidence" and result.records


def test_missing_audit_cannot_pass_even_with_high_scores():
    data,scope,output=evaluation_case("data_confirmation")
    data["answer_text"] += "主档を変更する。"
    with patch("docrag.evaluation.answer_eval.parse_text_response",return_value=output):
        result=evaluate_answer_payload(data,object(),standard_scope=scope)
    assert result["status"]=="completed" and result["total_score"]==20
    assert not result["passed"] and result["missing_audit_passage_ids"]==["A2"]


def test_evaluation_binds_source_text_by_stable_id():
    data,scope,output=evaluation_case("supported")
    span=list(_evidence_fragments(data["evidence_items"]))[0]
    output.claim_checks[0].evidence_id=span["evidence_id"]
    output.claim_checks[0].source_id=""
    output.claim_checks[0].evidence_quote=""
    with patch("docrag.evaluation.answer_eval.parse_text_response",return_value=output):
        result=evaluate_answer_payload(data,object(),standard_scope=scope)
    assert result["claim_checks"][0]["evidence_quote"]==data["evidence_items"][0]["text"]
    assert result["citation_error_count"]==0


def test_missing_coverage_can_cite_refusal_without_getting_credit():
    from docrag.evaluation.answer_eval import CoverageCheck
    data, scope, output = evaluation_case('supported')
    output.coverage_checks=[CoverageCheck(requirement_index=1,status='missing',answer_quote=data['answer_text'],answer_passage_id='A1')]
    with patch('docrag.evaluation.answer_eval.parse_text_response',return_value=output):
        result=evaluate_answer_payload(data,object(),standard_scope=scope)
    assert result['status']=='completed' and result['scores']['coverage']['score']==0


def test_recipient_goal_reserves_list_operations_over_repeated_report_labels():
    from docrag.retrieval.evidence_selection import evidence_excerpt
    question='四半期集計表の会員来店数，店舗・曜日別の対象者を確認したい。'
    text=('画面/メニュー: '+('会員来店数 店舗 曜日 四半期集計表 / '*12)+'\n'
          '操作: 会員別の一覧表にチェックし条件を選択、CSV出力を選んで実行ボタンを押す。\n'
          '検索語: '+('四半期集計表 会員来店数 店舗 曜日 対象者 / '*12))
    selected=evidence_excerpt(question,text,180)
    assert '会員別の一覧表にチェックし条件を選択' in selected
    assert 'CSV出力を選んで実行ボタンを押す。' in selected


def test_extra_coverage_item_is_retried_with_explicit_fixed_ids_and_feedback():
    from docrag.evaluation.answer_eval import CoverageCheck
    data,scope,valid=evaluation_case('supported')
    invalid=valid.model_copy(update={'coverage_checks':[*valid.coverage_checks,CoverageCheck(requirement_index=2,status='missing',answer_quote='')]})
    calls=[]
    def parse(system,prompt,*args,**kwargs):
        inputs=json.loads(prompt);calls.append(inputs)
        assert inputs['standard_answer_scope']['requirements'][0]['requirement_index']==1
        if len(calls)==1:return invalid
        assert '1〜1' in inputs['validation_feedback']
        assert inputs['evidence_items']==calls[0]['evidence_items']
        return valid
    with patch('docrag.evaluation.answer_eval.parse_text_response',side_effect=parse):
        result=evaluate_answer_payload(data,object(),standard_scope=scope)
    assert result['status']=='completed' and len(result['coverage_checks'])==1
    assert len(calls)==2 and len(result['standard_answer_scope']['requirements'])==1


def test_requester_terms_come_from_generic_defaults_and_the_domain_profile():
    """問い合わせ元の語は一般語の既定と profile の requester_terms。業種固有の語はコードに置かない (#849)。"""
    from types import SimpleNamespace
    from unittest.mock import patch
    from docrag.retrieval import task_contract as module
    cached = getattr(module, "_task_contract", None)  # 結果は lru_cache されるため profile を変えたら消す
    assert module.task_contract("顧客から取引先一覧のCSV提供の依頼があった")["requester_mentions"]
    assert not module.task_contract("本部から取引先一覧のCSV提供の依頼があった")["requester_mentions"]
    with patch("docrag.resources.runtime.current_profile", return_value=SimpleNamespace(requester_terms=("本部",))):
        if cached is not None:
            cached.cache_clear()
        assert module.task_contract("本部から売上一覧のCSV提供の依頼があった")["requester_mentions"]
    if cached is not None:
        cached.cache_clear()
