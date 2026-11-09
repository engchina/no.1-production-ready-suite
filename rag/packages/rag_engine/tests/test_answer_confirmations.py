"""回答の確定に必要な実データ・別の資料の確認を案内する (#688)。"""

from test_grounded_answer import FakeModel, audit, context, draft, record, run

from rag_engine.generation import grounded
from rag_engine.generation.citation_locator import EVIDENCE_LABEL
from rag_engine.models.llm import GroundedDraft


def _draft(**extra):
    return {**draft({"kind": "gap", "text": "今回の支払区分は資料からは確認できません。"}, summary="", confidence="low"), **extra}


def test_confirmation_lines_keep_data_and_documents_named_in_the_evidence_or_question():
    spans = [{"text": "支払区分は「支払区分コード表」を参照してください。"}]
    value = GroundedDraft.model_validate(
        _draft(
            external_data_items=["対象の取引先の支払区分の設定値（どの手順に当たるかを決める）", " "],
            reference_materials=[
                "「支払区分コード表」（区分の意味を確かめる）",
                "料金表（手数料を確かめる）",
                "「架空の運用規程」（創作された資料名）",
            ],
        )
    )
    assert grounded.confirmation_lines(value, "支払区分の意味は？", spans) == [
        "実データ: 対象の取引先の支払区分の設定値（どの手順に当たるかを決める）",
        "資料: 「支払区分コード表」（区分の意味を確かめる）",
        "資料: 料金表（手数料を確かめる）",
    ]


def test_no_evidence_with_confirmations_guides_instead_of_refusing():
    ctx = context(record(0, "帳票の一覧です。", "その他"))
    guided = _draft(external_data_items=["対象伝票の状態（取消できるかを決める）"])
    result = run(FakeModel([guided], [audit()]), ctx).response
    assert result.answer_text.startswith(grounded.answer_labels().confirmation_only_summary)
    assert "・実データ: 対象伝票の状態（取消できるかを決める）" in result.answer_text
    assert "回答できません" not in result.answer_text
    # 確認することも無ければ、今までどおり正直に拒答する（作り話をしない）。
    refused = run(FakeModel([_draft()], [audit()]), ctx).response
    assert refused.answer_text.startswith("検索された資料に回答を裏付ける十分な根拠がない")
    assert grounded.answer_labels().confirmations_section not in refused.answer_text


def test_reference_materials_need_human_review():
    ctx = context(record(0, "帳票の一覧です。", "その他"))
    result = run(FakeModel([_draft(reference_materials=["料金表（手数料を確かめる）"])], [audit()]), ctx).response
    assert result.needs_human_review is True
    assert "・資料: 料金表（手数料を確かめる）" in result.answer_text


def test_internal_evidence_ids_are_removed_from_confirmations():
    """確認の節と回答の構造に、根拠の表示 ID を出さない。ID だけの項目は内容が無いので外す（#4752）。"""
    ctx = context(record(0, "帳票の一覧です。", "その他"))
    generated = _draft(
        reference_materials=["E6 (区分の意味を確かめる)", "E23", "料金表（E6）（手数料を確かめる）"],
        external_data_items=["対象の設定値（E23）（どの手順に当たるかを決める）", "E6"],
    )
    result = run(FakeModel([generated], [audit()]), ctx).response
    assert EVIDENCE_LABEL.search(result.answer_text) is None
    assert result.envelope["reference_materials"] == ["(区分の意味を確かめる)", "料金表（手数料を確かめる）"]
    assert result.envelope["confirmations"] == [
        "実データ: 対象の設定値（どの手順に当たるかを決める）",
        "資料: (区分の意味を確かめる)",
        "資料: 料金表（手数料を確かめる）",
    ]


def test_codes_in_the_document_or_question_stay_in_confirmations():
    """資料の本文か質問にある同じ字のコードは、表示 ID ではないので残す（#4752）。"""
    ctx = context(record(0, "コード E7 は区分を表します。", "その他"))
    generated = _draft(reference_materials=["コード E7 の定義表（区分の意味を確かめる）", "コード E9 の定義表（対処を確かめる）"])
    result = run(FakeModel([generated], [audit()]), ctx, question="コード E9 の意味は？").response
    assert result.envelope["reference_materials"] == [
        "コード E7 の定義表（区分の意味を確かめる）",
        "コード E9 の定義表（対処を確かめる）",
    ]


def test_application_premises_do_not_show_internal_evidence_ids():
    spans = [{"text": "資料の本文。"}]
    value = GroundedDraft.model_validate(
        {
            **_draft(),
            "applications": [
                {
                    "request_id": "Q1",
                    "item_indexes": [0],
                    "premises": ["属性は有（E2）", "E2"],
                    "reasoning": "合計する。",
                    "result": "18 点です。",
                }
            ],
        }
    )
    assert grounded._without_evidence_labels(value, "質問の原文", spans).applications[0].premises == ["属性は有"]
