"""文書の値を今回の実データと同一視しない方針を全回答段階へ渡す。"""
from docrag.retrieval.evidence_selection import evidence_spans, format_evidence_packet
from test_rag_evidence_recovery import rec


def test_document_context_preserves_source_values_instead_of_deleting_them():
    text = '本文: 金額を入力して更新します。\n回答用本文: 画面例は5000円、コード001です。'
    record = rec('p1', 'parent', 1, text)
    spans = evidence_spans('金額の変更方法は？', [record])
    assert spans and all(s['value_context'] == 'reference_document' for s in spans)
    assert '5000円' in format_evidence_packet(spans)
    assert 'not verified as current case data' in format_evidence_packet(spans)
    assert record.text == text
    assert all(record.text[s['start']:s['end']] == s['text'] for s in spans)


def test_old_span_metadata_is_still_labeled_document_reference():
    span = dict(source_id='p1', evidence_id='E1', source='manual', page=1,
                section_path=[], excerpt_only=False, text='対象月はYYYY-MM形式で入力します。')
    packet = format_evidence_packet([span])
    assert 'Origin: unclassified' in packet
    assert 'Value context: reference_document (not verified as current case data)' in packet
    assert packet.endswith(span['text'])
    assert 'value_context' not in span


def test_displayed_range_is_not_an_input_constraint_even_when_image_text_says_so():
    from docrag.generation.value_grounding import unsupported_document_value_claim
    source = [dict(origin='image_extraction', text='表示コードは01～18。入力範囲は01～18です。')]
    assert unsupported_document_value_claim('管理番号の許容範囲は01～18です。', source, '許容範囲は？')
    assert not unsupported_document_value_claim('図の表示例は01～18です。', source, '図の値は？')
    assert not unsupported_document_value_claim('01～18が許容範囲かは確認できません。', source, '許容範囲は？')


def test_native_format_and_default_and_user_value_are_preserved():
    from docrag.generation.value_grounding import unsupported_document_value_claim
    native = [dict(origin='document_text', text='識別コードは半角数字6桁で入力します。初期値は000000です。')]
    assert not unsupported_document_value_claim('半角数字6桁です。初期値は000000です。', native, '形式は？')
    image = [dict(origin='image_extraction', text='図の金額は5000円です。')]
    assert unsupported_document_value_claim('あなたの支払金額は5000円です。', image, '私の金額は？')
    assert not unsupported_document_value_claim('今回は8000円を入力します。', image, '今回は8000円へ変更したい。')


def test_page_citations_and_unrelated_uncertainty_do_not_change_numeric_rule_validation():
    from docrag.generation.value_grounding import unsupported_document_value_claim
    native = [dict(origin='document_text', text='識別コードは半角数字6桁で入力します。')]
    assert not unsupported_document_value_claim('1. 半角数字6桁です（PDF p.12）。', native, '形式は？')
    image = [dict(origin='image_extraction', text='例は01～18です。')]
    assert unsupported_document_value_claim('許容範囲は01～18ですが、現在の設定は未確認です。', image, '範囲は？')
    example = [dict(origin='document_text', text='画面例の金額は5000円です。')]
    assert unsupported_document_value_claim('あなたの金額は5000円です。', example, '私の金額は？')


def test_numbers_given_in_the_question_are_not_display_examples():
    # 質問「年３回から４回」の 4 は利用者の条件で、図の例示値ではない (#629)。
    from docrag.generation.value_grounding import unsupported_document_value_claim
    source = [dict(origin='document_text', text='①新しい掛率コード（英数字 2 桁）を入力します。適用月にチェックを入れます。')]
    text = '新しい2桁のコードを入力し、適用月のチェックボックスで希望する月（例：4回分）にチェックを入れます。'
    assert not unsupported_document_value_claim(text, source, '掛率Bの適用月を年３回から４回に変更したい。')
    assert unsupported_document_value_claim(text, source, '掛率Bの適用月を変更したい。')
