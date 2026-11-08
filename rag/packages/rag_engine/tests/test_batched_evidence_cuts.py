"""長い根拠を分けて読むときの切れ目を、合成の本文で確かめる（#4667）。"""

import json
from dataclasses import replace

import pytest

from rag_engine.config import get_settings
from rag_engine.generation.answering import _invalid_structured_output
from rag_engine.generation.batched_evidence import prepare_batched_evidence


def config():
    base = get_settings(environ={}, dotenv_path=None)
    providers = {key: replace(value, max_output_tokens=100) for key, value in base.llm_providers.items()}
    return replace(base, llm_providers=providers, evidence_budget_chars=3500)


def block(spans):
    return json.dumps([{"id": span["evidence_id"], "text": span["text"]} for span in spans], ensure_ascii=False)


def run(value, findings=None):
    """読取の入力（組ごとの evidence）と診断を返す。``findings`` は組ごとの選択を返す関数。"""
    batches = []

    def read(system, prompt, schema):
        evidence = json.loads(prompt)["evidence"]
        batches.append(evidence)
        return schema.model_validate({"findings": findings(evidence) if findings else []})

    selected, _, diagnostic = prepare_batched_evidence(
        [value],
        config(),
        question="合成の質問",
        render=block,
        evidence_text=block,
        system="指示",
        schema={"type": "object"},
        read=read,
        unusable_output=_invalid_structured_output,
    )
    return batches, diagnostic, selected


def make_span(text, *, start=0, **extra):
    return {"evidence_id": "E1", "source_id": "p1", "source": "合成文書", "text": text, "start": start, "end": start + len(text), **extra}


def read_cuts(diagnostic, value):
    """最初の読取の組の範囲が原文を欠落も重複も無く覆うことを確かめ、組の境目（本文の中の位置）を返す。"""
    base = value["start"]
    ranges = sorted(
        (row["start"] - base, row["end"] - base) for batch in diagnostic["batches"] if batch["pass_index"] == 1 for row in batch["ranges"]
    )
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(value["text"])
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False))
    return [end for _start, end in ranges[:-1]]


def paragraph(label, sentences=12):
    return "".join(f"{label}の合成の本文その{index}です。" for index in range(sentences))


def sectioned_parent():
    """章節ごとの子の範囲と章節の名前を持つ親本文。"""
    titles = ["概要の節", "詳細の節", "補足の節"]
    parts = [f"{title}\n" + "\n".join(paragraph(f"{title}{index}") for index in range(6)) for title in titles]
    text = "\n".join(parts)
    starts = [text.index(part) for part in parts]
    base = 1000
    children = [{"source_id": f"c{index}", "section_path": ["合成の章", title]} for index, title in enumerate(titles)]
    ranges = [{"id": f"c{index}", "start": base + start, "end": base + start + len(parts[index])} for index, start in enumerate(starts)]
    value = make_span(
        text,
        start=base,
        section_path=["合成の章"],
        source_context={"document_name": "合成文書", "section_path": ["合成の章"], "children": children},
        child_ranges=ranges,
    )
    return value, starts


def test_section_boundary_is_used_when_the_section_information_is_known():
    value, starts = sectioned_parent()
    _batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert set(cuts) <= set(starts[1:])


def test_section_boundary_moves_back_to_the_title_line_of_a_section_before_its_first_known_child():
    """章節の最初の子が文脈に無いときも、章節の名前の行で切る。"""
    head = "概要の節\n" + "\n".join(paragraph(f"前半{index}") for index in range(10))
    tail_head = "\n".join(paragraph(f"後半の前置き{index}") for index in range(2))
    tail = "\n".join(paragraph(f"後半{index}") for index in range(8))
    text = f"{head}\n詳細の節\n{tail_head}\n{tail}"
    children = [{"source_id": "c0", "section_path": ["概要の節"]}, {"source_id": "c1", "section_path": ["詳細の節"]}]
    first = len(head)
    start = text.index(tail)
    value = make_span(
        text,
        source_context={"document_name": "合成文書", "section_path": ["概要の節"], "children": children},
        child_ranges=[{"id": "c0", "start": 0, "end": first}, {"id": "c1", "start": start, "end": len(text)}],
    )
    _batches, diagnostic, _selected = run(value)
    assert text.index("詳細の節") in read_cuts(diagnostic, value)


def test_heading_boundary_is_used_without_section_information():
    sections = [f"第{number}章 合成の章\n" + "\n".join(paragraph(f"章{number}段落{index}") for index in range(6)) for number in range(1, 4)]
    text = "\n".join(sections)
    value = make_span(text)
    _batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert set(cuts) <= {text.index(section) for section in sections[1:]}


def test_a_heading_line_stays_with_the_body_that_follows_it():
    """見出しの行と本文の間では切らない（段落の区切りの候補から外す）。"""
    body = "\n\n".join(paragraph(f"段落{index}") for index in range(30))
    text = f"第1章 合成の章\n\n{body}"
    value = make_span(text)
    _batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert text.index(body) not in cuts


def test_paragraph_boundary_is_preferred_over_a_later_sentence_end():
    paragraphs = [paragraph(f"段落{index}", sentences=30) for index in range(9)]
    text = "\n\n".join(paragraphs)
    value = make_span(text)
    _batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert set(cuts) <= {text.index(item) for item in paragraphs[1:]}


def long_table(rows=160, header="| 列A | 列B |", rule="| --- | --- |"):
    lines = [header, rule] + [f"| 項目{index} | 値{index}の説明です。続きの説明です。 |" for index in range(rows)]
    return "\n".join(lines)


def test_long_markdown_table_is_cut_between_rows_and_the_next_batch_repeats_the_header():
    text = long_table()
    value = make_span(text)
    batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert all(text[cut - 1] == "\n" and text[cut:].startswith("| 項目") for cut in cuts)
    assert len(batches) > 1
    assert "continued_table_header" not in batches[0][0]
    assert all(batch[0]["continued_table_header"] == "| 列A | 列B |\n| --- | --- |" for batch in batches[1:])


def test_repeated_table_header_is_not_a_citable_unit_and_coordinates_point_to_the_original_row():
    text = long_table()
    value = make_span(text, start=500)

    def findings(evidence):
        # 続きの組の最初の行だけを選ぶ（番号の付いた行は原文の行だけ）。
        first = evidence[0]
        if "continued_table_header" not in first:
            return []
        assert "列A" not in first["text"]
        return [{"unit_ids": [first["text"].split("[", 1)[1].split("]", 1)[0]], "answer": "そのとおりです。"}]

    _batches, _diagnostic, selected = run(value, findings)
    rows = [item for item in selected if item["text"].startswith("| 項目")]
    assert rows
    for item in rows:
        assert text[item["start"] - 500 : item["end"] - 500] == item["text"]


def test_a_table_with_a_different_column_count_is_a_separate_table():
    first = long_table(rows=40)
    second = long_table(rows=120, header="| 列X | 列Y | 列Z |", rule="| --- | --- | --- |").replace("値", "| 値")
    text = f"{first}\n{second}"
    value = make_span(text)
    batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    # 別の表の表頭と最初のデータ行の間では切らない。
    separator = text.index("| --- | --- | --- |") + len("| --- | --- | --- |\n")
    assert separator not in cuts
    headers = [batch[0].get("continued_table_header") for batch in batches]
    assert headers[-1] == "| 列X | 列Y | 列Z |\n| --- | --- | --- |"


def test_long_html_table_on_one_line_is_cut_after_a_row_and_repeats_the_header_row():
    rows = "".join(f"<tr><td>項目{index}</td><td>値{index}の説明です。続きの説明です。</td></tr>" for index in range(120))
    header = "<tr><th>列A</th><th>列B</th></tr>"
    text = f"<table>{header}{rows}</table>"
    value = make_span(text)
    batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert all(text[:cut].endswith("</tr>") for cut in cuts)
    assert text.index(header) + len(header) not in cuts
    assert all(batch[0]["continued_table_header"] == f"<table>{header}" for batch in batches[1:])


def test_sentence_boundary_is_used_only_when_there_is_no_larger_unit():
    text = paragraph("区切りの無い段落", sentences=300)
    value = make_span(text)
    _batches, diagnostic, _selected = run(value)
    cuts = read_cuts(diagnostic, value)
    assert cuts
    assert all(text[cut - 1] == "。" for cut in cuts)


def test_a_long_run_without_any_boundary_is_cut_by_characters():
    text = "長" * 12000
    value = make_span(text)
    _batches, diagnostic, _selected = run(value)
    assert read_cuts(diagnostic, value)


@pytest.mark.parametrize(
    "build",
    [
        lambda: sectioned_parent()[0],
        lambda: make_span("\n".join(f"第{n}章 合成の章\n" + paragraph(f"章{n}", sentences=120) for n in range(1, 5))),
        lambda: make_span(long_table()),
        lambda: make_span("長" * 12000),
    ],
    ids=["sections", "headings", "table", "no-boundary"],
)
def test_every_input_is_read_without_loss_or_duplication(build):
    value = build()
    _batches, diagnostic, _selected = run(value)
    read_cuts(diagnostic, value)
