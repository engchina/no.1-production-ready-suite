"""文書の値を今回の実データと同一視しない方針を全回答段階へ渡す。"""

from test_rag_evidence_recovery import rec

from rag_engine.retrieval.evidence_selection import evidence_spans, format_evidence_packet


def test_document_context_preserves_source_values_instead_of_deleting_them():
    text = "本文: 金額を入力して更新します。\n回答用本文: 画面例は5000円、コード001です。"
    record = rec("p1", "parent", 1, text)
    spans = evidence_spans("金額の変更方法は？", [record])
    assert spans and all(s["value_context"] == "reference_document" for s in spans)
    assert "5000円" in format_evidence_packet(spans)
    assert "not verified as current case data" in format_evidence_packet(spans)
    assert record.text == text
    assert all(record.text[s["start"] : s["end"]] == s["text"] for s in spans)


def test_old_span_metadata_is_still_labeled_document_reference():
    span = dict(
        source_id="p1",
        evidence_id="E1",
        source="manual",
        page=1,
        section_path=[],
        excerpt_only=False,
        text="対象月はYYYY-MM形式で入力します。",
    )
    packet = format_evidence_packet([span])
    assert "Origin: unclassified" in packet
    assert "Value context: reference_document (not verified as current case data)" in packet
    assert packet.endswith(span["text"])
    assert "value_context" not in span
