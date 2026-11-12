"""本人の下書き、明示確認、テンプレート六列往復（合成データ）。"""

from io import BytesIO
from typing import Any

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.clients.evaluation_testset_store import MemoryTestSetStore
from app.rag.evaluation_excel import (
    HEADERS,
    EvaluationDocumentRef,
    EvaluationExcelRow,
    EvaluationTemplateRow,
    build_evaluation_cases,
    evaluation_cases_xlsx,
    parse_evaluation_workbook,
)
from app.rag.evaluation_testsets import (
    TestSetError as DraftError,
)
from app.rag.evaluation_testsets import (
    edited_draft,
    export_draft,
    new_draft,
    source_items,
)
from app.schemas.evaluation_testsets import (
    TestSetDraft as Draft,
)
from app.schemas.evaluation_testsets import (
    TestSetEdit as DraftEdit,
)
from app.schemas.evaluation_testsets import (
    TestSetFields as DraftFields,
)
from app.schemas.evaluation_testsets import (
    TestSetItem as DraftItem,
)
from app.schemas.evaluation_testsets import (
    TestSetSaveRequest as DraftSaveRequest,
)


def item(**changes: Any) -> DraftItem:
    fields = DraftFields(question="合成の質問", standard_answer="合成の標準回答")
    values = dict(
        fields.model_dump(),
        item_id="i1",
        source_job_id="j1",
        source_case_id="row-2",
        original=fields,
    )
    values.update(changes)
    return DraftItem(**values)


def body(draft: Draft, **changes: Any) -> DraftSaveRequest:
    values = [entry.model_dump(include=set(DraftEdit.model_fields)) for entry in draft.items]
    values[0].update(changes)
    return DraftSaveRequest(name=draft.name, revision=draft.revision, items=values)


def test_confirm_edit_revokes_and_reference_is_never_promoted() -> None:
    draft = new_draft("合成の下書き", [item(reference_answer="参考の回答")])
    assert not draft.items[0].confirmed
    confirmed = edited_draft(draft, body(draft, confirmed=True))
    assert confirmed.items[0].confirmed
    changed = edited_draft(
        confirmed, body(confirmed, standard_answer="修正した回答", confirmed=True)
    )
    assert not changed.items[0].confirmed
    assert changed.items[0].original.standard_answer == "合成の標準回答"
    assert changed.items[0].reference_answer == "参考の回答"
    assert edited_draft(changed, body(changed, confirmed=True)).items[0].confirmed


@pytest.mark.parametrize("decision", ["clarify", "hold"])
def test_clarification_hold_allow_empty_standard(decision: str) -> None:
    draft = new_draft("合成", [item()])
    updated = edited_draft(draft, body(draft, standard_answer="", response_decision=decision))
    confirmed = edited_draft(updated, body(updated, confirmed=True))
    row = parse_evaluation_workbook(export_draft(confirmed)).rows[0]
    assert row.standard_answer is None and row.response_decision == decision


def test_answer_requires_standard_and_unconfirmed_cannot_export() -> None:
    draft = new_draft("合成", [item()])
    with pytest.raises(ValidationError):
        body(draft, standard_answer=" ", confirmed=True)
    with pytest.raises(DraftError, match="確認した項目"):
        export_draft(draft)


def test_all_columns_locations_and_literal_text_roundtrip() -> None:
    evidence = "合成資料.pdf | 文書ID: doc-a | p.2-4 | 章節: 合成章 | 抜粋: 合成の本文"
    table = "合成表.xlsx | 文書ID: doc-b | シート: 合成シート | 行: 10-12 | 抜粋: 合成の値"
    content = evaluation_cases_xlsx(
        [
            EvaluationTemplateRow(
                question="=合成の質問",
                evidence=[evidence, table],
                standard_answer="=合成の回答",
                required_answer_points=["合成の要点"],
                response_decision="answer",
                answer_process="合成の手順",
            )
        ]
    )
    book = load_workbook(BytesIO(content), data_only=False)
    sheet = book["評価リクエスト"]
    assert tuple(cell.value for cell in sheet[1]) == HEADERS
    assert sheet["A2"].data_type == sheet["C2"].data_type == "s"
    book.close()
    parsed = parse_evaluation_workbook(content)
    assert not parsed.issues
    row = parsed.rows[0]
    assert row.screen_lines == ("合成の要点",) and row.answer_process == "合成の手順"
    built = build_evaluation_cases(
        parsed.rows,
        [
            EvaluationDocumentRef("doc-a", "合成資料.pdf"),
            EvaluationDocumentRef("doc-b", "合成表.xlsx"),
        ],
    )
    assert not built.issues
    case = built.cases[0]
    assert case.expected_response_decision == "answer"
    assert case.required_evidence[0].page_start == 2 and case.required_evidence[0].page_end == 4
    assert (
        case.required_evidence[1].sheet == "合成シート" and case.required_evidence[1].row_end == 12
    )


def test_only_confirmed_export_preserves_unfinished_snapshot() -> None:
    first = item(confirmed=True)
    draft = new_draft(
        "合成", [first, first.model_copy(update={"item_id": "i2", "confirmed": False})]
    )
    assert len(parse_evaluation_workbook(export_draft(draft)).rows) == 1
    assert len(draft.items) == 2


class Reader:
    async def list_jobs(self) -> list[dict[str, Any]]:
        return []

    async def rows(self, workbook_id: str) -> list[EvaluationExcelRow]:
        return []

    async def job(self, job_id: str) -> dict[str, Any]:
        return {"result": {"case_results": [{"case_id": "row-2", "trace_id": "trace"}]}}

    async def answer(self, trace_id: str) -> dict[str, Any]:
        return {"question": "同じ合成の質問", "answer": "未確認の生成回答"}


@pytest.mark.asyncio
async def test_duplicates_independent_and_generated_never_standard() -> None:
    values = await source_items(Reader(), ["job-one", "job-two"])
    assert len(values) == 2 and values[0].item_id != values[1].item_id
    assert all(not entry.standard_answer and not entry.confirmed for entry in values)
    assert all(entry.source_notice for entry in values)


def test_invalid_evidence_rejects_confirmation() -> None:
    draft = new_draft("合成", [item()])
    draft = edited_draft(draft, body(draft, evidence=["合成.pdf | p.4-2"]))
    with pytest.raises(DraftError):
        edited_draft(draft, body(draft, confirmed=True))


@pytest.mark.asyncio
async def test_store_optimistic_revision_and_snapshot_copy() -> None:
    store = MemoryTestSetStore()
    draft = new_draft("合成", [item()])
    await store.create(draft)
    draft.name = "外側の変更"
    saved = await store.get(draft.draft_id)
    assert saved is not None and saved.name == "合成"
    changed = edited_draft(saved, body(saved, confirmed=True))
    assert await store.save(changed, 1)
    assert not await store.save(changed, 1)


@pytest.mark.asyncio
async def test_api_owner_save_reopen_export_and_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.main import app
    from app.rag import evaluation_testsets as service
    from tests.support import TEST_REQUEST_HEADERS

    reader = Reader()

    monkeypatch.setattr(service, "_STORE", MemoryTestSetStore())
    monkeypatch.setattr(service, "_READER", reader)
    transport = httpx.ASGITransport(app=app)
    base = "/api/evaluation/testsets"
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=dict(TEST_REQUEST_HEADERS, **{"X-User-ID": "owner-a"}),
    ) as client:
        candidates = (
            await client.post(base + "/sources/items", json={"job_ids": ["job-a"]})
        ).json()["data"]
        result = await client.post(
            base,
            json={
                "name": "合成下書き",
                "job_ids": ["job-a"],
                "item_ids": [candidates[0]["item_id"]],
            },
        )
        assert result.status_code == 201, result.text
        draft = result.json()["data"]
        draft_id = draft["draft_id"]
        payload = {
            "name": draft["name"],
            "revision": 1,
            "items": [
                {
                    key: value
                    for key, value in draft["items"][0].items()
                    if key in DraftEdit.model_fields
                }
            ],
        }
        payload["items"][0]["standard_answer"] = "合成の標準回答"
        saved = await client.put(base + "/" + draft_id, json=payload)
        assert saved.status_code == 200, saved.text
        assert not saved.json()["data"]["items"][0]["confirmed"]
        assert (await client.put(base + "/" + draft_id, json=payload)).status_code == 409
        payload["revision"] = 2
        payload["items"][0]["confirmed"] = True
        confirmed = await client.put(base + "/" + draft_id, json=payload)
        assert confirmed.status_code == 200, confirmed.text
        download = await client.get(base + "/" + draft_id + "/export.xlsx?revision=3")
        assert download.status_code == 200, download.text
        assert len(parse_evaluation_workbook(download.content).rows) == 1
        assert (
            await client.get(base + "/" + draft_id + "/export.xlsx?revision=2")
        ).status_code == 409
        # 別人（ローカルの構成管理者を含む）は一覧・詳細・編集・出力から分離される。
        other = dict(TEST_REQUEST_HEADERS, **{"X-User-ID": "owner-b"})
        assert (await client.get(base, headers=other)).json()["data"] == []
        assert (await client.get(base + "/" + draft_id, headers=other)).status_code == 404
        assert (
            await client.put(base + "/" + draft_id, headers=other, json=payload)
        ).status_code == 404
        assert (
            await client.get(base + "/" + draft_id + "/export.xlsx?revision=3", headers=other)
        ).status_code == 404
        # 元の評価が無くなっても、保存した入力と参考は読み直せる。
        monkeypatch.setattr(service, "_READER", None)
        reopened = (await client.get(base + "/" + draft_id)).json()["data"]
        assert reopened["items"][0]["reference_answer"] == "未確認の生成回答"
        assert reopened["items"][0]["confirmed"]


def test_atomic_review_add_remove_and_edit_revokes_confirmation() -> None:
    from app.rag.evaluation_testsets import manual_item

    initial = new_draft("合成のセット", [manual_item(DraftFields())])
    edit = DraftEdit(
        item_id=initial.items[0].item_id, question="合成の質問", standard_answer="合成の標準回答"
    )
    reviewed = edited_draft(
        initial,
        DraftSaveRequest(
            name=initial.name, revision=1, items=[edit], confirm_item_ids=[edit.item_id]
        ),
    )
    assert reviewed.items[0].confirmed
    changed = edited_draft(
        reviewed,
        DraftSaveRequest(
            name=reviewed.name,
            revision=reviewed.revision,
            items=[edit.model_copy(update={"question": "変更した合成の質問", "confirmed": True})],
        ),
    )
    assert not changed.items[0].confirmed
    added = edited_draft(
        changed,
        DraftSaveRequest(
            name=changed.name,
            revision=changed.revision,
            items=[
                DraftEdit(
                    item_id="new-item",
                    question="追加の合成質問",
                    standard_answer="合成の回答",
                    confirmed=True,
                )
            ],
        ),
    )
    assert [row.item_id for row in added.items] == ["new-item"]
    assert added.items[0].source_kind == "manual" and not added.items[0].reference_answer
    assert not added.items[0].confirmed
    with pytest.raises((DraftError, ValidationError)):
        edited_draft(
            added,
            DraftSaveRequest(
                name=added.name,
                revision=added.revision,
                items=[DraftEdit(item_id="new-item")],
                confirm_item_ids=["new-item"],
            ),
        )


def test_import_is_pending_and_keeps_all_six_columns() -> None:
    from app.rag.evaluation_testsets import imported_items

    content = evaluation_cases_xlsx(
        [
            EvaluationTemplateRow(
                question="=合成の質問",
                standard_answer="合成の回答",
                evidence=[],
                required_answer_points=["合成の要点"],
                response_decision="answer",
                answer_process="合成の手順",
            )
        ]
    )
    items = imported_items(content)
    assert len(items) == 1 and not items[0].confirmed
    assert items[0].source_kind == "excel"
    assert items[0].question == "=合成の質問"
    assert items[0].required_answer_points == ["合成の要点"]
    assert items[0].answer_process == "合成の手順"
    assert items[0].reference_answer == ""
