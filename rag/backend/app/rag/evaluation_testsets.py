"""本人の完了評価から入力と参考を写し、確認済みの六列だけを書き出す。LLM は使わない。"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from app.clients.evaluation_testset_store import (
    MemoryTestSetStore,
    OracleTestSetStore,
    TestSetStore,
    owner_binds,
)
from app.clients.oracle import OracleClient, _json_loads
from app.rag.evaluation_excel import (
    EvaluationExcelRow,
    EvaluationTemplateRow,
    evaluation_cases_xlsx,
    parse_evaluation_workbook,
)
from app.rag.evaluation_source_workbooks import (
    EvaluationSourceWorkbookNotFoundError,
    get_evaluation_source_workbook_service,
)
from app.schemas.evaluation_testsets import (
    TestSetDraft,
    TestSetFields,
    TestSetItem,
    TestSetSaveRequest,
)


class TestSetError(ValueError):
    """利用者に表示できる入力・競合の問題。"""


class TestSetSourceReader(Protocol):
    async def list_jobs(self) -> list[dict[str, Any]]: ...
    async def job(self, job_id: str) -> Mapping[str, Any] | None: ...
    async def rows(self, workbook_id: str) -> Sequence[EvaluationExcelRow]: ...
    async def answer(self, trace_id: str) -> Mapping[str, Any] | None: ...


class OracleTestSetSourceReader:
    def __init__(self, oracle: OracleClient | None = None) -> None:
        self.oracle = oracle or OracleClient()

    async def list_jobs(self) -> list[dict[str, Any]]:
        if not self.oracle.is_connection_configured():
            return []
        return await self.oracle._fetch_all(
            "SELECT job_id, created_at, finished_at, total_cases FROM rag_evaluation_jobs "
            "WHERE kind = 'run' AND status = 'SUCCEEDED' "
            "AND user_id_hash = :user_id_hash "
            "AND (tenant_id_hash = :tenant_id_hash OR "
            "(tenant_id_hash IS NULL AND :tenant_id_hash IS NULL)) "
            "ORDER BY finished_at DESC, job_id FETCH FIRST 200 ROWS ONLY",
            owner_binds(),
        )

    async def job(self, job_id: str) -> Mapping[str, Any] | None:
        row = await self.oracle._fetch_one(
            "SELECT result_json, source_workbook_id FROM rag_evaluation_jobs "
            "WHERE job_id = :job_id AND kind = 'run' AND status = 'SUCCEEDED' "
            "AND user_id_hash = :user_id_hash "
            "AND (tenant_id_hash = :tenant_id_hash OR "
            "(tenant_id_hash IS NULL AND :tenant_id_hash IS NULL))",
            dict(owner_binds(), job_id=job_id),
        )
        if row is None:
            return None
        return dict(
            result=_json_loads(row.get("result_json")),
            source_workbook_id=row.get("source_workbook_id"),
        )

    async def rows(self, workbook_id: str) -> Sequence[EvaluationExcelRow]:
        workbook = await get_evaluation_source_workbook_service().load(workbook_id)
        parsed = await asyncio.to_thread(parse_evaluation_workbook, workbook.content)
        if parsed.issues:
            raise TestSetError("元の評価リクエストを読めません。入力を確認してください。")
        return parsed.rows

    async def answer(self, trace_id: str) -> Mapping[str, Any] | None:
        return await self.oracle.get_answer_record(trace_id)


def _fields(row: EvaluationExcelRow) -> TestSetFields:
    return TestSetFields(
        question=row.question,
        standard_answer=row.standard_answer or "",
        evidence=[value.line for value in row.evidence],
        required_answer_points=list(row.screen_lines),
        response_decision=row.response_decision or "answer",
        answer_process=row.answer_process or "",
    )


async def source_items(
    reader: TestSetSourceReader,
    job_ids: Sequence[str],
    *,
    include_references: bool = True,
    item_ids: set[str] | None = None,
) -> list[TestSetItem]:
    items: list[TestSetItem] = []
    for job_id in dict.fromkeys(job_ids):
        job = await reader.job(job_id)
        if job is None:
            raise TestSetError("本人が実行した完了済みの評価を選んでください。")
        rows: dict[str, EvaluationExcelRow] = {}
        workbook_id = job.get("source_workbook_id")
        notice = None
        if workbook_id:
            try:
                rows = {row.case_id: row for row in await reader.rows(str(workbook_id))}
            except EvaluationSourceWorkbookNotFoundError:
                notice = "元の評価リクエストが残っていません。各入力を確認してください。"
        else:
            notice = "元の評価リクエストが残っていません。各入力を確認してください。"
        result = job.get("result")
        if not isinstance(result, Mapping):
            continue
        passes = [result]
        comparison = result.get("comparison")
        if isinstance(comparison, Mapping) and isinstance(comparison.get("metrics"), Mapping):
            passes.append(comparison["metrics"])
        for pass_index, metrics in enumerate(passes):
            cases = metrics.get("case_results")
            if not isinstance(cases, list):
                continue
            seen: set[str] = set()
            for case in cases:
                if not isinstance(case, Mapping) or not case.get("case_id"):
                    continue
                case_id = str(case["case_id"])
                if case_id in seen:
                    continue
                seen.add(case_id)
                item_id = uuid5(NAMESPACE_URL, f"evaluation:{job_id}:{pass_index}:{case_id}").hex
                if item_ids is not None and item_id not in item_ids:
                    continue
                trace_id = str(case.get("trace_id") or "")
                # 選択一覧は元の入力だけで足りる。選んだ項目の参考だけを下書き作成時に読む。
                read_answer = include_references or case_id not in rows
                record = await reader.answer(trace_id) if trace_id and read_answer else None
                record = record or {}
                original = (
                    _fields(rows[case_id])
                    if case_id in rows
                    else TestSetFields(
                        question=str(record.get("question") or ""),
                    )
                )
                citations = record.get("citations_json")
                reference = (
                    [dict(value) for value in citations if isinstance(value, dict)]
                    if isinstance(citations, list)
                    else []
                )
                items.append(
                    TestSetItem(
                        **original.model_dump(),
                        item_id=item_id,
                        source_job_id=job_id,
                        source_case_id=case_id,
                        source_pass=pass_index,
                        original=original,
                        reference_answer=str(record.get("answer") or ""),
                        reference_evidence=reference,
                        source_notice=(
                            notice or "元の入力が見つかりません。各入力を確認してください。"
                        )
                        if case_id not in rows
                        else None,
                    )
                )
    return items


def new_draft(name: str, items: list[TestSetItem]) -> TestSetDraft:
    now = datetime.now(UTC)
    return TestSetDraft(
        draft_id=uuid4().hex, name=name, revision=1, created_at=now, updated_at=now, items=items
    )


def edited_draft(previous: TestSetDraft, body: TestSetSaveRequest) -> TestSetDraft:
    if len({item.item_id for item in body.items}) != len(body.items):
        raise TestSetError("同じ項目が重複しています。")
    originals = {item.item_id: item for item in previous.items}
    if set(originals) != {item.item_id for item in body.items}:
        raise TestSetError("保存済みの項目をそのまま指定してください。")
    items = []
    for edit in body.items:
        old = originals[edit.item_id]
        fields = TestSetFields.model_validate(edit.model_dump(exclude={"item_id", "confirmed"}))
        changed = fields != TestSetFields.model_validate(
            old.model_dump(include=set(TestSetFields.model_fields))
        )
        confirmed = edit.confirmed and not changed
        items.append(old.model_copy(update={**fields.model_dump(), "confirmed": confirmed}))
    if confirmed_items := [item for item in items if item.confirmed]:
        validate_templates(confirmed_items)
    updated = previous.model_copy(
        update=dict(
            name=body.name,
            items=items,
            revision=previous.revision + 1,
            updated_at=datetime.now(UTC),
        )
    )

    return TestSetDraft.model_validate(updated.model_dump())


def template_row(item: TestSetFields) -> EvaluationTemplateRow:
    return EvaluationTemplateRow(
        question=item.question,
        evidence=item.evidence,
        standard_answer=item.standard_answer,
        required_answer_points=item.required_answer_points,
        response_decision=item.response_decision,
        answer_process=item.answer_process,
    )


def validate_templates(items: Sequence[TestSetFields]) -> bytes:
    content = evaluation_cases_xlsx([template_row(item) for item in items])
    parsed = parse_evaluation_workbook(content)
    if parsed.issues:
        raise TestSetError(" / ".join(issue.message for issue in parsed.issues))
    return content


def export_draft(draft: TestSetDraft) -> bytes:
    confirmed = [item for item in draft.items if item.confirmed]
    if not confirmed:
        raise TestSetError("収録を確認した項目がありません。")
    return validate_templates(confirmed)


_STORE: TestSetStore | None = None
_MEMORY = MemoryTestSetStore()
_READER: TestSetSourceReader | None = None


def get_store() -> TestSetStore:
    if _STORE is not None:
        return _STORE
    oracle = OracleClient()
    return OracleTestSetStore(oracle) if oracle.is_connection_configured() else _MEMORY


def get_reader() -> TestSetSourceReader:
    return _READER or OracleTestSetSourceReader()
