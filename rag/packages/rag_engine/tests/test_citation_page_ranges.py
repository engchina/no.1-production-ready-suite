"""引用の終頁を本文と公開する根拠の両方に保つ。"""

import pytest

from rag_engine.generation.answer_models import AnswerRecord
from rag_engine.generation.answer_payload import _published_reference_records
from rag_engine.generation.grounded import _citation


@pytest.mark.parametrize(("end", "label"), [(None, "p.3"), (3, "p.3"), (4, "p.3-4")])
def test_citation_formats_the_actual_page_range(end, label):
    assert _citation({"source": "sample.pdf", "page": 3, "page_end": end}).endswith(label)


def test_sheet_location_keeps_priority_over_pages():
    assert _citation({"source": "sample.xlsx", "page": 3, "page_end": 4, "location": "シート A A1:B2"}).endswith("シート A A1:B2")


@pytest.mark.parametrize(("start", "end", "expected"), [(3, 4, (3, 4)), (4, 4, (4, 4))])
def test_public_reference_only_narrows_a_confirmed_single_page(start, end, expected):
    record = AnswerRecord(
        id="a",
        engine="synthetic",
        engine_label="合成",
        page=3,
        page_end=4,
        seq_no=1,
        category="text",
        text="合成の本文",
        chunk_uid="a",
        source_seq_ranges=({"page": 3}, {"page": 4}),
    )
    trace = {
        "finalization": {"citations_synchronized": True, "retained_evidence_ids": ["e"]},
        "selected_evidence": [{"evidence_id": "e", "source_id": "a", "page": start, "page_end": end, "text": "採用した合成の本文"}],
    }
    result = _published_reference_records([record], trace)[0]
    assert (result.page, result.page_end) == expected
    assert [item["page"] for item in result.source_seq_ranges] == list(range(*[expected[0], expected[1] + 1]))
    assert (record.page, record.page_end, record.text) == (3, 4, "合成の本文")
