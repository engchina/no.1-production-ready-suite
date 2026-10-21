"""HTML 表の必要根拠は、同じ行の表示本文と位置条件を照合する。"""

import pytest

from app.rag.evaluation import evidence_matches_chunk
from app.schemas.evaluation import EvaluationEvidence
from app.schemas.search import RetrievedChunk


def _chunk(text: str) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="doc-a",
        chunk_id="chunk-a",
        text=text,
        score=1.0,
        metadata={"page_start": 2, "page_end": 2},
    )


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<table><tr><td>行 A</td><td>10</td><td>20</td></tr></table>", "行 A 10 20"),
        ("<TABLE><TR><TD>A &amp; B</TD><TD><b>10</b><br>円</TD></TR></TABLE>", "A & B 10 円"),
        ("<table><tr><th colspan='2'>行 A</th><td>10</td></tr></table>", "行 A 10"),
        ("<table><tr><td>行 A<td>10<td>20</table>", "行 A 10 20"),
        ("本文の条件 42。<table><tr><td>別の値</td></tr></table>", "本文の条件 42"),
        ("<table><tr><td>別の値</td></tr></table>末尾の条件 43。", "末尾の条件 43"),
    ],
)
def test_visible_table_row_matches(html: str, expected: str) -> None:
    assert evidence_matches_chunk(
        EvaluationEvidence(id="e", document_id="doc-a", page_start=2, text=expected),
        _chunk(html),
    )


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<table><tr><td>行 A</td><td>10</td><td>20</td></tr></table>", "行 A 11 20"),
        ("<table><tr><td>行 A</td><td>10</td><td>20</td></tr></table>", "行 A 20 10"),
        ("<table><tr><td>行 A</td><td>10</td></tr><tr><td>20</td></tr></table>", "行 A 10 20"),
        ("<table><tr><td title='行 A 10 20'>別の値</td></tr></table>", "行 A 10 20"),
        ("<div>行 A</div><div>10</div><div>20</div>", "行 A 10 20"),
        ("行 A | 10 | 20", "行 A 10 20"),
        ("行 A<table><tr><td>別の値</td></tr></table>10 20", "行 A 10 20"),
    ],
)
def test_different_or_unrelated_text_does_not_match(html: str, expected: str) -> None:
    assert not evidence_matches_chunk(EvaluationEvidence(id="e", text=expected), _chunk(html))


@pytest.mark.parametrize("condition", [{"document_id": "other"}, {"page_start": 3}])
def test_table_match_still_requires_document_and_page(condition: dict[str, object]) -> None:
    evidence = EvaluationEvidence.model_validate({"id": "e", "text": "行 A 10", **condition})
    assert not evidence_matches_chunk(
        evidence, _chunk("<table><tr><td>行 A</td><td>10</td></tr></table>")
    )
