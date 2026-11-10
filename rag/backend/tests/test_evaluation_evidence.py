"""評価 Excel の根拠は確定した採用と保存位置だけから作る。"""

from typing import Any

import pytest

from app.clients.oracle import (
    OracleClient,
    _answer_record_scope_sql,
    _with_answer_record_scope_bind,
)
from app.rag.evaluation_evidence import used_evidence_lines
from app.rag.evaluation_excel import EvidenceSpec, parse_evidence_line


def cite(name: str, **metadata: Any) -> dict[str, Any]:
    return {
        "source": name,
        "document_id": "doc-a",
        "metadata": {"evidence_model_used": True, **metadata},
    }


@pytest.mark.parametrize(
    ("citation", "expected"),
    [
        (cite("合成.pdf", page_start=3, page_end=5), "合成.pdf | p.3-5"),
        (cite("合成.pptx", page=2), "合成.pptx | スライド: 2"),
        (
            cite("合成.xlsx", sheet_name="Sheet1", row_start=10, row_end=12),
            "合成.xlsx | シート: Sheet1 | 行: 10-12",
        ),
        (cite("合成.md", section_path=["設定", "準備"]), "合成.md | 章節: 設定 > 準備"),
        (cite("", page=4), "文書ID: doc-a | p.4"),
    ],
)
def test_template_location_round_trip(citation: dict[str, Any], expected: str) -> None:
    assert used_evidence_lines([citation, citation]) == expected
    assert isinstance(parse_evidence_line(expected), EvidenceSpec)


@pytest.mark.parametrize(
    ("citation", "expected"),
    [
        (
            cite("合成.xlsx", page=2, sheet_name="Sheet1", row_start=10, row_end=12),
            "合成.xlsx | シート: Sheet1 | 行: 10-12",
        ),
        (cite("合成.md", page=1, section_path="設定"), "合成.md | 章節: 設定"),
        (cite("合成.pdf", page=3, row_start=10, row_end=12), "合成.pdf | p.3"),
    ],
)
def test_locations_follow_template_document_format(citation: dict[str, Any], expected: str) -> None:
    assert used_evidence_lines([citation]) == expected


def test_candidates_parent_only_and_legacy_records_are_not_actual_evidence() -> None:
    assert (
        used_evidence_lines(
            [
                cite("旧.pdf", evidence_model_used=None),
                cite("候補.pdf", evidence_model_used=False),
                cite("親.pdf", evidence_model_used=False, evidence_parent_used=True),
                cite("未確定.pdf", answer_citation_candidate_lines=[1]),
                {"source": "記録なし.pdf"},
            ]
        )
        == ""
    )
    assert (
        used_evidence_lines(
            [
                cite(
                    "確定.pdf",
                    page=2,
                    answer_citation_lines=[1],
                    answer_citation_provenance="child",
                    answer_citation_candidate_lines=[2],
                )
            ]
        )
        == "確定.pdf | p.2"
    )


async def test_saved_citations_reader_scopes_batches_and_skips_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fetch(self: OracleClient, sql: str, binds: dict[str, object]) -> list[dict[str, Any]]:
        calls.append((sql, binds))
        return [
            {
                "trace_id": "final-a",
                "citations_json": '[{"source":"合成.pdf","metadata":{"evidence_model_used":true}}]',
            },
            {"trace_id": "final-b", "citations_json": [cite("合成.xlsx")]},
            {"trace_id": "broken", "citations_json": "invalid"},
        ]

    monkeypatch.setattr(OracleClient, "_fetch_all", fetch)
    result = await OracleClient().evaluation_citations_by_trace_ids(
        ["final-a", "final-a", "final-b", ""]
    )
    assert set(result) == {"final-a", "final-b"}
    assert len(calls) == 1
    assert _answer_record_scope_sql() in calls[0][0]
    assert calls[0][1] == _with_answer_record_scope_bind(
        {"trace_id_0": "final-a", "trace_id_1": "final-b"}
    )
