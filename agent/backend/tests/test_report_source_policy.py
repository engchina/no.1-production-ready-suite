"""報告の出典検証と、数値検査用の原文ID集合を混同しない。"""

import pytest
from test_source_table_patches import fixture

from app.features.agent import builtin_runtime, source_handles
from app.features.agent.answer_data import DataReferenceError
from app.features.agent.report_contract import AnswerPlan, NarrativeBlock
from app.features.agent.report_runtime import render_plan


@pytest.mark.parametrize("with_data", [False, True])
@pytest.mark.parametrize("citation", ["[[cite:1]]", "【根拠: derived-a】"])
def test_report_retains_validated_derived_citation_outside_numeric_mask(
    with_data: bool, citation: str
) -> None:
    current, _, _, _ = fixture(with_data, "derived-a")
    output = current.steps[0].tool_result.output
    output["evidence"][0]["origin"] = "derived"
    current.metadata[source_handles.REGISTRY_KEY] = []
    source_handles.register_output(current.metadata, "rag", output, tool="rag_retrieve_evidence")
    trusted = builtin_runtime._citable_source_ids(current)
    assert trusted == frozenset()
    rendered = render_plan(
        current,
        AnswerPlan(blocks=(NarrativeBlock(text=citation),)),
        trusted_sources=trusted,
        render_sources=lambda text: builtin_runtime._render_source_citations(current, text),
        finalize=lambda body, _evidence: body,
    )
    assert "【根拠: derived-a】" in rendered.body
    assert rendered.evidence_text == "【根拠: derived-a】"


def test_report_rejects_unknown_canonical_source_before_selecting_evidence() -> None:
    current, _, _, _ = fixture(False)
    with pytest.raises(DataReferenceError):
        render_plan(
            current,
            AnswerPlan(blocks=(NarrativeBlock(text="【根拠: unknown-a】"),)),
            trusted_sources=builtin_runtime._citable_source_ids(current),
            render_sources=lambda text: builtin_runtime._render_source_citations(current, text),
            finalize=lambda body, _evidence: body,
        )
