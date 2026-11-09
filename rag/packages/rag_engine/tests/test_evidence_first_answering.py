"""長い候補末尾・後順位・操作境界・誤採点の回帰条件を検証する。"""

from rag_engine.generation.answering import AnswerContext, AnswerRecord, answer_image_evidence, merge_evidence
from rag_engine.retrieval.context_builder import ContextBuildRequest, build_chunk_context_bundle
from rag_engine.retrieval.evidence_selection import evidence_packet


def record(key, text, **kwargs):
    return AnswerRecord(
        id=key,
        chunk_id=key,
        engine="docling",
        engine_label="Docling",
        page=1,
        seq_no=1,
        category="Chunk",
        text=text,
        source="manual.pdf",
        **kwargs,
    )


def test_late_parameter_rule_survives_long_html_and_low_rank():
    rule = "会計年度の初期表示の切替日はパラメータ FiscalSwitch で設定する。"
    records = [record(f"p{i}", "無関係の表" * 800) for i in range(11)]
    records.append(record("p12", "<td>入力例</td>" * 300 + rule + "<td>項目</td>" * 100))
    original = records[-1].text
    AnswerContext(records=records, text="full")
    assert rule in evidence_packet("会計年度のデフォルトはいつ切り替わるか", records)
    assert records[-1].text == original


def test_crag_keeps_earlier_rule_when_rewrite_finds_other_context():
    first = record("p1", "パラメータによる切替規則")
    second = record("p2", "現在値は実環境で確認する")
    result = merge_evidence(
        AnswerContext(records=[first], text=first.text),
        AnswerContext(records=[second], text=second.text),
        "切替日",
        max_records=2,
        max_chars=2000,
    )
    assert {r.id for r in result.records} == {"p1", "p2"}
    assert first.text in result.text and second.text in result.text


def test_image_selection_reaches_relevant_operation_after_first_four(tmp_path):
    records = []
    for i in range(6):
        text = "別帳票の出力例" if i < 5 else "返品理由の直接入力には鉛筆ボタンを使う"
        records.append(
            record(f"p{i}", text, metadata={"image_evidence": [{"image_id": f"img{i}", "raw_type": "picture", "caption": text}]})
        )
    selected = answer_image_evidence(records, tmp_path, question="返品理由を直接入力したい", max_images=2)
    assert selected[0]["image_id"] == "img5"
    assert len(selected) == 2
    assert answer_image_evidence(records, tmp_path, max_images=0) == ()


def test_parent_context_does_not_use_operation_support_capacity():
    primary = record("p1", "画面", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    support = record("p2", "鉛筆の操作", chunk_level="parent", metadata={"section_path": ["〔返品理由入力〕"]})
    child = record("c1", "画面", chunk_level="child", parent_chunk_id="p1")
    result = build_chunk_context_bundle(
        ContextBuildRequest(
            question="直接入力",
            ranked_children=[child],
            active_records=[primary, child, support],
            top_k=1,
            neighbor_child_count=1,
            max_records=1,
            max_chars=2000,
            support_record_limit=1,
        )
    )
    assert [r.id for r in result.records] == ["p1"]
    assert all(p.reason != "same_operation_context" for p in result.evidence_tree)


def test_fragmented_native_ranges_are_rejoined_so_a_sentence_can_be_quoted():
    """解析が1文を数字ごとの原文範囲へ分けても、由来と頁が同じなら1つの根拠として引用できる。"""
    from rag_engine.retrieval.evidence_selection import _merge_adjacent_spans

    base = dict(source_id="p1", origin="document_text", page=1, section_path=[], excerpt_only=False, value_context="reference_document")
    # 解析結果では、原文範囲の間に改行や、範囲から漏れた短い語（「月」など）が未分類で挟まる。
    parts = [
        "支払変更サービスは、",
        "\n",
        "10",
        "\n",
        "連休中も受付けています。なお期限は",
        "\n",
        "5",
        "\n月\n",
        "7",
        "\n",
        "日（火）正午までです。",
    ]
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
