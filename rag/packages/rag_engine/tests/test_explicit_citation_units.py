"""新しい表示の明示番号と、旧未番号の引用契約を混同しない。"""

from typing import Any

import pytest
from citation_unit_support import without_unit_annotations

from rag_engine.generation import grounded
from rag_engine.generation.citation_wire import canonical_generation_draft, generation_draft_wire_model
from rag_engine.models.llm import GroundedDraft, GroundedItem


def span(index: int, text: str) -> dict[str, Any]:
    return {
        "evidence_id": f"stable-{index}",
        "label": f"E{index}",
        "source_id": f"parent-{index}",
        "source": "synthetic.pdf",
        "text": text,
        "function": ("synthetic.pdf", "対象操作"),
        "source_order": (0, index, 0),
        "tags": [],
        "child_ranges": [{"id": f"child-{index}", "start": 0, "end": len(text)}],
    }


def draft(start: int | None, end: int | None, identity: str = "E1") -> GroundedDraft:
    value = generation_draft_wire_model().model_validate(
        {
            "items": [
                {
                    "kind": "operation",
                    "request_id": "Q1",
                    "text": "対象の操作を案内します。",
                    "citations": [{"evidence_id": identity, "line_start": start, "line_end": end}],
                }
            ],
            "summary": "合成の案内です。",
            "confidence": "low",
        }
    )
    return canonical_generation_draft(value)


def extract(proposal: GroundedDraft, evidence: list[dict[str, Any]]) -> tuple[GroundedItem, str]:
    return grounded.cited_quote(proposal.items[0], evidence)


def test_short_multiline_block_numbers_each_physical_line_and_quotes_the_original() -> None:
    evidence = [span(1, "開く\n保存")]
    prompt = grounded.format_functions(evidence)
    view = grounded._citation_view(evidence[0])
    assert len(view.units) == 2
    assert view.numbered is True
    assert view.contract_version == grounded.CITATION_UNIT_CONTRACT
    assert "1| 開く\n2| 保存" in without_unit_annotations(prompt)
    assert "3|" not in without_unit_annotations(prompt)
    cited, error = extract(draft(1, 2), evidence)
    assert not error and cited.quote == "開く\n保存"
    assert cited.citations[0]._extraction["sent_view_status"] == "prepared"
    cited, error = extract(draft(2, 2), evidence)
    assert not error and cited.quote == "保存"


@pytest.mark.parametrize("text", ["実行", "42", "項目 | 値\nA | 1"])
def test_short_button_number_and_table_remain_numbered_original_content(text: str) -> None:
    evidence = [span(1, text)]
    lines = text.split("\n")
    assert "\n".join(f"{number}| {line}" for number, line in enumerate(lines, 1)) in without_unit_annotations(
        grounded.format_functions(evidence)
    )
    cited, error = extract(draft(1, len(lines)), evidence)
    assert not error and cited.quote == text


def test_each_evidence_has_its_own_upper_bound_not_the_neighbour_bound() -> None:
    long_text = "\n".join(character * 220 for character in ("甲", "乙", "丙"))
    evidence = [span(1, long_text), span(2, "実行")]
    grounded.format_functions(evidence)
    assert len(grounded._citation_view(evidence[0]).units) == 3
    cited, error = extract(draft(3, 3), evidence)
    assert not error and cited.quote == "丙" * 220
    invalid, error = extract(draft(3, 3, "E2"), evidence)
    assert error and not invalid.quote
    assert invalid.citations[0]._extraction["reason_code"] == "citation_range_out_of_bounds"


def test_null_is_missing_in_new_view_but_legacy_uncached_short_block_still_accepts_null() -> None:
    displayed = [span(1, "開く\n保存")]
    grounded.format_functions(displayed)
    invalid, error = extract(draft(None, None), displayed)
    assert error and not invalid.quote
    assert invalid.citations[0]._extraction["reason_code"] == "citation_range_missing"

    legacy = [span(1, "開く\n保存")]
    assert grounded._citation_view(legacy[0]).numbered is False
    cited, error = extract(draft(None, None), legacy)
    assert not error and cited.quote == legacy[0]["text"]
    assert cited.citations[0]._extraction["sent_view_status"] == "not_recorded"


@pytest.mark.parametrize(
    "start,end,reason",
    [
        (0, 0, "citation_range_out_of_bounds"),
        (3, 3, "citation_range_out_of_bounds"),
        (4, 4, "citation_range_out_of_bounds"),
        (5, 5, "citation_range_out_of_bounds"),
        (None, 1, "citation_range_invalid"),
    ],
)
def test_bad_saved_ranges_stay_bad_without_expansion(start: int | None, end: int | None, reason: str) -> None:
    evidence = [span(1, "開く\n保存")]
    grounded.format_functions(evidence)
    original = draft(start, end)
    before = original.model_dump()
    cited, error = extract(original, evidence)
    assert error and not cited.quote
    assert cited.citations[0]._extraction["reason_code"] == reason
    assert cited.text == original.items[0].text
    assert original.model_dump() == before


def test_reordered_siblings_resolve_current_labels_and_never_old_view_content() -> None:
    first, second = span(1, "開く"), span(2, "保存")
    grounded.format_functions([first, second])
    previous, error = extract(draft(1, 1), [first, second])
    assert not error and previous.quote == "開く"
    reordered = [dict(second, label="E1"), dict(first, label="E2")]
    grounded.format_functions(reordered)
    current, error = extract(draft(1, 1), reordered)
    assert not error and current.quote == "保存"
    assert current.citations[0]._extraction["source_id"] == "parent-2"
    assert previous.quote == "開く"
    assert current.citations[0]._extraction["view_sha256"] != previous.citations[0]._extraction["view_sha256"]
