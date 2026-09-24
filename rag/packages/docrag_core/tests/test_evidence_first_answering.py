"""長い候補末尾・後順位・操作境界・誤採点の回帰条件を検証する。"""
from unittest.mock import patch

import pytest

from docrag.generation.answering import (AnswerContext, AnswerRecord, _crag_grade_candidates,
    _merge_crag_evidence, answer_image_evidence)
from docrag.retrieval.evidence_selection import evidence_packet
from docrag.retrieval.task_contract import task_contract
from docrag.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle
from docrag.evaluation.answer_eval import (AnswerEvaluationOutput, AxisScore, ClaimCheck, CoverageCheck,
    StandardAnswerScope, StandardRequirement, evaluate_answer_payload, _evidence_fragments)


def record(key, text, **kwargs):
    return AnswerRecord(id=key, chunk_id=key, engine="docling", engine_label="Docling", page=1,
                        seq_no=1, category="Chunk", text=text, source="manual.pdf", **kwargs)


def test_late_parameter_rule_survives_long_html_and_low_rank():
    rule = "会計年度の初期表示の切替日はパラメータ FiscalSwitch で設定する。"
    records = [record(f"p{i}", "無関係の表" * 800) for i in range(11)]
    records.append(record("p12", "<td>入力例</td>" * 300 + rule + "<td>項目</td>" * 100))
    original = records[-1].text
    context = AnswerContext(records=records, text="full")
    candidates = _crag_grade_candidates(context, "会計年度のデフォルトはいつ切り替わるか")
    assert len(candidates) == 12
    assert rule in candidates[-1]["text"]
    assert rule in evidence_packet("会計年度のデフォルトはいつ切り替わるか", records)
    assert records[-1].text == original
    assert "[…]" in candidates[-1]["text"]


def test_crag_candidates_carry_the_section_heading_and_the_prompt_checks_the_change_target():
    """判定 LLM に機能見出しを渡し、別項目の手順の画面例だけで applicability を supported にさせない (#952)。"""
    from docrag.generation.answering import _build_crag_grade_prompt
    from docrag.generation.crag_support import CRAG_GRADE_PROMPT_TEMPLATE
    heading = ["拠点情報の変更手順", "（１）拠点名の変更"]
    rec = record("p1", "〔マスタ管理⇒マスタ管理2タブ⇒基本設定〕 電話番号 0123-45-6788", metadata={"section_path": heading})
    context = AnswerContext(records=[rec, record("p2", "見出しなし")], text="full")
    candidates = _crag_grade_candidates(context, "拠点の電話番号を変更する手順は")
    assert [c["section"] for c in candidates] == ["拠点情報の変更手順 > （１）拠点名の変更", ""]
    prompt = _build_crag_grade_prompt("拠点の電話番号を変更する手順は", "拠点 電話番号 変更", ["拠点 電話番号 変更"], context)
    assert '"section": "拠点情報の変更手順 > （１）拠点名の変更"' in prompt
    rules = [line for line in CRAG_GRADE_PROMPT_TEMPLATE.splitlines() if line.startswith("- ")]
    assert any("section（機能見出し" in line and "変更項目" in line for line in rules)
    assert any("別の変更項目の手順" in line and "missing" in line for line in rules)


def test_crag_prompt_limits_insufficiency_and_carries_no_output_example():
    """一般手順で十分・制限の記載は回答・複数要求は候補をまたぐ・エラー文は一致が必要、を規則に持ち、出力例 JSON を載せない (#983)。"""
    from docrag.generation.answering import _build_crag_grade_prompt
    from docrag.generation.crag_support import CRAG_GRADE_PROMPT_TEMPLATE
    from docrag.models.llm import CragRetrievalGradeOutput
    rules = [line for line in CRAG_GRADE_PROMPT_TEMPLATE.splitlines() if line.startswith("- ")]
    assert any("質問固有の値" in line and "不足の理由にしない" in line for line in rules)
    assert any("制限の記載" in line and "supported" in line for line in rules)
    assert any("複数の要求" in line and "別々の候補" in line for line in rules)
    assert any("エラー文・エラーコード" in line and "missing" in line for line in rules)
    assert any("観点ごとに独立" in line for line in rules)
    assert "sufficient=false にするのは" in CRAG_GRADE_PROMPT_TEMPLATE
    context = AnswerContext(records=[record("p1", "登録画面で実行を押す")], text="full")
    prompt = _build_crag_grade_prompt("登録方法", "登録方法", ["登録方法"], context)
    assert '"status": "missing"' not in prompt and '"sufficient": false' not in prompt
    schema = CragRetrievalGradeOutput.model_json_schema()
    assert all(schema["properties"][name].get("description") for name in ("sufficient", "candidate_verdicts", "aspect_checks"))
    # 候補ごとの判定と観点・理由を結論（sufficient）より前に生成させる（strict schema は項目順に生成する）。
    order = list(schema["properties"])
    assert order.index("candidate_verdicts") < order.index("aspect_checks") < order.index("reason") < order.index("sufficient")


def test_crag_grade_forces_candidates_without_the_asked_error_to_irrelevant():
    """質問のエラー文を含まない候補は、評価器が true と言っても relevant=false。表記差のあるエラー文は残る (#999)。"""
    from docrag.generation.answering import _grade_crag_retrieval
    from docrag.models.llm import CragRetrievalGradeOutput
    from test_task_contract_audit import record
    question = "入荷検品チェックリストで『仕入先コードが未登録です』のエラーが出ている。どうすればよいか。"
    assert task_contract(question)["error_messages"] == ["仕入先コードが未登録です"]
    other = record("a", "エラー番号 013 「【必須】仕入先コードが仕入先マスタに登録されていない」\n仕入先登録画面で仕入先コードを登録します。")
    same = record("b", "（７）仕入先コード未登録です\n仕入先登録画面で仕入先コードを登録してから再確認します。")
    checks = [dict(aspect=k, status="supported", source_ids=["b"], reason="関連") for k in task_contract(question)["required_aspects"]]
    grade = CragRetrievalGradeOutput(candidate_verdicts=[dict(chunk_uid="a", relevant=True, reason="別コードだが同じ業務"),
                                                         dict(chunk_uid="b", relevant=True, reason="同じエラー")],
                                     aspect_checks=checks, reason="関連", sufficient=True, confidence=0.9, rewritten_query="")
    with patch("docrag.generation.answering.parse_text_response", return_value=grade):
        result = _grade_crag_retrieval(1, question, question, [question], AnswerContext(records=[other, same], text="text"), object())
    assert result.relevant_chunk_ids == ("b",) and result.sufficient
    assert [v["relevant"] for v in result.candidate_verdicts] == [False, True]
    assert result.candidate_verdicts[0]["reason"].startswith("質問のエラー文『仕入先コードが未登録です』が候補に無い")
    only_other = CragRetrievalGradeOutput(candidate_verdicts=[dict(chunk_uid="a", relevant=True, reason="別コードだが同じ業務")],
                                          aspect_checks=[{**c, "source_ids": ["a"]} for c in checks], reason="関連",
                                          sufficient=True, confidence=0.9, rewritten_query="")
    with patch("docrag.generation.answering.parse_text_response", return_value=only_other):
        result = _grade_crag_retrieval(1, question, question, [question], AnswerContext(records=[other, same], text="text"), object())
    assert result.relevant_chunk_ids == () and not result.sufficient


def test_crag_grade_uses_relevant_candidate_verdicts_as_relevant_ids():
    """relevant=true の候補だけが relevant_chunk_ids になり、relevant な候補が無ければ十分としない (#983)。"""
    from docrag.generation.answering import _grade_crag_retrieval
    from docrag.models.llm import CragRetrievalGradeOutput
    a, b = record("a", "登録画面で実行を押す"), record("b", "別機能の説明")
    checks = [dict(aspect=k, status="supported", source_ids=["a"], reason="関連") for k in task_contract("登録方法")["required_aspects"]]
    grade = CragRetrievalGradeOutput(candidate_verdicts=[dict(chunk_uid="a", relevant=True, reason="同じ機能"),
                                                         dict(chunk_uid="b", relevant=False, reason="別機能")],
                                     aspect_checks=checks, reason="関連", sufficient=True, confidence=0.9, rewritten_query="")
    with patch("docrag.generation.answering.parse_text_response", return_value=grade):
        result = _grade_crag_retrieval(1, "登録方法", "登録方法", ["登録方法"], AnswerContext(records=[a, b], text="text"), object())
    assert result.relevant_chunk_ids == ("a",) and result.sufficient
    assert [v["relevant"] for v in result.candidate_verdicts] == [True, False]
    from docrag.models.llm import CragCandidateVerdict
    none_relevant = grade.model_copy(update={"candidate_verdicts": [CragCandidateVerdict(chunk_uid="a", relevant=False, reason="別機能")]})
    with patch("docrag.generation.answering.parse_text_response", return_value=none_relevant):
        result = _grade_crag_retrieval(1, "登録方法", "登録方法", ["登録方法"], AnswerContext(records=[a, b], text="text"), object())
    assert result.relevant_chunk_ids == () and not result.sufficient


def test_crag_keeps_earlier_rule_when_rewrite_finds_other_context():
    first = record("p1", "パラメータによる切替規則")
    second = record("p2", "現在値は実環境で確認する")
    result = _merge_crag_evidence(AnswerContext(records=[first], text=first.text),
        AnswerContext(records=[second], text=second.text), "切替日", max_records=2, max_chars=2000)
    assert {r.id for r in result.records} == {"p1", "p2"}
    assert first.text in result.text and second.text in result.text


def test_image_selection_reaches_relevant_operation_after_first_four(tmp_path):
    records = []
    for i in range(6):
        text = "別帳票の出力例" if i < 5 else "返品理由の直接入力には鉛筆ボタンを使う"
        records.append(record(f"p{i}", text, metadata={"image_evidence": [{
            "image_id": f"img{i}", "raw_type": "picture", "caption": text}]}))
    selected = answer_image_evidence(records, tmp_path, question="返品理由を直接入力したい", max_images=2)
    assert selected[0]["image_id"] == "img5"
    assert len(selected) == 2
    assert answer_image_evidence(records, tmp_path, max_images=0) == ()


def test_operation_support_has_explicit_capacity_without_evicting_primary():
    primary = record("p1", "画面", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    support = record("p2", "鉛筆の操作", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    child = record("c1", "画面", chunk_level="child", parent_chunk_id="p1")
    result = build_chunk_context_bundle(ContextBuildRequest(question="直接入力", ranked_children=[child],
        active_records=[primary, child, support], top_k=1, neighbor_child_count=1,
        max_records=1, max_chars=2000, support_record_limit=1))
    assert {r.id for r in result.records} == {"p1", "p2"}
    assert any(p.reason == "same_operation_context" for p in result.evidence_tree)


def test_evaluation_does_not_repeat_child_text_already_in_parent():
    fragments = list(_evidence_fragments([{"id": "p", "text": "前文。操作本文。後文。", "children": [
        {"id": "c", "text": "操作本文。"}, {"id": "c2", "text": "追加の操作"}]}]))
    assert sum(f["text"].count("操作本文。") for f in fragments) == 1
    assert any(f["id"] == "c2" and f["text"] == "追加の操作" for f in fragments)


def evaluation_case(status, goal="aligned"):
    """満点を要求するモデル出力に対して、引用監査の補正を検証する。"""
    data = {"question": "原因欄を編集する操作は？", "standard_answer": "※で開き鉛筆で編集する。",
        "answer_text": "鉛筆で画面を開く。", "evidence_items": [{"id": "p1", "text": "※で画面を開く。鉛筆で編集する。"}]}
    scope = StandardAnswerScope(requirements=[StandardRequirement(standard_answer_quote="※で開き鉛筆で編集する。",
        requirement="入口と編集ボタンの説明")], excluded_case_data=[])
    proof = "※で画面を開く。" if status in {"supported", "contradicted"} else ""
    output = AnswerEvaluationOutput(external_data_required=False, external_data_items=[],
        evaluated_content=["操作"], coverage_checks=[CoverageCheck(requirement_index=1, status="addressed", answer_quote="鉛筆で画面を開く。")],
        evidence_summary="入口と編集の役割", question_goal="原因欄の編集", goal_alignment=goal, goal_reason="目的との対応",
        claim_checks=[ClaimCheck(answer_quote="鉛筆で画面を開く。", status=status, source_id="p1" if proof else "", evidence_quote=proof, reason="役割を確認")],
        **{key: AxisScore(score=5, reason="モデルは満点と判定") for key in ["accuracy", "coverage", "evidence_consistency", "generation_quality"]})
    return data, scope, output


@pytest.mark.parametrize("status,expected", [("contradicted", 14), ("unsupported", 18)])
def test_unsupported_or_reversed_operations_cannot_receive_full_marks(status, expected):
    data, scope, output = evaluation_case(status)
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["total_score"] == expected
    assert result["scores_before_audit"]["evidence_consistency"]["score"] == 5
    if status == "contradicted":
        assert result["passed"] is False


def test_invented_evaluation_source_cannot_confirm_contradiction():
    data, scope, output = evaluation_case("contradicted")
    output.claim_checks[0].evidence_quote = "存在しない根拠"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed" and result["total_score"] == 20
    assert result["passed"] is False
    assert result["claim_checks"][0]["status"] == "citation_error"
    assert "一致を検証できない" in result["claim_checks"][0]["reason"]


def test_goal_alignment_and_missing_audit_are_separate_from_standard_coverage():
    data, scope, output = evaluation_case("data_confirmation", "off_target")
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["scores"]["coverage"]["score"] == 5
    assert result["goal_alignment"] == "off_target" and result["passed"] is False
    output.goal_alignment = "aligned"
    output.claim_checks = []
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["audit_complete"] is False and result["passed"] is False


def test_audit_binds_original_passage_including_parentheses():
    data, scope, output = evaluation_case("data_confirmation")
    data["answer_text"] = "鉛筆（編集用）で画面を開く。"
    output.coverage_checks[0].answer_quote = data["answer_text"]
    output.claim_checks[0].answer_passage_id = "A1"
    output.claim_checks[0].answer_quote = "鉛筆で画面を開く。"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert result["claim_checks"][0]["answer_quote"] == data["answer_text"]
    assert result["claim_checks"][0]["answer_passage_id"] == "A1"


def test_audit_rejects_nonexistent_answer_passage():
    data, scope, output = evaluation_case("data_confirmation")
    output.claim_checks[0].answer_passage_id = "A999"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output) as parse:
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "error"
    assert parse.call_count == 2


def test_coverage_binds_original_passage_instead_of_joined_steps():
    data, scope, output = evaluation_case("data_confirmation")
    data["answer_text"] = "最初に画面を開く。次に条件を入力する。最後に保存する。"
    output.claim_checks[0].answer_passage_id = "A1"
    output.coverage_checks[0].answer_passage_id = "A3"
    output.coverage_checks[0].answer_quote = "画面を開く。保存する。"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output):
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "completed"
    assert result["coverage_checks"][0]["answer_quote"] == "最後に保存する。"


def test_coverage_rejects_nonexistent_answer_passage():
    data, scope, output = evaluation_case("data_confirmation")
    output.coverage_checks[0].answer_passage_id = "A999"
    with patch("docrag.evaluation.answer_eval.parse_text_response", return_value=output) as parse:
        result = evaluate_answer_payload(data, object(), standard_scope=scope)
    assert result["status"] == "error"
    assert parse.call_count == 2


def test_fragmented_native_ranges_are_rejoined_so_a_sentence_can_be_quoted():
    """解析が1文を数字ごとの原文範囲へ分けても、由来と頁が同じなら1つの根拠として引用できる。"""
    from docrag.retrieval.evidence_selection import _merge_adjacent_spans
    base = dict(source_id="p1", origin="document_text", page=1, section_path=[], excerpt_only=False,
                value_context="reference_document")
    # 解析結果では、原文範囲の間に改行や、範囲から漏れた短い語（「月」など）が未分類で挟まる。
    parts = ["支払変更サービスは、", "\n", "10", "\n", "連休中も受付けています。なお期限は", "\n", "5", "\n月\n", "7", "\n", "日（火）正午までです。"]
    spans, cursor = [], 0
    for text in parts:
        origin = "document_text" if text.strip() and text != "\n月\n" else "unclassified"
        spans.append({**base, "origin": origin, "evidence_id": f"E{cursor}", "start": cursor, "end": cursor + len(text), "text": text})
        cursor += len(text)
    image = {**base, "origin": "image_extraction", "evidence_id": "Eimg", "start": cursor, "end": cursor + 3, "text": "図の値"}
    merged = _merge_adjacent_spans([*spans, image])
    assert [s["text"] for s in merged] == ["".join(parts), "図の値"]  # 由来が変わる境界は残す
    assert merged[0]["end"] == cursor and merged[0]["evidence_id"] not in {s["evidence_id"] for s in spans}
    assert spans[0]["text"] == parts[0]  # 入力は変更しない
