"""回答のモデルに渡す根拠の文字の形（全角・半角）をそろえ、引用の照合は原文で通ることを確かめる (#1350)。

モデルはスタブ。資料の全角の実体名（ＨＲＭ）と質問の半角（HRM）を、モデルが同じ語として読める形で渡し、
表示・保存する根拠と引用の原文は資料の表記のままにする。
"""

import json

from test_grounded_answer import FakeModel, audit, context, draft, ids, item, record, run

from rag_engine.generation import grounded
from rag_engine.generation.answering import _grounded_spans
from rag_engine.models.llm import GroundedDraft
from rag_engine.retrieval.character_forms import fold_model_view, fold_text, fold_text_with_positions, fold_width
from rag_engine.retrieval.text_search_tokenizer import normalize_text_search_index_text

PLAN = "定期保守計画\nＨＲＭ：第２水曜 22:00〜24:00\nＯＭＳ：第３木曜 21:00〜23:00\n上記以外のシステムは最終金曜に実施します。"
QUESTION = "HRM の人事評価の変更はいつの保守枠で実施しますか"
# モデルが読む形（全角・半角だけをそろえた形）で書き写した引用と説明。
QUOTE = "HRM:第2水曜 22:00〜24:00"
TEXT = "HRM の変更は第2水曜 22:00〜24:00 の保守枠で実施します。"


def _plan_context():
    return context(record(0, PLAN, "定期保守計画", page=3, source="保守計画.pdf"))


def test_fold_width_only_folds_full_and_half_width_forms():
    assert fold_width("ＨＲＭ：ＳＹＳ－１０４") == "HRM:SYS-104"
    assert fold_width("ｼｽﾃﾑ管理ｶﾞｲﾄﾞ　ﾊﾟｽ") == "システム管理ガイド パス"
    # 丸数字（手順の区切り）・ローマ数字・波ダッシュ・改行・大文字小文字は残す。
    assert fold_width("①申請\nⅤ章 10〜20 Abc") == "①申請\nⅤ章 10〜20 Abc"
    # JSON に入れる値は値の側でそろえる（全角の引用符を JSON の記号にしない）。
    view = fold_model_view({"機能（１）": ["＂ＨＲＭ＂", 3], "n": None})
    assert view == {"機能(1)": ['"HRM"', 3], "n": None}
    assert json.loads(json.dumps(view, ensure_ascii=False)) == view


def test_fold_text_matches_the_text_search_index_rule_with_positions():
    for raw in (PLAN, "ｶﾞｲﾄﾞ ㈱ ① 10〜20—30", "a\x00\x01b\tc", "が", ""):
        folded, positions = fold_text_with_positions(raw)
        assert folded == fold_text(raw) == normalize_text_search_index_text(raw)
        assert len(positions) == len(folded)
        assert all(0 <= p < len(raw) for p in positions)
    folded, positions = fold_text_with_positions("ｶﾞｲﾄﾞ")
    assert folded == "ガイド" and positions == [0, 2, 3]


def test_folded_quote_resolves_to_the_original_text():
    assert grounded.quote_in_text(QUOTE, PLAN)
    # 波ダッシュ・ダッシュの違いも索引と同じ規則で同一視する。
    assert grounded.quote_in_text("HRM:第2水曜 22:00~24:00", PLAN)
    assert grounded.quote_in_text("システム管理ガイドを開きます", "ｼｽﾃﾑ管理ｶﾞｲﾄﾞを開きます。")
    # 表示する引用は原文の範囲（全角のまま）。
    assert grounded._original_quote(QUOTE, PLAN) == "ＨＲＭ：第２水曜 22:00〜24:00"
    assert grounded._original_quote("システム管理ガイドを開きます", "ｼｽﾃﾑ管理ｶﾞｲﾄﾞを開きます。") == "ｼｽﾃﾑ管理ｶﾞｲﾄﾞを開きます。"
    # 文字や数字が違う引用は通さない。
    assert not grounded.quote_in_text("HRM:第3水曜 22:00〜24:00", PLAN)


def test_figure_dash_is_folded_only_for_comparison() -> None:
    from rag_engine.retrieval.character_forms import fold_text

    assert fold_text("row‒a") == "row-a"
    assert fold_width("row‒a") == "row‒a"


def test_original_forms_are_supplementary_and_bound_to_units():
    raw = "文字表記\n全角ＡＢＣ、半角ABC、ｶﾞ、全角＂と＼。\n変更のない本文。"
    spans = _grounded_spans("原文の文字は？", context(record(0, raw, "文字表記")), (), None)
    block = grounded.evidence_block_text(spans)
    assert "全角ABC、半角ABC" in block
    start = block.index("原文の文字表記: ") + len("原文の文字表記: ")
    annotations = json.JSONDecoder().raw_decode(block[start:])[0]
    assert annotations == ["ＡＢＣ", "ｶﾞ"]
    # 引用単位の全文は重ねない。句読点・空白の幅だけが変わる単位は載せない（#4139）。
    assert "全角ＡＢＣ、半角ABC" not in block
    view = grounded._citation_view(spans[0])
    assert view.model_rendered in block
    assert view.text == spans[0]["text"]
    assert grounded._model_source_note(spans[0], view.units) in grounded.evidence_metadata_text(spans[0])


def test_no_extra_metadata_without_character_changes():
    spans = _grounded_spans("内容は？", context(record(0, "本文Aを確認する。", "本文A")), (), None)
    block = grounded.evidence_block_text(spans)
    assert "原文の文字表記" not in block
    assert block == grounded.neutralize_boundary_markers(fold_width(grounded.format_functions(spans)))


def test_answer_can_distinguish_original_full_and_half_width_characters():
    raw = "文字表記\n全角ＡＢＣ、半角ABC。"
    question = "全角と半角の英字は、原文にどのように記載されていますか。"
    ctx = context(record(0, raw, "文字表記"))
    eid = ids(ctx, question)[raw]
    answer = "全角は「ＡＢＣ」、半角は「ABC」と記載されています。"

    def generate(prompt):
        assert '"ＡＢＣ"' in prompt
        assert "全角ABC、半角ABC。" in prompt
        return draft(item(eid, answer, "全角ＡＢＣ、半角ABC。", kind="rule"), summary=answer)

    model = FakeModel([generate], [audit((0, "supported", "matched", ""))])
    result = run(model, ctx, question).response
    assert answer in result.answer_text
    assert result.evidence_facts[0]["quote"] == "全角ＡＢＣ、半角ABC。"


def test_a_unit_where_only_punctuation_width_changes_adds_no_metadata():
    """#4139: 全角の句読点・括弧・空白だけが変わる単位は、字の違いが無いので補助情報を増やさない。"""
    raw = "第１条（目的），本文\u3000です。\n条件（Ａ）、半角（B）です。"
    spans = _grounded_spans("目的は？", context(record(0, "全角（括弧），記号：だけです。\u3000本文。", "本文")), (), None)
    assert "原文の文字表記" not in grounded.evidence_block_text(spans)
    spans = _grounded_spans("目的は？", context(record(0, raw, "本文")), (), None)
    block = grounded.evidence_block_text(spans)
    start = block.index("原文の文字表記: ") + len("原文の文字表記: ")
    annotations = json.JSONDecoder().raw_decode(block[start:])[0]
    # 中身だけを載せ、原文の全文を重ねない。
    assert annotations == ["１", "Ａ"]
    # 引用の原文はコードが切り出すので、全角のまま残る。
    assert spans[0]["text"] == raw
