"""REVIEW 中の表セルテキスト修正 `_apply_table_cell_edits` の単体テスト。"""

import pytest
from fastapi import HTTPException

from app.api.routes.documents import _apply_table_cell_edits
from app.schemas.document import DocumentTableCellTextEdit
from app.schemas.extraction import (
    ExtractionTable,
    ExtractionTableCell,
    StructuredExtraction,
)


def _extraction_with_table() -> StructuredExtraction:
    return StructuredExtraction(
        raw_text="料金表",
        tables=[
            ExtractionTable(
                table_id="tbl-1",
                page_number=1,
                cells=[
                    ExtractionTableCell(row=0, col=0, text="項目", bbox=[0, 0, 50, 10]),
                    ExtractionTableCell(row=0, col=1, text="値", bbox=[50, 0, 100, 10]),
                    ExtractionTableCell(row=1, col=0, text="交通費", bbox=[0, 10, 50, 20]),
                    ExtractionTableCell(row=1, col=1, text="1000円", bbox=[50, 10, 100, 20]),
                ],
            )
        ],
    )


def test_apply_table_cell_edits_replaces_only_targeted_cell_text() -> None:
    """指定セルの text のみ差し替え、他セル・bbox・構造は保持する。"""
    extraction = _extraction_with_table()
    updated = _apply_table_cell_edits(
        extraction,
        [DocumentTableCellTextEdit(table_id="tbl-1", row=1, col=1, text="2000円")],
    )

    cells = {(c.row, c.col): c for c in updated.tables[0].cells}
    assert cells[(1, 1)].text == "2000円"
    assert cells[(1, 1)].bbox == [50, 10, 100, 20]
    # 他セルは不変。
    assert cells[(0, 0)].text == "項目"
    assert cells[(1, 0)].text == "交通費"


def test_apply_table_cell_edits_rejects_unknown_cell() -> None:
    """存在しない表セルの修正は 400。"""
    extraction = _extraction_with_table()
    with pytest.raises(HTTPException) as exc_info:
        _apply_table_cell_edits(
            extraction,
            [DocumentTableCellTextEdit(table_id="tbl-1", row=9, col=9, text="x")],
        )
    assert exc_info.value.status_code == 400


def test_apply_table_cell_edits_rejects_unknown_table() -> None:
    """存在しない table_id の修正は 400。"""
    extraction = _extraction_with_table()
    with pytest.raises(HTTPException) as exc_info:
        _apply_table_cell_edits(
            extraction,
            [DocumentTableCellTextEdit(table_id="missing", row=0, col=0, text="x")],
        )
    assert exc_info.value.status_code == 400
